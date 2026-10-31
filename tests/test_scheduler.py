"""Scheduler tests against a fake BrowserPool / BrowserSlot (async, offline, no Chrome)."""
import asyncio
import signal
import threading
import unittest
from collections import Counter
from pathlib import Path

from ghscraper.browser import Cancelled, SlotCrashed
from ghscraper.config import MAX_BROWSERS, Config, parse_queries
from ghscraper.core.scheduler import MAX_REQUEUES, Scheduler, _is_target_search, search_url
from ghscraper.parsers import parse_search_page
from ghscraper.scraper import GitHubScraper

FIX = Path(__file__).parent / "fixtures"
SEARCH_HTML = (FIX / "search.html").read_text(encoding="utf-8")
REPO_HTML = (FIX / "repo.html").read_text(encoding="utf-8")


class FakeSlot:
    calls: Counter = Counter()      # worker index -> pages opened
    searches: Counter = Counter()   # worker index -> searches run
    urls: Counter = Counter()       # url -> times opened
    delay = 0.02                    # lets other workers interleave
    hook = None                     # hook(slot, url) may raise SlotCrashed etc.

    def __init__(self, index):
        self.index = index
        self.id = f"B{index + 1}"
        self.cur = ""
        self.closed = False
        self._html = ""

    async def open(self, url):
        await asyncio.sleep(FakeSlot.delay)
        FakeSlot.calls[self.index] += 1
        FakeSlot.urls[url] += 1
        if "/search?" in url:
            FakeSlot.searches[self.index] += 1
        if FakeSlot.hook:
            FakeSlot.hook(self, url)
        self.cur = url
        self._html = SEARCH_HTML if "/search?" in url else REPO_HTML
        return self._html

    async def html(self):
        return self._html

    async def url(self):
        return self.cur

    async def wait_for_search(self, timeout=10):
        return True

    async def type_search(self, query):
        return False

    async def pause(self, scale=1.0):
        await asyncio.sleep(0.01)

    async def start_screencast(self, fps, max_w): ...
    async def stop_screencast(self): ...
    async def dispatch_input(self, event): ...

    async def close(self):
        self.closed = True


class FakePool:
    def __init__(self, cfg, log, stop, workers):
        self.workers = workers
        self.on_frame = None
        self.started = self.closed = False
        self.slots = []
        self.recycled = []
        self.recycle_error = None

    async def start(self):
        self.started = True

    async def acquire(self, worker_index):
        slot = FakeSlot(worker_index)
        self.slots.append(slot)
        return slot

    async def recycle(self, slot):
        self.recycled.append(slot.index)
        if self.recycle_error and slot.index in self.recycle_error:
            raise RuntimeError("cannot relaunch")

    async def close(self):
        self.closed = True

    def processes(self):
        return []


class Base(unittest.TestCase):
    def setUp(self):
        FakeSlot.calls.clear()
        FakeSlot.searches.clear()
        FakeSlot.urls.clear()
        FakeSlot.delay = 0.02
        FakeSlot.hook = None
        self.pools: list[FakePool] = []

    def tearDown(self):
        FakeSlot.hook = None

    def factory(self, cfg, log, stop, workers):
        pool = FakePool(cfg, log, stop, workers)
        self.pools.append(pool)
        return pool

    def _cfg(self, **kw):
        base = dict(queries=["a", "b", "c", "d"], max_results=10, browsers=3,
                    human_search=False, fetch_manifests=False)
        base.update(kw)
        return Config(**base)

    def scraper(self, cfg=None, **kw):
        return GitHubScraper(cfg or self._cfg(), log=lambda m: None, pool_factory=self.factory, **kw)


