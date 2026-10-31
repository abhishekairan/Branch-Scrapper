"""Write results to JSON / CSV."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

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


def export(results: list[dict[str, Any]], queries: list[str], out_dir: str, formats: list[str]) -> list[Path]:
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = _slug(queries[0]) if queries else "search"
    if len(queries) > 1:
        name += f"_plus{len(queries) - 1}"
    stem = f"{name}_{datetime.now():%Y%m%d_%H%M%S}"
    written: list[Path] = []
    if "json" in formats:
        p = folder / f"{stem}.json"
        p.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(p)
    if "csv" in formats:
        p = folder / f"{stem}.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            w.writeheader()
            for r in results:
                w.writerow({c: _cell(r.get(c)) for c in CSV_COLUMNS})
        written.append(p)
    return written
