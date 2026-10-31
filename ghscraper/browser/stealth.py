"""Human-like behaviour and the shared rate-limit cooldown, independent of the browser library.

Every wait goes through a stop-aware sleeper so stopping a run wakes workers immediately.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable

from ..config import Config
from .backend import Cancelled

Sleep = Callable[[float], Awaitable[None]]


def make_sleeper(stop: asyncio.Event) -> Sleep:
    """A sleep that raises `Cancelled` as soon as `stop` is set (same semantics as the old threaded code)."""
    async def sleep(seconds: float) -> None:
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(stop.wait(), seconds)
        except asyncio.TimeoutError:
            return
        raise Cancelled
    return sleep


def pause_seconds(cfg: Config, scale: float = 1.0) -> float:
    lo, hi = sorted((cfg.min_delay, cfg.max_delay))
    return random.uniform(lo, hi) * scale


async def pause(cfg: Config, sleep: Sleep, scale: float = 1.0) -> None:
    await sleep(pause_seconds(cfg, scale))


async def human_scroll(run_js: Callable[[str], Awaitable[object]], sleep: Sleep) -> None:
    """A couple of random scrolls, sometimes one back up. Page errors are ignored."""
    try:
        for _ in range(random.randint(1, 3)):
            await run_js(f"window.scrollBy(0, {random.randint(250, 750)})")
            await sleep(random.uniform(0.25, 0.8))
        if random.random() < 0.4:
            await run_js(f"window.scrollBy(0, {-random.randint(100, 400)})")
    except Cancelled:
        raise
    except Exception:
        pass


def keystroke_delay(ch: str, mean: float) -> float:
    """Per-key delay: gaussian around `mean`, slower on spaces, clamped to [0.02, 4*mean]."""
    mean = max(mean, 0.01)
    delay = random.gauss(mean, mean / 3)
    if ch == " ":
        delay *= 1.6
    return min(max(delay, 0.02), mean * 4)


async def human_type(send_char: Callable[[str], Awaitable[object]], text: str, mean_delay: float,
                     sleep: Sleep) -> None:
    for ch in text:
        await send_char(ch)
        await sleep(keystroke_delay(ch, mean_delay))


class SharedCooldown:
    """A rate-limit hit by one session applies to every session sharing that exit IP,
    so cooldowns are shared per proxy ("" = direct connection).

    The asyncio.Lock is created lazily per running loop so the module-level instance
    survives several `asyncio.run()` calls (tests, repeated CLI runs).
    """

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    async def remaining(self, key: str) -> float:
        async with self._get_lock():
            return self._until.get(key, 0.0) - time.time()

    async def extend(self, key: str, seconds: float) -> None:
        async with self._get_lock():
            self._until[key] = max(self._until.get(key, 0.0), time.time() + seconds)

    def clear(self) -> None:
        self._until.clear()


COOLDOWNS = SharedCooldown()  # module-level: every slot in the process shares it


def backoff_seconds(attempt: int) -> float:
    return min(20 * 2 ** (attempt - 1), 300) + random.uniform(0, 10)
