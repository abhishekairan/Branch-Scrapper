"""Persistent, user-editable configuration (config.json)."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from .filters import build_query, clean_filters, split_queries

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

MAX_BROWSERS = 5  # hard cap on parallel workers (RAM + GitHub rate limits)
BROWSER_CHOICES = ("chrome", "msedge", "brave")

SORT_OPTIONS = {
    "best-match": None,
    "stars": "stars",
    "forks": "forks",
    "updated": "updated",
}

# Exactly the options in GitHub's search "Sort by" menu: (label, sort, order).
SORT_CHOICES = (
    ("Best match", "best-match", "desc"),
    ("Most stars", "stars", "desc"),
    ("Fewest stars", "stars", "asc"),
    ("Most forks", "forks", "desc"),
    ("Fewest forks", "forks", "asc"),
    ("Recently updated", "updated", "desc"),
    ("Least recently updated", "updated", "asc"),
)


@dataclass
class Config:
    # --- search ---
    queries: list[str] = field(default_factory=lambda: ["web scraper"])
    max_results: int = 20             # per query
    sort: str = "best-match"          # best-match | stars | forks | updated
    order: str = "desc"               # desc | asc
    filters: dict[str, Any] = field(default_factory=dict)  # applied to every query, see filters.py

    # --- what to collect ---
    deep_scrape: bool = True          # open every repo page for full details
    fetch_manifests: bool = True      # read package.json / pyproject etc. for tech stack

    # --- browser / stealth ---
    browsers: int = 2                 # parallel workers (1..MAX_BROWSERS); name kept for config compatibility
    workers_per_chrome: int = 3       # browser contexts (workers) sharing one Chrome process
    isolate_workers: bool = False     # True = one Chrome per worker (max isolation, more RAM)
    block_resources: bool = False     # block images/fonts/media (parsers only need HTML)
    browser: str = "chrome"           # chrome | msedge | brave
    browser_path: str = ""            # explicit browser executable (blank = auto-detect)
    headless: bool = False
    hide_taskbar_icons: bool = True   # Windows: keep the (off-screen, headed) Chrome windows out of the taskbar/Alt-Tab
    human_search: bool = True         # type the query into GitHub's search box
    min_delay: float = 2.0            # random pause between page loads (seconds)
    max_delay: float = 5.0
    typing_delay: float = 0.12        # mean per-keystroke delay when typing
    max_retries: int = 4              # retries on rate-limit / failed load
    proxy: str = ""                   # "host:port" / "user:pass@host:port"; comma-separate for one per browser
    user_data_dir: str = ""           # persistent Chrome profile; browsers 2+ get "<dir>-2", "<dir>-3"...
    page_timeout: int = 30

    # --- output ---
    output_dir: str = "output"
    formats: list[str] = field(default_factory=lambda: ["json", "csv"])

    # --- app UI ---
    ui_theme: str = "system"          # system | light | dark (desktop app appearance)
    check_updates: bool = True        # desktop app: look for a new version at startup (at most once a day)

    def search_query(self, query: str) -> str:
        return build_query(query, self.filters)

    def sort_label(self) -> str:
        if self.sort == "best-match":
            return "Best match"
        return next((label for label, s, o in SORT_CHOICES if s == self.sort and o == self.order), self.sort)

    def worker_count(self, searches_only: bool = False) -> int:
        """Browsers actually launched: capped, and never more than there is work for."""
        n = max(1, min(self.browsers, MAX_BROWSERS))
        # Without deep scrape the only tasks are searches, one per query.
        if searches_only or not self.deep_scrape:
            return min(n, max(1, len(self.queries)))
        return n

    def proxy_for(self, index: int) -> str:
        proxies = [p.strip() for p in self.proxy.split(",") if p.strip()]
        return proxies[index % len(proxies)] if proxies else ""

    def profile_for(self, index: int) -> str:
        # Chrome locks a profile dir, so each Chrome process needs its own;
        # `index` is the Chrome process number (see browser.zendriver_backend.plan_chromes).
        if not self.user_data_dir:
            return ""
        return self.user_data_dir if index == 0 else f"{self.user_data_dir}-{index + 1}"

    def save(self, path: Path = CONFIG_PATH) -> None:
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")     # atomic: a reader (or a copy of the data folder) never sees half a file
        try:
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "queries" not in raw and raw.get("query"):  # v1 config had a single query
            raw["queries"] = [raw["query"]]
        if "filters" not in raw:  # v2 config had language/min_stars only
            raw["filters"] = {}
            if raw.get("language"):
                raw["filters"]["language"] = [raw["language"]]
            if raw.get("min_stars"):
                raw["filters"]["stars"] = f">={raw['min_stars']}"
        raw["filters"] = clean_filters(raw["filters"])  # ValueError names the bad entry
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


def parse_queries(text: str) -> list[str]:
    """Split user input into queries (see filters.split_queries for the comma rules)."""
    return split_queries(text)


APP_DIR_NAME = "BranchScrapper"
LEGACY_APP_DIR_NAME = "GhostScraper"     # the product was called "Ghost Scraper" up to v0.1.0
MIGRATED_MARKER = "MIGRATED_TO.txt"
HOME_ENV = "GHOSTSCRAPER_HOME"           # env var name kept across the rename


def _default_data_dir(app_name: str) -> Path:
    """The OS-conventional location for `app_name`, ignoring any override. Does not create it."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / app_name.lower()


