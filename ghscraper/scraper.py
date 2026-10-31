"""Synchronous facade over the async scheduler (ghscraper.core.scheduler) for the CLI.

`GitHubScraper(...).run()` runs the whole scrape inside `asyncio.run`. `shutdown()` is safe to call
from another thread. Ctrl+C makes `run()` raise KeyboardInterrupt *after* every browser has been
stopped; `results` / `totals` keep whatever was collected so far.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from .browser import PoolFactory
from .config import Config
from .core.scheduler import MAX_SEARCH_PAGES, Scheduler, _is_target_search, search_url

__all__ = ["GitHubScraper", "MAX_SEARCH_PAGES", "search_url", "_is_target_search"]


class GitHubScraper:
    def __init__(self, cfg: Config, log: Callable[[str], None] = print,
                 on_status: Callable[[int, str], None] | None = None,
                 on_listing: Callable[[str, int, int], None] | None = None,
                 on_repo: Callable[[dict[str, Any], int, int], None] | None = None,
                 count_only: bool = False, pool_factory: PoolFactory | None = None):
        self.cfg = cfg
        self.count_only = count_only
        self._scheduler = Scheduler(cfg, log, on_status, on_listing, on_repo, count_only, pool_factory)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_lock = threading.Lock()

    @property
    def results(self) -> list[dict[str, Any]]:
        return self._scheduler.results

    @property
    def totals(self) -> dict[str, int | None]:
        return self._scheduler.totals

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    def run(self) -> list[dict[str, Any]]:
        return asyncio.run(self._main())

    async def _main(self) -> list[dict[str, Any]]:
        with self._loop_lock:
            self._loop = asyncio.get_running_loop()
        try:
            return await self._scheduler.run()
        finally:
            with self._loop_lock:
                self._loop = None

    def shutdown(self) -> None:
        """Stop all workers and close all browsers. Thread-safe; safe to call more than once."""
        with self._loop_lock:
            loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._scheduler.stop)
                return
            except RuntimeError:  # loop closed between the check and the call
                pass
        self._scheduler.stop_event.set()  # not running yet / already finished