class SchedulerTests(Base):
    def test_spreads_work_and_dedupes(self):
        # Every query returns the same 10 repos from the fixture.
        results = self.scraper().run()
        self.assertEqual(len(results), 10)                       # deduped across queries
        for r in results:
            self.assertEqual(sorted(r["matched_queries"]), ["a", "b", "c", "d"])
            self.assertIn("tech_stack", r)
        self.assertEqual(sum(FakeSlot.searches.values()), 4)     # each query searched once
        self.assertGreater(len(FakeSlot.calls), 1)               # more than one browser did work
        self.assertEqual(sum(FakeSlot.calls.values()), 4 + 10)
        pool = self.pools[0]
        self.assertTrue(pool.started and pool.closed)
        self.assertTrue(all(s.closed for s in pool.slots))

    def test_single_query_still_uses_all_browsers_for_details(self):
        self.scraper(self._cfg(queries=["only"], browsers=3)).run()
        self.assertEqual(len(FakeSlot.calls), 3)

    def test_no_deep_caps_workers_to_queries(self):
        cfg = self._cfg(queries=["x", "y"], browsers=5, deep_scrape=False)
        self.assertEqual(cfg.worker_count(), 2)
        results = self.scraper(cfg).run()
        self.assertEqual(len(results), 10)
        self.assertEqual(sum(FakeSlot.calls.values()), 2)
        self.assertEqual(self.pools[0].workers, 2)

    def test_max_results_is_per_query(self):
        cfg = self._cfg(queries=["a", "b"], max_results=4)
        s = self.scraper(cfg)
        results = s.run()
        self.assertEqual(len(results), 4)  # both queries get the same first 4 -> deduped
        self.assertTrue(all(sorted(r["matched_queries"]) == ["a", "b"] for r in results))

    def test_count_only_reads_totals_and_scrapes_nothing(self):
        cfg = self._cfg(queries=["a", "b", "c"], browsers=5)
        s = self.scraper(cfg, count_only=True)
        results = s.run()
        self.assertEqual(results, [])
        self.assertEqual(set(s.totals), {"a", "b", "c"})
        self.assertTrue(all(isinstance(t, int) and t > 0 for t in s.totals.values()))
        self.assertEqual(sum(FakeSlot.calls.values()), 3)          # one search page per query
        self.assertEqual(self.pools[0].workers, 3)                 # capped to the query count

    def test_callbacks_fire(self):
        statuses, listings, repos = [], [], []
        s = self.scraper(self._cfg(queries=["a"], browsers=2),
                         on_status=lambda w, t: statuses.append((w, t)),
                         on_listing=lambda q, f, t: listings.append((q, f, t)),
                         on_repo=lambda r, d, t: repos.append((d, t)))
        s.run()
        self.assertEqual(listings[-1], ("a", 10, 10))
        self.assertEqual(repos[-1], (10, 10))
        self.assertEqual(len(repos), 10)
        self.assertTrue(any("searching" in t for _, t in statuses))
        self.assertTrue(any("done" in t for _, t in statuses))

    def test_async_callbacks_are_awaited(self):
        seen = []

        async def on_repo(r, d, t):
            await asyncio.sleep(0)
            seen.append(d)

        self.scraper(self._cfg(queries=["a"]), on_repo=on_repo).run()
        self.assertEqual(sorted(seen), list(range(1, 11)))

    def test_no_queries_raises(self):
        with self.assertRaises(ValueError):
            self.scraper(self._cfg(queries=[])).run()

    def test_search_url_and_target_check(self):
        cfg = self._cfg(sort="stars", order="desc")
        url = search_url(cfg, "web scraper", 2)
        self.assertIn("s=stars", url)
        self.assertIn("p=2", url)
        self.assertTrue(_is_target_search(url, cfg, "web scraper"))
        self.assertFalse(_is_target_search(search_url(self._cfg(), "web scraper"), cfg, "web scraper"))

    def test_config_helpers(self):
        self.assertEqual(Config(browsers=99).worker_count(), MAX_BROWSERS)
        self.assertEqual(parse_queries("web scraper, cli tool; web scraper\n rust "),
                         ["web scraper", "cli tool", "rust"])
        cfg = Config(proxy="p1:1, p2:2", user_data_dir="prof")
        self.assertEqual([cfg.proxy_for(i) for i in range(3)], ["p1:1", "p2:2", "p1:1"])
        self.assertEqual([cfg.profile_for(i) for i in range(3)], ["prof", "prof-2", "prof-3"])


