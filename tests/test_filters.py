"""Filter normalisation, query building, query splitting, config migration and CLI flags."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ghscraper.cli import apply_args, build_parser
from ghscraper.config import Config
from ghscraper.filters import (build_query, check_query, clean_filters, describe, normalize, normalize_date,
                               normalize_range, resolve_date, split_queries)

TODAY = date(2026, 10, 23)


class RangeTests(unittest.TestCase):
    def test_forms(self):
        for raw, want in [("100", "100"), ("100+", ">=100"), (">=100", ">=100"), ("< 50", "<50"),
                          ("10..50", "10..50"), ("10-50", "10..50"), ("1.5k", "1500"), ("2k+", ">=2000"),
                          ("10..*", "10..*"), ("*..10", "*..10")]:
            self.assertEqual(normalize_range(raw), want, raw)

    def test_rejects(self):
        for bad in ("abc", "50..10", "*..*", ">>5", ""):
            with self.assertRaises(ValueError, msg=bad):
                normalize_range(bad)


class DateTests(unittest.TestCase):
    def test_forms(self):
        for raw, want in [("2024-01-01", "2024-01-01"), ("<2024-01-01", "<2024-01-01"),
                          ("before 2024-01-01", "<2024-01-01"), ("after 30d", ">30d"),
                          ("since 6m", ">=6m"), ("within 1y", ">=1y"), ("within the last 2w", ">=2w"),
                          ("between 2024-01-01 and 2024-06-30", "2024-01-01..2024-06-30"),
                          ("*..2024-01-01", "*..2024-01-01"), ("2024-01-01T10:00:00Z", "2024-01-01T10:00:00Z")]:
            self.assertEqual(normalize_date(raw), want, raw)

    def test_rejects(self):
        for bad in ("notadate", "2024-13-01", "2024-02-30", "before", ">2024-01-01..2024-02-01", "*..*"):
            with self.assertRaises(ValueError, msg=bad):
                normalize_date(bad)

    def test_relative_resolution(self):
        self.assertEqual(resolve_date(">=30d", TODAY), ">=2026-09-23")
        self.assertEqual(resolve_date("<6m", TODAY), "<2026-04-23")
        self.assertEqual(resolve_date(">=1y", TODAY), ">=2025-10-23")
        self.assertEqual(resolve_date("2w..today", TODAY), "2026-10-09..2026-10-23")
        self.assertEqual(resolve_date(">=1m", date(2026, 3, 31)), ">=2026-02-28")  # month-end clamp


class BuildTests(unittest.TestCase):
    def test_full_query(self):
        f = clean_filters({
            "topic": "Web Scraping, python", "exclude_topic": ["deprecated"], "language": "Jupyter Notebook",
            "stars": "100+", "pushed": "within 30d", "archived": "false", "include_forks": "only",
            "match": "name,description", "owner": "psf", "sponsorable": True, "license": "MIT",
            "qualifiers": "topics:>3",
        })
        q = build_query("web scraper", f, TODAY)
        self.assertEqual(q, 'web scraper in:name,description user:psf language:"Jupyter Notebook" '
                            'topic:web-scraping topic:python -topic:deprecated license:mit stars:>=100 '
                            'pushed:>=2026-09-23 archived:false fork:only is:sponsorable topics:>3')

    def test_validation(self):
        for key, bad in [("topic", "c++"), ("owner", "bad name!"), ("match", "title"),
                         ("archived", "maybe"), ("include_forks", "yes")]:
            with self.assertRaises(ValueError, msg=key):
                normalize(key, bad)
        with self.assertRaises(ValueError):
            clean_filters({"stargazers": "5"})

    def test_describe(self):
        self.assertEqual(describe("stars", ">=100"), "≥ 100")
        self.assertEqual(describe("stars", "10..50"), "10 – 50")
        self.assertEqual(describe("created", "<2024-01-01"), "before 2024-01-01")
        self.assertEqual(describe("pushed", ">=30d", TODAY), "on/after 30d ago (2026-09-23)")

    def test_typo_warning(self):
        self.assertEqual(check_query("shop topic:ecommerce stars:>20 in:name,readme"), [])
        w = check_query("shop topc:ecommerce")
        self.assertEqual(len(w), 1)
        self.assertIn("did you mean 'topic:'", w[0])
        self.assertEqual(check_query("time 10:30 http://x.com"), [])  # not qualifiers


class SplitTests(unittest.TestCase):
    def test_commas_inside_qualifiers_survive(self):
        # Regression: 'in:name,description' used to be split into two queries.
        self.assertEqual(split_queries("laptop store in:name,description, tech store"),
                         ["laptop store in:name,description", "tech store"])
        self.assertEqual(split_queries('"creative agency, studio" awwwards; cli'),
                         ['"creative agency, studio" awwwards', "cli"])
        self.assertEqual(split_queries("a,b; c\n d , a"), ["a", "b", "c", "d"])


class ConfigTests(unittest.TestCase):
    def test_migrates_old_language_and_min_stars(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"queries": ["x"], "language": "python", "min_stars": 50}))
            cfg = Config.load(p)
        self.assertEqual(cfg.filters, {"language": ["python"], "stars": ">=50"})
        self.assertEqual(cfg.search_query("x"), "x language:python stars:>=50")

    def test_bad_saved_filter_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"filters": {"stars": "lots"}}))
            with self.assertRaises(ValueError):
                Config.load(p)


class FlagTests(unittest.TestCase):
    def _cfg(self, *argv, saved=None):
        args = build_parser().parse_args(list(argv))
        cfg = Config(filters=dict(saved or {}))
        apply_args(cfg, args, ["q"])
        return cfg

    def test_gh_style_flags(self):
        cfg = self._cfg("--topic", "nextjs,gsap", "--topic", "seo", "--stars", "100+", "--created",
                        "after 2025-01-01", "--updated", "within 6m", "--include-forks", "true",
                        "--license", "mit", "--sponsorable", "--qualifier", "topics:>2", "--sort", "stars")
        self.assertEqual(cfg.filters["topic"], ["nextjs", "gsap", "seo"])
        self.assertEqual(cfg.filters["created"], ">2025-01-01")
        self.assertEqual(cfg.filters["pushed"], ">=6m")
        self.assertEqual(cfg.filters["include_forks"], "true")
        self.assertTrue(cfg.filters["sponsorable"])
        self.assertEqual(cfg.sort, "stars")

    def test_saved_filters_merge_or_clear(self):
        saved = {"language": ["go"]}
        self.assertEqual(self._cfg("--stars", "5+", saved=saved).filters, {"language": ["go"], "stars": ">=5"})
        self.assertEqual(self._cfg("--clear-filters", saved=saved).filters, {})

    def test_bad_flag_value_names_the_flag(self):
        with self.assertRaisesRegex(ValueError, "--created"):
            self._cfg("--created", "last tuesday")


if __name__ == "__main__":
    unittest.main()
