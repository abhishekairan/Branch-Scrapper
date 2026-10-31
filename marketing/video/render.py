"""Render the Ghost Scraper promo (marketing/video/index.html) to MP4 via headless Chrome (zendriver) + ffmpeg.

Every frame is produced by calling window.renderAt(t) and capturing the viewport, so the result is
deterministic and independent of wall-clock time.

Usage (from anywhere):
    F:/Projects/Github Scrapper/V1/.venv/Scripts/python.exe marketing/video/render.py            # both formats
    ... render.py --format 9x16                                                                 # one format
    ... render.py --stills 1.0,3.9,8.5 --still-dir C:/tmp/stills --format 9x16                   # review PNGs only

Outputs (marketing/out/): ghost-scraper-<fmt>.mp4, cover-<fmt>.png, contact-<fmt>.jpg
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import zendriver as zd
from zendriver import cdp

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
SIZES = {"9x16": (1080, 1920), "4x5": (1080, 1350)}
COVER_T = 4.2  # title card: icon + wordmark fully revealed


def kill_leftovers(marker: str) -> None:
    """Kill any process whose command line contains our private temp profile dir (never touches other Chrome)."""
    ps = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{marker}') }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)


async def open_page(browser, fmt: str):
    w, h = SIZES[fmt]
    tab = await browser.get("about:blank")
    await tab.send(cdp.emulation.set_device_metrics_override(width=w, height=h, device_scale_factor=1, mobile=False))
    url = (HERE / "index.html").as_uri() + f"?format={fmt}"
    tab = await browser.get(url)
    await tab.send(cdp.emulation.set_device_metrics_override(width=w, height=h, device_scale_factor=1, mobile=False))
    for _ in range(100):  # wait for app.js to define window.ready
        if await tab.evaluate("typeof window.ready !== 'undefined'"):
            break
        await asyncio.sleep(0.1)
    else:
        raise RuntimeError("page did not load window.ready")
    await tab.evaluate("window.ready", await_promise=True)
    return tab


async def grab(tab, t: float, fmt: str = "jpeg", quality: int = 95) -> bytes:
    await tab.evaluate(f"window.renderAt({t!r})")
    data = await tab.send(cdp.page.capture_screenshot(format_=fmt, quality=quality if fmt == "jpeg" else None))
    return base64.b64decode(data)


async def render_format(browser, fmt: str, args) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tab = await open_page(browser, fmt)
    tl = await tab.evaluate("JSON.stringify(window.TIMELINE)")
    import json

    tl = json.loads(tl)
    fps, total = args.fps, tl["total"]
    print(f"[{fmt}] timeline {total:.2f}s, {tl['w']}x{tl['h']}", flush=True)

    # cover (PNG, lossless)
    (OUT / f"cover-{fmt}.png").write_bytes(await grab(tab, args.cover_t, "png"))

    if args.stills:
        d = Path(args.still_dir)
        d.mkdir(parents=True, exist_ok=True)
        for t in args.stills:
            (d / f"still_{fmt}_{t:05.2f}.png").write_bytes(await grab(tab, t, "png"))
        print(f"[{fmt}] wrote {len(args.stills)} stills to {d}", flush=True)
        return

    n = int(round(total * fps))
    mp4 = OUT / f"ghost-scraper-{fmt}.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf), "-r", str(fps),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-movflags", "+faststart", "-an", str(mp4),
    ]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            ff.stdin.write(await grab(tab, i / fps))
            if i % 60 == 0:
                print(f"[{fmt}] frame {i}/{n}", flush=True)
        ff.stdin.close()
        rc = ff.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg exited with {rc}")
    except BaseException:
        ff.kill()
        raise

    # contact sheet: 12 frames, 4x3
    step = math.ceil(n / 12)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4),
            "-vf", f"select='not(mod(n\\,{step}))',scale=360:-1,tile=4x3",
            "-frames:v", "1", "-fps_mode", "vfr", "-q:v", "3", str(OUT / f"contact-{fmt}.jpg"),
        ],
        check=True,
    )
    print(f"[{fmt}] wrote {mp4.name} ({mp4.stat().st_size / 1e6:.1f} MB)", flush=True)


async def main(args) -> None:
    profile = tempfile.mkdtemp(prefix="gs-promo-chrome-")
    browser = None
    try:
        browser = await zd.start(
            headless=True,
            user_data_dir=profile,
            browser_args=["--hide-scrollbars", "--force-device-scale-factor=1", "--mute-audio"],
        )
        fmts = list(SIZES) if args.format == "both" else [args.format]
        for fmt in fmts:
            await render_format(browser, fmt, args)
    finally:
        try:
            if browser is not None:
                await browser.stop()
        except Exception:
            pass
        kill_leftovers(Path(profile).name)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["9x16", "4x5", "both"], default="both")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=19)
    ap.add_argument("--cover-t", type=float, default=COVER_T)
    ap.add_argument("--stills", type=lambda s: [float(x) for x in s.split(",")], default=None,
                    help="comma-separated times; writes PNGs to --still-dir and skips the video")
    ap.add_argument("--still-dir", default=str(HERE.parent / "out" / "_stills"))
    asyncio.run(main(ap.parse_args()))
    sys.exit(0)
