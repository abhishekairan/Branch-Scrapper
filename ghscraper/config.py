"""Persistent, user-editable configuration (config.json)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
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
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

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


def app_data_dir(app_name: str = "GhostScraper") -> Path:
    """Per-user data directory for the app. Does not create it."""
    override = os.environ.get("GHOSTSCRAPER_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / app_name.lower()
