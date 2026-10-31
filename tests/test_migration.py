"""Ghost Scraper -> Branch Scrapper data directory migration (ghscraper.config.migrate_legacy_data_dir)."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ghscraper import config as config_mod
from ghscraper.config import (
    LEGACY_APP_DIR_NAME, MIGRATED_MARKER, app_data_dir, legacy_data_dir, migrate_legacy_data_dir,
)
from ghscraper.store import Store


class RecordingLog:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def _add(self, level, msg, args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._add("info", msg, args)

    def warning(self, msg, *args):
        self._add("warning", msg, args)

    def error(self, msg, *args):
        self._add("error", msg, args)

    def text(self, level=None):
        return "\n".join(m for lv, m in self.lines if level in (None, lv))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bs-migration-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.roaming = self.tmp / "Roaming"
        self.roaming.mkdir()
        # A Windows-like world: APPDATA decides both the legacy and the new location, no override.
        env = patch.dict(os.environ, {"APPDATA": str(self.roaming)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GHOSTSCRAPER_HOME", None)
        plat = patch.object(config_mod.sys, "platform", "win32")
        plat.start()
        self.addCleanup(plat.stop)
        self.legacy = self.roaming / LEGACY_APP_DIR_NAME
        self.new = app_data_dir()
        self.log = RecordingLog()
        self._open: list[sqlite3.Connection] = []
        self.addCleanup(self._close_open)

    def _close_open(self):
        for c in self._open:
            try:
                c.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------ helpers
    def make_legacy(self, with_db=True, **files):
        self.legacy.mkdir(parents=True)
        (self.legacy / "config.json").write_text('{"queries": ["legacy query"]}', encoding="utf-8")
        (self.legacy / "logs").mkdir()
        (self.legacy / "logs" / "sidecar.log").write_text("old log\n", encoding="utf-8")
        for name, text in files.items():
            (self.legacy / name).write_text(text, encoding="utf-8")
        if with_db:
            self.make_wal_db(self.legacy / "data.db")

    def make_wal_db(self, path: Path):
        """A WAL-mode database whose newest rows live only in data.db-wal (autocheckpoint off, connection open)."""
        con = sqlite3.connect(path)
        self._open.append(con)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        con.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",)])
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("INSERT INTO t (v) VALUES ('only-in-wal')")
        con.commit()
        self.assertTrue((path.parent / "data.db-wal").stat().st_size > 0, "test setup: the row must be in the WAL")
        return con

    def values(self, db: Path):
        con = sqlite3.connect(db)
        try:
            return [r[0] for r in con.execute("SELECT v FROM t ORDER BY id")]
        finally:
            con.close()

    def tree(self, root: Path, skip_db=False):
        """{relative path: bytes}. The pre-copy WAL checkpoint legitimately rewrites data.db*, so those can be skipped."""
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and not (skip_db and p.name.startswith("data.db"))}

    def temp_siblings(self):
        return [p.name for p in self.new.parent.iterdir() if ".migrating-" in p.name]

    # ------------------------------------------------------------------ paths
    def test_default_paths_per_platform(self):
        self.assertEqual(self.new, self.roaming / "BranchScrapper")
        self.assertEqual(legacy_data_dir(), self.roaming / "GhostScraper")
        with patch.object(config_mod.sys, "platform", "darwin"):
            self.assertEqual(app_data_dir(), Path.home() / "Library" / "Application Support" / "BranchScrapper")
            self.assertEqual(legacy_data_dir(), Path.home() / "Library" / "Application Support" / "GhostScraper")
        with patch.object(config_mod.sys, "platform", "linux"), patch.dict(os.environ, {"XDG_DATA_HOME": "/x"}):
            self.assertEqual(app_data_dir(), Path("/x") / "branchscrapper")
            self.assertEqual(legacy_data_dir(), Path("/x") / "ghostscraper")

    # ------------------------------------------------------------------ the happy paths
    def test_fresh_install_has_nothing_to_migrate(self):
        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "none")
        self.assertFalse(self.new.exists())
        self.assertEqual(self.temp_siblings(), [])

    def test_legacy_with_db_and_wal_is_copied_and_readable(self):
        self.make_legacy()
        before = self.tree(self.legacy)
        con = sqlite3.connect(self.legacy / "data.db")     # a row inserted by the "old app" just before
        self._open.append(con)
        con.execute("INSERT INTO t (v) VALUES ('inserted-in-legacy')")
        con.commit()

        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "migrated", self.log.text())

        self.assertEqual(self.values(self.new / "data.db"), ["a", "b", "only-in-wal", "inserted-in-legacy"])
        self.assertEqual((self.new / "config.json").read_text(encoding="utf-8"), '{"queries": ["legacy query"]}')
        self.assertEqual((self.new / "logs" / "sidecar.log").read_text(encoding="utf-8"), "old log\n")
        self.assertEqual(self.temp_siblings(), [])
        # the legacy tree is still complete and still readable (only the marker was added)
        self.assertEqual(self.values(self.legacy / "data.db"), ["a", "b", "only-in-wal", "inserted-in-legacy"])
        for rel in before:
            self.assertTrue((self.legacy / rel).exists(), rel)
        self.assertFalse((self.new / MIGRATED_MARKER).exists())

    def test_real_store_opens_the_migrated_database(self):
        self.legacy.mkdir(parents=True)
        s = Store(self.legacy / "data.db")
        run = s.start_run({"q": 1})
        s.upsert_repo({"full_name": "a/b", "url": "https://github.com/a/b", "stars": 3}, run)
        # not closed on purpose: the data may still sit in the WAL
        try:
            self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "migrated", self.log.text())
        finally:
            s.close()
        s2 = Store(self.new / "data.db")
        self.addCleanup(s2.close)
        self.assertEqual(s2.count_repos(), 1)
        self.assertEqual(s2.get_repo("a/b")["stars"], 3)

    def test_marker_file_is_written_into_the_legacy_dir(self):
        self.make_legacy()
        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "migrated")
        note = (self.legacy / MIGRATED_MARKER).read_text(encoding="utf-8")
        self.assertIn(str(self.new), note)
        self.assertIn("Copied at:", note)
        self.assertIn("safe to delete after you have checked Branch Scrapper", note)

    def test_empty_new_dir_counts_as_not_migrated(self):
        self.make_legacy()
        self.new.mkdir()
        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "migrated", self.log.text())
        self.assertEqual(self.values(self.new / "data.db"), ["a", "b", "only-in-wal"])

    # ------------------------------------------------------------------ nothing to do / refusing to touch things
    def test_existing_non_empty_new_dir_is_authoritative(self):
        self.make_legacy()
        self.new.mkdir()
        (self.new / "data.db").write_bytes(b"mine")
        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "skipped-existing")
        self.assertEqual(self.tree(self.new), {"data.db": b"mine"})
        self.assertFalse((self.legacy / MIGRATED_MARKER).exists())
        self.assertEqual(self.temp_siblings(), [])

    def test_ghostscraper_home_override_skips_migration(self):
        self.make_legacy()
        custom = self.tmp / "custom-home"
        with patch.dict(os.environ, {"GHOSTSCRAPER_HOME": str(custom)}):
            self.assertEqual(app_data_dir(), custom)
            self.assertEqual(migrate_legacy_data_dir(app_data_dir(), self.log), "none")
        self.assertFalse(custom.exists())
        self.assertFalse((self.legacy / MIGRATED_MARKER).exists())
        self.assertFalse(self.new.exists())

    def test_a_legacy_dir_that_was_already_migrated_is_not_copied_again(self):
        self.make_legacy()
        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "migrated")
        shutil.rmtree(self.new)                      # the user reset the new app on purpose
        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "none")
        self.assertFalse(self.new.exists())

    def test_empty_legacy_dir_is_ignored(self):
        self.legacy.mkdir(parents=True)
        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "none")
        self.assertFalse(self.new.exists())

    # ------------------------------------------------------------------ failure and races
    def test_failure_mid_copy_leaves_legacy_intact_and_no_temp_dir(self):
        self.make_legacy()
        before = self.tree(self.legacy, skip_db=True)
        real_copytree = shutil.copytree

        def partial_then_boom(src, dst, *a, **kw):
            Path(dst).mkdir()
            (Path(dst) / "half.db").write_bytes(b"partial")
            raise OSError("disk full (simulated)")

        with patch.object(config_mod.shutil, "copytree", side_effect=partial_then_boom):
            status = migrate_legacy_data_dir(self.new, self.log)

        self.assertTrue(status.startswith("failed: "), status)
        self.assertIn("disk full", status)
        self.assertIn("disk full", self.log.text("error"))
        self.assertFalse(self.new.exists())
        self.assertEqual(self.temp_siblings(), [])
        self.assertEqual(self.tree(self.legacy, skip_db=True), before)     # byte-identical, no marker
        self.assertEqual(self.values(self.legacy / "data.db"), ["a", "b", "only-in-wal"])   # data unchanged
        self.assertFalse((self.legacy / MIGRATED_MARKER).exists())
        self.assertIs(shutil.copytree, real_copytree)
        # and a later start can still migrate
        self.assertEqual(migrate_legacy_data_dir(self.new, self.log), "migrated")

    def test_losing_the_race_discards_our_copy_and_never_clobbers(self):
        self.make_legacy()
        real_copytree = shutil.copytree

        def copy_then_rival_appears(src, dst, *a, **kw):
            real_copytree(src, dst, *a, **kw)
            if not self.new.exists():                      # (copytree recurses through the patched name)
                self.new.mkdir()                           # another sidecar finished first
                (self.new / "data.db").write_bytes(b"rival")

        with patch.object(config_mod.shutil, "copytree", side_effect=copy_then_rival_appears):
            status = migrate_legacy_data_dir(self.new, self.log)

        self.assertEqual(status, "skipped-existing")
        self.assertEqual(self.tree(self.new), {"data.db": b"rival"})
        self.assertEqual(self.temp_siblings(), [])
        self.assertFalse((self.legacy / MIGRATED_MARKER).exists())

    def test_rename_collision_after_the_last_check_is_also_not_clobbered(self):
        self.make_legacy()
        real_rename = os.rename

        def rival_wins_just_before_rename(src, dst):
            Path(dst).mkdir()
            (Path(dst) / "data.db").write_bytes(b"rival")
            return real_rename(src, dst)                  # fails: destination exists and is not empty

        with patch.object(config_mod.os, "rename", side_effect=rival_wins_just_before_rename):
            status = migrate_legacy_data_dir(self.new, self.log)

        self.assertEqual(status, "skipped-existing", self.log.text())
        self.assertEqual(self.tree(self.new), {"data.db": b"rival"})
        self.assertEqual(self.temp_siblings(), [])

    def test_checkpoint_failure_is_logged_and_the_copy_still_happens(self):
        self.make_legacy()
        real_connect = sqlite3.connect

        def connect(*a, **kw):
            raise sqlite3.OperationalError("database is locked (simulated)")

        with patch.object(config_mod.sqlite3, "connect", side_effect=connect):
            status = migrate_legacy_data_dir(self.new, self.log)
        self.assertEqual(status, "migrated")
        self.assertIn("checkpoint", self.log.text("warning"))
        self.assertIn("locked", self.log.text("warning"))
        # the -wal file was copied along, so the newest row is still there
        self.assertEqual(self.values(self.new / "data.db"), ["a", "b", "only-in-wal"])
        self.assertIs(sqlite3.connect, real_connect)


if __name__ == "__main__":
    unittest.main()
