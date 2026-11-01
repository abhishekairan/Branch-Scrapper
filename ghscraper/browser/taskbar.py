"""Keep the scraper's (headed, off-screen) Chrome windows out of the Windows taskbar and Alt-Tab.

Chrome stays headed, so web pages see no change. We only change the *native window style* of the
top-level windows that belong to this pool's Chrome process trees: `WS_EX_TOOLWINDOW` set and
`WS_EX_APPWINDOW` cleared, which removes a window from the taskbar, Alt-Tab and Task View. Windows of
any other process (the user's own Chrome) are never queried for style changes: the enumeration is
filtered by PID before anything else, and the selection logic is pure and unit-tested.

Windows only; everything is a no-op elsewhere. Uses ctypes, no extra dependencies.

A style change does not refresh an already-visible taskbar button, so a fixed window is hidden and
re-shown without activation (it lives at -32000,-32000, so nothing is visible on screen and focus is not
taken). New windows (a new worker context, a recycle, a relaunched Chrome) are caught by a background
thread: it sweeps quickly for a few seconds after `kick()` and slowly otherwise.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

log = logging.getLogger("ghscraper.taskbar")

CHROME_WINDOW_CLASS = "Chrome_WidgetWin_1"    # browser windows of Chrome, Edge and Brave
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

IDLE_INTERVAL = 1.0      # seconds between sweeps in steady state
BURST_INTERVAL = 0.1     # seconds between sweeps right after a launch / new context
BURST_SECONDS = 4.0


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    cls: str
    visible: bool
    has_owner: bool
    ex_style: int


class WindowApi(Protocol):
    def enumerate(self, pids: frozenset[int]) -> list[WindowInfo]: ...
    def hide_from_taskbar(self, w: WindowInfo) -> bool: ...


# ------------------------------------------------------------------ pure logic (unit-tested)
def in_taskbar(w: WindowInfo) -> bool:
    """Would the shell give this window a taskbar button / Alt-Tab entry?"""
    if not w.visible:
        return False
    if w.ex_style & WS_EX_APPWINDOW:
        return True
    return not w.has_owner and not (w.ex_style & WS_EX_TOOLWINDOW)


def hidden_style(ex_style: int) -> int:
    return (ex_style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW


def select_windows(windows: Iterable[WindowInfo], pids: Iterable[int]) -> list[WindowInfo]:
    """The windows to fix: Chrome browser windows of `pids` that currently show up in the taskbar."""
    mine = set(pids)
    return [w for w in windows if w.pid in mine and w.cls == CHROME_WINDOW_CLASS and in_taskbar(w)]


# ------------------------------------------------------------------ Win32
class Win32Api:
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4
    GWL_EXSTYLE = -20
    GW_OWNER = 4

    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt
        self._ct, self._wt = ctypes, wt
        u = self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._proc_t = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        u.EnumWindows.argtypes = [self._proc_t, wt.LPARAM]
        u.EnumWindows.restype = wt.BOOL
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.IsWindowVisible.restype = wt.BOOL
        u.GetWindow.argtypes = [wt.HWND, wt.UINT]
        u.GetWindow.restype = wt.HWND
        u.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]
        u.ShowWindowAsync.restype = wt.BOOL
        get = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW      # the Ptr forms don't exist on 32-bit
        set_ = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
        for fn in (get, set_):
            fn.restype = ctypes.c_ssize_t
        get.argtypes = [wt.HWND, ctypes.c_int]
        set_.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self._get_long, self._set_long = get, set_

    def enumerate(self, pids: frozenset[int]) -> list[WindowInfo]:
        ct, wt, u = self._ct, self._wt, self.user32
        out: list[WindowInfo] = []
        pid_box, name = wt.DWORD(), ct.create_unicode_buffer(256)

        def visit(hwnd, _lparam):
            try:
                u.GetWindowThreadProcessId(hwnd, ct.byref(pid_box))
                if pid_box.value in pids:        # never look at other processes' windows any further
                    n = u.GetClassNameW(hwnd, name, 256)
                    out.append(WindowInfo(
                        hwnd=int(hwnd or 0), pid=int(pid_box.value), cls=name.value[:n],
                        visible=bool(u.IsWindowVisible(hwnd)), has_owner=bool(u.GetWindow(hwnd, self.GW_OWNER)),
                        ex_style=int(self._get_long(hwnd, self.GWL_EXSTYLE)) & 0xFFFFFFFF))
            except Exception:
                pass
            return True

        cb = self._proc_t(visit)
        u.EnumWindows(cb, 0)
        return out

    def hide_from_taskbar(self, w: WindowInfo) -> bool:
        """Set the tool-window style, then hide + re-show (no activation) so the taskbar drops the button."""
        u, hwnd = self.user32, self._wt.HWND(w.hwnd)
        self._set_long(hwnd, self.GWL_EXSTYLE, hidden_style(w.ex_style))
        now = int(self._get_long(hwnd, self.GWL_EXSTYLE)) & 0xFFFFFFFF
        if now != hidden_style(w.ex_style):
            return False
        u.ShowWindowAsync(hwnd, self.SW_HIDE)               # async: never block on a busy Chrome UI thread
        u.ShowWindowAsync(hwnd, self.SW_SHOWNOACTIVATE)     # posted in order to the same window thread
        return True


# ------------------------------------------------------------------ background sweeper
class TaskbarHider:
    """Periodically hides the taskbar buttons of Chrome windows belonging to `pids()`."""

    def __init__(self, pids: Callable[[], Iterable[int]], api: WindowApi | None = None):
        self._pids = pids
        self._api = api
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._burst_until = 0.0
        self.fixed = 0

    @staticmethod
    def supported() -> bool:
        return sys.platform == "win32"

    def start(self) -> None:
        if self._thread is not None or (self._api is None and not self.supported()):
            return
        self._stop.clear()
        self.kick()
        self._thread = threading.Thread(target=self._run, name="taskbar-hider", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2)

    def kick(self) -> None:
        """A Chrome or window was just created: sweep quickly for a few seconds."""
        self._burst_until = time.monotonic() + BURST_SECONDS
        self._wake.set()

    def sweep(self) -> int:
        """One pass. Returns how many windows were fixed. Never raises."""
        try:
            if self._api is None:
                self._api = Win32Api()
            pids = frozenset(p for p in self._pids() if p)
            if not pids:
                return 0
            n = 0
            for w in select_windows(self._api.enumerate(pids), pids):
                if self._api.hide_from_taskbar(w):
                    n += 1
                    log.debug("removed taskbar button of hwnd=%#x pid=%d", w.hwnd, w.pid)
            self.fixed += n
            return n
        except Exception as e:
            log.debug("taskbar sweep failed: %s: %s", type(e).__name__, e)
            return 0

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sweep()
            wait = BURST_INTERVAL if time.monotonic() < self._burst_until else IDLE_INTERVAL
            self._wake.wait(wait)
            self._wake.clear()
