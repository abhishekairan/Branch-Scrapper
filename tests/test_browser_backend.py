"""Offline tests for the browser layer's pure logic (no Chrome is launched)."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import websockets

from ghscraper.browser import procguard, stealth
from ghscraper.browser.backend import Cancelled
from ghscraper.browser.zendriver_backend import (BLOCK_PATTERNS, LAUNCH_ARGS, FrameThrottle, ZendriverPool,
                                                 _char_key_fields, build_input_command, is_crash_error,
                                                 normalize_proxy, plan_chromes)
from ghscraper.config import Config
from zendriver.core.connection import ProtocolException


def first_command(gen):
    return next(gen)  # a CDP command generator yields {"method": ..., "params": ...} first


class ProxyTests(unittest.TestCase):
    def test_normalize(self):
        self.assertIsNone(normalize_proxy(""))
        self.assertIsNone(normalize_proxy("   "))
        self.assertEqual(normalize_proxy("1.2.3.4:8080"), "http://1.2.3.4:8080")
        self.assertEqual(normalize_proxy(" user:pw@host:3128 "), "http://user:pw@host:3128")
        self.assertEqual(normalize_proxy("socks5://h:1080"), "socks5://h:1080")
        self.assertEqual(normalize_proxy("https://h:443"), "https://h:443")

    def test_per_worker_proxy_round_robin_feeds_normalizer(self):
        cfg = Config(proxy="p1:1, p2:2")
        self.assertEqual([normalize_proxy(cfg.proxy_for(i)) for i in range(3)],
                         ["http://p1:1", "http://p2:2", "http://p1:1"])


class PoolSizingTests(unittest.TestCase):
    def test_plan_chromes(self):
        self.assertEqual(plan_chromes(5, 3, False), [[0, 1, 2], [3, 4]])
        self.assertEqual(plan_chromes(2, 3, False), [[0, 1]])
        self.assertEqual(plan_chromes(3, 3, False), [[0, 1, 2]])
        self.assertEqual(plan_chromes(4, 2, False), [[0, 1], [2, 3]])
        self.assertEqual(plan_chromes(4, 1, False), [[0], [1], [2], [3]])
        self.assertEqual(plan_chromes(1, 3, False), [[0]])

    def test_isolate_means_one_chrome_per_worker(self):
        self.assertEqual(plan_chromes(3, 3, True), [[0], [1], [2]])

    def test_degenerate_values(self):
        self.assertEqual(plan_chromes(3, 0, False), [[0], [1], [2]])    # workers_per_chrome < 1 -> 1
        self.assertEqual(plan_chromes(0, 3, False), [[0]])

    def test_pool_groups_follow_config(self):
        stop = asyncio.Event()
        pool = ZendriverPool(Config(workers_per_chrome=2), print, stop, workers=5)
        self.assertEqual(pool.groups, [[0, 1], [2, 3], [4]])
        self.assertEqual(len(pool._chromes), 3)
        pool = ZendriverPool(Config(workers_per_chrome=3, isolate_workers=True), print, stop, workers=2)
        self.assertEqual(pool.groups, [[0], [1]])
        pool = ZendriverPool(Config(), print, stop, workers=2)       # defaults: 3 per Chrome
        self.assertEqual(pool.groups, [[0, 1]])
        self.assertEqual(pool.processes(), [])                        # nothing launched yet

    def test_launch_kwargs(self):
        stop = asyncio.Event()
        pool = ZendriverPool(Config(browser="msedge", browser_path="C:/x/msedge.exe", headless=True),
                             print, stop, workers=2)
        kw = pool._launch_kwargs(pool._chromes[0])
        self.assertEqual(kw["browser"], "msedge")
        self.assertEqual(kw["browser_executable_path"], "C:/x/msedge.exe")
        self.assertTrue(kw["headless"])
        self.assertEqual(kw["lang"], "en-US")
        self.assertEqual(kw["browser_args"], LAUNCH_ARGS)
        self.assertNotIn("user_data_dir", kw)
        self.assertIn("--window-position=-32000,-32000", LAUNCH_ARGS)

    def test_profile_per_chrome_and_proxy_for_profile_worker(self):
        stop = asyncio.Event()
        cfg = Config(user_data_dir="prof", workers_per_chrome=2, proxy="h1:1, h2:2, h3:3")
        pool = ZendriverPool(cfg, print, stop, workers=3)
        k0, k1 = (pool._launch_kwargs(c) for c in pool._chromes)
        self.assertEqual(k0["user_data_dir"], "prof")
        self.assertEqual(k1["user_data_dir"], "prof-2")
        self.assertIn("--proxy-server=http://h1:1", k0["browser_args"])   # worker 0 of Chrome 0
        self.assertIn("--proxy-server=http://h3:3", k1["browser_args"])   # worker 2 = first of Chrome 1
        auth = ZendriverPool(Config(user_data_dir="prof", proxy="u:p@h:1"), print, stop, workers=1)
        with self.assertRaises(ValueError):
            auth._launch_kwargs(auth._chromes[0])

    def test_acquire_rejects_unknown_worker(self):
        pool = ZendriverPool(Config(), print, asyncio.Event(), workers=2)
        with self.assertRaises(IndexError):
            asyncio.run(pool.acquire(5))

    def test_block_patterns_cover_images_fonts_media(self):
        for ext in ("png", "jpg", "woff2", "mp4"):
            self.assertIn(f"*.{ext}", BLOCK_PATTERNS)


class ConfigTests(unittest.TestCase):
    def test_new_fields_and_defaults(self):
        c = Config()
        self.assertEqual((c.workers_per_chrome, c.isolate_workers, c.block_resources, c.browser, c.browser_path),
                         (3, False, False, "chrome", ""))

    def test_old_config_with_unknown_keys_loads(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"queries": ["x"], "browsers": 4, "some_future_key": 1,
                                     "seleniumbase_thing": True}), encoding="utf-8")
            c = Config.load(p)
            self.assertEqual((c.queries, c.browsers, c.workers_per_chrome), (["x"], 4, 3))

    def test_new_fields_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            Config(workers_per_chrome=2, isolate_workers=True, block_resources=True, browser="brave").save(p)
            c = Config.load(p)
            self.assertEqual((c.workers_per_chrome, c.isolate_workers, c.block_resources, c.browser),
                             (2, True, True, "brave"))


class ScreencastThrottleTests(unittest.TestCase):
    @staticmethod
    def count(fps, timestamps):
        t = FrameThrottle(fps)
        return sum(t.allow(x) for x in timestamps)

    def test_first_frame_always_passes(self):
        self.assertTrue(FrameThrottle(1).allow(1000.0))

    def test_uncapped_100fps_stream_is_cut_to_target(self):
        stream = [i / 100 for i in range(1000)]               # 10 s at 100 fps (the measured paint rate)
        self.assertEqual(self.count(10, stream), 100)         # 10 fps over 10 s
        self.assertEqual(self.count(2, stream), 20)           # grid tile
        self.assertEqual(self.count(1, stream), 10)

    def test_slow_page_is_not_padded(self):
        stream = [i * 0.7 for i in range(10)]                 # page only repaints every 0.7 s
        self.assertEqual(self.count(2, stream), 10)           # nothing dropped: already below 2 fps

    def test_dropped_frames_do_not_move_the_clock(self):
        t = FrameThrottle(10)
        self.assertTrue(t.allow(0.0))
        self.assertFalse(t.allow(0.05))
        self.assertFalse(t.allow(0.09))
        self.assertTrue(t.allow(0.10))
        self.assertFalse(t.allow(0.15))
        self.assertTrue(t.allow(0.20))

    def test_nonpositive_fps_does_not_crash(self):
        self.assertTrue(FrameThrottle(0).allow(0.0))


class InputTests(unittest.TestCase):
    def test_mouse_click_sequence(self):
        down = first_command(build_input_command({"type": "mouse", "action": "down", "x": 10, "y": 20.5}))
        self.assertEqual(down["method"], "Input.dispatchMouseEvent")
        self.assertEqual(down["params"]["type"], "mousePressed")
        self.assertEqual((down["params"]["x"], down["params"]["y"]), (10.0, 20.5))
        self.assertEqual((down["params"]["button"], down["params"]["buttons"], down["params"]["clickCount"]),
                         ("left", 1, 1))
        up = first_command(build_input_command({"type": "mouse", "action": "up", "x": 1, "y": 2, "button": "right"}))
        self.assertEqual((up["params"]["type"], up["params"]["button"], up["params"]["buttons"]),
                         ("mouseReleased", "right", 0))
        move = first_command(build_input_command({"type": "mouse", "x": 5, "y": 6}))
        self.assertEqual(move["params"]["type"], "mouseMoved")

    def test_wheel(self):
        w = first_command(build_input_command({"type": "mouse", "action": "wheel", "x": 1, "y": 1, "delta_y": -120}))
        self.assertEqual((w["params"]["type"], w["params"]["deltaY"], w["params"]["deltaX"]),
                         ("mouseWheel", -120.0, 0.0))

    def test_key_and_text(self):
        k = first_command(build_input_command({"type": "key", "action": "down", "key": "a", "code": "KeyA",
                                               "text": "a", "vk": 65}))
        self.assertEqual(k["method"], "Input.dispatchKeyEvent")
        self.assertEqual((k["params"]["type"], k["params"]["key"], k["params"]["windowsVirtualKeyCode"]),
                         ("keyDown", "a", 65))
        bs = first_command(build_input_command({"type": "key", "action": "up", "key": "Backspace", "vk": 8}))
        self.assertEqual(bs["params"]["type"], "keyUp")
        self.assertNotIn("text", bs["params"])
        t = first_command(build_input_command({"type": "text", "text": "XYZ"}))
        self.assertEqual((t["method"], t["params"]["text"]), ("Input.insertText", "XYZ"))

    def test_invalid_events(self):
        for bad in ({"type": "touch"}, {"type": "mouse", "action": "teleport", "x": 0, "y": 0},
                    {"type": "key", "action": "smash"}, {"type": "mouse", "button": "laser", "x": 0, "y": 0}, {}):
            with self.assertRaises(ValueError):
                build_input_command(bad)

    def test_char_key_fields(self):
        self.assertEqual(_char_key_fields("a")["code"], "KeyA")
        self.assertEqual(_char_key_fields("A")["windows_virtual_key_code"], ord("A"))
        self.assertEqual(_char_key_fields("7")["code"], "Digit7")
        self.assertEqual(_char_key_fields(" ")["code"], "Space")
        self.assertEqual(_char_key_fields(":"), {"key": ":", "text": ":"})


class CrashClassificationTests(unittest.TestCase):
    def test_crash_errors(self):
        self.assertTrue(is_crash_error(ConnectionResetError()))
        self.assertTrue(is_crash_error(websockets.exceptions.ConnectionClosedError(None, None)))
        self.assertTrue(is_crash_error(ProtocolException("connection was closed")))
        self.assertTrue(is_crash_error(ProtocolException("{'code': -32001, 'message': 'Session with given id not found.'}")))
        self.assertTrue(is_crash_error(ProtocolException("target session was detached")))

    def test_ordinary_errors_are_not_crashes(self):
        self.assertFalse(is_crash_error(asyncio.TimeoutError()))
        self.assertFalse(is_crash_error(ValueError("bad")))
        self.assertFalse(is_crash_error(ProtocolException("Cannot find context with specified id")))


class StealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_sleeper_runs_and_cancels(self):
        stop = asyncio.Event()
        sleep = stealth.make_sleeper(stop)
        await sleep(0.01)
        await sleep(0)
        asyncio.get_running_loop().call_later(0.05, stop.set)
        with self.assertRaises(Cancelled):
            await sleep(5)                       # woken by stop long before 5 s
        with self.assertRaises(Cancelled):
            await sleep(1)                       # already stopped

    async def test_pause_respects_delay_range(self):
        waited = []

        async def fake_sleep(s):
            waited.append(s)
        cfg = Config(min_delay=2, max_delay=5)
        for _ in range(20):
            await stealth.pause(cfg, fake_sleep, 0.5)
        self.assertTrue(all(1.0 <= w <= 2.5 for w in waited))

    async def test_human_type_sends_every_char_with_jitter(self):
        sent, waits = [], []

        async def send(ch):
            sent.append(ch)

        async def sleep(s):
            waits.append(s)
        await stealth.human_type(send, "a b", 0.12, sleep)
        self.assertEqual(sent, ["a", " ", "b"])
        self.assertTrue(all(0.02 <= w <= 0.48 for w in waits))

    async def test_human_scroll_swallows_page_errors_but_not_cancel(self):
        async def boom(js):
            raise RuntimeError("page gone")

        async def sleep(s):
            pass
        await stealth.human_scroll(boom, sleep)

        async def cancel(s):
            raise Cancelled
        calls = []

        async def ok(js):
            calls.append(js)
        with self.assertRaises(Cancelled):
            await stealth.human_scroll(ok, cancel)
        self.assertTrue(calls[0].startswith("window.scrollBy(0, "))

    async def test_shared_cooldown_across_runs(self):
        cd = stealth.SharedCooldown()
        self.assertLessEqual(await cd.remaining("p"), 0)
        await cd.extend("p", 30)
        await cd.extend("p", 5)                  # never shortens
        self.assertGreater(await cd.remaining("p"), 20)
        self.assertLessEqual(await cd.remaining("other"), 0)

    def test_shared_cooldown_survives_new_event_loops(self):
        cd = stealth.SharedCooldown()

        async def hit():
            await asyncio.gather(*(cd.extend("p", 10) for _ in range(5)))
            return await cd.remaining("p")
        self.assertGreater(asyncio.run(hit()), 5)
        self.assertGreater(asyncio.run(hit()), 5)   # a second loop must not trip over the old lock

    def test_backoff_grows_and_caps(self):
        self.assertTrue(20 <= stealth.backoff_seconds(1) <= 30)
        self.assertTrue(40 <= stealth.backoff_seconds(2) <= 50)
        self.assertTrue(300 <= stealth.backoff_seconds(9) <= 310)


class ProcGuardTests(unittest.TestCase):
    def setUp(self):
        self._saved = (procguard._guarded, procguard._job_handle)
        procguard._guarded, procguard._job_handle = False, None

    def tearDown(self):
        procguard._guarded, procguard._job_handle = self._saved

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object")
    def test_guard_is_idempotent(self):
        with mock.patch.object(procguard, "_windows_assign_self_to_job", return_value=object()) as m:
            self.assertTrue(procguard.guard_current_process())
            self.assertTrue(procguard.guard_current_process())
            self.assertEqual(m.call_count, 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object")
    def test_guard_survives_job_that_forbids_nesting(self):
        with mock.patch.object(procguard, "_windows_assign_self_to_job", side_effect=PermissionError("denied")):
            with self.assertLogs("ghscraper.procguard", level="WARNING"):
                self.assertFalse(procguard.guard_current_process())
            self.assertFalse(procguard.guard_current_process())   # no retry storm, still no exception

    def test_snapshot_and_kill_ignore_missing_and_reused_pids(self):
        self.assertEqual(procguard.snapshot([None, 0]), [])
        self.assertEqual(procguard.kill_snapshot([(2_000_000_000, 1.0)]), 0)
        import os
        me = procguard.snapshot([os.getpid()])
        self.assertTrue(me and me[0][0] == os.getpid())
        # wrong create_time = a different (reused) process: must not be killed
        self.assertEqual(procguard.kill_snapshot([(os.getpid(), me[0][1] + 1000)]), 0)

    def test_kill_pids_skips_self_and_bad_pids(self):
        import os
        self.assertEqual(procguard.kill_pids([os.getpid(), 0, 2_000_000_000]), 0)


if __name__ == "__main__":
    unittest.main()
