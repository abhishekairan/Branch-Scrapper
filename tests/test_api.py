"""Sidecar API tests: real WebSocket server + client in-process, fake browser pool, in-memory store.

Plus subprocess tests of `python -m ghscraper.sidecar` (stdin EOF, app.shutdown, token checks).
"""
import asyncio
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ghscraper import exporters
from ghscraper.api import protocol
from ghscraper.api.methods import Api
from ghscraper.api.screen import ScreenRelay
from ghscraper.api.server import ApiServer, Client, Hub, origin_allowed
from ghscraper.api.testing import fake_pool_factory
from ghscraper.core.runmanager import RunManager
from ghscraper.store import Store

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "k" * 40

RUN = {"queries": ["a", "b"], "max_results": 10, "browsers": 2, "human_search": False,
       "fetch_manifests": False, "min_delay": 0, "max_delay": 0}


async def until(cond, timeout=10.0, what="condition"):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


class Rpc:
    """Minimal test client: sorts incoming messages into responses, events and binary frames."""

    def __init__(self, ws):
        self.ws = ws
        self.events: list[dict] = []
        self.frames: list[bytes] = []
        self.closed: ConnectionClosed | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._id = 0
        self._task = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, port, token=TOKEN, origin=None):
        ws = await connect(f"ws://127.0.0.1:{port}", origin=origin)
        await ws.send(json.dumps({"type": "auth", "token": token}))
        rpc = cls(ws)
        await rpc.event("auth.ok")
        return rpc

    async def _read(self):
        try:
            async for m in self.ws:
                if isinstance(m, bytes):
                    self.frames.append(m)
                    continue
                obj = json.loads(m)
                fut = self._pending.pop(obj.get("id"), None) if "method" not in obj else None
                if fut is not None:
                    fut.set_result(obj)
                else:
                    self.events.append(obj)
        except ConnectionClosed as e:
            self.closed = e
        finally:
            for f in self._pending.values():
                if not f.done():
                    f.cancel()

    async def raw(self, method, params=None, **kw):
        self._id += 1
        fut = self._pending[self._id] = asyncio.get_running_loop().create_future()
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, 15)

    async def ok(self, method, **params):
        r = await self.raw(method, params)
        assert "error" not in r, r
        return r["result"]

    async def err(self, method, **params):
        r = await self.raw(method, params)
        assert "error" in r, r
        return r["error"]

    async def event(self, name, pred=None, timeout=15.0, after=0):
        end = asyncio.get_running_loop().time() + timeout
        while True:
            for i in range(after, len(self.events)):
                e = self.events[i]
                if e["method"] == name and (pred is None or pred(e["params"])):
                    return e
            if asyncio.get_running_loop().time() > end:
                raise AssertionError(f"timed out waiting for event {name}; got "
                                     f"{[e['method'] for e in self.events][-12:]}")
            await asyncio.sleep(0.01)

    async def close(self):
        await self.ws.close()
        await asyncio.gather(self._task, return_exceptions=True)


class ApiTestCase(unittest.IsolatedAsyncioTestCase):
    delay = 0.02
    pids = None
    stats_interval = 0.1

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(":memory:")
        self.factory = fake_pool_factory(self.delay, self.pids)
        self.manager = RunManager(self.store, self.tmp / "config.json", pool_factory=self.factory,
                                  stats_interval=self.stats_interval, seed_config=None)
        self.hub = Hub()
        self.relay = ScreenRelay(self.manager, self.hub)
        self.manager.listeners.append(self.hub.broadcast)
        self.shutdown_requested = asyncio.Event()
        self.api = Api(manager=self.manager, store=self.store, relay=self.relay, data_dir=self.tmp,
                       db_path=self.tmp / "data.db", request_shutdown=self.shutdown_requested.set)
        self.server = ApiServer(self.hub, self.api, TOKEN, auth_timeout=0.4)
        await self.server.start()
        self.clients: list[Rpc] = []

    async def asyncTearDown(self):
        await self.manager.shutdown(5)
        await self.server.close()
        self.store.close()

    async def client(self, **kw) -> Rpc:
        c = await Rpc.open(self.server.port, **kw)
        self.clients.append(c)
        return c

    async def run_to_end(self, c: Rpc, overrides=None) -> dict:
        n = len(c.events)
        await c.ok("run.start", overrides={**RUN, **(overrides or {})})
        return (await c.event("run.finished", after=n))["params"]

    def slot(self, i):
        return self.factory.pools[-1].slots[i]


