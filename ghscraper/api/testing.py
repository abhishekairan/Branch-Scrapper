"""Offline fake browser pool for API tests. NEVER imported in normal operation: the sidecar
loads it only when started with ``--test-mode`` *and* GHOSTSCRAPER_FAKE_POOL=1.

Serves the HTML fixtures in tests/fixtures, so it only works from a source checkout.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

_FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def _read(name: str) -> str:
    return (_FIX / name).read_text(encoding="utf-8")


class FakeSlot:
    def __init__(self, pool: "FakePool", index: int):
        self.pool = pool
        self.index = index
        self.id = f"B{index + 1}"
        self.cur = ""
        self._html = ""
        self.closed = False
        self.casting = False
        self.calls: list[tuple] = []        # ("start", fps, w) / ("stop",) / ("input", event)

    async def open(self, url):
        await asyncio.sleep(self.pool.delay)
        self.cur = url
        self._html = self.pool.search_html if "/search?" in url else self.pool.repo_html
        return self._html

    async def html(self):
        return self._html

    async def url(self):
        return self.cur

    async def wait_for_search(self, timeout=10):
        return True

    async def type_search(self, query):
        return False

    async def pause(self, scale=1.0):
        await asyncio.sleep(0.01)

    async def start_screencast(self, fps, max_w):
        self.casting = True
        self.calls.append(("start", fps, max_w))

    async def stop_screencast(self):
        self.casting = False
        self.calls.append(("stop",))

    async def dispatch_input(self, event):
        self.calls.append(("input", event))

    async def close(self):
        self.closed = True

    def emit_frame(self, jpeg: bytes, width: int = 320, height: int = 200) -> None:
        """Push a frame through the pool's on_frame callback like the real backend does."""
        cb = self.pool.on_frame
        if cb is not None:
            cb(self.id, jpeg, {"width": width, "height": height, "scroll_x": 0, "scroll_y": 0,
                               "timestamp": None})


class FakePool:
    def __init__(self, cfg, log, stop, workers, delay: float = 0.02, pids=None):
        self.cfg, self.workers, self.delay = cfg, workers, delay
        self.on_frame = None
        self.started = self.closed = False
        self.slots: list[FakeSlot] = []
        self.pids = list(pids or [])
        self.search_html = _read("search.html")
        self.repo_html = _read("repo.html")

    async def start(self):
        self.started = True

    async def acquire(self, worker_index):
        slot = FakeSlot(self, worker_index)
        self.slots.append(slot)
        return slot

    async def recycle(self, slot):
        pass

    async def close(self):
        self.closed = True

    def processes(self):
        return list(self.pids)


def fake_pool_factory(delay: float = 0.02, pids=None):
    pools: list[FakePool] = []

    def factory(cfg, log, stop, workers):
        pool = FakePool(cfg, log, stop, workers, delay, pids)
        pools.append(pool)
        return pool
    factory.pools = pools            # type: ignore[attr-defined]
    return factory
