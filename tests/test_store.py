import csv
import glob
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from ghscraper import exporters
from ghscraper.store import Store
from ghscraper.store import importer, query


def rec(name, **kw):
    r = {
        "full_name": name, "url": f"https://github.com/{name}", "description": f"desc of {name}",
        "stars": 10, "forks": 1, "watchers": 1, "language": "Python",
        "languages": {"Python": 80.0, "Shell": 20.0}, "topics": ["scraper", "cli"],
        "tech_stack": ["Python", "Docker"], "license": "MIT", "created_at": "2024-06-15T10:00:00.000Z",
        "last_commit_at": "2025-01-10T10:00:00.000Z", "archived": False, "is_fork": False,
        "is_template": False, "commits": 50, "contributors": 2, "open_issues": 3,
        "open_pull_requests": 0, "releases": 0, "query": "web scraper", "matched_queries": ["web scraper"],
    }
    r.update(kw)
    return r


def names(res):
    return sorted(r["full_name"] for r in res["rows"])


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")
        self.run = self.s.start_run({"q": 1})
        self.addCleanup(self.s.close)


class TestUpsert(StoreTestBase):
    def test_roundtrip_types(self):
        self.s.upsert_repo(rec("a/one"), self.run)
        r = self.s.get_repo("a/one")
        self.assertIs(r["archived"], False)
        self.assertEqual(r["languages"], {"Python": 80.0, "Shell": 20.0})
        self.assertEqual(r["topics"], ["scraper", "cli"])
        self.assertEqual(r["stars"], 10)
        self.assertEqual(self.s.get_repo("A/ONE")["full_name"], "a/one")  # case-insensitive
        self.assertIsNone(self.s.get_repo("nope/nope"))

    def test_latest_wins_and_side_tables_replaced(self):
        self.s.upsert_repo(rec("a/one", topics=["old"], tech_stack=["Perl"], languages={"Perl": 100}), self.run)
        run2 = self.s.start_run({})
        self.s.upsert_repo(rec("a/one", stars=99, topics=["new"], tech_stack=["Go"],
                               languages={"Go": 100}, description="fresh words",
                               query="other", matched_queries=["other"]), run2)
        self.assertEqual(self.s.count_repos(), 1)
        r = self.s.get_repo("a/one")
        self.assertEqual(r["stars"], 99)
        self.assertEqual(r["topics"], ["new"])
        self.assertEqual(self.s.read("SELECT topic FROM repo_topics")[0][0], "new")
        self.assertEqual(len(self.s.read("SELECT * FROM repo_topics")), 1)
        self.assertEqual(len(self.s.read("SELECT * FROM repo_tech")), 1)
        self.assertEqual(len(self.s.read("SELECT * FROM repo_languages")), 1)
        # provenance accumulates
        self.assertEqual(r["matched_queries"], ["web scraper", "other"])
        self.assertEqual(len(self.s.read("SELECT * FROM repo_runs")), 2)
        # FTS reflects the new text only
        self.assertEqual(query.search(self.s, {"text": "fresh"})["total"], 1)
        self.assertEqual(query.search(self.s, {"text": "old"})["total"], 0)
        self.assertEqual(len(self.s.read("SELECT rowid FROM repos_fts")), 1)

    def test_delete_cleans_everything(self):
        self.s.upsert_repo(rec("a/one"), self.run)
        self.assertTrue(self.s.delete_repo("a/one"))
        self.assertFalse(self.s.delete_repo("a/one"))
        for t in ("repos", "repo_topics", "repo_tech", "repo_languages", "repo_runs", "repos_fts"):
            self.assertEqual(self.s.read(f"SELECT COUNT(*) FROM {t}")[0][0], 0, t)

    def test_upsert_many_atomic(self):
        with self.assertRaises(ValueError):
            self.s.upsert_many([rec("a/one"), {"no": "name"}], self.run)
        self.assertEqual(self.s.count_repos(), 0)
        self.assertEqual(self.s.upsert_many([rec("a/one"), rec("a/two")], self.run), 2)

    def test_run_lifecycle(self):
        self.s.finish_run(self.run, "done", {"repos": 3})
        r = self.s.list_runs()[0]
        self.assertEqual((r["status"], r["counts"], r["config"]), ("done", {"repos": 3}, {"q": 1}))
        self.assertIsNotNone(r["finished_at"])

    def test_query_fallback_when_no_matched_queries(self):
        self.s.upsert_repo(rec("a/one", matched_queries=None, query="solo"), self.run)
        self.assertEqual(self.s.get_repo("a/one")["matched_queries"], ["solo"])


