"""Zendriver implementation of the browser interface (the only module that imports zendriver).

Layout: a pool runs `ceil(workers / workers_per_chrome)` Chrome processes (one per worker with
`isolate_workers`). Every worker gets its own browser context + window inside its Chrome, with
its own proxy. Chrome is headed but parked off-screen (headless leaks via empty Client Hints,
see docs/spike-results.md). With `user_data_dir` set, worker 0 of each Chrome uses that
Chrome's default tab so it runs inside the persistent (logged-in) profile.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import inspect
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import websockets
import zendriver as zd
from zendriver import cdp
from zendriver.core.connection import ProtocolException

from ..config import Config
from ..parsers import detect_block
from . import procguard, stealth
from .taskbar import TaskbarHider
from .backend import BlockedError, Cancelled, FrameCallback, SlotCrashed, plan_chromes

dbg = logging.getLogger("ghscraper.browser")

SEARCH_TRIGGER = 'button[aria-label^="Search or jump to"]'
SEARCH_INPUT = 'input[role="combobox"][aria-label^="Search or jump to"]'

# Off-screen but real windows that Chrome keeps rendering (verified in the spike).
LAUNCH_ARGS = [
    "--window-position=-32000,-32000",
    "--window-size=1280,800",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
]
BLOCK_PATTERNS = ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp", "*.ico",
                  "*.woff", "*.woff2", "*.ttf", "*.mp4", "*.webm"]

_CRASH_MARKERS = ("connection was closed", "session was detached", "target closed", "target crashed",
                  "no target with given id", "session with given id not found", "no session with given id")


# ------------------------------------------------------------------ pure helpers (unit-tested)
def normalize_proxy(proxy: str) -> str | None:
    """'host:port' -> 'http://host:port'; schemes (http/https/socks5) and credentials are kept."""
    proxy = (proxy or "").strip()
    if not proxy:
        return None
    return proxy if "://" in proxy else f"http://{proxy}"


class FrameThrottle:
    """Honour a target fps by dropping frames: Chrome sends one per paint (~100 fps measured)."""

    def __init__(self, fps: float):
        self.interval = 1.0 / max(float(fps), 0.1)
        self._last: float | None = None

    def allow(self, now: float) -> bool:
        if self._last is None or now - self._last >= self.interval - 1e-9:
            self._last = now
            return True
        return False


def is_crash_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, websockets.exceptions.WebSocketException)):
        return True
    if isinstance(exc, ProtocolException):
        msg = str(exc).lower()
        return any(m in msg for m in _CRASH_MARKERS)
    return False


_MOUSE_TYPES = {"move": "mouseMoved", "down": "mousePressed", "up": "mouseReleased", "wheel": "mouseWheel"}
_KEY_TYPES = {"down": "keyDown", "up": "keyUp", "raw": "rawKeyDown", "char": "char"}
_BUTTON_MASK = {"left": 1, "right": 2, "middle": 4}


def build_input_command(event: dict):
    """Translate a UI input event into a CDP command (a generator for `tab.send`).

    {"type": "mouse", "action": "move|down|up|wheel", "x", "y", "button": "left|right|middle",
     "click_count", "delta_x", "delta_y", "modifiers"}      (x, y: page CSS pixels)
    {"type": "key", "action": "down|up|raw|char", "key", "code", "text", "vk", "modifiers"}
    {"type": "text", "text": "..."}
    """
    kind = event.get("type")
    if kind == "mouse":
        action = event.get("action", "move")
        if action not in _MOUSE_TYPES:
            raise ValueError(f"unknown mouse action {action!r}")
        name = event.get("button", "left")
        if name not in _BUTTON_MASK and name != "none":
            raise ValueError(f"unknown mouse button {name!r}")
        kw: dict[str, Any] = {}
        if action in ("down", "up"):
            kw["button"] = cdp.input_.MouseButton(name if name != "none" else "left")
            kw["buttons"] = _BUTTON_MASK.get(name, 1) if action == "down" else 0
            kw["click_count"] = int(event.get("click_count", 1))
        elif action == "wheel":
            kw["delta_x"] = float(event.get("delta_x", 0))
            kw["delta_y"] = float(event.get("delta_y", 0))
        if event.get("modifiers"):
            kw["modifiers"] = int(event["modifiers"])
        return cdp.input_.dispatch_mouse_event(_MOUSE_TYPES[action], x=float(event["x"]), y=float(event["y"]), **kw)
    if kind == "key":
        action = event.get("action", "down")
        if action not in _KEY_TYPES:
            raise ValueError(f"unknown key action {action!r}")
        kw = {}
        for src, dst in (("key", "key"), ("code", "code"), ("text", "text"),
                         ("vk", "windows_virtual_key_code"), ("modifiers", "modifiers")):
            if event.get(src) is not None:
                kw[dst] = event[src]
        return cdp.input_.dispatch_key_event(_KEY_TYPES[action], **kw)
    if kind == "text":
        return cdp.input_.insert_text(str(event["text"]))
    raise ValueError(f"unknown input event type {kind!r}")


def _char_key_fields(ch: str) -> dict:
    fields: dict[str, Any] = {"key": ch, "text": ch}
    if ch.isascii() and ch.isalpha():
        fields.update(code=f"Key{ch.upper()}", windows_virtual_key_code=ord(ch.upper()))
    elif ch.isascii() and ch.isdigit():
        fields.update(code=f"Digit{ch}", windows_virtual_key_code=ord(ch))
    elif ch == " ":
        fields.update(code="Space", windows_virtual_key_code=32)
    return fields


# ------------------------------------------------------------------ slot
def _guard(fn):
    """Turn "the tab / Chrome is gone" errors into SlotCrashed so the scheduler can recycle and requeue."""
    @functools.wraps(fn)
    async def wrapper(self: "ZendriverSlot", *args, **kwargs):
        try:
            return await fn(self, *args, **kwargs)
        except (Cancelled, SlotCrashed, BlockedError):
            raise
        except Exception as e:
            if self._is_dead(e):
                raise SlotCrashed(f"{type(e).__name__}: {e}") from e
            raise
    return wrapper


class ZendriverSlot:
    def __init__(self, pool: "ZendriverPool", chrome: "_Chrome", index: int):
        self.pool = pool
        self.cfg = pool.cfg
        self.chrome = chrome
        self.index = index
        self.id = f"B{index + 1}"
        self.tab: zd.Tab | None = None
        self.context_id = None
        self.gen = 0                      # Chrome generation this tab belongs to
        self._sleep = pool._sleep
        self._casting = False
        self._throttle: FrameThrottle | None = None
        self._handler_tab: zd.Tab | None = None

    # -- internals
    def _is_dead(self, exc: BaseException) -> bool:
        return is_crash_error(exc) or self.gen != self.chrome.gen or not self.chrome.alive()

    def _reset_state(self) -> None:
        self._casting = False
        self._throttle = None
        self._handler_tab = None

    async def _eval(self, js: str):
        return await self.tab.evaluate(js)

    async def _probe(self) -> bool:
        """Is the tab still answering? Used to tell a slow page from a dead renderer."""
        try:
            await asyncio.wait_for(self.tab.evaluate("1"), 4)
            return True
        except Exception:
            return False

    async def _wait_for_js(self, js: str, timeout: float, every: float = 0.4) -> bool:
        end = time.monotonic() + timeout
        while True:
            try:
                if await self._eval(js):
                    return True
            except Exception as e:
                if self._is_dead(e):
                    raise
            if time.monotonic() >= end:
                return False
            await self._sleep(every)

    async def _wait_ready(self, timeout: float = 15) -> None:
        try:
            await self.tab.wait_for_ready_state("complete", timeout=int(timeout))
        except asyncio.TimeoutError:
            pass  # read whatever has rendered, like the old code did

    # -- BrowserSlot
    @_guard
    async def html(self) -> str:
        return await self.tab.get_content()

    @_guard
    async def url(self) -> str:
        return str(await self._eval("location.href"))

    @_guard
    async def pause(self, scale: float = 1.0) -> None:
        await stealth.pause(self.cfg, self._sleep, scale)

    @_guard
    async def open(self, url: str) -> str:
        """Open a URL with retries/back-off; returns the page HTML. Shares rate-limit cooldowns per proxy."""
        cfg = self.cfg
        ip_key = cfg.proxy_for(self.index)
        for attempt in range(1, cfg.max_retries + 1):
            wait_shared = await stealth.COOLDOWNS.remaining(ip_key)
            if wait_shared > 0:
                await self._sleep(wait_shared + random.uniform(0, 5))
            if self.pool.stop.is_set():
                raise Cancelled
            try:
                await asyncio.wait_for(self.tab.get(url), cfg.page_timeout)
                await self._wait_ready(min(cfg.page_timeout, 15))
                html = await self.tab.get_content()
                title = await self._eval("document.title")
                if str(await self._eval("location.href")).startswith("chrome-error:"):
                    raise ConnectionAbortedError("navigation failed (chrome-error page)")
                reason = detect_block(html, title or "")
                if not reason:
                    await stealth.human_scroll(self._eval, self._sleep)
                    return await self.tab.get_content()
                raise BlockedError(reason)
            except (Cancelled, SlotCrashed):
                raise
            except Exception as e:
                if not isinstance(e, BlockedError):
                    if self._is_dead(e) or (isinstance(e, asyncio.TimeoutError) and not await self._probe()):
                        raise SlotCrashed(f"{type(e).__name__}: {e}") from e
                wait = stealth.backoff_seconds(attempt)
                msg = str(e).splitlines()[0][:90] if str(e) else ""
                self.pool.log(f"[yellow][{self.id}] Attempt {attempt}/{cfg.max_retries} for {url} "
                              f"failed ({type(e).__name__}: {msg}). Backing off {wait:.0f}s[/]")
                if attempt == cfg.max_retries:
                    raise
                if isinstance(e, BlockedError):
                    await stealth.COOLDOWNS.extend(ip_key, wait)
                    continue  # the shared cooldown check at loop top does the sleeping
                await self._sleep(wait)
        raise RuntimeError("unreachable")

    @_guard
    async def wait_for_search(self, timeout: float = 10) -> bool:
        return await self._wait_for_js(
            "!!document.querySelector('script[data-target=\"react-app.embeddedData\"]')"
            " || !!document.querySelector('[data-testid=\"results-list\"]')", timeout)

    async def _click_selector(self, selector: str) -> bool:
        raw = await self._eval(
            "JSON.stringify((()=>{const e=document.querySelector(" + json.dumps(selector) + ");"
            "if(!e)return null;const r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})())")
        pos = json.loads(raw) if raw else None
        if not pos:
            return False
        x, y = pos
        send = self.tab.send
        btn = cdp.input_.MouseButton.LEFT
        await send(cdp.input_.dispatch_mouse_event("mouseMoved", x=x, y=y))
        await self._sleep(random.uniform(0.05, 0.2))
        await send(cdp.input_.dispatch_mouse_event("mousePressed", x=x, y=y, button=btn, buttons=1, click_count=1))
        await self._sleep(random.uniform(0.03, 0.09))
        await send(cdp.input_.dispatch_mouse_event("mouseReleased", x=x, y=y, button=btn, buttons=0, click_count=1))
        return True

    async def _send_char(self, ch: str) -> None:
        fields = _char_key_fields(ch)
        await self.tab.send(cdp.input_.dispatch_key_event("keyDown", **fields))
        fields.pop("text")
        await self.tab.send(cdp.input_.dispatch_key_event("keyUp", **fields))

    async def _press_enter(self) -> None:
        kw = dict(key="Enter", code="Enter", windows_virtual_key_code=13)
        await self.tab.send(cdp.input_.dispatch_key_event("keyDown", text="\r", **kw))
        await self.tab.send(cdp.input_.dispatch_key_event("keyUp", **kw))

    @_guard
    async def type_search(self, query: str) -> bool:
        """Use GitHub's header search box like a person would. Returns success."""
        try:
            await self.open("https://github.com/")
            await self.pause(0.5)
            if not await self._wait_for_js(f"!!document.querySelector({json.dumps(SEARCH_TRIGGER)})", 8):
                return False
            if not await self._click_selector(SEARCH_TRIGGER):
                return False
            if not await self._wait_for_js(f"!!document.querySelector({json.dumps(SEARCH_INPUT)})", 8):
                return False
            await self._sleep(random.uniform(0.3, 0.9))
            await stealth.human_type(self._send_char, query, self.cfg.typing_delay, self._sleep)
            await self._sleep(random.uniform(0.4, 1.0))
            await self._press_enter()
            ok = await self._wait_for_js(
                "location.pathname.startsWith('/search') && !!document.querySelector('[data-testid=\"results-list\"]')",
                20)
            if ok:
                await self._wait_ready()
                await stealth.human_scroll(self._eval, self._sleep)
            return bool(ok)
        except (Cancelled, SlotCrashed, BlockedError):
            raise
        except Exception as e:
            if self._is_dead(e):
                raise
            self.pool.log(f"[yellow]Search-box typing failed ({type(e).__name__}); falling back to URL navigation[/]")
            return False

    # -- live view
    @_guard
    async def start_screencast(self, fps: int, max_w: int) -> None:
        if self._casting:
            await self.stop_screencast()
        tab = self.tab
        self._throttle = FrameThrottle(fps)
        if self._handler_tab is not tab:
            tab.add_handler(cdp.page.ScreencastFrame, self._on_frame)
            self._handler_tab = tab
        self._casting = True
        await tab.send(cdp.page.start_screencast(format_="jpeg", quality=50, max_width=int(max_w),
                                                 every_nth_frame=1))

    @_guard
    async def stop_screencast(self) -> None:
        self._casting = False
        if self.tab is not None:
            try:
                await self.tab.send(cdp.page.stop_screencast())
            except Exception as e:
                if not self._is_dead(e):
                    raise

    async def _on_frame(self, ev: "cdp.page.ScreencastFrame") -> None:
        tab = self.tab
        try:  # always ack, or Chrome stops sending; throttling is done after the ack
            await tab.send(cdp.page.screencast_frame_ack(ev.session_id))
        except Exception:
            return
        cb = self.pool.on_frame
        if not self._casting or cb is None or self._throttle is None or not self._throttle.allow(time.monotonic()):
            return
        m = ev.metadata
        meta = {"width": m.device_width, "height": m.device_height, "scroll_x": m.scroll_offset_x,
                "scroll_y": m.scroll_offset_y, "timestamp": m.timestamp}
        try:
            res = cb(self.id, base64.b64decode(ev.data), meta)
            if inspect.isawaitable(res):
                await res
        except Exception as e:  # a broken consumer must not kill the stream
            dbg.debug("on_frame callback failed: %s", e)

    @_guard
    async def dispatch_input(self, event: dict) -> None:
        await self.tab.send(build_input_command(event))

    @_guard
    async def close(self) -> None:
        """Release this worker's context/tab. Chrome itself is stopped by the pool."""
        self._casting = False
        await self.pool._detach(self)


