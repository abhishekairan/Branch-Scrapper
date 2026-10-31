"""Development / test harness: the REAL sidecar (``python -m ghscraper.sidecar --test-mode``) with one
difference - the offline fake browsers also emit screencast frames while a client watches them.

The stock fake pool never produces frames, so without this the Browsers panel cannot be exercised
without Chrome. Same contract as the sidecar: GHOSTSCRAPER_TOKEN env, READY line on stdout, exits when
stdin closes. Extra arguments are passed through (``--port N``, ``--data-dir DIR``).

    GHOSTSCRAPER_TOKEN=<>=32 chars> .venv/Scripts/python.exe app/scripts/dev_sidecar.py [--data-dir DIR]

Env: HARNESS_DELAY (seconds the fake browser takes per page, default 0.3), HARNESS_FRAME_FPS (default 10).
Never used in production; lives outside the ghscraper package on purpose.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["GHOSTSCRAPER_FAKE_POOL"] = "1"

from ghscraper.api import testing  # noqa: E402

FRAMES = [p.read_bytes() for p in sorted((Path(__file__).parent / "fixtures").glob("frame*.jpg"))]
DELAY = float(os.environ.get("HARNESS_DELAY", "0.3"))
FPS = float(os.environ.get("HARNESS_FRAME_FPS", "10"))

_orig_init = testing.FakePool.__init__
_orig_start = testing.FakeSlot.start_screencast
_orig_stop = testing.FakeSlot.stop_screencast
_orig_close = testing.FakeSlot.close


def _pool_init(self, cfg, log, stop, workers, delay=None, pids=None):
    _orig_init(self, cfg, log, stop, workers, DELAY, pids)   # the factory's own 0.02 s default is too fast to watch


async def _cast(slot: "testing.FakeSlot", fps: float) -> None:
    seq = 0
    while True:
        # a different picture per slot; page viewport reported as 1280x800 CSS px
        slot.emit_frame(FRAMES[(slot.index + seq // 20) % len(FRAMES)], 1280, 800)
        seq += 1
        await asyncio.sleep(1.0 / max(1.0, min(fps, FPS)))


def _cancel(slot) -> None:
    task = getattr(slot, "_cast_task", None)
    if task is not None:
        task.cancel()
        slot._cast_task = None


async def _start(self, fps, max_w):
    await _orig_start(self, fps, max_w)
    _cancel(self)
    self._cast_task = asyncio.get_running_loop().create_task(_cast(self, float(fps)))


async def _stop(self):
    _cancel(self)
    await _orig_stop(self)


async def _close(self):
    _cancel(self)
    await _orig_close(self)


testing.FakePool.__init__ = _pool_init
testing.FakeSlot.start_screencast = _start
testing.FakeSlot.stop_screencast = _stop
testing.FakeSlot.close = _close

from ghscraper.sidecar import main  # noqa: E402

if __name__ == "__main__":
    code = main(["--test-mode", *sys.argv[1:]])
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