class AuthTests(ApiTestCase):
    async def _expect_4401(self, send):
        ws = await connect(f"ws://127.0.0.1:{self.server.port}")
        if send is not None:
            await ws.send(send)
        with self.assertRaises(ConnectionClosed) as cm:
            await ws.recv()
        self.assertEqual(cm.exception.rcvd.code, protocol.CLOSE_AUTH)

    async def test_valid_token(self):
        c = await self.client()
        info = await c.ok("app.info")
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(info["migration"], "none")

    async def test_wrong_token(self):
        await self._expect_4401(json.dumps({"type": "auth", "token": "x" * 40}))

    async def test_first_message_must_be_auth(self):
        await self._expect_4401(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "app.info"}))
        await self._expect_4401("not json")
        await self._expect_4401(json.dumps({"type": "auth", "token": 5}))
        await self._expect_4401(b"\x00binary")

    async def test_auth_timeout(self):
        await self._expect_4401(None)       # sends nothing; server gives up after 0.4 s

    async def test_bad_origin_rejected(self):
        for origin in ("https://evil.example", "http://localhost", "http://localhost.evil.com:80",
                       "http://tauri.localhost.evil.com", "tauri://localhost.evil.com", "null"):
            with self.assertRaises(InvalidStatus, msg=origin) as cm:
                await connect(f"ws://127.0.0.1:{self.server.port}", origin=origin)
            self.assertEqual(cm.exception.response.status_code, 403)

    async def test_good_origins_accepted(self):
        for origin in ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                       "http://localhost:1420", None):
            c = await self.client(origin=origin)
            self.assertIn("version", await c.ok("app.info"))
        self.assertTrue(origin_allowed("http://localhost:5173"))
        self.assertFalse(origin_allowed("http://127.0.0.1:5173"))

    async def test_short_token_refused(self):
        with self.assertRaises(ValueError):
            ApiServer(self.hub, self.api, "short")

    async def test_oversized_message_closes_connection(self):
        c = await self.client()
        await c.ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "app.info",
                                    "params": {"pad": "x" * (2 * 1024 * 1024)}}))
        await asyncio.wait_for(c._task, 5)
        self.assertEqual(c.closed.rcvd.code, 1009)

    async def test_events_reach_every_client(self):
        a, b = await self.client(), await self.client()
        await self.run_to_end(a)
        await b.event("run.finished")
        self.assertTrue(any(e["method"] == "repo.saved" for e in b.events))


class JsonRpcTests(ApiTestCase):
    async def test_error_codes(self):
        c = await self.client()
        self.assertEqual((await c.err("nope.nothing"))["code"], protocol.METHOD_NOT_FOUND)
        self.assertEqual((await c.err("worker.pause", index="x"))["code"], protocol.INVALID_PARAMS)
        self.assertEqual((await c.err("worker.pause"))["code"], protocol.INVALID_PARAMS)
        self.assertEqual((await c.err("worker.pause", index=0))["code"], protocol.NO_RUN)
        self.assertEqual((await c.err("data.search", filters={"bogus": 1}))["code"], protocol.INVALID_PARAMS)
        self.assertEqual((await c.err("data.search", limit=10 ** 6))["code"], protocol.INVALID_PARAMS)
        self.assertEqual((await c.err("data.get", full_name="no/such"))["code"], protocol.NOT_FOUND)
        r = await c.raw("app.info", [1, 2])
        self.assertEqual(r["error"]["code"], protocol.INVALID_PARAMS)

    async def test_malformed_messages(self):
        c = await self.client()
        await c.ws.send("{not json")
        await until(lambda: any(x.get("error", {}).get("code") == protocol.PARSE_ERROR for x in c.events),
                    what="parse error")
        await c.ws.send(json.dumps({"jsonrpc": "1.0", "id": 7, "method": "app.info"}))
        await until(lambda: any(x.get("error", {}).get("code") == protocol.INVALID_REQUEST for x in c.events),
                    what="invalid request")
        # a notification (no id) gets no reply and doesn't break the connection
        await c.ws.send(json.dumps({"jsonrpc": "2.0", "method": "app.info"}))
        self.assertIn("pid", await c.ok("app.info"))

    async def test_app_shutdown_replies_then_signals(self):
        c = await self.client()
        self.assertEqual(await c.ok("app.shutdown"), {"ok": True})
        await asyncio.wait_for(self.shutdown_requested.wait(), 3)


