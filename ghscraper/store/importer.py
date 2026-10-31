"""Import existing ``output/*.json`` result files (arrays of repo dicts) into the store."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .db import Store


def import_json_files(store: Store, paths: Iterable[str | Path]) -> dict:
    """One run per file. Returns {"imported": [{file, run_id, repos, skipped_records}],
    "skipped": [{file, reason}], "total_repos": int}."""
    summary: dict = {"imported": [], "skipped": [], "total_repos": 0}
    for p in paths:
        path = Path(p)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            summary["skipped"].append({"file": path.name, "reason": "file not found"})
            continue
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            summary["skipped"].append({"file": path.name, "reason": f"unreadable or invalid JSON: {e}"})
            continue
        if not isinstance(data, list):
            summary["skipped"].append({"file": path.name, "reason": "top level is not an array"})
            continue
        good = [r for r in data if isinstance(r, dict) and isinstance(r.get("full_name"), str) and r["full_name"].strip()]
        if not good:
            summary["skipped"].append({"file": path.name, "reason": "no records with a full_name"})
            continue
        run_id = store.start_run({"imported_from": path.name})
        try:
            n = store.upsert_many(good, run_id)
        except Exception as e:  # transaction rolled back; record the failure on the run
            store.finish_run(run_id, "failed", {"error": str(e)})
            summary["skipped"].append({"file": path.name, "reason": f"import failed: {e}"})
            continue
        store.finish_run(run_id, "imported", {"repos": n, "skipped_records": len(data) - len(good)})
        summary["imported"].append({"file": path.name, "run_id": run_id, "repos": n,
                                    "skipped_records": len(data) - len(good)})
        summary["total_repos"] += n
    return summary
