"""SQLite store: schema migrations, upserts, datasets, facets.

Threading model (decision): one sqlite3 connection per thread (``threading.local``),
opened with ``check_same_thread=False`` only so ``close()`` can release them all from any thread; each is used by its owning thread only. Writes are serialised in-process by an
RLock and run in ``BEGIN IMMEDIATE`` transactions, so the app's dedicated writer
thread and any reader threads never share a connection. WAL lets readers proceed
while a write is in flight. Connections of finished threads stay open until
``Store.close()`` (acceptable for a small, long-lived thread set).

``":memory:"`` is the exception: every connection would get its own private DB, so
an in-memory store uses a single shared connection (``check_same_thread=False``)
and serialises *all* access, reads included, with the RLock. It is for tests.

Latest scrape wins: ``repos`` has one row per ``full_name`` (case-insensitive),
overwritten on re-scrape. ``repo_runs`` keeps provenance across runs.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

_SCHEMA_V1 = Path(__file__).with_name("schema.sql")

# Migration list: index i migrates user_version i -> i+1. Each entry is SQL text
# (or a callable taking the connection). Append new entries; never edit old ones.
MIGRATIONS: list[Any] = [lambda: _SCHEMA_V1.read_text(encoding="utf-8")]

INT_COLS = ["stars", "forks", "watchers", "branches", "tags", "releases", "commits",
            "contributors", "open_issues", "open_pull_requests"]
BOOL_COLS = ["archived", "is_fork", "is_template"]
TEXT_COLS = ["name", "owner", "url", "description", "website", "language", "license",
             "default_branch", "created_at", "updated_at", "last_commit_at", "error", "scraped_at"]
SCALAR_COLS = TEXT_COLS + INT_COLS + BOOL_COLS  # columns mirrored from the record (besides full_name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None if v is None else int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        try:
            return int(v.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def _to_bool(v: Any) -> int | None:
    return None if v is None else (1 if v else 0)


def _to_text(v: Any) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else str(v)


def _str_list(v: Any) -> list[str]:
    if not isinstance(v, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in v:
        if x is None:
            continue
        s = str(x).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._memory = self.path == ":memory:"
        self._lock = threading.RLock()
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._closed = False
        if not self._memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._migrate()
        except BaseException:
            self.close()
            raise

    # ---- connections -------------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None,
                               check_same_thread=False, timeout=30)  # affinity is by design; False lets close() run from any thread
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if not self._memory:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        with self._lock:
            self._conns.append(conn)
        return conn

    def conn(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("Store is closed")
        if self._memory:
            if not self._conns:
                with self._lock:
                    if not self._conns:
                        self._open()
            return self._conns[0]
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._local.conn = self._open()
        return c

    @contextmanager
    def _read_guard(self) -> Iterator[None]:
        if self._memory:
            with self._lock:
                yield
        else:
            yield

    def read(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        """Run a read-only statement on this thread's connection and fetch all rows."""
        with self._read_guard():
            return self.conn().execute(sql, tuple(params)).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction; re-entrant (inner calls join the outer one)."""
        with self._lock:
            c = self.conn()
            if self._tx_depth() > 0:  # only the lock holder can see depth > 0
                self._bump(1)
                try:
                    yield c
                finally:
                    self._bump(-1)
                return
            c.execute("BEGIN IMMEDIATE")
            self._bump(1)
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")
            finally:
                self._bump(-1)

    # depth is tracked per Store for memory mode (shared conn) and per thread otherwise;
    # since the RLock is held for the whole transaction, one counter on self suffices.
    def _tx_depth(self) -> int:
        return getattr(self, "_depth", 0)

    def _bump(self, n: int) -> None:
        self._depth = self._tx_depth() + n

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for c in self._conns:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._conns.clear()

    # ---- migrations --------------------------------------------------------------
    def _migrate(self) -> None:
        c = self.conn()
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError(f"database schema v{version} is newer than this app supports (v{len(MIGRATIONS)})")
        for n in range(version, len(MIGRATIONS)):
            step = MIGRATIONS[n]
            sql = step() if callable(step) else step
            script = f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version={n + 1};\nCOMMIT;"
            try:
                c.executescript(script)
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise

    def schema_version(self) -> int:
        return self.read("PRAGMA user_version")[0][0]

    # ---- runs --------------------------------------------------------------------
    def start_run(self, config: dict) -> int:
        with self.transaction() as c:
            cur = c.execute("INSERT INTO runs(started_at, status, config_json) VALUES (?,?,?)",
                            (_now(), "running", json.dumps(config or {}, ensure_ascii=False, default=str)))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, counts: dict | None = None) -> None:
        with self.transaction() as c:
            c.execute("UPDATE runs SET finished_at=?, status=?, counts_json=? WHERE id=?",
                      (_now(), status, json.dumps(counts or {}, default=str), run_id))

    def list_runs(self) -> list[dict]:
        out = []
        for r in self.read("SELECT * FROM runs ORDER BY id DESC"):
            d = dict(r)
            d["config"] = json.loads(d.pop("config_json"))
            d["counts"] = json.loads(d.pop("counts_json"))
            out.append(d)
        return out

    # ---- repos -------------------------------------------------------------------
    def upsert_repo(self, record: dict, run_id: int) -> None:
        with self.transaction() as c:
            self._upsert(c, record, run_id)

    def upsert_many(self, records: Iterable[dict], run_id: int) -> int:
        """Upsert all records in one transaction (all-or-nothing). Returns the count."""
        n = 0
        with self.transaction() as c:
            for rec in records:
                self._upsert(c, rec, run_id)
                n += 1
        return n

    def _upsert(self, c: sqlite3.Connection, record: dict, run_id: int) -> None:
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        full_name = record.get("full_name")
        if not isinstance(full_name, str) or not full_name.strip():
            raise ValueError("record has no full_name")
        full_name = full_name.strip()

        vals: dict[str, Any] = {}
        for k in TEXT_COLS:
            vals[k] = _to_text(record.get(k))
        for k in INT_COLS:
            vals[k] = _to_int(record.get(k))
        for k in BOOL_COLS:
            vals[k] = _to_bool(record.get(k))
        raw = json.dumps(record, ensure_ascii=False, default=str)

        topics = _str_list(record.get("topics"))
        tech = _str_list(record.get("tech_stack"))
        langs: dict[str, float] = {}
        if isinstance(record.get("languages"), dict):
            for name, pct in record["languages"].items():
                try:
                    langs[str(name)] = float(pct)
                except (TypeError, ValueError):
                    continue

        row = c.execute("SELECT rowid, full_name FROM repos WHERE full_name=?", (full_name,)).fetchone()
        cols = SCALAR_COLS
        if row is None:
            names = ["full_name", *cols, "raw_json"]
            cur = c.execute(f"INSERT INTO repos({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                            [full_name, *(vals[k] for k in cols), raw])
            rowid = int(cur.lastrowid)
        else:
            rowid = int(row["rowid"])
            sets = ",".join(f"{k}=?" for k in cols)
            c.execute(f"UPDATE repos SET {sets}, raw_json=? WHERE rowid=?",
                      [*(vals[k] for k in cols), raw, rowid])
            c.execute("DELETE FROM repo_topics WHERE repo=?", (full_name,))
            c.execute("DELETE FROM repo_tech WHERE repo=?", (full_name,))
            c.execute("DELETE FROM repo_languages WHERE repo=?", (full_name,))
            c.execute("DELETE FROM repos_fts WHERE rowid=?", (rowid,))

        c.executemany("INSERT OR IGNORE INTO repo_topics(repo, topic) VALUES (?,?)", [(full_name, t) for t in topics])
        c.executemany("INSERT OR IGNORE INTO repo_tech(repo, tech) VALUES (?,?)", [(full_name, t) for t in tech])
        c.executemany("INSERT OR REPLACE INTO repo_languages(repo, language, pct) VALUES (?,?,?)",
                      [(full_name, k, v) for k, v in langs.items()])
        c.execute("INSERT INTO repos_fts(rowid, full_name, description, topics, website) VALUES (?,?,?,?,?)",
                  (rowid, full_name, vals["description"] or "", " ".join(topics), vals["website"] or ""))

        queries = _str_list(record.get("matched_queries"))
        q = record.get("query")
        if isinstance(q, str) and q.strip() and q.strip().lower() not in {x.lower() for x in queries}:
            queries.append(q.strip())
        c.executemany("INSERT OR IGNORE INTO repo_runs(repo, run_id, query) VALUES (?,?,?)",
                      [(full_name, run_id, x) for x in queries])

    def decode_rows(self, rows: list[sqlite3.Row]) -> list[dict]:
        """Turn repos rows (must include raw_json) into scraper-shaped records."""
        if not rows:
            return []
        prov: dict[str, list[str]] = {}
        names = [r["full_name"] for r in rows]
        for i in range(0, len(names), 400):
            chunk = names[i:i + 400]
            q = ",".join("?" * len(chunk))
            for pr in self.read(f"SELECT repo, query FROM repo_runs WHERE repo IN ({q}) ORDER BY rowid", chunk):
                lst = prov.setdefault(pr["repo"].lower(), [])
                if pr["query"] not in lst:
                    lst.append(pr["query"])
        out = []
        for r in rows:
            rec = json.loads(r["raw_json"])
            rec["full_name"] = r["full_name"]
            for k in TEXT_COLS + INT_COLS:
                if r[k] is not None or k in rec:
                    rec[k] = r[k]
            for k in BOOL_COLS:
                if r[k] is not None or k in rec:
                    rec[k] = None if r[k] is None else bool(r[k])
            mq = prov.get(r["full_name"].lower())
            if mq:
                rec["matched_queries"] = mq
            out.append(rec)
        return out

    def get_repo(self, full_name: str) -> dict | None:
        rows = self.read("SELECT * FROM repos WHERE full_name=?", (full_name,))
        return self.decode_rows(rows)[0] if rows else None

    def delete_repo(self, full_name: str) -> bool:
        with self.transaction() as c:
            row = c.execute("SELECT rowid FROM repos WHERE full_name=?", (full_name,)).fetchone()
            if row is None:
                return False
            c.execute("DELETE FROM repos_fts WHERE rowid=?", (row["rowid"],))
            c.execute("DELETE FROM repos WHERE rowid=?", (row["rowid"],))  # side tables cascade
            return True

    # ---- datasets ----------------------------------------------------------------
    def save_dataset(self, name: str, filters: dict, columns: list[str] | None = None,
                     sort: list | None = None) -> int:
        """Create, or update if ``name`` exists. Filters/columns/sort are validated first."""
        from .query import compile_filters, validate_columns, validate_sort
        if not isinstance(name, str) or not name.strip():
            raise ValueError("dataset name required")
        compile_filters(filters or {})
        validate_columns(columns)
        validate_sort(sort, has_text=True)
        now = _now()
        fj = json.dumps(filters or {}, ensure_ascii=False)
        cj = json.dumps(columns or [], ensure_ascii=False)
        sj = json.dumps([list(s) for s in (sort or [])])
        with self.transaction() as c:
            c.execute(
                "INSERT INTO datasets(name, filter_json, columns_json, sort_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET filter_json=excluded.filter_json, "
                "columns_json=excluded.columns_json, sort_json=excluded.sort_json, updated_at=excluded.updated_at",
                (name.strip(), fj, cj, sj, now, now))
            return int(c.execute("SELECT id FROM datasets WHERE name=?", (name.strip(),)).fetchone()["id"])

    def list_datasets(self) -> list[dict]:
        out = []
        for r in self.read("SELECT * FROM datasets ORDER BY name COLLATE NOCASE"):
            out.append({"id": r["id"], "name": r["name"], "filters": json.loads(r["filter_json"]),
                        "columns": json.loads(r["columns_json"]),
                        "sort": [tuple(s) for s in json.loads(r["sort_json"])],
                        "created_at": r["created_at"], "updated_at": r["updated_at"]})
        return out

    def delete_dataset(self, dataset_id: int) -> bool:
        with self.transaction() as c:
            return c.execute("DELETE FROM datasets WHERE id=?", (dataset_id,)).rowcount > 0

    # ---- facets ------------------------------------------------------------------
    def facets(self, limit: int | None = None) -> dict[str, list[dict]]:
        """Distinct values with repo counts, most common first (for filter dropdowns)."""
        lim = f" LIMIT {int(limit)}" if limit else ""

        def q(sql: str) -> list[dict]:
            return [{"value": r[0], "count": r[1]} for r in self.read(sql + lim)]

        order = " ORDER BY 2 DESC, 1 COLLATE NOCASE"
        return {
            "languages": q("SELECT language, COUNT(DISTINCT repo) FROM repo_languages GROUP BY language" + order),
            "topics": q("SELECT topic, COUNT(*) FROM repo_topics GROUP BY topic" + order),
            "tech": q("SELECT tech, COUNT(*) FROM repo_tech GROUP BY tech" + order),
            "licenses": q("SELECT license, COUNT(*) FROM repos WHERE license IS NOT NULL AND license<>'' "
                          "GROUP BY license" + order),
        }

    def count_repos(self) -> int:
        return self.read("SELECT COUNT(*) FROM repos")[0][0]