class TestFilters(StoreTestBase):
    def setUp(self):
        super().setUp()
        self.run2 = self.s.start_run({})
        self.s.upsert_many([
            rec("a/py", stars=100, forks=5, license="MIT", topics=["scraper", "cli"], tech_stack=["Python", "Docker"]),
            rec("a/js", stars=5000, forks=50, language="JavaScript", languages={"JavaScript": 95, "CSS": 5},
                license="Apache-2.0", topics=["web", "scraper"], tech_stack=["React"],
                created_at="2023-03-01T00:00:00Z", last_commit_at="2023-04-01T00:00:00Z",
                website="https://x.dev", archived=True),
            rec("a/old", stars=9000, language="Go", languages={}, license=None, topics=[], tech_stack=[],
                is_fork=True, is_template=True, created_at="2024-12-31T23:59:59Z", website="",
                matched_queries=["other"], query="other"),
        ], self.run)
        self.s.upsert_repo(rec("a/py2", stars=7, matched_queries=["third"], query="third",
                               topics=["cli"]), self.run2)

    def n(self, f):
        return names(query.search(self.s, f, limit=100))

    def test_text(self):
        self.assertEqual(self.n({"text": "desc"}), ["a/js", "a/old", "a/py", "a/py2"])
        self.assertEqual(self.n({"text": "a/p"}), ["a/py", "a/py2"])  # prefix over name tokens
        self.assertEqual(self.n({"text": "x.dev"}), ["a/js"])
        self.assertEqual(self.n({"text": "nomatchzzz"}), [])

    def test_text_hostile(self):
        for t in ['"', 'foo"bar', '*', 'a*b', '-x', 'NOT', 'a AND', '(', ')', "'; DROP TABLE repos;--",
                  'col:val', '^', '{}', '', '   ', 'web scraper "quoted"', '\\', 'NEAR(']:
            query.search(self.s, {"text": t})  # must not raise
        self.assertEqual(self.s.count_repos(), 4)
        self.assertEqual(query.search(self.s, {"text": '"scra'})["total"], 0 + query.search(self.s, {"text": "scra"})["total"])

    def test_ranges(self):
        self.assertEqual(self.n({"stars": {"min": 100, "max": 5000}}), ["a/js", "a/py"])
        self.assertEqual(self.n({"stars": {"min": 9000}}), ["a/old"])
        self.assertEqual(self.n({"forks": {"max": 1}}), ["a/old", "a/py2"])
        for k in query.RANGE_KEYS:
            self.assertEqual(query.search(self.s, {k: {"min": 10**9}})["total"], 0)
        with self.assertRaises(ValueError):
            self.n({"stars": {"min": "abc"}})
        with self.assertRaises(ValueError):
            self.n({"stars": {"lo": 1}})

    def test_language(self):
        self.assertEqual(self.n({"language": {"any": ["python"]}}), ["a/py", "a/py2"])
        self.assertEqual(self.n({"language": {"any": ["Go"]}}), ["a/old"])  # fallback to repos.language
        self.assertEqual(self.n({"language": {"any": ["Shell"], "min_pct": 25}}), [])
        self.assertEqual(self.n({"language": {"any": ["Shell"], "min_pct": 10}}), ["a/py", "a/py2"])
        self.assertEqual(self.n({"language": {"min_pct": 90}}), ["a/js"])
        self.assertEqual(self.n({"language": {"any": ["CSS", "Shell"]}}), ["a/js", "a/py", "a/py2"])

    def test_topics_tech(self):
        self.assertEqual(self.n({"topics": {"any": ["web", "cli"]}}), ["a/js", "a/py", "a/py2"])
        self.assertEqual(self.n({"topics": {"all": ["scraper", "cli"]}}), ["a/py"])
        self.assertEqual(self.n({"topics": {"none": ["scraper"]}}), ["a/old", "a/py2"])
        self.assertEqual(self.n({"topics": {"any": ["scraper"], "none": ["web"]}}), ["a/py"])
        self.assertEqual(self.n({"tech": {"all": ["python", "docker"]}}), ["a/py", "a/py2"])
        self.assertEqual(self.n({"tech": {"any": ["React"]}}), ["a/js"])
        self.assertEqual(self.n({"tech": {"none": ["Python"]}}), ["a/js", "a/old"])

    def test_license(self):
        self.assertEqual(self.n({"license": ["MIT", "Apache-2.0"]}), ["a/js", "a/py", "a/py2"])
        self.assertEqual(self.n({"license": ["GPL"]}), [])

    def test_dates(self):
        self.assertEqual(self.n({"created": {"from": "2024-01-01", "to": "2024-12-31"}}), ["a/old", "a/py", "a/py2"])
        self.assertEqual(self.n({"created": {"to": "2023-12-31"}}), ["a/js"])
        self.assertEqual(self.n({"created": {"from": "2025-01-01"}}), [])
        self.assertEqual(self.n({"last_commit_at": {"to": "2023-04-01"}}), ["a/js"])
        self.assertEqual(self.n({"pushed": {"from": "2024-01-01"}}), ["a/old", "a/py", "a/py2"])
        with self.assertRaises(ValueError):
            self.n({"created": {"from": "yesterday"}})

    def test_bools(self):
        self.assertEqual(self.n({"archived": True}), ["a/js"])
        self.assertEqual(self.n({"archived": False}), ["a/old", "a/py", "a/py2"])
        self.assertEqual(self.n({"is_fork": True}), ["a/old"])
        self.assertEqual(self.n({"is_template": True}), ["a/old"])
        self.assertEqual(self.n({"has_website": True}), ["a/js"])
        self.assertEqual(self.n({"has_website": False}), ["a/old", "a/py", "a/py2"])
        with self.assertRaises(ValueError):
            self.n({"archived": "yes"})

    def test_query_and_run(self):
        self.assertEqual(self.n({"query": ["web scraper"]}), ["a/js", "a/py"])
        self.assertEqual(self.n({"query": ["other", "third"]}), ["a/old", "a/py2"])
        self.assertEqual(self.n({"run_id": [self.run2]}), ["a/py2"])
        self.assertEqual(self.n({"run_id": [self.run, self.run2]}), ["a/js", "a/old", "a/py", "a/py2"])
        self.assertEqual(self.n({"run_id": [999]}), [])

    def test_combos(self):
        f = {"text": "desc", "stars": {"min": 50}, "topics": {"any": ["scraper"]}, "archived": False,
             "license": ["MIT"], "query": ["web scraper"], "language": {"any": ["Python"], "min_pct": 50}}
        self.assertEqual(self.n(f), ["a/py"])
        f["stars"] = {"min": 101}
        res = query.search(self.s, f)
        self.assertEqual((res["rows"], res["total"]), ([], 0))

    def test_unknown_key(self):
        with self.assertRaisesRegex(ValueError, "bogus"):
            self.n({"bogus": 1})
        with self.assertRaisesRegex(ValueError, "bogus"):
            self.n({"topics": {"bogus": []}})

    def test_none_and_empty_filters_ignored(self):
        self.assertEqual(len(self.n({})), 4)
        self.assertEqual(len(self.n({"stars": None, "license": []})), 4)

    def test_sort_paging_total(self):
        r = query.search(self.s, {}, sort=[("stars", "desc")], limit=2, offset=0)
        self.assertEqual([x["full_name"] for x in r["rows"]], ["a/old", "a/js"])
        self.assertEqual(r["total"], 4)
        r = query.search(self.s, {}, sort=[("stars", "desc")], limit=2, offset=2)
        self.assertEqual([x["full_name"] for x in r["rows"]], ["a/py", "a/py2"])
        r = query.search(self.s, {}, sort=[("stars", "asc")], limit=1)
        self.assertEqual(r["rows"][0]["full_name"], "a/py2")
        r = query.search(self.s, {}, sort=[("full_name", "desc")])
        self.assertEqual(r["rows"][0]["full_name"], "a/py2")
        self.assertEqual(query.search(self.s, {}, offset=50)["rows"], [])
        self.assertEqual(query.search(self.s, {}, limit=0)["total"], 4)
        r = query.search(self.s, {"text": "desc"}, sort=[("relevance", "asc")])
        self.assertEqual(r["total"], 4)
        query.search(self.s, {}, sort=[("relevance", "asc")])  # ignored without text

    def test_columns_projection(self):
        r = query.search(self.s, {"language": {"any": ["Go"]}}, columns=["full_name", "stars", "topics"])
        self.assertEqual(r["rows"], [{"full_name": "a/old", "stars": 9000, "topics": []}])

    def test_rejections(self):
        for bad in (dict(sort=[("stars; DROP TABLE repos", "asc")]), dict(sort=[("nope", "asc")]),
                    dict(sort=[("stars", "sideways")]), dict(columns=["nope"]),
                    dict(columns=["stars) FROM x --"]), dict(limit=-1), dict(offset=-1)):
            with self.assertRaises(ValueError, msg=str(bad)):
                query.search(self.s, {}, **bad)

    def test_injection_in_values_harmless(self):
        evil = "x' OR 1=1; DROP TABLE repos; --"
        for f in ({"license": [evil]}, {"language": {"any": [evil]}}, {"topics": {"any": [evil]}},
                  {"tech": {"all": [evil]}}, {"query": [evil]}, {"text": evil}):
            self.assertEqual(query.search(self.s, f)["total"], 0, f)
        with self.assertRaises(ValueError):
            query.search(self.s, {"run_id": ["1 OR 1=1"]})
        self.assertEqual(self.s.count_repos(), 4)

    def test_iter_all(self):
        rows = list(query.iter_all(self.s, {"archived": False}, sort=[("stars", "desc")]))
        self.assertEqual([r["full_name"] for r in rows], ["a/old", "a/py", "a/py2"])
        # chunk boundary
        old = query.CHUNK
        query.CHUNK = 2
        try:
            self.assertEqual(len(list(query.iter_all(self.s, {}))), 4)
            self.assertEqual(len(list(query.iter_all(self.s, {"archived": False}))), 3)
        finally:
            query.CHUNK = old
        with self.assertRaises(ValueError):
            list(query.iter_all(self.s, {"zzz": 1}))

    def test_facets(self):
        f = self.s.facets()
        langs = {x["value"]: x["count"] for x in f["languages"]}
        self.assertEqual(langs["Python"], 2)
        self.assertEqual(langs["JavaScript"], 1)
        self.assertEqual({x["value"]: x["count"] for x in f["topics"]}["cli"], 2)
        self.assertEqual(f["topics"][0]["count"], 2)
        self.assertEqual({x["value"]: x["count"] for x in f["licenses"]}, {"MIT": 2, "Apache-2.0": 1})
        self.assertEqual({x["value"]: x["count"] for x in f["tech"]}["Python"], 2)

    def test_export_roundtrip(self):
        res = query.search(self.s, {"archived": False}, sort=[("stars", "desc")])
        with tempfile.TemporaryDirectory() as d:
            p = exporters.export_rows(res["rows"], Path(d) / "o.csv", "csv")
            with open(p, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r["full_name"] for r in rows], ["a/old", "a/py", "a/py2"])
            self.assertEqual(rows[1]["languages"], "Python 80.0%; Shell 20.0%")
            self.assertEqual(rows[1]["topics"], "scraper, cli")
            pj = exporters.export_rows(list(query.iter_all(self.s, {})), Path(d) / "o.json", "json",
                                       columns=["full_name", "stars"])
            self.assertEqual(len(json.loads(pj.read_text(encoding="utf-8"))), 4)