def app_data_dir(app_name: str = APP_DIR_NAME) -> Path:
    """Per-user data directory for the app. Does not create it."""
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    return _default_data_dir(app_name)


POINTER_NAME = "location.json"           # lives in the DEFAULT data dir: {"data_dir": "<absolute path>"}
UI_THEMES = ("system", "light", "dark")


@dataclass
class Location:
    """Where the data dir came from. `source`: arg (--data-dir) | env (GHOSTSCRAPER_HOME) | pointer | default."""
    data_dir: Path
    source: str
    default_dir: Path
    problem: str | None = None            # e.g. the pointer file named a folder that is gone

    @property
    def pointer_file(self) -> Path:
        return self.default_dir / POINTER_NAME

    @property
    def can_change(self) -> bool:
        """The pointer file only has an effect when neither --data-dir nor GHOSTSCRAPER_HOME decides."""
        return self.source in ("pointer", "default")


def read_location_pointer(default_dir: Path) -> tuple[Path | None, str | None]:
    """The data dir named by `<default_dir>/location.json` -> (path, None); (None, None) when there is no pointer;
    (None, reason) when it exists but is unusable (bad JSON, relative path, folder missing)."""
    f = default_dir / POINTER_NAME
    try:
        raw = f.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"{f} could not be read ({e})"
    try:
        value = json.loads(raw).get("data_dir")
    except (ValueError, AttributeError):
        return None, f"{f} is not valid JSON of the form {{\"data_dir\": \"<path>\"}}"
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return None, f"{f} does not name an absolute folder"
    p = Path(value)
    if not p.is_dir():
        return None, f"the chosen data folder {p} does not exist"
    return p, None


def write_location_pointer(default_dir: Path, target: Path) -> None:
    """Atomically point the app at `target` (temp file + replace)."""
    default_dir.mkdir(parents=True, exist_ok=True)
    f = default_dir / POINTER_NAME
    tmp = f.with_name(f"{POINTER_NAME}.tmp-{os.getpid()}")
    try:
        tmp.write_text(json.dumps({"data_dir": str(target)}, indent=2), encoding="utf-8")
        os.replace(tmp, f)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def clear_location_pointer(default_dir: Path) -> bool:
    """Remove the pointer (the data at the custom location is untouched). True if there was one."""
    try:
        (default_dir / POINTER_NAME).unlink()
        return True
    except FileNotFoundError:
        return False


def resolve_data_dir(arg: Path | None = None, log=None) -> Location:
    """Order: --data-dir > GHOSTSCRAPER_HOME > pointer file (if it names an existing folder) > default."""
    if arg:
        return Location(Path(arg), "arg", app_data_dir())
    if os.environ.get(HOME_ENV):
        return Location(app_data_dir(), "env", app_data_dir())
    default = app_data_dir()               # no override is set here, so this is the OS default
    target, problem = read_location_pointer(default)
    if problem and log is not None:
        log.warning("ignoring the data folder pointer: %s; using the default folder", problem)
    if target is not None and target.resolve() != default.resolve():
        return Location(target, "pointer", default)
    return Location(default, "default", default, problem)


