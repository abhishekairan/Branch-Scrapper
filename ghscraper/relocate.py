"""Moving the data folder (the ``storage.move`` RPC).

The data of the current folder is only ever *copied*; the old folder is never modified or deleted. The switch
itself is the pointer file (``config.write_location_pointer``), written last and atomically, so any failure before
that point leaves the app exactly as it was.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import write_location_pointer
from .store.db import MIGRATIONS

log = logging.getLogger("ghscraper.relocate")

DB_FILES = ("data.db", "data.db-wal", "data.db-shm")      # data.db first; the others only if present
SPACE_MARGIN = 1.05


class MoveError(Exception):
    """A refusal or failure with a message for the user. `kind`: "invalid" (bad target) or "failed" (I/O)."""

    def __init__(self, message: str, kind: str = "invalid"):
        super().__init__(message)
        self.kind = kind


@dataclass
class MovePlan:
    source: Path
    target: Path
    mode: str                          # "copy" (empty / new folder) | "switch" (target already has data.db)
    bytes_to_copy: int = 0
    existing: dict[str, int] | None = None     # table row counts of the data found at the target (mode "switch")

    def describe(self) -> dict:
        return {"mode": self.mode, "from": str(self.source), "to": str(self.target),
                "bytes_to_copy": self.bytes_to_copy, "existing": self.existing}


# ------------------------------------------------------------------ helpers
def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=10)


def table_counts(db: Path) -> dict[str, int]:
    """Row count of every table (FTS shadow tables included), read-only."""
    con = _ro(db)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


def _user_version(db: Path) -> int:
    con = _ro(db)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def _check_existing_db(db: Path) -> dict[str, int]:
    """Counts of an existing Branch Scrapper database, or MoveError if it is not one we can use."""
    try:
        version = _user_version(db)
        counts = table_counts(db)
    except sqlite3.Error as e:
        raise MoveError(f"{db} is not a readable Branch Scrapper database ({e})") from e
    if "repos" not in counts or "runs" not in counts:
        raise MoveError(f"{db} is not a Branch Scrapper database (no repos/runs tables)")
    if version > len(MIGRATIONS):
        raise MoveError(f"the data in that folder was written by a newer Branch Scrapper (schema v{version}); "
                        "update the app first")
    return counts


def _writable(folder: Path) -> None:
    try:
        fd, name = tempfile.mkstemp(prefix=".bs-write-test-", dir=folder)
        os.close(fd)
        os.unlink(name)
    except OSError as e:
        raise MoveError(f"cannot write to {folder}: {e}") from e


def _nearest_existing(p: Path) -> Path:
    while not p.exists():
        if p.parent == p:
            break
        p = p.parent
    return p


def _files_to_copy(src: Path) -> list[Path]:
    out = [src / n for n in DB_FILES if (src / n).is_file()]
    if (src / "config.json").is_file():
        out.append(src / "config.json")
    if (src / "logs").is_dir():
        out += [p for p in (src / "logs").rglob("*") if p.is_file()]
    return out


def _size(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


# ------------------------------------------------------------------ validation
def plan_move(current: Path, raw_target: object, default_dir: Path) -> MovePlan:
    """Validate a move to `raw_target`. Creates nothing (apart from a throw-away probe file)."""
    if not isinstance(raw_target, str) or not raw_target.strip() or not Path(raw_target).is_absolute():
        raise MoveError("the new folder must be an absolute path")
    target = Path(raw_target).resolve()
    cur = current.resolve()
    if target == cur:
        raise MoveError("that is the folder already in use")
    if target == default_dir.resolve():
        raise MoveError("that is the default location; use \"Use the default location\" to go back to it")
    if cur in target.parents:
        raise MoveError("the new folder cannot be inside the current data folder")
    if target in cur.parents:
        raise MoveError("the new folder cannot contain the current data folder")
    if target.parent == target:
        raise MoveError("choose a folder, not a drive root")

    if target.exists():
        if not target.is_dir():
            raise MoveError(f"{target} is a file, not a folder")
        try:
            entries = list(target.iterdir())
        except OSError as e:
            raise MoveError(f"cannot read {target}: {e}") from e
        _writable(target)
        if not entries:
            mode = "copy"
        elif (target / "data.db").is_file():
            return MovePlan(cur, target, "switch", 0, _check_existing_db(target / "data.db"))
        else:
            raise MoveError(f"{target} is not empty and holds no Branch Scrapper data (no data.db). "
                            "Choose an empty or new folder.")
    else:
        anchor = _nearest_existing(target)
        if not anchor.is_dir():
            raise MoveError(f"{anchor} is a file, not a folder")
        _writable(anchor)
        mode = "copy"

    need = _size(_files_to_copy(cur))
    free = shutil.disk_usage(_nearest_existing(target)).free
    if free < need * SPACE_MARGIN + (1 << 20):
        raise MoveError(f"not enough free space at the new location ({need // (1 << 20)} MB needed, "
                        f"{free // (1 << 20)} MB free)")
    return MovePlan(cur, target, mode, need)


# ------------------------------------------------------------------ the move
def _checkpoint(db: Path) -> None:
    """Fold the WAL into data.db so the copy is self-contained. A failure only means -wal is copied as well."""
    try:
        con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=rw", uri=True, timeout=10)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        log.warning("storage.move: WAL checkpoint failed (%s); copying data.db-wal too", e)


def copy_data(plan: MovePlan) -> list[str]:
    """Copy data.db (+ -wal/-shm), config.json and logs/ into a temp sibling, verify, rename into place.
    Returns warnings (the logs are best effort). Raises MoveError("failed") and leaves no trace on failure."""
    src, target = plan.source, plan.target
    warnings: list[str] = []
    tmp = target.parent / f"{target.name}.moving-{os.getpid()}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        if (src / "data.db").is_file():
            _checkpoint(src / "data.db")
        for name in (*DB_FILES, "config.json"):
            if (src / name).is_file():
                shutil.copy2(src / name, tmp / name)
        if (src / "logs").is_dir():
            try:
                shutil.copytree(src / "logs", tmp / "logs")
            except (OSError, shutil.Error) as e:      # logs matter least; the sidecar holds sidecar.log open
                warnings.append(f"logs were not copied: {e}")
                shutil.rmtree(tmp / "logs", ignore_errors=True)
        if (src / "data.db").is_file():
            _verify_copy(src / "data.db", tmp / "data.db")
        if target.exists():
            os.rmdir(target)               # empty (checked in plan_move); fails if somebody filled it meanwhile
        os.rename(tmp, target)             # atomic on one volume
    except MoveError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except (OSError, shutil.Error, sqlite3.Error) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise MoveError(f"could not copy the data to {target}: {e}", "failed") from e
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return warnings


def _verify_copy(src_db: Path, copy_db: Path) -> None:
    if _user_version(copy_db) != _user_version(src_db):
        raise MoveError("verification failed: the copied database has a different schema version", "failed")
    a, b = table_counts(src_db), table_counts(copy_db)
    if a != b:
        diff = sorted(t for t in set(a) | set(b) if a.get(t) != b.get(t))
        raise MoveError(f"verification failed: row counts differ in {', '.join(diff)}", "failed")


def switch_pointer(default_dir: Path, plan: MovePlan, created: bool) -> None:
    """Point the app at the new folder. If that cannot be written, a folder we just created is removed again."""
    try:
        write_location_pointer(default_dir, plan.target)
    except OSError as e:
        if created:
            shutil.rmtree(plan.target, ignore_errors=True)       # our own fresh copy, not user data
        raise MoveError(f"could not save the new location: {e}", "failed") from e


@dataclass
class MoveResult:
    plan: MovePlan
    warnings: list[str] = field(default_factory=list)
    tables: dict[str, int] | None = None


def move(default_dir: Path, plan: MovePlan) -> MoveResult:
    """Execute `plan`: copy (mode "copy"), then write the pointer. Blocking; run it in a thread."""
    warnings: list[str] = []
    created = plan.mode == "copy"
    if created:
        warnings = copy_data(plan)
    tables = table_counts(plan.target / "data.db") if (plan.target / "data.db").is_file() else {}
    switch_pointer(default_dir, plan, created)
    return MoveResult(plan, warnings, tables)