class TestDatasets(StoreTestBase):
    def test_crud(self):
        i = self.s.save_dataset("Hot", {"stars": {"min": 5}}, ["full_name", "stars"], [("stars", "desc")])
        self.assertEqual(self.s.save_dataset("Hot", {"stars": {"min": 9}}, None, None), i)  # update
        j = self.s.save_dataset("Cold", {}, [], [])
        ds = {d["name"]: d for d in self.s.list_datasets()}
        self.assertEqual(set(ds), {"Hot", "Cold"})
        self.assertEqual(ds["Hot"]["filters"], {"stars": {"min": 9}})
        self.assertEqual(ds["Hot"]["columns"], [])
        self.assertTrue(self.s.delete_dataset(i))
        self.assertFalse(self.s.delete_dataset(i))
        self.assertEqual([d["id"] for d in self.s.list_datasets()], [j])

    def test_invalid_rejected_not_saved(self):
        with self.assertRaises(ValueError):
            self.s.save_dataset("bad", {"nope": 1}, [], [])
        with self.assertRaises(ValueError):
            self.s.save_dataset("bad", {}, ["nope"], [])
        with self.assertRaises(ValueError):
            self.s.save_dataset("bad", {}, [], [("nope", "asc")])
        with self.assertRaises(ValueError):
            self.s.save_dataset("  ", {}, [], [])
        self.assertEqual(self.s.list_datasets(), [])

    def test_stored_dataset_runs(self):
        self.s.upsert_repo(rec("a/one"), self.run)
        self.s.save_dataset("d", {"stars": {"min": 1}}, ["full_name"], [("stars", "desc")])
        d = self.s.list_datasets()[0]
        r = query.search(self.s, d["filters"], d["sort"], 10, 0, d["columns"])
        self.assertEqual(r["rows"], [{"full_name": "a/one"}])