class BrowserCheckTests(ApiTestCase):
    """app.info.browser: the first-run "is a browser installed?" report."""

    async def test_app_info_reports_browser(self):
        from unittest import mock
        c = await self.client()
        found = {"chrome": None, "msedge": r"C:\edge\msedge.exe", "brave": None}
        with mock.patch("ghscraper.browser.detect.find_browser", side_effect=found.get):
            b = (await c.ok("app.info"))["browser"]
        self.assertEqual(b["selected"], "chrome")             # the default config
        self.assertFalse(b["available"])
        self.assertIsNone(b["path"])
        self.assertEqual(b["found"], found)
        await c.ok("config.update", values={"browser": "msedge"})
        with mock.patch("ghscraper.browser.detect.find_browser", side_effect=found.get):
            b = (await c.ok("app.info"))["browser"]
        self.assertTrue(b["available"])
        self.assertEqual(b["path"], r"C:\edge\msedge.exe")

    async def test_explicit_browser_path(self):
        from ghscraper.browser.detect import browser_status
        from unittest import mock
        exe = self.tmp / "mybrowser.exe"
        exe.write_text("x")
        with mock.patch("ghscraper.browser.detect.find_browser", return_value=None):
            ok = browser_status("chrome", str(exe))
            missing = browser_status("chrome", str(self.tmp / "nope.exe"))
        self.assertTrue(ok["available"] and ok["custom_path"])
        self.assertEqual(ok["path"], str(exe))
        self.assertFalse(missing["available"])


class ConfigTests(ApiTestCase):
    async def test_get_update_persist(self):
        c = await self.client()
        got = await c.ok("config.get")
        self.assertEqual(got["config"]["browsers"], 2)
        self.assertEqual(got["meta"]["max_browsers"], 5)
        self.assertTrue(any(f["key"] == "stars" for f in got["meta"]["filters"]))
        res = await c.ok("config.update", values={"queries": ["x y", "z"], "max_results": 5,
                                                  "filters": {"stars": ">=100"}})
        self.assertEqual(res["config"]["queries"], ["x y", "z"])
        on_disk = json.loads((self.tmp / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["max_results"], 5)
        self.assertEqual(on_disk["filters"]["stars"], ">=100")

    async def test_update_rejects_bad_values(self):
        c = await self.client()
        e = await c.err("config.update", values={"browsers": 99, "sort": "nope", "mystery": 1,
                                                  "filters": {"stars": "abc"}})
        self.assertEqual(e["code"], protocol.INVALID_PARAMS)
        fields = {x["field"] for x in e["data"]["errors"]}
        self.assertEqual(fields, {"browsers", "sort", "mystery", "filters.stars"})
        self.assertEqual((await c.ok("config.get"))["config"]["browsers"], 2)   # nothing applied

    async def test_validate(self):
        c = await self.client()
        r = await c.ok("config.validate", overrides={"queries": ["web scraper", "topc:foo bar"],
                                                      "filters": {"stars": ">100", "language": ["python"]}})
        self.assertTrue(r["ok"])
        self.assertEqual(r["queries"][0]["github_query"], "web scraper language:python stars:>100")
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("topic:", r["warnings"][0]["message"])
        self.assertEqual(r["queries"][1]["warnings"], [r["warnings"][0]["message"]])
        self.assertEqual(r["estimate"]["workers"], 2)
        self.assertGreater(r["estimate"]["pages"], 0)
        self.assertIn("stars", r["filters_described"])
        bad = await c.ok("config.validate", overrides={"queries": [], "min_delay": 5, "max_delay": 1})
        self.assertFalse(bad["ok"])
        self.assertEqual({e["field"] for e in bad["errors"]}, {"queries", "max_delay"})


class RunTests(ApiTestCase):
    async def test_full_run(self):
        c = await self.client()
        cfg = await c.ok("config.get")
        self.assertEqual(cfg["config"]["queries"], ["web scraper"])
        started = await c.ok("run.start", overrides=RUN)
        self.assertEqual(started["workers"], 2)
        self.assertEqual((await c.err("run.start", overrides=RUN))["code"], protocol.RUN_ACTIVE)
        fin = (await c.event("run.finished"))["params"]
        self.assertEqual(fin["status"], "done")
        self.assertEqual(fin["counts"]["repos"], 10)
        self.assertEqual(fin["counts"]["queries_done"], 2)

        names = [e["method"] for e in c.events]
        self.assertEqual(names[names.index("run.started")], "run.started")
        first_saved = names.index("repo.saved")
        self.assertLess(names.index("run.started"), first_saved)
        self.assertGreater(names.index("run.finished"), max(i for i, n in enumerate(names) if n == "repo.saved"))
        self.assertEqual(names.count("repo.saved"), 10)
        self.assertEqual(names[-1], "run.finished")
        saved = [e["params"] for e in c.events if e["method"] == "repo.saved"]
        self.assertTrue(all(s["full_name"] and "stars" in s for s in saved))

        prog = [e["params"] for e in c.events if e["method"] == "run.progress"]
        self.assertEqual(prog[-1]["repos_done"], 10)
        self.assertEqual(prog[-1]["repos_total"], 10)
        self.assertEqual(prog[-1]["queries_total"], 2)
        self.assertTrue(all(p["repos_done"] <= p["repos_total"] for p in prog))

        statuses = [e["params"] for e in c.events if e["method"] == "worker.status"]
        self.assertTrue(statuses)
        for s in statuses:
            self.assertNotIn("[", s["text"].replace("[B", ""))
            self.assertIn(s["state"], {"starting", "searching", "repo", "idle", "paused", "recovering",
                                       "done", "stopped", "failed", "other"})
        self.assertIn("done", {s["state"] for s in statuses})

        logs = [e["params"] for e in c.events if e["method"] == "log"]
        self.assertTrue(logs)
        self.assertTrue(all(set(l) == {"level", "text"} and "[/]" not in l["text"] and "[bold]" not in l["text"]
                            for l in logs))
        self.assertTrue(any(l["text"].startswith("Launching 2 browser") for l in logs))

        # the rows are in the store and searchable
        res = await c.ok("data.search", filters={"text": ""}, limit=5, columns=["full_name", "stars"])
        self.assertEqual(res["total"], 10)
        self.assertEqual(len(res["rows"]), 5)
        self.assertEqual(set(res["rows"][0]), {"full_name", "stars"})
        name = saved[0]["full_name"]
        rec = await c.ok("data.get", full_name=name)
        self.assertEqual(rec["full_name"], name)
        self.assertEqual(sorted(rec["matched_queries"]), ["a", "b"])
        runs = (await c.ok("runs.list"))["runs"]
        self.assertEqual((runs[0]["status"], runs[0]["counts"]["repos"]), ("done", 10))
        self.assertEqual(runs[0]["config"]["queries"], ["a", "b"])
        status = await c.ok("run.status")
        self.assertFalse(status["active"])
        self.assertEqual(status["last"]["status"], "done")
        facets = await c.ok("data.facets")
        self.assertIn("languages", facets)

        # a second run is allowed after the first one ended
        self.assertEqual((await self.run_to_end(c))["status"], "done")
        self.assertEqual(self.store.count_repos(), 10)       # latest scrape wins: still one row each
        pool = self.factory.pools[0]
        self.assertTrue(pool.started and pool.closed)

    async def test_start_validation(self):
        c = await self.client()
        e = await c.err("run.start", overrides={"queries": [], "browsers": 0, "filters": {"stars": "x"}})
        self.assertEqual(e["code"], protocol.INVALID_PARAMS)
        self.assertEqual({x["field"] for x in e["data"]["errors"]}, {"browsers", "filters.stars"})
        e = await c.err("run.start", overrides={"queries": []})
        self.assertEqual(e["data"]["errors"][0]["field"], "queries")
        self.assertEqual(self.store.list_runs(), [])        # nothing started

    async def test_preview_counts(self):
        c = await self.client()
        r = await c.ok("run.preview_counts", overrides={"queries": ["a", "b"], "filters": {"stars": ">5"}})
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["totals"], {"a": 66499, "b": 66499})
        self.assertEqual(r["queries"][0]["github_query"], "a stars:>5")
        self.assertTrue(r["queries"][0]["over_cap"])
        self.assertEqual(self.store.list_runs(), [])
        self.assertEqual(self.store.count_repos(), 0)
        fin = [e["params"] for e in c.events if e["method"] == "run.finished"][-1]
        self.assertEqual((fin["kind"], fin["run_id"]), ("preview", None))
        self.assertFalse(any(e["method"] == "repo.saved" for e in c.events))