# ------------------------------------------------------------------ pool
@dataclass
class _Chrome:
    index: int
    workers: list[int]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    browser: zd.Browser | None = None
    default_tab: zd.Tab | None = None
    default_tab_taken: bool = False
    gen: int = 0
    snapshot: list = field(default_factory=list)   # [(pid, create_time)] of the last known process tree

    def alive(self) -> bool:
        b = self.browser
        return bool(b is not None and not b.stopped and b.connection is not None and not b.connection.closed)

    @property
    def pid(self) -> int | None:
        b = self.browser
        return getattr(b, "_process_pid", None) if b is not None else None


class ZendriverPool:
    def __init__(self, cfg: Config, log: Callable[[str], None], stop: asyncio.Event, workers: int):
        self.cfg = cfg
        self.log = log
        self.stop = stop
        self.on_frame: FrameCallback | None = None
        self.groups = plan_chromes(workers, cfg.workers_per_chrome, cfg.isolate_workers)
        self._chromes = [_Chrome(k, g) for k, g in enumerate(self.groups)]
        self._chrome_of = {w: c for c in self._chromes for w in c.workers}
        self._slots: dict[int, ZendriverSlot] = {}
        self._launch_lock = asyncio.Lock()
        self._sleep = stealth.make_sleeper(stop)
        self._closed = False
        # Windows: Chrome is headed (off-screen); keep its windows out of the taskbar / Alt-Tab.
        self._taskbar = TaskbarHider(self.processes) if cfg.hide_taskbar_icons else None

    # ---- lifecycle
    async def start(self) -> None:
        procguard.guard_current_process()
        procguard.track(self.processes)
        self._hide_taskbar_icons()

    def _hide_taskbar_icons(self) -> None:
        """(Re)start the taskbar sweeper and make it look now; no-op off Windows or when disabled."""
        if self._taskbar is not None and not self.cfg.headless and not self._closed:
            self._taskbar.start()
            self._taskbar.kick()

    async def acquire(self, worker_index: int) -> ZendriverSlot:
        if worker_index not in self._chrome_of:
            raise IndexError(f"worker {worker_index} is outside this pool ({sum(map(len, self.groups))} workers)")
        if self._closed or self.stop.is_set():
            raise Cancelled
        rec = self._chrome_of[worker_index]
        slot = ZendriverSlot(self, rec, worker_index)
        async with rec.lock:
            if not rec.alive():
                await self._launch(rec)
            await self._attach(slot)
        self._slots[worker_index] = slot
        return slot

    async def recycle(self, slot: ZendriverSlot) -> None:
        rec = slot.chrome
        async with rec.lock:
            if self._closed or self.stop.is_set():
                raise Cancelled
            if rec.alive() and slot.gen == rec.gen:
                await self._detach(slot)
            if not rec.alive():
                self.log(f"[yellow][{slot.id}] Browser #{rec.index + 1} died; relaunching it.[/]")
                await self._stop_browser(rec)
                await self._launch(rec)
            await self._attach(slot)

    async def close(self) -> None:
        self._closed = True
        if self._taskbar is not None:
            self._taskbar.stop()
        procguard.untrack(self.processes)
        for rec in self._chromes:
            await self._stop_browser(rec)

    def roots(self) -> list[int]:
        """PIDs of the Chrome browser processes (one per live record)."""
        return [rec.pid for rec in self._chromes if rec.pid]

    def processes(self) -> list[int]:
        pids: list[int] = []
        for rec in self._chromes:
            pid = rec.pid
            if not pid:
                continue
            pids.append(pid)
            try:
                import psutil
                pids.extend(c.pid for c in psutil.Process(pid).children(recursive=True))
            except Exception:
                pass
        return pids

    # ---- Chrome processes
    def _launch_kwargs(self, rec: _Chrome) -> dict[str, Any]:
        cfg = self.cfg
        args = list(LAUNCH_ARGS)
        profile = cfg.profile_for(rec.index)
        kwargs: dict[str, Any] = dict(headless=cfg.headless, lang="en-US", browser=cfg.browser)
        if cfg.browser_path:
            kwargs["browser_executable_path"] = cfg.browser_path
        if profile:
            kwargs["user_data_dir"] = profile
            proxy = normalize_proxy(cfg.proxy_for(rec.workers[0]))
            if proxy:
                if "@" in proxy:
                    raise ValueError("an authenticated proxy can't be used together with user_data_dir "
                                     "(the logged-in profile worker); use a proxy without credentials")
                args.append(f"--proxy-server={proxy}")
        kwargs["browser_args"] = args
        return kwargs

    async def _launch(self, rec: _Chrome) -> None:
        kwargs = self._launch_kwargs(rec)
        # Launches are serialised with a small random gap: avoids a burst of simultaneous
        # starts, which also looks less human.
        async with self._launch_lock:
            if self.stop.is_set():
                raise Cancelled
            browser = await zd.start(**kwargs)
            rec.browser, rec.default_tab, rec.default_tab_taken = browser, browser.main_tab, False
            rec.gen += 1
            rec.snapshot = procguard.snapshot([rec.pid])
            self._hide_taskbar_icons()
            await self._sleep(random.uniform(1.0, 2.5))

    async def _stop_browser(self, rec: _Chrome) -> None:
        b = rec.browser
        if b is None:
            return
        snap = procguard.snapshot([rec.pid]) or rec.snapshot
        try:
            await asyncio.wait_for(b.stop(), 15)
        except Exception as e:
            dbg.debug("browser.stop failed: %s", e)
        procguard.kill_snapshot(snap)  # fallback: anything still alive from this Chrome's tree
        rec.browser = rec.default_tab = None

    # ---- tabs / contexts
    async def _attach(self, slot: ZendriverSlot) -> None:
        """Give `slot` a fresh tab on its Chrome's current browser."""
        rec, cfg = slot.chrome, self.cfg
        browser = rec.browser
        slot._reset_state()
        slot.context_id = None
        uses_profile_tab = bool(cfg.profile_for(rec.index)) and slot.index == rec.workers[0]
        if uses_profile_tab:
            if not rec.default_tab_taken and rec.default_tab is not None:
                tab = rec.default_tab
                rec.default_tab_taken = True
            else:  # recycling: a fresh tab in the same (default, logged-in) context
                tab = await browser.get("about:blank", new_tab=True)
        else:
            tab = await browser.create_context("about:blank", new_window=True,
                                               proxy_server=normalize_proxy(cfg.proxy_for(slot.index)))
            slot.context_id = tab.target.browser_context_id
            self._hide_taskbar_icons()    # the new context has its own window
        slot.tab, slot.gen = tab, rec.gen
        if cfg.block_resources:
            await tab.send(cdp.network.enable())
            await tab.send(cdp.network.set_blocked_ur_ls(urls=BLOCK_PATTERNS))

    async def _detach(self, slot: ZendriverSlot) -> None:
        """Best-effort disposal of a slot's context/tab (never raises)."""
        rec, tab = slot.chrome, slot.tab
        if tab is None or not rec.alive() or slot.gen != rec.gen:
            return
        try:
            if slot.context_id is not None:
                await asyncio.wait_for(
                    rec.browser.connection.send(cdp.target.dispose_browser_context(slot.context_id)), 5)
            elif tab is not rec.default_tab:
                await asyncio.wait_for(tab.close(), 5)
        except Exception as e:
            dbg.debug("detach failed: %s", e)
        slot.tab = None
