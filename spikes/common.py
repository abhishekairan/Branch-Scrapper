"""Shared helpers for the Phase-0 Zendriver spike."""
from __future__ import annotations
import asyncio, json, random, shutil, sys, tempfile, time
from pathlib import Path

import psutil
import zendriver as zd
from zendriver import cdp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

CHROME = "C:/Users/cubic/AppData/Local/Google/Chrome/Application/chrome.exe"
STEALTH_ARGS = [
    "--window-position=-32000,-32000",
    "--window-size=1280,800",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
]
LOADS = 0  # GitHub page loads made by this process


def new_profile(tag: str = "") -> str:
    return tempfile.mkdtemp(prefix=f"ghspike_{tag}_")


async def start(headless=False, profile=None, extra=None, args=None):
    profile = profile or new_profile()
    a = list(STEALTH_ARGS if args is None else args) + list(extra or [])
    b = await zd.start(headless=headless, browser_args=a, user_data_dir=profile,
                       browser="chrome", browser_executable_path=CHROME)
    b._spike_profile = profile
    return b


async def stop(b):
    prof = getattr(b, "_spike_profile", None)
    try:
        await b.stop()
    except Exception as e:  # noqa
        print("stop error", e)
    if prof:
        shutil.rmtree(prof, ignore_errors=True)


async def polite():
    await asyncio.sleep(random.uniform(3, 6))


async def goto(tab, url, settle=3.0):
    """Navigate (counts as a GitHub load), wait readyState complete + settle."""
    global LOADS
    LOADS += 1
    await tab.get(url)
    await tab.wait_for_ready_state("complete", timeout=30)
    await asyncio.sleep(settle)


def tree(b):
    p = psutil.Process(b._process_pid)
    return [p] + p.children(recursive=True)


def ram(procs):
    rss = uss = 0
    n = 0
    for p in procs:
        try:
            rss += p.memory_info().rss
            try:
                uss += p.memory_full_info().uss
            except Exception:
                pass
            n += 1
        except psutil.Error:
            pass
    return {"procs": n, "rss_mb": round(rss / 2**20, 1), "uss_mb": round(uss / 2**20, 1)}


def procs_with_profile(profile_marker: str):
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["name"] and "chrome" in p.info["name"].lower() and any(
                profile_marker in (c or "") for c in (p.info["cmdline"] or [])
            ):
                out.append(p)
        except psutil.Error:
            pass
    return out


def leftover_spike_chromes():
    return procs_with_profile("ghspike_")


def fields(d: dict) -> dict:
    return {k: ("EMPTY" if v in (None, "", [], {}) else "ok") for k, v in d.items()}