class SlowRunTests(ApiTestCase):
    delay = 0.25

    async def test_stop(self):
        c = await self.client()
        self.assertEqual(await c.ok("run.stop"), {"stopping": False})
        await c.ok("run.start", overrides=RUN)
        await c.event("worker.status", lambda p: p["state"] in ("searching", "repo"))
        self.assertTrue((await c.ok("run.stop"))["stopping"])
        fin = (await c.event("run.finished"))["params"]
        self.assertEqual(fin["status"], "stopped")
        self.assertEqual(self.store.list_runs()[0]["status"], "stopped")

    async def test_pause_resume_and_input_gate(self):
        c = await self.client()
        await c.ok("run.start", overrides={**RUN, "browsers": 1})
        await c.event("worker.status", lambda p: p["state"] in ("searching", "repo"))
        ev = {"type": "mouse", "action": "move", "x": 5, "y": 6}
        self.assertEqual((await c.err("browser.input", index=0, event=ev))["code"], protocol.NOT_PAUSED)
        self.assertEqual((await c.err("browser.input", index=0, event={"type": "zzz"}))["code"],
                         protocol.INVALID_PARAMS)
        self.assertEqual((await c.err("worker.pause", index=3))["code"], protocol.BAD_WORKER)
        r = await c.ok("worker.pause", index=0)
        self.assertEqual((r["paused"], r["engaged"]), (True, False))
        # paused by the user, but still finishing its page: input is still refused
        self.assertEqual((await c.err("browser.input", index=0, event=ev))["code"], protocol.NOT_PAUSED)
        await c.event("worker.status", lambda p: p["state"] == "paused")
        self.assertEqual(await c.ok("browser.input", index=0, event=ev), {"ok": True})
        self.assertIn(("input", ev), self.slot(0).calls)
        st = await c.ok("run.status")
        self.assertEqual((st["workers"][0]["state"], st["workers"][0]["paused"]), ("paused", True))
        self.assertEqual((await c.ok("worker.resume", index=0))["paused"], False)
        self.assertEqual((await c.err("browser.input", index=0, event=ev))["code"], protocol.NOT_PAUSED)
        await c.ok("run.stop")
        self.assertEqual((await c.event("run.finished"))["params"]["status"], "stopped")

    async def test_stop_while_paused(self):
        c = await self.client()
        await c.ok("run.start", overrides={**RUN, "browsers": 1})
        await c.ok("worker.pause", index=0)
        await c.event("worker.status", lambda p: p["state"] == "paused")
        await c.ok("run.stop")
        self.assertEqual((await c.event("run.finished"))["params"]["status"], "stopped")

    async def test_view_starts_and_stops_the_right_slots(self):
        c = await self.client()
        await c.ok("run.start", overrides=RUN)
        await until(lambda: self.factory.pools and len(self.factory.pools[-1].slots) == 2
                    and len(self.manager.slots) == 2, what="two slots")
        r = await c.ok("browser.view", indices=[0], fps=5, max_width=320)
        self.assertEqual(r, {"active": [0], "pending": []})
        self.assertEqual(self.slot(0).calls, [("start", 5, 320)])
        self.assertEqual(self.slot(1).calls, [])
        r = await c.ok("browser.view", indices=[1], fps=5, max_width=320)
        self.assertEqual(r, {"active": [1], "pending": []})
        self.assertEqual(self.slot(0).calls, [("start", 5, 320), ("stop",)])
        self.assertEqual(self.slot(1).calls, [("start", 5, 320)])
        await c.ok("browser.view", indices=[1], fps=10, max_width=640)       # parameter change restarts
        self.assertEqual(self.slot(1).calls[-1], ("start", 10, 640))
        await c.ok("browser.view", indices=[])
        self.assertEqual(self.slot(1).calls[-1], ("stop",))
        self.assertFalse(self.slot(0).casting or self.slot(1).casting)
        self.assertEqual((await c.err("browser.view", indices=[9]))["code"], protocol.INVALID_PARAMS)
        self.assertEqual((await c.err("browser.view", indices=[0], fps=0))["code"], protocol.INVALID_PARAMS)
        await c.ok("run.stop")
        await c.event("run.finished")

    async def test_view_before_slot_exists_starts_when_it_appears(self):
        c = await self.client()
        await c.ok("run.start", overrides=RUN)
        r = await c.ok("browser.view", indices=[0])
        self.assertIn(r["active"] + r["pending"], ([0],))
        await until(lambda: self.manager.slots.get(0) is not None and self.slot(0).casting,
                    what="slot 0 to start casting")
        await c.ok("run.stop")
        await c.event("run.finished")

    async def test_frames_are_packed_and_routed(self):
        c = await self.client()
        await c.ok("run.start", overrides=RUN)
        await until(lambda: len(self.manager.slots) == 2, what="slots")
        await c.ok("browser.view", indices=[0], fps=5, max_width=320)
        jpeg = b"\xff\xd8fake-jpeg\xff\xd9"
        self.slot(0).emit_frame(jpeg, 320, 200)
        await until(lambda: len(c.frames) == 1, what="a frame")
        f = c.frames[0]
        self.assertEqual(f[:15], bytes([1]) + (0).to_bytes(2, "big") + (1).to_bytes(4, "big")
                         + (320).to_bytes(4, "big") + (200).to_bytes(4, "big"))
        self.assertEqual(f[15:], jpeg)
        self.assertEqual(protocol.unpack_frame(f)["seq"], 1)
        self.slot(1).emit_frame(b"other")                       # not viewed: never sent
        for _ in range(50):                                     # burst with no await in between
            self.slot(0).emit_frame(jpeg, 320, 200)
        await until(lambda: protocol.unpack_frame(c.frames[-1])["seq"] == 51, what="last frame")
        self.assertLessEqual(len(c.frames), 51)
        self.assertTrue(all(protocol.unpack_frame(x)["index"] == 0 for x in c.frames))
        seqs = [protocol.unpack_frame(x)["seq"] for x in c.frames]
        self.assertEqual(seqs, sorted(seqs))
        await c.ok("run.stop")
        await c.event("run.finished")

    async def test_disconnect_stops_streams(self):
        c = await self.client()
        d = await self.client()
        await c.ok("run.start", overrides=RUN)
        await until(lambda: len(self.manager.slots) == 2, what="slots")
        await c.ok("browser.view", indices=[0])
        await d.ok("browser.view", indices=[0, 1])
        await until(lambda: self.slot(0).casting and self.slot(1).casting)
        await d.close()
        await until(lambda: not self.slot(1).casting, what="slot 1 to stop")
        self.assertTrue(self.slot(0).casting)                    # c still watches it
        await c.close()
        await until(lambda: not self.slot(0).casting, what="slot 0 to stop")
        self.manager.stop()