class TestFileDb(unittest.TestCase):
    def test_wal_migration_persistence_threads(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "app.db")
            s = Store(p)
            self.assertEqual(s.schema_version(), 1)
            self.assertEqual(s.read("PRAGMA journal_mode")[0][0].lower(), "wal")
            self.assertEqual(s.read("PRAGMA foreign_keys")[0][0], 1)
            run = s.start_run({})
            s.upsert_many([rec(f"a/r{i}", stars=i) for i in range(20)], run)

            errs, got = [], []

            def reader():
                try:
                    got.append(query.search(s, {"stars": {"min": 10}})["total"])
                except Exception as e:  # pragma: no cover
                    errs.append(e)

            def writer():
                try:
                    s.upsert_repo(rec("a/extra", stars=1000), run)
                except Exception as e:  # pragma: no cover
                    errs.append(e)

            ts = [threading.Thread(target=reader) for _ in range(3)] + [threading.Thread(target=writer)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(errs, [])
            self.assertTrue(all(g >= 10 for g in got))
            s.close()

            s2 = Store(p)  # reopen: no re-migration, data intact
            self.assertEqual(s2.schema_version(), 1)
            self.assertEqual(s2.count_repos(), 21)
            s2.close()

    def test_newer_schema_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "app.db")
            Store(p).close()
            import sqlite3
            c = sqlite3.connect(p)
            c.execute("PRAGMA user_version=99")
            c.close()
            with self.assertRaises(RuntimeError):
                Store(p)

    def test_migration_failure_rolls_back(self):
        from ghscraper.store import db
        orig = list(db.MIGRATIONS)
        db.MIGRATIONS.append("CREATE TABLE t2(x); THIS IS NOT SQL;")
        try:
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "app.db")
                with self.assertRaises(Exception):
                    Store(p)
                db.MIGRATIONS[:] = orig
                s = Store(p)  # v1 applied cleanly, t2 absent
                self.assertEqual(s.schema_version(), 1)
                self.assertEqual(s.read("SELECT name FROM sqlite_master WHERE name='t2'"), [])
                s.close()
        finally:
            db.MIGRATIONS[:] = orig


