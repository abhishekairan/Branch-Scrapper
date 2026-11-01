"""Data folder location: resolution order (config.resolve_data_dir), the pointer file, and the storage.move /
storage.reset RPCs (ghscraper.relocate). Nothing here touches the real %APPDATA%: every path is a temp dir and
app_data_dir is patched (or APPDATA is redirected for the real-process test)."""
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from websockets.asyncio.client import connect

from ghscraper import config as config_mod
from ghscraper import relocate
from ghscraper.api import protocol
from ghscraper.api.methods import Api
from ghscraper.api.screen import ScreenRelay
from ghscraper.api.server import Hub
from ghscraper.api.protocol import RpcError
from ghscraper.config import (POINTER_NAME, Config, Location, clear_location_pointer, read_location_pointer,
                              resolve_data_dir, write_location_pointer)
from ghscraper.core.runmanager import RunManager, apply_overrides
from ghscraper.store import Store

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "s" * 40


def seed_store(db: Path, n: int = 3) -> None:
    s = Store(db)
    run = s.start_run({"q": 1})
    for i in range(n):
        s.upsert_repo({"full_name": f"o/r{i}", "url": f"https://github.com/o/r{i}", "stars": i,
                       "description": f"repo {i}", "topics": ["a", "b"], "tech_stack": ["py"]}, run)
    s.close()


def counts(db: Path) -> dict:
    return relocate.table_counts(db)


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bs-storage-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.default = self.tmp / "Roaming" / "BranchScrapper"
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GHOSTSCRAPER_HOME", None)
        p = patch.object(config_mod, "app_data_dir", lambda *a, **k: self.default)
        p.start()
        self.addCleanup(p.stop)