class LatestWinsTests(unittest.IsolatedAsyncioTestCase):
    async def test_frames_replace_json_never_dropped(self):
        gate = asyncio.Event()
        sent = []

        async def send(m):
            await gate.wait()
            sent.append(m)

        c = Client(send)
        c.start()
        c.send_json("e1")
        c.offer_frame(0, b"f1")
        await asyncio.sleep(0.05)               # the writer is now stuck sending e1
        c.offer_frame(0, b"f2")
        c.offer_frame(0, b"f3")
        c.offer_frame(1, b"g1")
        c.send_json("e2")
        c.send_json("e3")
        gate.set()
        await until(lambda: len(sent) == 5, what="all messages")
        self.assertEqual(sent[:3], ["e1", "e2", "e3"])           # JSON first, in order, none lost
        self.assertEqual(sorted(sent[3:]), [b"f3", b"g1"])       # only the newest frame per slot
        self.assertEqual(c.dropped_frames, 2)
        await c.aclose()


class DataTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.c = await self.client()
        self.assertEqual((await self.run_to_end(self.c))["status"], "done")

    async def test_export_csv_and_json(self):
        c = self.c
        csv_path = self.tmp / "out" / "repos.csv"
        r = await c.ok("export.write", filters={}, format="csv", path=str(csv_path),
                       columns=["full_name", "stars", "topics"], sort=[["stars", "desc"], ["full_name", "asc"]])
        self.assertEqual((r["rows"], r["format"]), (10, "csv"))
        raw = csv_path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 10)
        self.assertEqual(list(rows[0]), ["full_name", "stars", "topics"])
        stars = [int(x["stars"]) for x in rows]
        self.assertEqual(stars, sorted(stars, reverse=True))

        json_path = self.tmp / "repos.json"
        r = await c.ok("export.write", run_id=self.store.list_runs()[0]["id"], format="json",
                       path=str(json_path))
        self.assertEqual(r["rows"], 10)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 10)
        self.assertIn("matched_queries", data[0])

        names = [d["full_name"] for d in data][:3][::-1]
        p3 = self.tmp / "three.json"
        r = await c.ok("export.write", full_names=names, format="json", path=str(p3), columns=["full_name"])
        self.assertEqual(json.loads(p3.read_text(encoding="utf-8")), [{"full_name": n} for n in names])
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("*.part")), [])

        p0 = self.tmp / "none.json"
        r = await c.ok("export.write", filters={"stars": {"min": 10 ** 12}}, format="json", path=str(p0))
        self.assertEqual((r["rows"], p0.read_text(encoding="utf-8")), (0, "[]"))
        p1 = self.tmp / "none.csv"
        self.assertEqual((await c.ok("export.write", filters={"stars": {"min": 10 ** 12}}, format="csv",
                                     path=str(p1)))["rows"], 0)

    async def test_export_rejects_bad_requests(self):
        c = self.c
        ok = str(self.tmp / "x.csv")
        for params in (
            dict(filters={}, format="csv", path="relative.csv"),
            dict(filters={}, format="csv", path=str(self.tmp / "x.json")),
            dict(filters={}, format="xlsx", path=ok),
            dict(format="csv", path=ok),
            dict(filters={}, run_id=1, format="csv", path=ok),
            dict(filters={"bogus": 1}, format="csv", path=ok),
            dict(filters={}, format="csv", path=ok, columns=["nope"]),
            dict(filters={}, format="csv", path=str(self.tmp)),
        ):
            self.assertEqual((await c.err("export.write", **params))["code"], protocol.INVALID_PARAMS, params)
        self.assertFalse((self.tmp / "x.csv").exists())

    async def test_delete_datasets_import(self):
        c = self.c
        name = (await c.ok("data.search", limit=1))["rows"][0]["full_name"]
        self.assertEqual(await c.ok("data.delete", full_names=[name, "no/such"]), {"deleted": 1})
        self.assertEqual((await c.ok("data.search"))["total"], 9)

        saved = await c.ok("datasets.save", name="big", filters={"stars": {"min": 1}},
                           columns=["full_name"], sort=[["stars", "desc"]])
        ds = (await c.ok("datasets.list"))["datasets"]
        self.assertEqual([d["name"] for d in ds], ["big"])
        self.assertEqual(ds[0]["id"], saved["id"])
        self.assertEqual((await c.err("datasets.save", name="bad", filters={"zzz": 1}))["code"],
                         protocol.INVALID_PARAMS)
        self.assertEqual(await c.ok("datasets.delete", id=saved["id"]), {"deleted": True})

        f = self.tmp / "old.json"
        f.write_text(json.dumps([{"full_name": "o/one", "stars": 3}, {"full_name": "o/two", "stars": 4}]),
                     encoding="utf-8")
        res = await c.ok("data.import_json", paths=[str(f), str(self.tmp / "missing.json")])
        self.assertEqual(res["total_repos"], 2)
        self.assertEqual(len(res["skipped"]), 1)
        self.assertEqual((await c.ok("data.search"))["total"], 11)
        self.assertEqual((await c.err("data.import_json", paths=["rel.json"]))["code"], protocol.INVALID_PARAMS)


