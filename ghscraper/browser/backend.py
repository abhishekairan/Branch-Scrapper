"""The browser interface the scheduler and parsers are written against.

Anything that drives a real browser (today: Zendriver) implements `BrowserPool` and
`BrowserSlot`; tests use an in-memory fake. Nothing outside `zendriver_backend.py`
may import zendriver.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, Union, runtime_checkable


class BlockedError(RuntimeError):
    """GitHub served a rate-limit / abuse page."""


class Cancelled(Exception):
    """Raised inside a worker when the run is being stopped."""


class SlotCrashed(RuntimeError):
    """The slot's tab / context / Chrome died. The scheduler recycles the slot and requeues the task."""


# on_frame(slot_id, jpeg_bytes, meta); may be sync or async. meta has width, height,
# scroll_x, scroll_y and timestamp (seconds, may be None).
FrameCallback = Callable[[str, bytes, dict], Union[None, Awaitable[None]]]


@runtime_checkable
class BrowserSlot(Protocol):
    """One worker's browsing surface (a tab in its own context/window)."""

    id: str

    async def open(self, url: str) -> str:
        """Navigate, wait for the load, block-check, return the page HTML."""

    async def html(self) -> str:
        """Current DOM as HTML."""

    async def type_search(self, query: str) -> bool:
        """Type `query` into GitHub's header search box. False if it could not be done."""

    async def pause(self, scale: float = 1.0) -> None:
        """Random human-like delay (min_delay..max_delay) * scale. Raises Cancelled on stop."""

    async def start_screencast(self, fps: int, max_w: int) -> None: ...

    async def stop_screencast(self) -> None: ...

    async def dispatch_input(self, event: dict) -> None: ...

    async def close(self) -> None: ...

    # --- additions to the plan section 4 interface (the scheduler needs them) ---
    async def url(self) -> str:
        """Current page URL."""

    async def wait_for_search(self, timeout: float = 10) -> bool:
        """True once a GitHub search page's results payload / list is in the DOM."""


@runtime_checkable
class BrowserPool(Protocol):
    on_frame: FrameCallback | None

    async def start(self) -> None: ...

    async def acquire(self, worker_index: int) -> BrowserSlot:
        """Return the slot for this worker (launching its Chrome on first use)."""

    async def recycle(self, slot: BrowserSlot) -> None:
        """Replace a crashed slot's tab/context (relaunching its Chrome if it died). Mutates `slot` in place."""

    async def close(self) -> None: ...

    def processes(self) -> list[int]:
        """PIDs of every browser process (for RAM display)."""


def plan_chromes(workers: int, workers_per_chrome: int, isolate: bool) -> list[list[int]]:
    """Worker indices per browser process: worker i -> process i // size (size 1 when isolated)."""
    workers = max(1, workers)
    size = 1 if isolate else max(1, workers_per_chrome)
    return [list(range(s, min(s + size, workers))) for s in range(0, workers, size)]


# pool_factory(cfg, log, stop_event, workers) -> BrowserPool
PoolFactory = Callable[[Any, Callable[[str], None], Any, int], BrowserPool]