class ResolutionTests(TmpCase):
    def custom(self) -> Path:
        d = self.tmp / "custom"
        d.mkdir()
        return d

    def test_default_when_nothing_is_set(self):
        loc = resolve_data_dir()
        self.assertEqual((loc.data_dir, loc.source, loc.problem), (self.default, "default", None))
        self.assertTrue(loc.can_change)
        self.assertEqual(loc.pointer_file, self.default / POINTER_NAME)

    def test_valid_pointer_wins_over_default(self):
        d = self.custom()
        write_location_pointer(self.default, d)
        loc = resolve_data_dir()
        self.assertEqual((loc.data_dir, loc.source), (d, "pointer"))
        self.assertEqual(json.loads((self.default / POINTER_NAME).read_text("utf-8")), {"data_dir": str(d)})
        self.assertEqual([p.name for p in self.default.iterdir()], [POINTER_NAME])      # no temp file left

    def test_pointer_to_missing_dir_falls_back_with_a_problem(self):
        write_location_pointer(self.default, self.tmp / "gone")
        warnings = []

        class Log:
            def warning(self, msg, *a):
                warnings.append(msg % a)

        loc = resolve_data_dir(None, Log())
        self.assertEqual((loc.data_dir, loc.source), (self.default, "default"))
        self.assertIn("does not exist", loc.problem)
        self.assertEqual(len(warnings), 1)
        self.assertTrue((self.default / POINTER_NAME).exists())          # left alone; the user may re-attach a drive

    def test_invalid_pointers_are_ignored(self):
        self.default.mkdir(parents=True)
        f = self.default / POINTER_NAME
        d = self.custom()
        for text in ("{not json", "[]", "{}", '{"data_dir": 5}', '{"data_dir": ""}', '{"data_dir": "relative/dir"}',
                     json.dumps({"data_dir": str(d / "file.txt")})):
            f.write_text(text, encoding="utf-8")
            loc = resolve_data_dir()
            self.assertEqual((loc.data_dir, loc.source), (self.default, "default"), text)
            self.assertIsNotNone(loc.problem, text)

    def test_pointer_naming_a_file_is_invalid(self):
        (self.tmp / "afile").write_text("x")
        write_location_pointer(self.default, self.tmp / "afile")
        self.assertEqual(resolve_data_dir().source, "default")

    def test_pointer_to_the_default_itself_is_just_default(self):
        self.default.mkdir(parents=True)
        write_location_pointer(self.default, self.default)
        self.assertEqual(resolve_data_dir().source, "default")

    def test_env_beats_pointer_and_arg_beats_env(self):
        d, env_dir, arg = self.custom(), self.tmp / "envhome", self.tmp / "argdir"
        write_location_pointer(self.default, d)
        with patch.object(config_mod, "app_data_dir", lambda *a, **k: Path(os.environ["GHOSTSCRAPER_HOME"])):
            os.environ["GHOSTSCRAPER_HOME"] = str(env_dir)
            loc = resolve_data_dir()
            self.assertEqual((loc.data_dir, loc.source, loc.can_change), (env_dir, "env", False))
            loc = resolve_data_dir(arg)
            self.assertEqual((loc.data_dir, loc.source, loc.can_change), (arg, "arg", False))

    def test_read_missing_and_clear(self):
        self.assertEqual(read_location_pointer(self.default), (None, None))
        self.assertFalse(clear_location_pointer(self.default))
        d = self.custom()
        write_location_pointer(self.default, d)
        self.assertEqual(read_location_pointer(self.default), (d, None))
        self.assertTrue(clear_location_pointer(self.default))
        self.assertEqual(read_location_pointer(self.default), (None, None))
        self.assertTrue(d.exists())

    def test_pointer_write_is_atomic_on_failure(self):
        d = self.custom()
        write_location_pointer(self.default, d)
        before = (self.default / POINTER_NAME).read_text("utf-8")
        with patch.object(config_mod.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                write_location_pointer(self.default, self.tmp / "other")
        self.assertEqual((self.default / POINTER_NAME).read_text("utf-8"), before)
        self.assertEqual([p.name for p in self.default.iterdir()], [POINTER_NAME])

    def test_ui_theme_is_a_validated_config_field(self):
        self.assertEqual(Config().ui_theme, "system")
        cfg, errors = apply_overrides(Config(), {"ui_theme": "dark"})
        self.assertEqual((cfg.ui_theme, errors), ("dark", []))
        cfg, errors = apply_overrides(Config(), {"ui_theme": "neon"})
        self.assertEqual(cfg.ui_theme, "system")
        self.assertEqual(errors[0]["field"], "ui_theme")
        path = self.tmp / "config.json"
        Config(ui_theme="light").save(path)
        self.assertEqual(Config.load(path).ui_theme, "light")
        path.write_text(json.dumps({"queries": ["x"]}), encoding="utf-8")       # an old config without the field
        self.assertEqual(Config.load(path).ui_theme, "system")

    def test_check_updates_is_a_validated_boolean_config_field(self):
        self.assertTrue(Config().check_updates)                                  # on by default
        cfg, errors = apply_overrides(Config(), {"check_updates": False})
        self.assertEqual((cfg.check_updates, errors), (False, []))
        cfg, errors = apply_overrides(Config(), {"check_updates": "no"})
        self.assertTrue(cfg.check_updates)
        self.assertEqual(errors[0]["field"], "check_updates")
        path = self.tmp / "config.json"
        Config(check_updates=False).save(path)
        self.assertFalse(Config.load(path).check_updates)
        path.write_text(json.dumps({"queries": ["x"]}), encoding="utf-8")       # an old config without the field
        self.assertTrue(Config.load(path).check_updates)


class StorageApiTests(TmpCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.default.mkdir(parents=True)
        self.store = Store(self.default / "data.db")       # stays open, like the running app: rows may sit in the WAL
        run = self.store.start_run({"q": 1})
        for i in range(3):
            self.store.upsert_repo({"full_name": f"o/r{i}", "url": f"https://github.com/o/r{i}", "stars": i,
                                    "topics": ["a", "b"], "tech_stack": ["py"]}, run)
        (self.default / "config.json").write_text('{"queries": ["mine"], "ui_theme": "dark"}', encoding="utf-8")
        (self.default / "logs").mkdir()
        (self.default / "logs" / "sidecar.log").write_text("log line\n", encoding="utf-8")
        self.manager = RunManager(self.store, self.default / "config.json", seed_config=None)
        self.api = self.make_api(Location(self.default, "default", self.default))

    async def asyncTearDown(self):
        await self.manager.shutdown(5)
        self.store.close()

    def make_api(self, loc, store=None):
        return Api(manager=self.manager, store=store or self.store, relay=ScreenRelay(self.manager, Hub()),
                   data_dir=loc.data_dir if loc else self.default, db_path=(loc.data_dir if loc else self.default) / "data.db",
                   request_shutdown=lambda: None, location=loc)

    def pointer(self):
        return read_location_pointer(self.default)

    def assertNothingChanged(self, target: Path, before_default: dict, target_existed: bool = False):
        self.assertEqual(tree_ignoring_db(self.default), before_default)
        self.assertEqual(self.pointer(), (None, None))
        self.assertEqual(target.exists(), target_existed)
        self.assertEqual([p.name for p in target.parent.iterdir() if ".moving-" in p.name] if target.parent.exists() else [], [])

    async def rpc_error(self, coro) -> RpcError:
        with self.assertRaises(RpcError) as cm:
            await coro
        return cm.exception

    # ------------------------------------------------------------------ happy path
    async def test_move_to_a_new_folder_copies_everything_and_keeps_the_old_data(self):
        target = self.tmp / "D" / "Branch data"
        src_counts = counts(self.default / "data.db")
        self.assertEqual(src_counts["repos"], 3)
        old_cfg = (self.default / "config.json").read_bytes()
        old_log = (self.default / "logs" / "sidecar.log").read_bytes()

        res = await self.api.storage_move(None, {"path": str(target)})

        self.assertTrue(res["restart_required"])
        self.assertEqual((res["mode"], res["from"], res["to"]), ("copy", str(self.default), str(target)))
        self.assertEqual(res["warnings"], [])
        self.assertEqual(counts(target / "data.db"), src_counts)                   # every table, FTS included
        self.assertEqual(res["tables"], src_counts)
        self.assertEqual((target / "config.json").read_bytes(), old_cfg)
        self.assertEqual((target / "logs" / "sidecar.log").read_bytes(), old_log)
        # the new database is a real Branch Scrapper store
        s2 = Store(target / "data.db")
        self.addCleanup(s2.close)
        self.assertEqual(s2.count_repos(), 3)
        self.assertEqual(s2.get_repo("o/r2")["topics"], ["a", "b"])
        # the old data is still there and still readable
        self.assertEqual(counts(self.default / "data.db"), src_counts)
        self.assertEqual((self.default / "config.json").read_bytes(), old_cfg)
        self.assertEqual((self.default / "logs" / "sidecar.log").read_bytes(), old_log)
        self.assertEqual(self.store.count_repos(), 3)
        # pointer written, no temp leftovers, and the next start would use it
        self.assertEqual(self.pointer(), (target, None))
        self.assertEqual([p.name for p in target.parent.iterdir()], [target.name])
        self.assertLessEqual({"data.db", "config.json", "logs", POINTER_NAME}, {p.name for p in self.default.iterdir()})
        loc = resolve_data_dir()
        self.assertEqual((loc.data_dir, loc.source), (target, "pointer"))
        self.assertEqual(self.api._storage_info()["pending_restart"], str(target))
        # no second change before the restart
        err = await self.rpc_error(self.api.storage_move(None, {"path": str(self.tmp / "other")}))
        self.assertIn("restart", err.message)
        # no writes either: they would land in the old folder and be missing after the restart
        for method, params in (("datasets.save", {"name": "x", "filters": {}}), ("data.delete", {"full_names": ["a/b"]}),
                               ("config.update", {"ui_theme": "dark"}), ("run.start", {})):
            err = await self.rpc_error(self.api.handle(None, method, params))
            self.assertIn("restart", err.message, method)
        # reads still work
        self.assertIn("rows", await self.api.handle(None, "data.search", {}))

    async def test_dry_run_changes_nothing(self):
        target = self.tmp / "new"
        before = tree_ignoring_db(self.default)
        res = await self.api.storage_move(None, {"path": str(target), "dry_run": True})
        self.assertEqual((res["mode"], res["dry_run"]), ("copy", True))
        self.assertGreater(res["bytes_to_copy"], 0)
        self.assertNothingChanged(target, before)
        self.assertIsNone(self.api._storage_info()["pending_restart"])

    async def test_move_into_an_existing_empty_folder(self):
        target = self.tmp / "empty"
        target.mkdir()
        res = await self.api.storage_move(None, {"path": str(target)})
        self.assertEqual(res["mode"], "copy")
        self.assertEqual(counts(target / "data.db")["repos"], 3)
        self.assertEqual(self.pointer(), (target, None))

    async def test_rows_that_only_exist_in_the_wal_are_copied(self):
        wal = self.default / "data.db-wal"
        self.store.upsert_repo({"full_name": "o/late", "url": "https://github.com/o/late"}, 1)
        self.assertTrue(wal.exists())
        target = self.tmp / "t"
        await self.api.storage_move(None, {"path": str(target)})
        self.assertEqual(counts(target / "data.db")["repos"], 4)

    # ------------------------------------------------------------------ refusals leave everything unchanged
    async def test_target_not_empty_without_data_db_is_refused(self):
        target = self.tmp / "busy"
        target.mkdir()
        (target / "holiday.jpg").write_bytes(b"precious")
        before = tree_ignoring_db(self.default)
        err = await self.rpc_error(self.api.storage_move(None, {"path": str(target)}))
        self.assertEqual(err.code, protocol.INVALID_PARAMS)
        self.assertIn("not empty", err.message)
        self.assertEqual(tree(target), {"holiday.jpg": b"precious"})
        self.assertNothingChanged(target, before, target_existed=True)

    async def test_target_with_existing_data_switches_without_copying(self):
        target = self.tmp / "other-install"
        target.mkdir()
        seed_store(target / "data.db", n=7)
        (target / "config.json").write_text('{"queries": ["theirs"]}', encoding="utf-8")
        snapshot = tree(target)
        dry = await self.api.storage_move(None, {"path": str(target), "dry_run": True})
        self.assertEqual((dry["mode"], dry["existing"]["repos"], dry["bytes_to_copy"]), ("switch", 7, 0))

        res = await self.api.storage_move(None, {"path": str(target)})

        self.assertEqual((res["mode"], res["restart_required"]), ("switch", True))
        self.assertEqual(res["tables"]["repos"], 7)
        after = {k: v for k, v in tree(target).items() if not k.startswith("data.db-")}    # SQLite may add -wal/-shm
        self.assertEqual(after.pop("config.json"), snapshot.pop("config.json"))        # nothing was copied over it
        self.assertEqual(after, snapshot)                                              # data.db byte-identical, no logs/ added
        self.assertEqual(self.pointer(), (target, None))
        self.assertEqual(self.store.count_repos(), 3)                                  # ours untouched

    async def test_target_with_a_corrupt_or_foreign_data_db_is_refused(self):
        before = tree_ignoring_db(self.default)
        bad = self.tmp / "bad"
        bad.mkdir()
        (bad / "data.db").write_bytes(b"this is not sqlite" * 50)
        err = await self.rpc_error(self.api.storage_move(None, {"path": str(bad)}))
        self.assertEqual(err.code, protocol.INVALID_PARAMS)
        foreign = self.tmp / "foreign"
        foreign.mkdir()
        con = sqlite3.connect(foreign / "data.db")
        con.execute("CREATE TABLE t (x)")
        con.commit()
        con.close()
        err = await self.rpc_error(self.api.storage_move(None, {"path": str(foreign)}))
        self.assertIn("not a Branch Scrapper database", err.message)
        self.assertEqual(self.pointer(), (None, None))
        self.assertEqual(tree_ignoring_db(self.default), before)

    async def test_newer_schema_is_refused(self):
        newer = self.tmp / "newer"
        newer.mkdir()
        seed_store(newer / "data.db")
        con = sqlite3.connect(newer / "data.db")
        con.execute("PRAGMA user_version=99")
        con.close()
        err = await self.rpc_error(self.api.storage_move(None, {"path": str(newer)}))
        self.assertIn("newer", err.message)

    async def test_bad_targets(self):
        before = tree_ignoring_db(self.default)
        f = self.tmp / "afile"
        f.write_text("x")
        cases = {
            "relative": "some/relative/dir",
            "empty": "",
            "a file": str(f),
            "inside the current folder": str(self.default / "sub"),
            "the current folder": str(self.default),
            "a parent of the current folder": str(self.default.parent),
            "below a file": str(f / "child"),
        }
        for name, path in cases.items():
            err = await self.rpc_error(self.api.storage_move(None, {"path": path}))
            self.assertEqual(err.code, protocol.INVALID_PARAMS, name)
        self.assertEqual(tree_ignoring_db(self.default), before)
        self.assertEqual(self.pointer(), (None, None))
        self.assertEqual(f.read_text(), "x")
        err = await self.rpc_error(self.api.storage_move(None, {}))
        self.assertEqual(err.code, protocol.INVALID_PARAMS)

    async def test_unwritable_target_is_refused(self):
        target = self.tmp / "ro"
        target.mkdir()
        with patch.object(relocate.tempfile, "mkstemp", side_effect=PermissionError("denied")):
            err = await self.rpc_error(self.api.storage_move(None, {"path": str(target)}))
        self.assertIn("cannot write", err.message)
        self.assertEqual(self.pointer(), (None, None))

    async def test_not_enough_space_is_refused(self):
        usage = shutil.disk_usage(self.tmp)
        with patch.object(relocate.shutil, "disk_usage", return_value=usage._replace(free=10)):
            err = await self.rpc_error(self.api.storage_move(None, {"path": str(self.tmp / "full")}))
        self.assertIn("free space", err.message)
        self.assertFalse((self.tmp / "full").exists())

    async def test_active_run_is_refused(self):
        target = self.tmp / "t"
        before = tree_ignoring_db(self.default)
        with patch.object(RunManager, "active", new_callable=PropertyMock, return_value=True):
            for method, params in (("storage_move", {"path": str(target)}), ("storage_move", {"path": str(target), "dry_run": True}),
                                   ("storage_reset", {})):
                err = await self.rpc_error(getattr(self.api, method)(None, params))
                self.assertEqual(err.code, protocol.RUN_ACTIVE, method)
        self.assertNothingChanged(target, before)

    async def test_runs_cannot_start_while_moving(self):
        self.api._moving = True
        err = await self.rpc_error(self.api.run_start(None, {"overrides": {}}))
        self.assertEqual(err.code, protocol.RUN_ACTIVE)
        self.api._moving = False

    async def test_fixed_locations_refuse_to_change(self):
        for loc in (None, Location(self.default, "arg", self.default), Location(self.default, "env", self.default)):
            api = self.make_api(loc)
            err = await self.rpc_error(api.storage_move(None, {"path": str(self.tmp / "t")}))
            self.assertEqual(err.code, protocol.INVALID_PARAMS)
            err = await self.rpc_error(api.storage_reset(None, {}))
            self.assertEqual(err.code, protocol.INVALID_PARAMS)
            info = api._storage_info()
            self.assertFalse(info["can_change"])
        self.assertEqual(self.pointer(), (None, None))
        self.assertFalse((self.tmp / "t").exists())

    # ------------------------------------------------------------------ failures: nothing changed
    async def test_copy_failure_leaves_everything_unchanged(self):
        target = self.tmp / "vol" / "data"
        before = tree_ignoring_db(self.default)
        real = shutil.copy2
        calls = []

        def flaky(src, dst, *a, **kw):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("disk full (simulated)")
            return real(src, dst, *a, **kw)

        with patch.object(relocate.shutil, "copy2", side_effect=flaky):
            err = await self.rpc_error(self.api.storage_move(None, {"path": str(target)}))
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(err.code, protocol.STORAGE_FAILED)
        self.assertIn("disk full", err.message)
        self.assertNothingChanged(target, before)
        self.assertEqual(counts(self.default / "data.db")["repos"], 3)
        self.assertIsNone(self.api._storage_info()["pending_restart"])
        # and it works once the disk problem is gone
        res = await self.api.storage_move(None, {"path": str(target)})
        self.assertEqual(res["mode"], "copy")

    async def test_verification_failure_leaves_everything_unchanged(self):
        target = self.tmp / "data2"
        before = tree_ignoring_db(self.default)
        real = relocate.table_counts
        seen = []

        def lying(db):
            out = dict(real(db))
            seen.append(db)
            if Path(db).parent.name.startswith("data2.moving"):
                out["repos"] += 1
            return out

        with patch.object(relocate, "table_counts", side_effect=lying):
            err = await self.rpc_error(self.api.storage_move(None, {"path": str(target)}))
        self.assertEqual(err.code, protocol.STORAGE_FAILED)
        self.assertIn("row counts differ in repos", err.message)
        self.assertNothingChanged(target, before)

    async def test_pointer_write_failure_rolls_back_the_new_folder(self):
        target = self.tmp / "data3"
        before = tree_ignoring_db(self.default)
        with patch.object(relocate, "write_location_pointer", side_effect=PermissionError("denied")):
            err = await self.rpc_error(self.api.storage_move(None, {"path": str(target)}))
        self.assertEqual(err.code, protocol.STORAGE_FAILED)
        self.assertNothingChanged(target, before)

    async def test_rollback_never_removes_a_folder_we_did_not_create(self):
        target = self.tmp / "theirs"
        target.mkdir()
        seed_store(target / "data.db", n=2)
        with patch.object(relocate, "write_location_pointer", side_effect=PermissionError("denied")):
            await self.rpc_error(self.api.storage_move(None, {"path": str(target)}))
        self.assertEqual(counts(target / "data.db")["repos"], 2)

    async def test_target_filled_by_somebody_else_during_the_copy_is_not_clobbered(self):
        target = self.tmp / "race"
        target.mkdir()
        real = relocate._verify_copy

        def rival(*a, **kw):
            real(*a, **kw)
            (target / "rival.txt").write_text("hi")

        with patch.object(relocate, "_verify_copy", side_effect=rival):
            err = await self.rpc_error(self.api.storage_move(None, {"path": str(target)}))
        self.assertEqual(err.code, protocol.STORAGE_FAILED)
        self.assertEqual(tree(target), {"rival.txt": b"hi"})
        self.assertEqual(self.pointer(), (None, None))
        self.assertEqual([p.name for p in self.tmp.iterdir() if ".moving-" in p.name], [])

    async def test_a_log_that_cannot_be_copied_is_only_a_warning(self):
        target = self.tmp / "nolog"
        real = relocate.shutil.copytree
        with patch.object(relocate.shutil, "copytree", side_effect=OSError("log is locked")):
            res = await self.api.storage_move(None, {"path": str(target)})
        self.assertTrue(any("logs were not copied" in w for w in res["warnings"]), res["warnings"])
        self.assertEqual(counts(target / "data.db")["repos"], 3)
        self.assertFalse((target / "logs").exists())
        self.assertIs(relocate.shutil.copytree, real)

    # ------------------------------------------------------------------ reset
    async def test_reset_removes_the_pointer_and_keeps_the_custom_data(self):
        target = self.tmp / "custom"
        await self.api.storage_move(None, {"path": str(target)})
        # "restart": the app now runs from the custom folder
        loc = resolve_data_dir()
        self.assertEqual(loc.source, "pointer")
        api2 = self.make_api(loc)
        info = api2._storage_info()
        self.assertEqual((info["source"], info["custom"], info["can_change"], info["default_has_data"]),
                         ("pointer", True, True, True))
        self.assertGreater(info["db_bytes"], 0)

        res = await api2.storage_reset(None, {})

        self.assertEqual((res["restart_required"], res["data_dir"], res["kept"]), (True, str(self.default), str(target)))
        self.assertEqual(self.pointer(), (None, None))
        self.assertFalse((self.default / POINTER_NAME).exists())
        self.assertEqual(counts(target / "data.db")["repos"], 3)                # custom data kept
        self.assertEqual(resolve_data_dir().source, "default")

    async def test_reset_without_a_custom_folder_is_refused(self):
        err = await self.rpc_error(self.api.storage_reset(None, {}))
        self.assertEqual(err.code, protocol.INVALID_PARAMS)

    async def test_reset_clears_a_dangling_pointer(self):
        write_location_pointer(self.default, self.tmp / "unplugged")
        loc = resolve_data_dir()
        self.assertEqual(loc.source, "default")
        api = self.make_api(loc)
        self.assertIn("does not exist", api._storage_info()["problem"])
        await api.storage_reset(None, {})
        self.assertEqual(self.pointer(), (None, None))

    async def test_moving_from_a_custom_folder_to_another_keeps_the_default_pointer_location(self):
        first = self.tmp / "first"
        await self.api.storage_move(None, {"path": str(first)})
        loc = resolve_data_dir()
        store2 = Store(first / "data.db")
        self.addCleanup(store2.close)
        manager2 = RunManager(store2, first / "config.json", seed_config=None)
        api2 = Api(manager=manager2, store=store2, relay=ScreenRelay(manager2, Hub()), data_dir=first,
                   db_path=first / "data.db", request_shutdown=lambda: None, location=loc)
        second = self.tmp / "second"
        await api2.storage_move(None, {"path": str(second)})
        await manager2.shutdown(5)
        self.assertEqual(self.pointer(), (second, None))                  # still in the DEFAULT dir
        self.assertFalse((first / POINTER_NAME).exists())
        self.assertEqual(counts(first / "data.db")["repos"], 3)
        # going "back" to the default folder by path is steered to the reset action
        err = await self.rpc_error(self.make_api(resolve_data_dir()).storage_move(None, {"path": str(self.default)}))
        self.assertIn("default location", err.message)


def tree_ignoring_db(root: Path) -> dict:
    """Files of `root` except data.db* (the store keeps them open and may fold the WAL), minus the pointer file."""
    return {k: v for k, v in tree(root).items() if not k.startswith("data.db") and k != POINTER_NAME}


# ---------------------------------------------------------------------- the real process
class RealSidecarTests(unittest.IsolatedAsyncioTestCase):
    """The packaged entry point with APPDATA redirected to a temp dir: resolution order and a full move/restart."""

    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bs-storage-proc-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.roaming = self.tmp / "Roaming"
        self.default = self.roaming / "BranchScrapper"
        self.procs: list[subprocess.Popen] = []
        self.pids: list[int] = []
        self.addCleanup(self._kill)

    def _kill(self):
        import psutil
        for pid in self.pids:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait(5)
            for f in (p.stdin, p.stdout):
                if f:
                    f.close()

    async def start(self, *flags, home=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GHOSTSCRAPER_")}
        env.update(GHOSTSCRAPER_TOKEN=TOKEN, APPDATA=str(self.roaming))
        if home:
            env["GHOSTSCRAPER_HOME"] = str(home)
        err = open(self.tmp / "stderr.txt", "ab")
        self.addCleanup(err.close)
        p = subprocess.Popen([sys.executable, "-m", "ghscraper.sidecar", *flags], cwd=ROOT, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
        self.procs.append(p)
        line = (await asyncio.wait_for(asyncio.to_thread(p.stdout.readline), 30)).decode()
        self.assertTrue(line.startswith("READY "), line + (self.tmp / "stderr.txt").read_text(errors="replace")[-1500:])
        info = json.loads(line[6:])
        self.pids.append(info["pid"])
        ws = await connect(f"ws://127.0.0.1:{info['port']}")
        await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
        return p, ws

    async def call(self, ws, method, **params):
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if msg.get("id") == 1:
                return msg

    async def stop(self, p, ws):
        await ws.close()
        p.stdin.close()
        await asyncio.wait_for(asyncio.to_thread(p.wait), 15)

    async def test_resolution_order_and_a_full_move_cycle(self):
        # existing data in the default folder
        self.default.mkdir(parents=True)
        seed_store(self.default / "data.db", n=4)

        # 1. default
        p, ws = await self.start()
        info = (await self.call(ws, "app.info"))["result"]
        self.assertEqual(Path(info["data_dir"]), self.default)
        self.assertEqual((info["storage"]["source"], info["storage"]["custom"], info["storage"]["can_change"]),
                         ("default", False, True))
        self.assertGreater(info["storage"]["db_bytes"], 0)

        # 2. move (dry run first)
        target = self.tmp / "elsewhere" / "BS"
        dry = (await self.call(ws, "storage.move", path=str(target), dry_run=True))["result"]
        self.assertEqual(dry["mode"], "copy")
        self.assertFalse(target.exists())
        moved = (await self.call(ws, "storage.move", path=str(target)))["result"]
        self.assertTrue(moved["restart_required"])
        self.assertEqual(moved["tables"]["repos"], 4)
        self.assertEqual((await self.call(ws, "app.info"))["result"]["storage"]["pending_restart"], str(target))
        await self.stop(p, ws)

        # 3. restart: the pointer is honoured, the default folder is only the pointer's home
        p, ws = await self.start()
        info = (await self.call(ws, "app.info"))["result"]
        self.assertEqual(Path(info["data_dir"]), target)
        self.assertEqual((info["storage"]["source"], info["storage"]["custom"]), ("pointer", True))
        self.assertEqual((await self.call(ws, "data.search", limit=1))["result"]["total"], 4)
        self.assertEqual(counts(self.default / "data.db")["repos"], 4)              # old data kept

        # 4. an explicit --data-dir / GHOSTSCRAPER_HOME beats the pointer and freezes the setting
        other = self.tmp / "explicit"
        p2, ws2 = await self.start("--data-dir", str(other))
        info2 = (await self.call(ws2, "app.info"))["result"]
        self.assertEqual((Path(info2["data_dir"]), info2["storage"]["source"], info2["storage"]["can_change"]),
                         (other, "arg", False))
        err = (await self.call(ws2, "storage.move", path=str(self.tmp / "nope")))["error"]
        self.assertEqual(err["code"], protocol.INVALID_PARAMS)
        await self.stop(p2, ws2)
        home = self.tmp / "homeenv"
        p3, ws3 = await self.start(home=home)
        info3 = (await self.call(ws3, "app.info"))["result"]
        self.assertEqual((Path(info3["data_dir"]), info3["storage"]["source"]), (home, "env"))
        await self.stop(p3, ws3)
        self.assertEqual(read_location_pointer(self.default), (target, None))        # pointer untouched by all that

        # 5. reset
        reset = (await self.call(ws, "storage.reset"))["result"]
        self.assertEqual((reset["data_dir"], reset["kept"]), (str(self.default), str(target)))
        await self.stop(p, ws)
        p, ws = await self.start()
        info = (await self.call(ws, "app.info"))["result"]
        self.assertEqual((Path(info["data_dir"]), info["storage"]["source"]), (self.default, "default"))
        self.assertEqual(counts(target / "data.db")["repos"], 4)
        await self.stop(p, ws)

    async def test_dangling_pointer_falls_back_to_the_default(self):
        self.default.mkdir(parents=True)
        write_location_pointer(self.default, self.tmp / "unplugged-drive")
        p, ws = await self.start()
        info = (await self.call(ws, "app.info"))["result"]
        self.assertEqual((Path(info["data_dir"]), info["storage"]["source"]), (self.default, "default"))
        self.assertIn("does not exist", info["storage"]["problem"])
        await self.stop(p, ws)
        self.assertFalse((self.tmp / "unplugged-drive").exists())


if __name__ == "__main__":
    unittest.main()