class StatsTests(ApiTestCase):
    delay = 0.2
    pids = [os.getpid()]

    async def test_pool_stats(self):
        c = await self.client()
        await c.ok("run.start", overrides=RUN)
        ev = await c.event("pool.stats", timeout=10)
        p = ev["params"]
        self.assertEqual((p["chrome_count"], p["process_count"]), (1, 1))
        self.assertGreater(p["total_rss"], 0)
        self.assertEqual([s["index"] for s in p["slots"]], [0, 1])
        self.assertEqual(p["chromes"][0]["pid"], os.getpid())
        await c.ok("run.stop")
        await c.event("run.finished")
        n = len([e for e in c.events if e["method"] == "pool.stats"])
        await asyncio.sleep(0.4)
        self.assertEqual(n, len([e for e in c.events if e["method"] == "pool.stats"]))  # loop ended with the run


class ProtocolHelperTests(unittest.TestCase):
    def test_strip_markup_and_levels(self):
        self.assertEqual(protocol.plain_log("[yellow][B1] browser slot crashed[/]"),
                         ("warning", "[B1] browser slot crashed"))
        self.assertEqual(protocol.plain_log("[red]Failed: x[/]")[0], "error")
        self.assertEqual(protocol.plain_log("Launching [bold]2[/] browser(s)"),
                         ("info", "Launching 2 browser(s)"))
        self.assertEqual(protocol.plain_log("[dim]idle[/]"), ("debug", "idle"))
        self.assertEqual(protocol.strip_markup(r"keep \[bold] literal"), "keep [bold] literal")
        self.assertEqual(protocol.strip_markup("[#ff0000]x[/#ff0000]"), "x")

    def test_worker_states(self):
        for text, state in (("starting browser...", "starting"), ("searching web scraper", "searching"),
                            ("repo a/b", "repo"), ("idle (waiting for searches)", "idle"),
                            ("paused (you have control)", "paused"), ("recovering browser...", "recovering"),
                            ("done", "done"), ("stopped", "stopped"), ("failed to start: X", "failed"),
                            ("???", "other")):
            self.assertEqual(protocol.worker_state(text), state)

    def test_frame_roundtrip(self):
        data = protocol.pack_frame(3, 2 ** 32 + 5, 1280, 720, b"JPEG")
        self.assertEqual(protocol.FRAME_HEADER_SIZE, 15)
        self.assertEqual(protocol.unpack_frame(data), {"version": 1, "index": 3, "seq": 5, "width": 1280,
                                                       "height": 720, "jpeg": b"JPEG"})


