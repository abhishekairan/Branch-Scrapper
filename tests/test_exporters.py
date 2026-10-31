"""Exporters (JSON/CSV writing, CSV injection guard) and app_data_dir."""
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ghscraper import config as config_mod
from ghscraper.config import app_data_dir
from ghscraper.exporters import CSV_COLUMNS, _cell, _safe_cell, export, export_rows


def read_csv(path: Path) -> list[list[str]]:
    """Parse a CSV written with a UTF-8 BOM into a list of rows."""
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "missing UTF-8 BOM"
    return list(csv.reader(io.StringIO(raw[3:].decode("utf-8"), newline="")))


class ExportRowsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_json_without_columns_keeps_all_keys(self):
        rows = [{"full_name": "a/b", "stars": 3, "extra": "x"}]
        p = export_rows(rows, self.dir / "sub" / "out.json", "json")
        self.assertTrue(p.exists())
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), rows)

    def test_json_with_columns_reduces_and_orders_keys(self):
        rows = [{"full_name": "a/b", "stars": 3, "extra": "x"}]
        p = export_rows(rows, self.dir / "out.json", "json", columns=["stars", "full_name"])
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data, [{"stars": 3, "full_name": "a/b"}])
        self.assertEqual(list(data[0]), ["stars", "full_name"])

    def test_json_is_utf8_not_escaped(self):
        p = export_rows([{"description": "café ✓"}], self.dir / "u.json", "json")
        self.assertIn("café ✓", p.read_text(encoding="utf-8"))

    def test_csv_has_bom_and_default_header(self):
        p = export_rows([{"full_name": "a/b"}], self.dir / "out.csv", "csv")
        rows = read_csv(p)
        self.assertEqual(rows[0], CSV_COLUMNS)
        self.assertEqual(rows[1][CSV_COLUMNS.index("full_name")], "a/b")

    def test_csv_custom_columns_header(self):
        p = export_rows([{"a": 1, "b": 2}], self.dir / "out.csv", "csv", columns=["b", "a"])
        self.assertEqual(read_csv(p), [["b", "a"], ["2", "1"]])

    def test_csv_formula_cell_is_prefixed(self):
        p = export_rows([{"description": '=HYPERLINK("x")'}], self.dir / "f.csv", "csv",
                        columns=["description"])
        self.assertEqual(read_csv(p)[1], ['\'=HYPERLINK("x")'])

    def test_csv_negative_int_not_prefixed(self):
        p = export_rows([{"stars": -5}], self.dir / "n.csv", "csv", columns=["stars"])
        self.assertEqual(read_csv(p)[1], ["-5"])

    def test_csv_flattens_lists_and_dicts(self):
        row = {"topics": ["a", "b"], "languages": {"Python": 90, "Go": 10}}
        p = export_rows([row], self.dir / "c.csv", "csv", columns=["topics", "languages"])
        self.assertEqual(read_csv(p)[1], ["a, b", "Python 90%; Go 10%"])

    def test_bad_format_raises(self):
        with self.assertRaises(ValueError):
            export_rows([], self.dir / "x.txt", "xml")

    def test_export_writes_both_formats(self):
        results = [{"full_name": "a/b", "description": "=cmd"}]
        written = export(results, ["web scraper", "other"], str(self.dir / "out"), ["json", "csv"])
        self.assertEqual(len(written), 2)
        self.assertEqual({p.suffix for p in written}, {".json", ".csv"})
        for p in written:
            self.assertTrue(p.exists())
            self.assertTrue(p.name.startswith("web-scraper_plus1_"))
        json_path = next(p for p in written if p.suffix == ".json")
        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), results)


class SafeCellTests(unittest.TestCase):
    def test_prefixes(self):
        for ch in ["=", "+", "-", "@", "\t", "\r"]:
            self.assertEqual(_safe_cell(ch + "x"), "'" + ch + "x")

    def test_passthrough(self):
        self.assertEqual(_safe_cell("hello"), "hello")
        self.assertEqual(_safe_cell(-5), -5)
        self.assertEqual(_safe_cell(""), "")
        self.assertEqual(_cell(None), "")


class AppDataDirTests(unittest.TestCase):
    def _env(self, **values):
        """Isolate os.environ for one test. GHOSTSCRAPER_HOME is cleared first; None removes a key."""
        env_patch = patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("GHOSTSCRAPER_HOME", None)
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_env_override_wins_on_every_platform(self):
        self._env(GHOSTSCRAPER_HOME="D:/custom")
        for plat in ("win32", "darwin", "linux"):
            with patch.object(config_mod.sys, "platform", plat):
                self.assertEqual(app_data_dir(), Path("D:/custom"))

    def test_windows_uses_appdata(self):
        self._env(APPDATA="C:/Users/me/AppData/Roaming")
        with patch.object(config_mod.sys, "platform", "win32"):
            self.assertEqual(app_data_dir(), Path("C:/Users/me/AppData/Roaming") / "GhostScraper")

    def test_windows_falls_back_to_home_roaming(self):
        self._env(APPDATA=None)
        with patch.object(config_mod.sys, "platform", "win32"):
            self.assertEqual(app_data_dir(), Path.home() / "AppData" / "Roaming" / "GhostScraper")

    def test_macos_application_support(self):
        self._env()
        with patch.object(config_mod.sys, "platform", "darwin"):
            self.assertEqual(app_data_dir(), Path.home() / "Library" / "Application Support" / "GhostScraper")

    def test_linux_xdg_data_home(self):
        self._env(XDG_DATA_HOME="/data/xdg")
        with patch.object(config_mod.sys, "platform", "linux"):
            self.assertEqual(app_data_dir(), Path("/data/xdg") / "ghostscraper")

    def test_linux_default_local_share(self):
        self._env(XDG_DATA_HOME=None)
        with patch.object(config_mod.sys, "platform", "linux"):
            self.assertEqual(app_data_dir(), Path.home() / ".local" / "share" / "ghostscraper")

    def test_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "not-yet"
            self._env(GHOSTSCRAPER_HOME=str(target))
            self.assertEqual(app_data_dir(), target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
