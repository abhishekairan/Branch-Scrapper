"""Write results to JSON / CSV."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

CSV_COLUMNS = [
    "query", "matched_queries", "full_name", "url", "description", "website", "stars", "forks", "watchers",
    "language", "languages", "tech_stack", "topics", "license", "default_branch",
    "branches", "tags", "releases", "commits", "contributors", "open_issues",
    "open_pull_requests", "created_at", "updated_at", "last_commit_at", "archived",
    "is_fork", "is_template", "owner", "error", "scraped_at",
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "search"


def _cell(value: Any) -> Any:
    if isinstance(value, dict):
        return "; ".join(f"{k} {v}%" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "" if value is None else value


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection: prefix text that Excel would treat as a formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _stream_json(rows: Iterable[dict[str, Any]], p: Path, columns: list[str] | None) -> None:
    """Write an iterable as a JSON array one record at a time; byte-identical to
    ``json.dumps(list(rows), indent=2, ensure_ascii=False)``."""
    with p.open("w", encoding="utf-8") as f:
        f.write("[")
        first = True
        for r in rows:
            item = r if columns is None else {c: r.get(c) for c in columns}
            text = json.dumps(item, indent=2, ensure_ascii=False).replace("\n", "\n  ")
            f.write(("\n  " if first else ",\n  ") + text)
            first = False
        f.write("]" if first else "\n]")


def export_rows(rows: Iterable[dict[str, Any]], path: str | Path, fmt: str,
                columns: list[str] | None = None) -> Path:
    """Write rows (a list, or any iterable that is streamed) to ``path``."""
    if fmt not in ("json", "csv"):
        raise ValueError(f"unsupported export format: {fmt!r}")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        if not isinstance(rows, (list, tuple)):
            _stream_json(rows, p, columns)
            return p
        data = rows if columns is None else [{c: r.get(c) for c in columns} for r in rows]
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return p
    header = columns if columns is not None else CSV_COLUMNS
    with p.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: _safe_cell(_cell(r.get(c))) for c in header})
    return p


def export(results: list[dict[str, Any]], queries: list[str], out_dir: str, formats: list[str]) -> list[Path]:
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = _slug(queries[0]) if queries else "search"
    if len(queries) > 1:
        name += f"_plus{len(queries) - 1}"
    stem = f"{name}_{datetime.now():%Y%m%d_%H%M%S}"
    written: list[Path] = []
    if "json" in formats:
        written.append(export_rows(results, folder / f"{stem}.json", "json"))
    if "csv" in formats:
        written.append(export_rows(results, folder / f"{stem}.csv", "csv"))
    return written