class ExportStreamingTests(unittest.TestCase):
    def test_streamed_json_identical_to_list_json(self):
        rows = [{"a": 1, "b": "é\n", "c": [1, {"d": None}]}, {"a": 2, "b": "x", "c": []}]
        with tempfile.TemporaryDirectory() as d:
            for cols in (None, ["a", "c"]):
                p1, p2 = Path(d, "list.json"), Path(d, "gen.json")
                exporters.export_rows(rows, p1, "json", cols)
                exporters.export_rows((r for r in rows), p2, "json", cols)
                self.assertEqual(p1.read_bytes(), p2.read_bytes())
            exporters.export_rows(iter([]), p2, "json")
            exporters.export_rows([], p1, "json")
            self.assertEqual(p1.read_bytes(), p2.read_bytes())


# ------------------------------------------------------------------ the real entry point
def _env(token=TOKEN, fake=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GHOSTSCRAPER_")}
    if token is not None:
        env["GHOSTSCRAPER_TOKEN"] = token
    if fake:
        env["GHOSTSCRAPER_FAKE_POOL"] = "1"
    return env


class SidecarProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.procs: list[subprocess.Popen] = []
        self.real_pids: list[int] = []
        self.addCleanup(self._kill)

    def _kill(self):
        import psutil
        for pid in self.real_pids:
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

    async def spawn(self, *flags, env=None):
        err = open(self.tmp / "stderr.txt", "ab")
        self.addCleanup(err.close)
        p = subprocess.Popen([sys.executable, "-m", "ghscraper.sidecar", "--data-dir", str(self.tmp / "data"),
                              *flags], cwd=ROOT, env=env or _env(), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=err)
        self.procs.append(p)
        return p

    async def ready(self, p) -> dict:
        line = await asyncio.wait_for(asyncio.to_thread(p.stdout.readline), 30)
        text = line.decode("utf-8")
        m = re.fullmatch(r'READY (\{"port": \d+, "pid": \d+, "version": "[^"]+"\})\r?\n', text)
        self.assertIsNotNone(m, f"bad READY line {text!r}; stderr: "
                                f"{(self.tmp / 'stderr.txt').read_text(errors='replace')[-2000:]}")
        info = json.loads(m.group(1))
        # On Windows the venv's python.exe is a launcher that spawns the real interpreter, so
        # READY's pid is the child's and need not equal Popen's.
        self.real_pids.append(info["pid"])
        return info

    async def wait_exit(self, p, timeout=10) -> int:
        return await asyncio.wait_for(asyncio.to_thread(p.wait), timeout)

    async def test_stdin_eof_shuts_down_cleanly(self):
        p = await self.spawn("--test-mode")
        info = await self.ready(p)
        c = await Rpc.open(info["port"])
        app = await c.ok("app.info")
        self.assertTrue(app["test_mode"])
        self.assertEqual(Path(app["db_path"]), self.tmp / "data" / "data.db")
        # a whole run through the real entry point, with the fake pool
        await c.ok("run.start", overrides=RUN)
        fin = (await c.event("run.finished"))["params"]
        self.assertEqual((fin["status"], fin["counts"]["repos"]), ("done", 10))
        self.assertEqual((await c.ok("data.search"))["total"], 10)
        self.assertTrue((self.tmp / "data" / "config.json").exists())
        self.assertTrue((self.tmp / "data" / "logs" / "sidecar.log").exists())
        p.stdin.close()
        self.assertEqual(await self.wait_exit(p, 8), 0)
        await asyncio.gather(c._task, return_exceptions=True)
        self.assertEqual(c.ws.close_code, 1001)          # told "going away" before the process exited

    async def test_app_shutdown_rpc(self):
        p = await self.spawn("--test-mode")
        c = await Rpc.open((await self.ready(p))["port"])
        self.assertEqual(await c.ok("app.shutdown"), {"ok": True})
        self.assertEqual(await self.wait_exit(p, 8), 0)

    async def test_fake_pool_ignored_without_test_mode(self):
        p = await self.spawn()                                  # env says fake, but no --test-mode
        c = await Rpc.open((await self.ready(p))["port"])
        self.assertFalse((await c.ok("app.info"))["test_mode"])
        self.assertIn("ignored", (self.tmp / "stderr.txt").read_text(errors="replace"))
        p.stdin.close()
        self.assertEqual(await self.wait_exit(p, 8), 0)

    async def test_token_required(self):
        for token in (None, "short", "x" * 31):
            p = await self.spawn(env=_env(token))
            self.assertEqual(await self.wait_exit(p, 15), 2, token)
            self.assertEqual(p.stdout.read(), b"")             # no READY line

    async def test_wrong_token_rejected_by_real_process(self):
        p = await self.spawn("--test-mode")
        info = await self.ready(p)
        ws = await connect(f"ws://127.0.0.1:{info['port']}")
        await ws.send(json.dumps({"type": "auth", "token": "z" * 40}))
        with self.assertRaises(ConnectionClosed) as cm:
            await ws.recv()
        self.assertEqual(cm.exception.rcvd.code, 4401)
        p.stdin.close()
        self.assertEqual(await self.wait_exit(p, 8), 0)


if __name__ == "__main__":
    unittest.main()
