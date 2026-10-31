"""Multi-browser scheduling tests with a fake browser (no network, no Chrome)."""
import threading
import time
import unittest
from collections import Counter
from pathlib import Path

from ghscraper import scraper as scraper_mod
from ghscraper.config import MAX_BROWSERS, Config, parse_queries

FIX = Path(__file__).parent / "fixtures"
SEARCH_HTML = (FIX / "search.html").read_text(encoding="utf-8")
REPO_HTML = (FIX / "repo.html").read_text(encoding="utf-8")


class FakeBrowser:
    calls: Counter = Counter()      # worker index -> pages opened
    searches: Counter = Counter()   # worker index -> searches run
    lock = threading.Lock()

    def __init__(self, cfg, log, index=0, stop=None):
        self.index = index
        self.url = ""

    def start(self):
        pass

    def close(self):
        pass

    def pause(self, scale=1.0):
        time.sleep(0.01)

    def open(self, url):
        time.sleep(0.02)  # let other workers interleave
        with self.lock:
            FakeBrowser.calls[self.index] += 1
            if "/search?" in url:
                FakeBrowser.searches[self.index] += 1
        self.url = url
        self.html = SEARCH_HTML if "/search?" in url else REPO_HTML
        return self.html

    def wait_for_search(self, timeout=10):
        return True


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        FakeBrowser.calls.clear()
        FakeBrowser.searches.clear()
        self._orig = scraper_mod.StealthBrowser
        scraper_mod.StealthBrowser = FakeBrowser

    def tearDown(self):
        scraper_mod.StealthBrowser = self._orig

    def _cfg(self, **kw):
        base = dict(queries=["a", "b", "c", "d"], max_results=10, browsers=3,
                    human_search=False, fetch_manifests=False)
        base.update(kw)
        return Config(**base)

    def test_spreads_work_and_dedupes(self):
        # Every query returns the same 10 repos from the fixture.
        results = scraper_mod.GitHubScraper(self._cfg()).run()
        self.assertEqual(len(results), 10)                       # deduped across queries
        for r in results:
            self.assertEqual(sorted(r["matched_queries"]), ["a", "b", "c", "d"])
            self.assertIn("tech_stack", r)
        self.assertEqual(sum(FakeBrowser.searches.values()), 4)  # each query searched once
        self.assertGreater(len(FakeBrowser.calls), 1)            # more than one browser did work
        self.assertEqual(sum(FakeBrowser.calls.values()), 4 + 10)

    def test_single_query_still_uses_all_browsers_for_details(self):
        scraper_mod.GitHubScraper(self._cfg(queries=["only"], browsers=3)).run()
        self.assertEqual(len(FakeBrowser.calls), 3)

    def test_no_deep_caps_workers_to_queries(self):
        cfg = self._cfg(queries=["x", "y"], browsers=5, deep_scrape=False)
        self.assertEqual(cfg.worker_count(), 2)
        results = scraper_mod.GitHubScraper(cfg).run()
        self.assertEqual(len(results), 10)
        self.assertEqual(sum(FakeBrowser.calls.values()), 2)

    def test_config_helpers(self):
        self.assertEqual(Config(browsers=99).worker_count(), MAX_BROWSERS)
        self.assertEqual(parse_queries("web scraper, cli tool; web scraper\n rust "),
                         ["web scraper", "cli tool", "rust"])
        cfg = Config(proxy="p1:1, p2:2", user_data_dir="prof")
        self.assertEqual([cfg.proxy_for(i) for i in range(3)], ["p1:1", "p2:2", "p1:1"])
        self.assertEqual([cfg.profile_for(i) for i in range(3)], ["prof", "prof-2", "prof-3"])


if __name__ == "__main__":
    unittest.main()
