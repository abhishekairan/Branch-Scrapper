"""ScreenRelay: starts/stops screencasts to match what clients are watching, packs frames.

Rule: a slot streams only while some client has it in its view set; every slot we started
that is no longer wanted is stopped, so unviewed browsers cost nothing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..core.runmanager import RunManager
from .protocol import pack_frame
from .server import Client, Hub

log = logging.getLogger("ghscraper.screen")

DEFAULT_FPS = 2.0
DEFAULT_MAX_WIDTH = 480


class ScreenRelay:
    def __init__(self, manager: RunManager, hub: Hub):
        self.manager = manager
        self.hub = hub
        manager.frame_sink = self.on_frame
        manager.listeners.append(self.on_event)
        self._casting: dict[int, tuple[Any, tuple[float, int]]] = {}   # index -> (slot, (fps, max_w))
        self._seq: dict[int, int] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    # -- frames (called from the pool's on_frame, event-loop thread)
    def on_frame(self, index: int, jpeg: bytes, meta: dict) -> None:
        seq = self._seq[index] = (self._seq.get(index, 0) + 1) & 0xFFFFFFFF
        self.hub.publish_frame(index, pack_frame(index, seq, meta.get("width") or 0,
                                                 meta.get("height") or 0, jpeg))

    # -- run lifecycle
    def on_event(self, name: str, params: dict) -> None:
        if name == "run.started" or name == "run.finished":
            self._casting.clear()      # the old pool is gone / a new one is coming
            self._seq.clear()
            if name == "run.started":
                return
        elif name == "worker.status":
            if params.get("state") == "recovering":
                self._casting.pop(params["index"], None)   # tab is being replaced
            if params["index"] not in self._desired() and params["index"] not in self._casting:
                return
        else:
            return
        task = asyncio.get_running_loop().create_task(self.reconcile())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- views
    def _desired(self) -> dict[int, tuple[float, int]]:
        want: dict[int, tuple[float, int]] = {}
        for c in self.hub.clients:
            for idx, (fps, width) in c.views.items():
                old = want.get(idx)
                want[idx] = (fps, width) if old is None else (max(old[0], fps), max(old[1], width))
        return want

    async def set_view(self, client: Client, indices: list[int], fps: float, max_width: int) -> dict:
        client.views = {i: (fps, max_width) for i in indices}
        await self.reconcile()
        return self.state(indices)

    async def client_gone(self, client: Client) -> None:
        client.views = {}
        await self.reconcile()

    def state(self, indices: list[int]) -> dict:
        return {"active": [i for i in indices if i in self._casting],
                "pending": [i for i in indices if i not in self._casting]}

    async def reconcile(self) -> None:
        async with self._lock:
            want = self._desired()
            slots = self.manager.slots
            for idx in list(self._casting):
                slot, _ = self._casting[idx]
                if idx in want and slots.get(idx) is slot:
                    continue
                del self._casting[idx]
                if slots.get(idx) is slot:      # still alive: switch it off
                    try:
                        await slot.stop_screencast()
                    except Exception as e:
                        log.warning("stop_screencast(%s) failed: %s", idx, e)
            for idx, params in want.items():
                slot = slots.get(idx)
                if slot is None:
                    continue
                cur = self._casting.get(idx)
                if cur is not None and cur[0] is slot and cur[1] == params:
                    continue
                try:
                    await slot.start_screencast(int(round(params[0])), params[1])
                    self._casting[idx] = (slot, params)
                except Exception as e:
                    log.warning("start_screencast(%s) failed: %s", idx, e)
