"""Is a usable browser installed? (First-run check for the desktop app.)

Uses Zendriver's own lookup so the answer matches what a run would actually launch.
"""
from __future__ import annotations

import os

from ..config import BROWSER_CHOICES


def find_browser(name: str) -> str | None:
    """Absolute path of the installed browser `name` (chrome | msedge | brave), or None."""
    from zendriver.core.config import find_executable   # lazy: zendriver is heavy to import
    try:
        return str(find_executable(name))
    except Exception:       # zendriver raises FileNotFoundError when nothing matches
        return None


def browser_status(selected: str, browser_path: str = "") -> dict:
    """`selected` is the configured browser, `browser_path` the optional explicit executable.

    -> {selected, custom_path, path, available, found: {chrome|msedge|brave: path | None}}
    `path` is what a run would launch (None = nothing usable)."""
    found = {name: find_browser(name) for name in BROWSER_CHOICES}
    if browser_path:
        path = browser_path if os.path.isfile(browser_path) else None
    else:
        path = found.get(selected)
    return {"selected": selected, "custom_path": bool(browser_path), "path": path,
            "available": path is not None, "found": found}
