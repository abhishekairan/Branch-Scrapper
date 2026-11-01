"""Offline tests for the taskbar-hiding logic (fake window lists; no real windows are touched)."""
import asyncio
import time
import unittest
from unittest import mock

from ghscraper.browser import taskbar
from ghscraper.browser.taskbar import (CHROME_WINDOW_CLASS, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW, TaskbarHider,
                                       WindowInfo, hidden_style, in_taskbar, select_windows)
from ghscraper.browser.zendriver_backend import ZendriverPool
from ghscraper.config import Config


def win(hwnd, pid=100, cls=CHROME_WINDOW_CLASS, visible=True, owner=False, ex=0x100):
    return WindowInfo(hwnd=hwnd, pid=pid, cls=cls, visible=visible, has_owner=owner, ex_style=ex)


class SelectionTests(unittest.TestCase):
    def test_visible_unowned_chrome_window_is_selected(self):
        self.assertEqual([w.hwnd for w in select_windows([win(1)], [100])], [1])

    def test_other_processes_are_never_selected(self):
        users_chrome = win(2, pid=999)
        self.assertEqual(select_windows([users_chrome, win(1)], [100]), [win(1)])
        self.assertEqual(select_windows([users_chrome], []), [])

    def test_class_filter(self):
        others = [win(3, cls="Chrome_RenderWidgetHostHWND"), win(4, cls="Notepad"), win(5, cls="")]
        self.assertEqual(select_windows(others, [100]), [])

    def test_invisible_owned_and_already_hidden_are_skipped(self):
        skip = [win(1, visible=False),
                win(2, owner=True),
                win(3, ex=0x100 | WS_EX_TOOLWINDOW)]
        self.assertEqual(select_windows(skip, [100]), [])

    def test_appwindow_style_overrides_owner_and_toolwindow(self):
        self.assertTrue(in_taskbar(win(1, owner=True, ex=WS_EX_APPWINDOW)))
        self.assertTrue(in_taskbar(win(1, ex=WS_EX_APPWINDOW | WS_EX_TOOLWINDOW)))
        self.assertFalse(in_taskbar(win(1, visible=False, ex=WS_EX_APPWINDOW)))

    def test_hidden_style_sets_tool_clears_app_and_keeps_the_rest(self):
        ex = 0x00000100 | WS_EX_APPWINDOW | 0x00080000
        out = hidden_style(ex)
        self.assertTrue(out & WS_EX_TOOLWINDOW)
        self.assertFalse(out & WS_EX_APPWINDOW)
        self.assertEqual(out & ~WS_EX_TOOLWINDOW, ex & ~WS_EX_APPWINDOW)
        self.assertFalse(in_taskbar(win(1, ex=out)))


class FakeApi:
    def __init__(self, windows):
        self.windows = list(windows)
        self.asked_pids = []
        self.fixed = []

    def enumerate(self, pids):
        self.asked_pids.append(pids)
        return [w for w in self.windows if w.pid in pids]

    def hide_from_taskbar(self, w):
        self.fixed.append(w.hwnd)
        self.windows = [x if x.hwnd != w.hwnd else win(w.hwnd, w.pid, ex=hidden_style(w.ex_style))
                        for x in self.windows]
        return True


class HiderTests(unittest.TestCase):
    def test_sweep_fixes_only_our_windows_once(self):
        api = FakeApi([win(1), win(2), win(3, pid=999), win(4, cls="Other")])
        h = TaskbarHider(lambda: [100, 0], api)
        self.assertEqual(h.sweep(), 2)
        self.assertEqual(sorted(api.fixed), [1, 2])      # never 3 (other pid) or 4 (other class)
        self.assertEqual(api.asked_pids, [frozenset({100})])
        self.assertEqual(h.sweep(), 0)                   # idempotent: already hidden
        api.windows.append(win(5))                       # a new worker window appears
        self.assertEqual(h.sweep(), 1)
        self.assertEqual(sorted(api.fixed), [1, 2, 5])

    def test_no_pids_means_no_enumeration(self):
        api = FakeApi([win(1)])
        self.assertEqual(TaskbarHider(lambda: [], api).sweep(), 0)
        self.assertEqual(api.asked_pids, [])

    def test_sweep_never_raises(self):
        def boom():
            raise RuntimeError("psutil exploded")
        self.assertEqual(TaskbarHider(boom, FakeApi([])).sweep(), 0)

    def test_thread_start_stop_and_kick(self):
        api = FakeApi([win(1)])
        h = TaskbarHider(lambda: [100], api)
        h.start()
        try:
            for _ in range(50):
                if api.fixed:
                    break
                time.sleep(0.05)
            self.assertEqual(api.fixed, [1])
        finally:
            h.stop()
        self.assertIsNone(h._thread)

    def test_noop_off_windows(self):
        h = TaskbarHider(lambda: [100])
        with mock.patch.object(taskbar.sys, "platform", "linux"):
            h.start()
        self.assertIsNone(h._thread)


class PoolWiringTests(unittest.TestCase):
    def test_option_controls_the_hider(self):
        self.assertTrue(Config().hide_taskbar_icons)
        on = ZendriverPool(Config(), print, asyncio.Event(), workers=1)
        off = ZendriverPool(Config(hide_taskbar_icons=False), print, asyncio.Event(), workers=1)
        self.assertIsNotNone(on._taskbar)
        self.assertIsNone(off._taskbar)
        off._hide_taskbar_icons()    # must be a harmless no-op

    def test_cli_flag(self):
        from ghscraper import cli
        ap = cli.build_parser()
        cfg = Config()
        cli.apply_args(cfg, ap.parse_args(["-q", "x"]), ["x"])
        self.assertTrue(cfg.hide_taskbar_icons)
        cli.apply_args(cfg, ap.parse_args(["-q", "x", "--show-taskbar-icons"]), ["x"])
        self.assertFalse(cfg.hide_taskbar_icons)


if __name__ == "__main__":
    unittest.main()
