"""Capture the README screenshots from a REAL scrape driven through the real UI.

    .venv/Scripts/python.exe app/scripts/capture_screenshots.py            # real sidecar, real GitHub (~14 page loads)
    .venv/Scripts/python.exe app/scripts/capture_screenshots.py --fake     # offline harness (no GitHub), for debugging this script

What it does: starts the sidecar (temp data dir, random token), starts Vite, opens the Vite UI in a SEPARATE
zendriver-controlled Chrome (temp profile, 1440x900 @1x) with ?port=&token=, fills the Run form like a user,
starts a run, screenshots Run / Browsers (grid, single) / Data / About, and writes PNGs to docs/screenshots/.
It also records what the page saw on the WebSocket (frames per slot, outgoing browser.view calls, pool.stats,
console errors) and prints a JSON report at the end. Every process it starts is killed in `finally`.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import zendriver as zd
from zendriver import cdp

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
OUT = ROOT / "docs" / "screenshots"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
VITE_PORT = 1420
W, H = 1440, 900

# Injected before any page script: observe the app's WebSocket without changing its behaviour.
HOOK = r"""
(() => {
  const W = window.WebSocket;
  const S = (window.__ws = { frames: {}, sizes: {}, out: [], ev: [], poolStats: null, statuses: [], t0: performance.now() });
  function P(url, proto) {
    const ws = proto === undefined ? new W(url) : new W(url, proto);
    ws.addEventListener('message', (e) => {
      const t = performance.now();
      if (typeof e.data === 'string') {
        let m; try { m = JSON.parse(e.data); } catch { return; }
        if (!m.method) return;
        if (m.method === 'pool.stats') S.poolStats = { t, ...m.params };
        else if (m.method === 'worker.status') S.statuses.push({ t, ...m.params });
        else if (m.method === 'log' || m.method.startsWith('run.')) S.ev.push({ t, method: m.method, params: m.params });
      } else if (e.data instanceof ArrayBuffer && e.data.byteLength > 15) {
        const dv = new DataView(e.data); const slot = dv.getUint16(1);
        (S.frames[slot] ||= []).push(t); (S.sizes[slot] ||= []).push(e.data.byteLength);
      }
    });
    const send = ws.send.bind(ws);
    ws.send = (d) => {
      try { const m = JSON.parse(d); if (m.method === 'browser.view') S.out.push({ t: performance.now(), params: m.params }); } catch {}
      return send(d);
    };
    return ws;
  }
  P.prototype = W.prototype; P.CONNECTING = 0; P.OPEN = 1; P.CLOSING = 2; P.CLOSED = 3;
  window.WebSocket = P;
})();
"""

# helpers available in the page after we define them once
HELPERS = r"""
window.__h = {
  field(label) {
    for (const f of document.querySelectorAll('.field')) {
      const s = f.querySelector('.label > span');
      if (s && s.textContent.trim() === label) return f.querySelector('.control input, .control textarea, .control select');
    }
    return null;
  },
  set(el, v) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  },
  clickText(sel, text) {
    const el = [...document.querySelectorAll(sel)].find((e) => e.textContent.trim().startsWith(text));
    if (!el) return false; el.click(); return true;
  },
};
"""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def tree(pid: int) -> list[psutil.Process]:
    try:
        p = psutil.Process(pid)
        return [p, *p.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def kill_tree(pid: int):
    procs = tree(pid)
    for p in reversed(procs):
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=10)


def procs_with(marker: str) -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if any(marker in (a or "") for a in (p.info["cmdline"] or [])):
                out.append(p)
        except psutil.Error:
            pass
    return out


class Page:
    def __init__(self, tab: zd.Tab, console: list):
        self.tab = tab
        self.console = console

    async def js(self, expr: str):
        return await self.tab.evaluate(expr, await_promise=True, return_by_value=True)

    async def wait(self, expr: str, timeout=30.0, what=""):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if await self.js(f"Boolean({expr})"):
                    return
            except Exception:
                pass
            await asyncio.sleep(0.25)
        raise TimeoutError(f"timed out waiting for {what or expr}")

    async def shot(self, name: str):
        await asyncio.sleep(0.4)
        r = await self.tab.send(cdp.page.capture_screenshot(format_="png"))
        data = base64.b64decode(r)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_bytes(data)
        log(f"screenshot {name} {len(data) // 1024} KB")

    async def scheme(self, mode: str):
        await self.tab.send(cdp.emulation.set_emulated_media(features=[cdp.emulation.MediaFeature("prefers-color-scheme", mode)]))

    async def nav(self, label: str):
        ok = await self.js(f"__h.clickText('nav button', {json.dumps(label)})")
        assert ok, f"nav button {label} not found"
        await asyncio.sleep(0.5)


async def main(fake: bool):
    root = Path(tempfile.mkdtemp(prefix="ghs-shots-"))
    data_dir = root / "data"
    profile = root / "chrome-profile"
    token = secrets.token_hex(32)
    marker = str(root)
    report: dict = {"fake": fake, "problems": []}
    children: list[subprocess.Popen] = []
    browser = None
    sidecar_log = root / "sidecar-stderr.log"
    try:
        # ---------------------------------------------------------------- sidecar
        env = {**os.environ, "GHOSTSCRAPER_TOKEN": token}
        if fake:
            cmd = [str(PY), str(APP / "scripts" / "dev_sidecar.py"), "--data-dir", str(data_dir)]
            env.update(HARNESS_DELAY="1.5", HARNESS_FRAME_FPS="10")
        else:
            cmd = [str(PY), "-m", "ghscraper.sidecar", "--data-dir", str(data_dir)]
        errf = open(sidecar_log, "wb")
        sc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf)
        children.append(sc)
        ready = None
        t_end = time.time() + 30
        while time.time() < t_end:
            line = sc.stdout.readline().decode(errors="replace").strip()
            m = re.match(r"READY (\{.*\})$", line)
            if m:
                ready = json.loads(m.group(1))
                break
            if sc.poll() is not None:
                break
        assert ready, f"no READY line; stderr: {sidecar_log.read_text(errors='replace')[-800:]}"
        log("sidecar READY", ready)
        report["sidecar_pid"] = ready["pid"]

        # ---------------------------------------------------------------- vite
        vite = subprocess.Popen(
            ["cmd", "/c", "npm", "run", "dev"], cwd=APP, stdout=open(root / "vite.log", "wb"), stderr=subprocess.STDOUT,
        )
        children.append(vite)
        t_end = time.time() + 60
        while time.time() < t_end:
            if "ready in" in (root / "vite.log").read_text(errors="replace"):
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("vite did not become ready: " + (root / "vite.log").read_text(errors="replace")[-500:])
        log("vite ready")

        # ---------------------------------------------------------------- UI chrome
        browser = await zd.start(
            user_data_dir=str(profile), headless=False,
            browser_args=[f"--window-size={W},{H + 120}", "--no-first-run", "--no-default-browser-check"],
        )
        tab = browser.main_tab
        console: list = []
        page = Page(tab, console)

        def on_console(ev: cdp.runtime.ConsoleAPICalled):
            if ev.type_ in ("error", "warning", "assert"):
                console.append(f"console.{ev.type_}: " + " ".join(str(a.value if a.value is not None else a.description) for a in ev.args)[:400])

        def on_exc(ev: cdp.runtime.ExceptionThrown):
            d = ev.exception_details
            console.append("exception: " + (d.exception.description if d.exception and d.exception.description else d.text)[:400])

        def on_log(ev: cdp.log.EntryAdded):
            if ev.entry.level in ("error", "warning"):
                console.append(f"log.{ev.entry.level}: {ev.entry.text} {ev.entry.url or ''}"[:400])

        tab.add_handler(cdp.runtime.ConsoleAPICalled, on_console)
        tab.add_handler(cdp.runtime.ExceptionThrown, on_exc)
        tab.add_handler(cdp.log.EntryAdded, on_log)
        await tab.send(cdp.runtime.enable())
        await tab.send(cdp.log.enable())
        await tab.send(cdp.page.enable())
        await tab.send(cdp.emulation.set_device_metrics_override(width=W, height=H, device_scale_factor=1, mobile=False))
        await page.scheme("dark")
        await tab.send(cdp.page.add_script_to_evaluate_on_new_document(HOOK))
        await tab.get(f"http://localhost:{VITE_PORT}/?port={ready['port']}&token={token}")
        await page.wait("document.querySelector('nav')", 30, "app shell")
        await page.js(HELPERS)
        await page.wait("__h.field('Search queries')", 30, "run form (config loaded)")
        log("UI connected")

        # ---------------------------------------------------------------- run form, like a user
        await page.js(f"__h.set(__h.field('Search queries'), {json.dumps('web scraper' + chr(10) + 'headless browser stars:>500')})")
        for label, val in [("Max results per query", "5"), ("Workers", "3"), ("Workers per Chrome", "3"), ("Min delay (s)", "2"), ("Max delay (s)", "4")]:
            await page.js(f"__h.set(__h.field({json.dumps(label)}), {json.dumps(val)})")
        # checkboxes: deep scrape on, manifests off (fewer page loads), human search off, block resources on
        await page.js(
            """(() => {
              const cb = (label) => __h.field(label);
              for (const [l, want] of [['Deep scrape', true], ['Fetch manifests', false], ['Isolate workers', false]]) {
                const e = cb(l); if (e && e.checked !== want) e.click();
              }
              const d = document.querySelector('details.adv'); if (d) d.open = true;
              const h = cb("Type the query into GitHub's search box"); if (h && h.checked) h.click();
              if (d) d.open = false;
            })()"""
        )
        await page.wait("document.querySelector('table.q tbody tr') && document.querySelector('.estimate')", 20, "validation plan + estimate")
        await asyncio.sleep(1.0)
        plan = await page.js("[...document.querySelectorAll('table.q tbody tr')].map(r => r.innerText.replace(/\\s+/g,' ')).join(' | ')")
        est = await page.js("document.querySelector('.estimate')?.innerText")
        report["plan"] = plan
        report["estimate"] = est
        log("plan:", plan, "| estimate:", est)
        await page.js("document.querySelector('.left').scrollTop = 0")
        await page.shot("run.png")

        # ---------------------------------------------------------------- start
        t_start = time.time()
        assert await page.js("__h.clickText('button', 'Start run')"), "Start run button missing"
        await page.wait("window.__ws.ev.some(e => e.method === 'run.started')", 30, "run.started")
        log("run started")
        await page.nav("Browsers")
        await page.wait("document.querySelectorAll('.tile').length >= 3", 30, "3 tiles")
        # tiles show live frames when the canvas inside has been painted - wait for frames from all 3 slots
        await page.wait("[0,1,2].every(s => (window.__ws.frames[s]||[]).length >= 5)", 90, "frames on slots 0-2")
        # wait until each worker has navigated to a github page so the frames show real content
        try:
            await page.wait("document.body.innerText.includes('github.com/')", 60, "worker url on a tile")
        except TimeoutError:
            report["problems"].append("no github.com url visible on tiles within 60 s")
        await asyncio.sleep(3)
        await page.shot("browsers-grid.png")

        # fps: frames per second per slot over 5 s
        a = await page.js("JSON.stringify(Object.fromEntries(Object.entries(window.__ws.frames).map(([k,v]) => [k, v.length])))")
        await asyncio.sleep(5)
        b = await page.js("JSON.stringify(Object.fromEntries(Object.entries(window.__ws.frames).map(([k,v]) => [k, v.length])))")
        a, b = json.loads(a), json.loads(b)
        report["fps_grid_5s"] = {k: round((b.get(k, 0) - a.get(k, 0)) / 5, 2) for k in b}
        log("fps in grid view:", report["fps_grid_5s"])
        report["view_calls_grid"] = await page.js("JSON.stringify(window.__ws.out.slice(-3))")
        report["pool_stats_ui_grid"] = await page.js("document.querySelector('header')?.innerText.replace(/\\s+/g,' ')")
        report["pool_stats_raw"] = await page.js("JSON.stringify(window.__ws.poolStats)")

        # single view
        await page.js("__h.clickText('.seg button', 'Single')")
        await page.wait("document.querySelector('.single canvas, .single .stage')", 10, "single view")
        await asyncio.sleep(4)
        f0 = await page.js("JSON.stringify(Object.fromEntries(Object.entries(window.__ws.frames).map(([k,v]) => [k, v.length])))")
        await page.shot("browsers-single.png")
        await asyncio.sleep(5)
        f1 = await page.js("JSON.stringify(Object.fromEntries(Object.entries(window.__ws.frames).map(([k,v]) => [k, v.length])))")
        f0, f1 = json.loads(f0), json.loads(f1)
        report["fps_single_5s"] = {k: round((f1.get(k, 0) - f0.get(k, 0)) / 5, 2) for k in f1}
        report["view_calls_single"] = await page.js("JSON.stringify(window.__ws.out.slice(-3))")
        log("fps in single view:", report["fps_single_5s"])

        # List view must stop streaming
        await page.js("__h.clickText('.seg button', 'List')")
        await asyncio.sleep(2)
        g0 = await page.js("JSON.stringify(Object.fromEntries(Object.entries(window.__ws.frames).map(([k,v]) => [k, v.length])))")
        await asyncio.sleep(4)
        g1 = await page.js("JSON.stringify(Object.fromEntries(Object.entries(window.__ws.frames).map(([k,v]) => [k, v.length])))")
        report["frames_during_list_4s"] = {k: json.loads(g1).get(k, 0) - json.loads(g0).get(k, 0) for k in json.loads(g1)}
        report["view_calls_list"] = await page.js("JSON.stringify(window.__ws.out.slice(-2))")
        log("frames while in List view (4 s):", report["frames_during_list_4s"])

        # Run screen with progress + log
        await page.js("__h.clickText('.seg button', 'Grid')")
        await page.nav("Run")
        await page.js("document.querySelector('.right')?.scrollTo?.(0, 0)")
        await page.shot("run-progress.png")
        report["pool_stats_ui_run"] = await page.js("document.querySelector('header')?.innerText.replace(/\\s+/g,' ')")

        # go back to browsers and leave the tab there while the run finishes (keeps view calls realistic)
        await page.nav("Browsers")
        await page.wait("window.__ws.ev.some(e => e.method === 'run.finished')", 600, "run.finished")
        fin = await page.js("JSON.stringify(window.__ws.ev.filter(e => e.method === 'run.finished').pop().params)")
        report["finished"] = json.loads(fin)
        report["run_seconds"] = round(time.time() - t_start, 1)
        log("run finished:", fin)
        logs = await page.js("JSON.stringify(window.__ws.ev.filter(e => e.method === 'log' && e.params.level !== 'info' && e.params.level !== 'debug').map(e => e.params.level + ': ' + e.params.text))")
        report["log_warnings_errors"] = json.loads(logs)
        await asyncio.sleep(1)

        # ---------------------------------------------------------------- data
        await page.nav("Data")
        await page.wait("document.querySelector('.rrow')", 20, "data rows")
        await asyncio.sleep(1)
        # filter: pick the most common language through the TagPicker (type + Enter)
        # ask the sidecar through the same WebSocket the app uses: a one-off client for data checks
        report["data_check"] = await page.js(
            f"""(async () => {{
              const ws = new WebSocket('ws://127.0.0.1:{ready['port']}');
              const call = (id, method, params) => new Promise((res) => {{
                const h = (e) => {{ if (typeof e.data !== 'string') return; const m = JSON.parse(e.data); if (m.id === id) {{ ws.removeEventListener('message', h); res(m.result ?? m.error); }} }};
                ws.addEventListener('message', h); ws.send(JSON.stringify({{jsonrpc:'2.0', id, method, params}}));
              }});
              await new Promise(r => ws.addEventListener('open', r));
              ws.send(JSON.stringify({{type:'auth', token:'{token}'}}));
              await new Promise(r => setTimeout(r, 300));
              const s = await call(1, 'data.search', {{limit: 100}});
              const f = await call(2, 'data.facets', {{limit: 10}});
              ws.close();
              const rows = s.rows;
              const filled = (k) => rows.filter(r => r[k] !== null && r[k] !== undefined && r[k] !== '' && !(Array.isArray(r[k]) && !r[k].length) && !(typeof r[k] === 'object' && !Array.isArray(r[k]) && !Object.keys(r[k]).length)).length;
              return {{ total: s.total, filled: Object.fromEntries(['languages','contributors','branches','last_commit_at','stars','forks','topics','license','tech','description','error'].map(k => [k, filled(k)])),
                       errors: rows.filter(r => r.error).map(r => r.full_name + ': ' + r.error),
                       sample: rows.slice(0,2).map(r => ({{full_name: r.full_name, languages: r.languages, contributors: r.contributors, branches: r.branches, last_commit_at: r.last_commit_at}})),
                       langs: f.languages }};
            }})()"""
        )
        log("data check:", json.dumps(report["data_check"])[:600])
        langs = (report["data_check"] or {}).get("langs") or []
        total_rows = (report["data_check"] or {}).get("total") or 0
        # prefer a language that actually narrows the list (present in some but not all rows), else the most common
        narrowing = [x for x in langs if 0 < x["count"] < total_rows]
        top_lang = (narrowing[0] if narrowing else langs[0])["value"] if langs else None
        report["filter_language"] = top_lang
        if top_lang:
            inp = "document.querySelector('input[aria-label=\"Language\"]')"
            await page.js(f"{inp}.focus()")
            await page.tab.send(cdp.input_.insert_text(top_lang))
            await asyncio.sleep(0.5)
            await page.tab.send(cdp.input_.dispatch_key_event("keyDown", key="Enter", code="Enter", windows_virtual_key_code=13, native_virtual_key_code=13, text="\r"))
            await page.tab.send(cdp.input_.dispatch_key_event("keyUp", key="Enter", code="Enter", windows_virtual_key_code=13, native_virtual_key_code=13))
            await page.js(f"{inp}.blur()")
            await asyncio.sleep(1.5)
        # sort by stars desc: click the Stars header (default is stars desc already; make it explicit if not)
        sorted_info = await page.js("[...document.querySelectorAll('[role=columnheader][aria-sort]')].map(h => h.innerText.trim()+':'+h.getAttribute('aria-sort')).join(', ')")
        report["sort_headers"] = sorted_info
        # open the drawer on the first row
        await page.js("document.querySelector('.rrow .cell:nth-child(2)')?.click()")
        await asyncio.sleep(1.5)
        report["drawer_open"] = await page.js("Boolean(document.querySelector('aside, [role=dialog], .drawer'))")
        await page.shot("data-dark.png")
        await page.scheme("light")
        await asyncio.sleep(0.5)
        await page.shot("data-light.png")
        await page.scheme("dark")
        await page.nav("About")
        await asyncio.sleep(0.5)
        await page.shot("about.png")

        report["console"] = console
        await asyncio.sleep(0.5)
    except BaseException as e:  # noqa: BLE001
        report["problems"].append(f"script aborted: {type(e).__name__}: {e}")
        try:
            if browser is not None:
                report["console"] = console
                await page.shot("_debug_failure.png")
        except Exception:
            pass
        raise
    finally:
        print("REPORT " + json.dumps(report, indent=1, default=str), flush=True)
        # ---------------------------------------------------------------- cleanup
        try:
            if browser is not None:
                await browser.stop()
        except Exception:
            pass
        for c in children:
            try:
                if c.stdin:
                    c.stdin.close()  # sidecar: stdin EOF = clean shutdown
            except Exception:
                pass
        await asyncio.sleep(4)
        for c in children:
            if c.poll() is None:
                kill_tree(c.pid)
        # anything still referencing our temp root (our Chrome, scraper Chromes)
        await asyncio.sleep(1)
        for p in procs_with(marker):
            try:
                p.kill()
            except psutil.Error:
                pass
        # vite listens on its own node process; find it by the app dir + port
        for p in psutil.process_iter(["pid", "cmdline", "cwd"]):
            try:
                cl = " ".join(p.info["cmdline"] or [])
                if "vite" in cl and str(APP).lower().replace("\\", "/") in cl.lower().replace("\\", "/"):
                    kill_tree(p.pid)
            except psutil.Error:
                pass
        left = procs_with(marker)
        sidecar_alive = psutil.pid_exists(report.get("sidecar_pid", -1))
        print(f"CLEANUP leftovers_with_marker={[p.pid for p in left]} sidecar_alive={sidecar_alive}", flush=True)
        try:
            lines = (data_dir / "logs" / "sidecar.log").read_text(errors="replace").splitlines()
            keep = [x for x in lines if re.search(r"WARNING|ERROR|CRITICAL|Traceback|screencast|browser\.view|view", x)]
            print(f"SIDECAR LOG: {len(lines)} lines total; {len(keep)} notable:", flush=True)
            print("\n".join(keep[-60:]), flush=True)
        except Exception as e:  # noqa: BLE001
            print("no sidecar log:", e, flush=True)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="use the offline test harness instead of GitHub")
    args = ap.parse_args()
    if args.fake:  # never overwrite the real screenshots with fake-browser ones
        OUT = Path(tempfile.gettempdir()) / "ghs-shots-fake-out"
    asyncio.run(main(args.fake))
