"""Offline parser tests against saved GitHub pages (tests/fixtures).

Re-capture the fixtures when GitHub changes its markup and these start failing.
Run: .venv\\Scripts\\python.exe -m unittest discover tests
"""
import unittest
from pathlib import Path

from ghscraper.parsers import detect_block, parse_count, parse_repo_page, parse_search_page, strip_highlight
from ghscraper.techstack import detect_from_files

FIX = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.search_html = (FIX / "search.html").read_text(encoding="utf-8")
        cls.repo_html = (FIX / "repo.html").read_text(encoding="utf-8")

    def test_helpers(self):
        self.assertEqual(parse_count("6,495 Commits"), 6495)
        self.assertEqual(parse_count("1.1k"), 1100)
        self.assertEqual(strip_highlight("mnmldave/<em>scraper</em> &lt;x&gt;"), "mnmldave/scraper <x>")

    def test_search_json(self):
        res = parse_search_page(self.search_html, expected_page=1)
        self.assertEqual(res["source"], "json")
        self.assertEqual(len(res["results"]), 10)
        self.assertTrue(res["has_next"])
        r = res["results"][0]
        self.assertRegex(r["url"], r"^https://github\.com/[^/]+/[^/]+$")
        self.assertIsInstance(r["stars"], int)
        self.assertNotIn("<em>", r["description"])

    def test_search_dom_fallback_agrees_with_json(self):
        json_res = parse_search_page(self.search_html, expected_page=1)
        dom_res = parse_search_page(self.search_html, expected_page=99)  # forces DOM path
        self.assertEqual(dom_res["source"], "dom")
        self.assertEqual([r["full_name"] for r in dom_res["results"]],
                         [r["full_name"] for r in json_res["results"]])
        self.assertEqual(dom_res["results"][0]["language"], json_res["results"][0]["language"])

    def test_repo_page(self):
        d = parse_repo_page(self.repo_html, "vercel/next.js")
        self.assertTrue(d["description"])
        self.assertTrue(d["website"].startswith("http"))
        for key in ("stars", "forks", "watchers", "branches", "tags", "commits",
                    "contributors", "releases", "open_issues", "open_pull_requests"):
            self.assertIsInstance(d[key], int, key)
        self.assertEqual(d["default_branch"], "canary")
        self.assertIn("TypeScript", d["languages"])
        self.assertIn("package.json", d["root_files"])
        self.assertTrue(d["topics"])

    def test_no_false_block(self):
        self.assertIsNone(detect_block(self.repo_html, "GitHub - vercel/next.js"))
        self.assertIsNotNone(detect_block("<html><h1>Whoa there!</h1></html>", "GitHub"))

    def test_techstack_files(self):
        stack = detect_from_files(["package.json", "next.config.js", "Dockerfile", "src/app", "pnpm-lock.yaml"])
        for t in ("Node.js", "Next.js", "Docker", "pnpm"):
            self.assertIn(t, stack)


if __name__ == "__main__":
    unittest.main()