class TestImporter(unittest.TestCase):
    def test_real_file_and_broken(self):
        root = Path(__file__).resolve().parent.parent / "output"
        files = sorted(glob.glob(str(root / "*.json")))
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "broken.json"
            bad.write_text("{not json", encoding="utf-8")
            notlist = Path(d) / "obj.json"
            notlist.write_text('{"a": 1}', encoding="utf-8")
            empty = Path(d) / "empty.json"
            empty.write_text('[{"x": 1}, 5]', encoding="utf-8")
            partial = Path(d) / "partial.json"
            partial.write_text(json.dumps([rec("p/one"), {"x": 1}]), encoding="utf-8")
            s = Store(":memory:")
            self.addCleanup(s.close)
            summ = importer.import_json_files(s, [bad, notlist, empty, Path(d) / "missing.json", partial])
            self.assertEqual({x["file"] for x in summ["skipped"]},
                             {"broken.json", "obj.json", "empty.json", "missing.json"})
            self.assertTrue(all(x["reason"] for x in summ["skipped"]))
            self.assertEqual(summ["imported"][0]["skipped_records"], 1)
            self.assertEqual(summ["total_repos"], 1)
            runs = s.list_runs()
            self.assertEqual(runs[0]["config"], {"imported_from": "partial.json"})
            if not files:
                self.skipTest("no output/*.json present")
            f = files[0]
            data = json.loads(Path(f).read_text(encoding="utf-8-sig"))
            if not isinstance(data, list):
                self.skipTest("first output json is not an array")
            summ = importer.import_json_files(s, [f])
            self.assertEqual(summ["skipped"], [])
            imp = summ["imported"][0]
            valid = {r["full_name"].lower() for r in data if isinstance(r, dict) and r.get("full_name")}
            self.assertEqual(imp["repos"], len(valid) if len(valid) == len(data) else imp["repos"])
            self.assertGreaterEqual(s.count_repos(), len(valid))
            first = data[0]
            got = s.get_repo(first["full_name"])
            self.assertEqual(got["stars"], first.get("stars"))
            self.assertEqual(got.get("languages"), first.get("languages"))
            self.assertEqual(got.get("topics"), first.get("topics"))
            # imported rows export fine
            with tempfile.TemporaryDirectory() as out:
                p = exporters.export_rows(list(query.iter_all(s, {})), Path(out) / "x.csv", "csv")
                self.assertTrue(p.exists())


if __name__ == "__main__":
    unittest.main()