class CrashTests(Base):
    TARGET = "https://github.com/"  # prefix of repo urls in the fixture

    def _first_repo_url(self):
        return parse_search_page(SEARCH_HTML, expected_page=1)["results"][3]["url"]

    def test_requeues_task_after_slot_crash(self):
        url = self._first_repo_url()
        FakeSlot.calls.clear(); FakeSlot.urls.clear(); FakeSlot.searches.clear()
        state = {"n": 0}

        def hook(slot, u):
            if u == url and state["n"] == 0:
                state["n"] += 1
                raise SlotCrashed("tab died")
        FakeSlot.hook = hook
        results = self.scraper(self._cfg(queries=["a"], browsers=2)).run()
        self.assertEqual(len(results), 10)
        self.assertTrue(all("error" not in r for r in results))
        self.assertEqual(FakeSlot.urls[url], 2)             # crashed once, then succeeded
        self.assertEqual(len(self.pools[-1].recycled), 1)   # the crashed slot was recycled

    def test_gives_up_after_max_requeues(self):
        url = self._first_repo_url()
        FakeSlot.calls.clear(); FakeSlot.urls.clear(); FakeSlot.searches.clear()

        def hook(slot, u):
            if u == url:
                raise SlotCrashed("always dies")
        FakeSlot.hook = hook
        results = self.scraper(self._cfg(queries=["a"], browsers=2)).run()
        self.assertEqual(len(results), 10)                  # failed repo is kept, flagged
        bad = [r for r in results if r.get("error")]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["url"], url)
        self.assertEqual(FakeSlot.urls[url], 1 + MAX_REQUEUES)
        self.assertEqual(MAX_REQUEUES, 2)
        self.assertEqual(len(self.pools[-1].recycled), 1 + MAX_REQUEUES)

    def test_search_task_is_requeued_too(self):
        state = {"n": 0}

        def hook(slot, u):
            if "/search?" in u and state["n"] == 0:
                state["n"] += 1
                raise SlotCrashed("died on search")
        FakeSlot.hook = hook
        results = self.scraper(self._cfg(queries=["a"], browsers=2)).run()
        self.assertEqual(len(results), 10)

    def test_worker_that_cannot_recover_exits_others_finish(self):
        state = {"n": 0}

        def hook(slot, u):
            if slot.index == 0 and state["n"] == 0:
                state["n"] += 1
                raise SlotCrashed("died")
        FakeSlot.hook = hook

        def factory(cfg, log, stop, workers):
            pool = self.factory(cfg, log, stop, workers)
            pool.recycle_error = {0}
            return pool
        s = GitHubScraper(self._cfg(queries=["a"], browsers=2), log=lambda m: None, pool_factory=factory)
        results = s.run()
        self.assertEqual(len(results), 10)  # worker 2 picked up the requeued task

    def test_pool_start_failure_leaves_tasks_undone(self):
        logs = []

        class DeadPool(FakePool):
            async def acquire(self, worker_index):
                raise RuntimeError("chrome not installed")
        s = GitHubScraper(self._cfg(queries=["a"]), log=logs.append,
                          pool_factory=lambda c, l, st, w: DeadPool(c, l, st, w))
        self.assertEqual(s.run(), [])
        self.assertTrue(any("failed to start" in m for m in logs))
        self.assertTrue(any("left undone" in m for m in logs))


class ControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeSlot.calls.clear(); FakeSlot.searches.clear(); FakeSlot.urls.clear()
        FakeSlot.hook = None
        FakeSlot.delay = 0.05
        self.pool = None

    def tearDown(self):
        FakeSlot.delay = 0.02

    def sched(self, cfg=None, statuses=None):
        cfg = cfg or Config(queries=["a", "b"], max_results=10, browsers=2, human_search=False,
                            fetch_manifests=False)

        def factory(c, l, stop, w):
            self.pool = FakePool(c, l, stop, w)
            return self.pool
        return Scheduler(cfg, log=lambda m: None, pool_factory=factory,
                         on_status=(lambda w, t: statuses.append((w, t))) if statuses is not None else None)

    async def test_pause_before_start_and_resume(self):
        statuses = []
        s = self.sched(statuses=statuses)
        s.pause_worker(0)
        self.assertTrue(s.is_paused(0))
        task = asyncio.create_task(s.run())
        for _ in range(300):                                    # worker 1 alone does all the work
            if len(s.results) == 10:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        self.assertEqual(FakeSlot.calls[0], 0)                  # paused worker took nothing
        self.assertGreater(FakeSlot.calls[1], 0)
        self.assertEqual(len(s.results), 10)
        self.assertFalse(task.done())                           # run stays open while a worker is paused
        self.assertTrue(any(w == 0 and "paused" in t for w, t in statuses))
        s.resume_worker(0)
        results = await asyncio.wait_for(task, 5)
        self.assertEqual(len(results), 10)

    async def test_pause_mid_run_finishes_current_page_then_waits(self):
        s = self.sched()
        task = asyncio.create_task(s.run())
        while FakeSlot.calls[0] < 1:
            await asyncio.sleep(0.01)
        s.pause_worker(0)
        await asyncio.sleep(0.4)                                # let its in-flight task finish
        frozen = FakeSlot.calls[0]
        await asyncio.sleep(0.5)
        self.assertEqual(FakeSlot.calls[0], frozen)             # nothing new while paused
        self.assertTrue(s.is_paused(0))
        s.resume_worker(0)
        results = await asyncio.wait_for(task, 10)
        self.assertEqual(len(results), 10)

    async def test_stop_mid_run_keeps_partial_results(self):
        s = self.sched(Config(queries=["a"], max_results=10, browsers=2, human_search=False,
                              fetch_manifests=False))
        task = asyncio.create_task(s.run())
        while len(s.results) < 3:
            await asyncio.sleep(0.01)
        s.stop()
        results = await asyncio.wait_for(task, 5)
        self.assertGreaterEqual(len(results), 3)
        self.assertLess(len(results), 10)
        self.assertIs(results, s.results)
        self.assertTrue(self.pool.closed)
        self.assertTrue(all(sl.closed for sl in self.pool.slots))

    async def test_stop_releases_paused_workers(self):
        s = self.sched()
        s.pause_worker(0)
        s.pause_worker(1)
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.2)
        s.stop()
        await asyncio.wait_for(task, 5)
        self.assertTrue(self.pool.closed)

    async def test_stop_before_run_does_nothing_but_close(self):
        s = self.sched()
        s.stop()
        results = await asyncio.wait_for(s.run(), 5)
        self.assertEqual(results, [])
        self.assertTrue(self.pool.closed)

    async def test_cancelled_run_closes_pool(self):
        s = self.sched()
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.pool.closed)


class FacadeTests(Base):
    def test_shutdown_from_another_thread_keeps_partial_results(self):
        FakeSlot.delay = 0.1
        s = self.scraper(self._cfg(queries=["a"], browsers=2))
        timer = threading.Timer(1.0, s.shutdown)
        timer.start()
        results = s.run()
        timer.join()
        self.assertIs(results, s.results)
        self.assertLess(len(results), 10)
        self.assertTrue(self.pools[0].closed)

    def test_shutdown_before_run_is_harmless(self):
        s = self.scraper()
        s.shutdown()
        self.assertEqual(s.run(), [])

    def test_keyboard_interrupt_stops_browsers_and_keeps_results(self):
        FakeSlot.delay = 0.1
        s = self.scraper(self._cfg(queries=["a"], browsers=2))
        timer = threading.Timer(1.2, lambda: signal.raise_signal(signal.SIGINT))
        timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                s.run()
        finally:
            timer.cancel()
        self.assertTrue(self.pools[0].closed)
        self.assertTrue(all(sl.closed for sl in self.pools[0].slots))
        self.assertGreater(len(s.results), 0)
        self.assertLess(len(s.results), 10)


if __name__ == "__main__":
    unittest.main()
