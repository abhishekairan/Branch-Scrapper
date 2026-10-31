import unittest
from unittest import mock

import psutil

from ghscraper.core.runmanager import measure_processes


class FakeProc:
    def __init__(self, pid, ppid, ppid_error=False):
        self.pid, self._ppid, self._err = pid, ppid, ppid_error

    def ppid(self):
        if self._err:
            raise psutil.NoSuchProcess(self.pid)
        return self._ppid

    def memory_info(self):
        return mock.Mock(rss=100)

    def memory_full_info(self):
        return mock.Mock(uss=60)


def run(table, pids, roots=None):
    def factory(pid):
        if pid not in table:
            raise psutil.NoSuchProcess(pid)
        return table[pid]
    with mock.patch("psutil.Process", side_effect=factory):
        return measure_processes(pids, roots)


class MeasureProcessesTests(unittest.TestCase):
    def test_one_tree(self):
        table = {1: FakeProc(1, 99), 2: FakeProc(2, 1), 3: FakeProc(3, 2)}
        out = run(table, [1, 2, 3], roots=[1])
        self.assertEqual([(t["pid"], t["processes"]) for t in out], [(1, 3)])

    def test_child_exiting_between_listing_and_measuring_is_not_a_chrome(self):
        # the observed bug: a renderer exits after psutil.Process() but before ppid()
        table = {1: FakeProc(1, 99), 2: FakeProc(2, 1), 3: FakeProc(3, 1, ppid_error=True)}
        for roots in (None, [1]):
            out = run(table, [1, 2, 3], roots)
            self.assertEqual([(t["pid"], t["processes"]) for t in out], [(1, 2)])

    def test_orphan_not_under_a_known_root_is_ignored(self):
        table = {1: FakeProc(1, 99), 2: FakeProc(2, 1), 7: FakeProc(7, 500)}
        out = run(table, [1, 2, 7], roots=[1])
        self.assertEqual([(t["pid"], t["processes"]) for t in out], [(1, 2)])

    def test_two_real_chromes(self):
        table = {1: FakeProc(1, 99), 2: FakeProc(2, 1), 10: FakeProc(10, 99), 11: FakeProc(11, 10)}
        out = run(table, [1, 2, 10, 11], roots=[1, 10])
        self.assertEqual(sorted((t["pid"], t["processes"]) for t in out), [(1, 2), (10, 2)])


if __name__ == "__main__":
    unittest.main()
