"""Compile a filter dict into parameterised SQL and run searches.

Safety: user values only ever travel as bound parameters. Column and sort names
come from whitelists; the FTS text is reduced to quoted prefix tokens. Anything
unrecognised raises ValueError naming the offender.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterator

from .db import BOOL_COLS, INT_COLS, SCALAR_COLS, Store

RANGE_KEYS = ["stars", "forks", "watchers", "contributors", "open_issues",
              "open_pull_requests", "commits", "releases"]
BOOL_KEYS = ["archived", "is_fork", "is_template", "has_website"]
DATE_KEYS = {"created": "created_at", "last_commit_at": "last_commit_at", "pushed": "last_commit_at"}
SET_KEYS = {"topics": ("repo_topics", "topic"), "tech": ("repo_tech", "tech")}
ALL_KEYS = (["text", "language", "license", "query", "run_id"] + RANGE_KEYS + BOOL_KEYS
            + list(DATE_KEYS) + list(SET_KEYS))

# Record fields a caller may request as output columns / sort by.
OUTPUT_COLUMNS = set(SCALAR_COLS) | {"full_name", "query", "matched_queries", "languages", "tech_stack", "topics"}
SORT_COLUMNS = set(SCALAR_COLS) | {"full_name"}
CHUNK = 500


@dataclass
class Compiled:
    where: list[str] = field(default_factory=list)
    params: list[Any] = field(default_factory=list)
    fts: str | None = None

    def add(self, clause: str, *params: Any) -> None:
        self.where.append(clause)
        self.params.extend(params)


def _num(key: str, v: Any) -> int | float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"filter {key!r}: expected a number, got {v!r}")
    return v


def _strs(key: str, v: Any) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) for x in v):
        raise ValueError(f"filter {key!r}: expected a list of strings")
    seen, out = set(), []
    for x in v:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def _check_keys(name: str, d: Any, allowed: set[str]) -> dict:
    if not isinstance(d, dict):
        raise ValueError(f"filter {name!r}: expected an object")
    for k in d:
        if k not in allowed:
            raise ValueError(f"unknown key {k!r} in filter {name!r}")
    return d


def fts_query(text: str) -> str | None:
    """Reduce free text to ``"tok1"* "tok2"*`` (implicit AND). Safe for any input."""
    toks = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    return " ".join(f'"{t}"*' for t in toks) or None


def _day(key: str, v: Any) -> date:
    if not isinstance(v, str):
        raise ValueError(f"filter {key!r}: expected a YYYY-MM-DD string")
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        raise ValueError(f"filter {key!r}: invalid date {v!r}") from None


def _in(n: int) -> str:
    return ",".join("?" * n)


def compile_filters(filters: dict | None) -> Compiled:
    c = Compiled()
    filters = filters or {}
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")
    for key in filters:
        if key not in ALL_KEYS:
            raise ValueError(f"unknown filter key {key!r}")
    for key, val in filters.items():
        if val is None:
            continue
        if key == "text":
            if not isinstance(val, str):
                raise ValueError("filter 'text': expected a string")
            c.fts = fts_query(val)
        elif key in RANGE_KEYS:
            d = _check_keys(key, val, {"min", "max"})
            if d.get("min") is not None:
                c.add(f"repos.{key} >= ?", _num(key, d["min"]))
            if d.get("max") is not None:
                c.add(f"repos.{key} <= ?", _num(key, d["max"]))
        elif key == "language":
            d = _check_keys(key, val, {"any", "min_pct"})
            langs = _strs("language.any", d["any"]) if d.get("any") else []
            pct = _num("language.min_pct", d["min_pct"]) if d.get("min_pct") is not None else None
            if langs and pct is None:
                n = _in(len(langs))
                c.add(f"(EXISTS (SELECT 1 FROM repo_languages l WHERE l.repo = repos.full_name AND l.language IN ({n})) "
                      f"OR (NOT EXISTS (SELECT 1 FROM repo_languages l WHERE l.repo = repos.full_name) "
                      f"AND repos.language IN ({n})))", *langs, *langs)
            elif langs:
                n = _in(len(langs))
                c.add(f"(EXISTS (SELECT 1 FROM repo_languages l WHERE l.repo = repos.full_name "
                      f"AND l.language IN ({n}) AND l.pct >= ?) "
                      f"OR (NOT EXISTS (SELECT 1 FROM repo_languages l WHERE l.repo = repos.full_name) "
                      f"AND repos.language IN ({n})))", *langs, pct, *langs)
            elif pct is not None:
                c.add("EXISTS (SELECT 1 FROM repo_languages l WHERE l.repo = repos.full_name AND l.pct >= ?)", pct)
        elif key in SET_KEYS:
            table, col = SET_KEYS[key]
            d = _check_keys(key, val, {"any", "all", "none"})
            if d.get("any"):
                v = _strs(f"{key}.any", d["any"])
                c.add(f"EXISTS (SELECT 1 FROM {table} t WHERE t.repo = repos.full_name AND t.{col} IN ({_in(len(v))}))", *v)
            if d.get("all"):
                for v in _strs(f"{key}.all", d["all"]):
                    c.add(f"EXISTS (SELECT 1 FROM {table} t WHERE t.repo = repos.full_name AND t.{col} = ?)", v)
            if d.get("none"):
                v = _strs(f"{key}.none", d["none"])
                c.add(f"NOT EXISTS (SELECT 1 FROM {table} t WHERE t.repo = repos.full_name AND t.{col} IN ({_in(len(v))}))", *v)
        elif key == "license":
            v = _strs(key, val)
            if v:
                c.add(f"repos.license IN ({_in(len(v))})", *v)
        elif key in DATE_KEYS:
            col = DATE_KEYS[key]
            d = _check_keys(key, val, {"from", "to"})
            if d.get("from"):
                c.add(f"repos.{col} >= ?", _day(key, d["from"]).isoformat())
            if d.get("to"):  # inclusive of the whole 'to' day
                c.add(f"repos.{col} < ?", (_day(key, d["to"]) + timedelta(days=1)).isoformat())
        elif key in BOOL_KEYS:
            if not isinstance(val, bool):
                raise ValueError(f"filter {key!r}: expected true or false")
            if key == "has_website":
                c.add("(repos.website IS NOT NULL AND repos.website <> '')" if val
                      else "(repos.website IS NULL OR repos.website = '')")
            else:
                c.add(f"repos.{key} = ?", int(val))
        elif key == "query":
            v = _strs(key, val)
            if v:
                c.add(f"EXISTS (SELECT 1 FROM repo_runs r WHERE r.repo = repos.full_name AND r.query IN ({_in(len(v))}))", *v)
        elif key == "run_id":
            ids = val if isinstance(val, (list, tuple)) else [val]
            if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
                raise ValueError("filter 'run_id': expected integers")
            if ids:
                c.add(f"EXISTS (SELECT 1 FROM repo_runs r WHERE r.repo = repos.full_name AND r.run_id IN ({_in(len(ids))}))", *ids)
    return c


def validate_columns(columns: list[str] | None) -> list[str] | None:
    if columns is None:
        return None
    if not isinstance(columns, (list, tuple)):
        raise ValueError("columns must be a list")
    for col in columns:
        if col not in OUTPUT_COLUMNS:
            raise ValueError(f"unknown column {col!r}")
    return list(columns)


def validate_sort(sort: list | None, has_text: bool) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in sort or []:
        try:
            col, direction = item
        except (TypeError, ValueError):
            raise ValueError(f"invalid sort entry {item!r}") from None
        d = str(direction).lower()
        if d not in ("asc", "desc"):
            raise ValueError(f"invalid sort direction {direction!r}")
        if col == "relevance":
            if has_text:
                out.append((col, d))
            continue  # without text there is no relevance; ignored
        if col not in SORT_COLUMNS:
            raise ValueError(f"unknown sort column {col!r}")
        out.append((col, d))
    return out


def _order_by(sort: list[tuple[str, str]]) -> str:
    parts = []
    for col, d in sort:
        if col == "relevance":
            parts.append(f"bm25(repos_fts) {'ASC' if d == 'asc' else 'DESC'}")
        else:  # col is whitelisted
            parts.append(f"repos.{col} IS NULL, repos.{col} {d.upper()}")
    parts.append("repos.full_name ASC")  # stable tiebreaker, needed for paging
    return " ORDER BY " + ", ".join(parts)


def _from(c: Compiled) -> tuple[str, list[str], list[Any]]:
    where, params = list(c.where), list(c.params)
    if c.fts:
        frm = "repos JOIN repos_fts ON repos_fts.rowid = repos.rowid"
        where.insert(0, "repos_fts MATCH ?")
        params.insert(0, c.fts)
    else:
        frm = "repos"
    return frm, where, params


def _project(rows: list[dict], columns: list[str] | None) -> list[dict]:
    if columns is None:
        return rows
    return [{k: r.get(k) for k in columns} for r in rows]


def search(store: Store, filters: dict | None = None, sort: list[tuple[str, str]] | None = None,
           limit: int = 100, offset: int = 0, columns: list[str] | None = None) -> dict:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    c = compile_filters(filters)
    cols = validate_columns(columns)
    s = validate_sort(sort if sort is not None else [("stars", "desc")], has_text=bool(c.fts))
    if sort is None and c.fts:
        s = [("relevance", "asc")]  # bm25: lower is better
    frm, where, params = _from(c)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = store.read(f"SELECT COUNT(*) FROM {frm}{w}", params)[0][0]
    rows = store.read(f"SELECT repos.* FROM {frm}{w}{_order_by(s)} LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"rows": _project(store.decode_rows(rows), cols), "total": total}


def iter_all(store: Store, filters: dict | None = None, sort: list[tuple[str, str]] | None = None,
             columns: list[str] | None = None) -> Iterator[dict]:
    """Stream every matching record in chunks (for exports). Rows written mid-iteration
    by another thread may shift paging; exports should tolerate that or run when idle."""
    c = compile_filters(filters)
    cols = validate_columns(columns)
    s = validate_sort(sort if sort is not None else [("stars", "desc")], has_text=bool(c.fts))
    if sort is None and c.fts:
        s = [("relevance", "asc")]
    frm, where, params = _from(c)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT repos.* FROM {frm}{w}{_order_by(s)} LIMIT ? OFFSET ?"
    offset = 0
    while True:
        rows = store.read(sql, [*params, CHUNK, offset])
        if not rows:
            return
        yield from _project(store.decode_rows(rows), cols)
        if len(rows) < CHUNK:
            return
        offset += CHUNK