def legacy_data_dir() -> Path:
    """Where the app (then called Ghost Scraper) kept its data up to v0.1.0."""
    return _default_data_dir(LEGACY_APP_DIR_NAME)


def _is_nonempty_dir(p: Path) -> bool:
    try:
        return p.is_dir() and any(p.iterdir())
    except OSError:
        return False


def _checkpoint_legacy_db(db: Path, log) -> None:
    """Fold a WAL into the main file so that the copy is consistent. Errors are logged, never raised."""
    if not db.is_file():
        return
    try:
        con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=rw", uri=True, timeout=5)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        log.warning("legacy data migration: WAL checkpoint of %s failed (%s); copying the -wal/-shm files too", db, e)


def migrate_legacy_data_dir(new: Path, log) -> str:
    """One-time copy of the pre-rename data directory (Ghost Scraper) to `new`.

    Never moves or deletes anything in the legacy directory; a failure leaves it untouched and the caller
    simply starts with a fresh `new`. `log` is anything with info/warning/error (a logging.Logger).
    Returns "none" (nothing to do), "migrated", "skipped-existing" (`new` already holds data) or
    "failed: <reason>". With GHOSTSCRAPER_HOME set no migration happens (the user picked the folder).
    """
    try:
        if os.environ.get(HOME_ENV):
            return "none"
        legacy = legacy_data_dir()
        if not _is_nonempty_dir(legacy) or legacy.resolve() == new.resolve():
            return "none"
        if _is_nonempty_dir(new):
            log.info("legacy data migration: %s already has data, leaving %s alone", new, legacy)
            return "skipped-existing"
        if new.exists() and not new.is_dir():
            return f"failed: {new} exists and is not a directory"
        if (legacy / MIGRATED_MARKER).exists():
            log.info("legacy data migration: %s was already migrated once; not copying again", legacy)
            return "none"

        log.info("legacy data migration: copying %s -> %s", legacy, new)
        _checkpoint_legacy_db(legacy / "data.db", log)
        new.parent.mkdir(parents=True, exist_ok=True)
        tmp = new.with_name(f"{new.name}.migrating-{os.getpid()}")
        try:
            if tmp.exists():                       # leftover of an earlier crash of this very pid
                shutil.rmtree(tmp)
            shutil.copytree(legacy, tmp)           # copy, never move: the legacy tree stays as it was
            # Another sidecar may have won the race while we were copying. Look again just before the rename
            # and never clobber: an empty `new` is removed first (Windows cannot rename onto an existing
            # directory), a non-empty one means somebody else finished first.
            if _is_nonempty_dir(new):
                shutil.rmtree(tmp, ignore_errors=True)
                log.info("legacy data migration: %s appeared meanwhile; discarding our copy", new)
                return "skipped-existing"
            if new.is_dir():
                os.rmdir(new)
            try:
                os.rename(tmp, new)                # atomic on one volume; fails if `new` exists again
            except OSError:
                if _is_nonempty_dir(new) or new.exists():
                    shutil.rmtree(tmp, ignore_errors=True)
                    log.info("legacy data migration: %s appeared meanwhile; discarding our copy", new)
                    return "skipped-existing"
                raise
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

        try:
            (legacy / MIGRATED_MARKER).write_text(
                f"Your Ghost Scraper data was copied to:\n{new}\n\n"
                f"Copied at: {datetime.now().astimezone().isoformat(timespec='seconds')}\n\n"
                "This folder is the old Ghost Scraper location and is no longer used by Branch Scrapper.\n"
                "It is safe to delete after you have checked Branch Scrapper.\n",
                encoding="utf-8")
        except OSError as e:
            log.warning("legacy data migration: could not write %s into %s (%s)", MIGRATED_MARKER, legacy, e)
        log.info("legacy data migration: done")
        return "migrated"
    except Exception as e:   # the app must still start, with a fresh data directory
        log.error("legacy data migration failed: %s: %s", type(e).__name__, e)
        return f"failed: {type(e).__name__}: {e}"
