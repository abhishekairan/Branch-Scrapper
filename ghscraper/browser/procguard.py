"""Make sure the browsers die with us.

Windows: the *current process* is put in a Job Object with KILL_ON_JOB_CLOSE, before any
Chrome is launched. Every descendant inherits the job, so even `taskkill /F` on the
sidecar takes all Chrome processes with it (verified in the Phase-0 spike; assigning
Chrome's root PID after launch is NOT enough, its sandboxed children refuse assignment).

Linux / macOS (best effort, untested here): an atexit hook plus SIGTERM/SIGHUP handlers
kill the PIDs reported by the registered pool. A hard `kill -9` of the sidecar is not covered.
TODO(phase 7): PR_SET_PDEATHSIG on Linux (prctl via ctypes) and a watchdog on macOS.
`os.setpgrp()` is deliberately not used: it would detach a CLI from its terminal's Ctrl+C.

On every OS `ZendriverPool.close()` also kills its own PIDs as a fallback.
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
import threading
from typing import Callable, Iterable

log = logging.getLogger("ghscraper.procguard")

_lock = threading.Lock()
_guarded = False
_job_handle = None            # kept alive for the life of the process (closing it kills the job)
_providers: list[Callable[[], Iterable[int]]] = []
_hooks_installed = False


# ------------------------------------------------------------------ killing
def kill_pids(pids: Iterable[int]) -> int:
    """Kill these processes and their descendants. Returns how many were signalled. Never raises."""
    try:
        import psutil
    except ImportError:  # psutil is a hard dependency; stay defensive in atexit paths
        return 0
    victims = []
    for pid in set(pids):
        if not pid or pid == os.getpid():
            continue
        try:
            p = psutil.Process(pid)
            victims.append(p)
            victims.extend(p.children(recursive=True))
        except psutil.Error:
            pass
    n = 0
    for p in victims:
        try:
            p.kill()
            n += 1
        except psutil.Error:
            pass
    return n


def snapshot(pids: Iterable[int | None]) -> list[tuple[int, float]]:
    """(pid, create_time) of these processes and their descendants, for a later PID-reuse-safe kill."""
    out: list[tuple[int, float]] = []
    try:
        import psutil
    except ImportError:
        return out
    for pid in pids:
        if not pid:
            continue
        try:
            p = psutil.Process(pid)
            for q in [p, *p.children(recursive=True)]:
                try:
                    out.append((q.pid, q.create_time()))
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
    return out


def kill_snapshot(snap: Iterable[tuple[int, float]]) -> int:
    """Kill the snapshotted processes that are still the same process (same create time). Never raises."""
    try:
        import psutil
    except ImportError:
        return 0
    n = 0
    for pid, created in snap:
        try:
            p = psutil.Process(pid)
            if abs(p.create_time() - created) < 1e-3:
                p.kill()
                n += 1
        except psutil.Error:
            pass
    return n


def track(provider: Callable[[], Iterable[int]]) -> None:
    """Register a function returning PIDs to kill if we exit unexpectedly (non-Windows hooks + atexit)."""
    with _lock:
        if provider not in _providers:
            _providers.append(provider)
    _install_hooks()


def untrack(provider: Callable[[], Iterable[int]]) -> None:
    with _lock:
        if provider in _providers:
            _providers.remove(provider)


def _kill_tracked() -> None:
    with _lock:
        providers = list(_providers)
    for provider in providers:
        try:
            kill_pids(provider())
        except Exception:
            pass


def _install_hooks() -> None:
    global _hooks_installed
    with _lock:
        if _hooks_installed:
            return
        _hooks_installed = True
    atexit.register(_kill_tracked)
    if threading.current_thread() is not threading.main_thread():
        return  # signal.signal only works on the main thread
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous = signal.getsignal(sig)

            def handler(signum, frame, _previous=previous):
                _kill_tracked()
                if callable(_previous):
                    _previous(signum, frame)
                    return
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


# ------------------------------------------------------------------ guard
def guard_current_process() -> bool:
    """Arrange for browser processes to die with this process. Idempotent; never raises.

    Returns True if an OS-level mechanism (Windows Job Object) is active. On other
    platforms the atexit/signal fallback is installed via `track()` and False is returned.
    """
    global _guarded, _job_handle
    with _lock:
        if _guarded:
            return _job_handle is not None
        _guarded = True
    if sys.platform != "win32":
        return False
    try:
        _job_handle = _windows_assign_self_to_job()
        return True
    except Exception as e:  # already in a job that forbids nesting, no permission, ...
        log.warning("Could not put the process in a kill-on-close Job Object (%s); "
                    "falling back to best-effort cleanup on exit.", e)
        _job_handle = None
        return False


def _windows_assign_self_to_job():
    import ctypes
    from ctypes import wintypes as wt

    k = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k.CreateJobObjectW.restype = wt.HANDLE
    k.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
    k.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    k.GetCurrentProcess.restype = wt.HANDLE
    k.CloseHandle.argtypes = [wt.HANDLE]

    job = k.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9 = ExtendedLimitInformation
            raise ctypes.WinError(ctypes.get_last_error())
        if not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception:
        k.CloseHandle(job)
        raise
    return job
