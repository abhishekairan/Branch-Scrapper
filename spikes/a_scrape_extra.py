"""(a) extras: userAgentData brands via localhost (secure context) headed vs headless, and
time-to-appear of the JS-rendered repo fields (1 GitHub load)."""
import asyncio, json, threading, http.server, functools, time
from common import *
import common

UA_JS = "JSON.stringify({ua:navigator.userAgent, webdriver:navigator.webdriver, hasUAD: !!navigator.userAgentData, brands:navigator.userAgentData ? navigator.userAgentData.brands : null, secure: isSecureContext})"

def serve():
    (OUT / "blank.html").write_text("<html><body>x</body></html>")
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    h.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

PROBES = {
  "branches": "!![...document.querySelectorAll('a[href=\"/scrapy/scrapy/branches\"]')].some(a=>/^[\d,.]+/.test(a.textContent.trim()))",
  "contributors": "!!document.querySelector('h2 a[href=\"/scrapy/scrapy/graphs/contributors\"]')?.closest('h2')?.querySelector('[data-component=\"CounterLabel\"]')",
  "languages": "!!document.querySelector('span[role=progressbar][aria-label*=\"%\"]')",
  "last_commit_at": "!!document.querySelector('relative-time[datetime]')",
}

async def main():
    srv = serve(); url = f"http://127.0.0.1:{srv.server_port}/blank.html"
    res = {}
    for hl in (False, True):
        b = await start(headless=hl, profile=new_profile("ua"))
        try:
            t = await b.get(url); await t.wait_for_ready_state("complete")
            res["headless" if hl else "headed"] = json.loads(await t.evaluate(UA_JS))
        finally:
            await stop(b)
    # timing probe, headed offscreen, one load
    b = await start(headless=False)
    try:
        t = await b.get("about:blank")
        t0 = time.time()
        await t.get("https://github.com/scrapy/scrapy"); common.LOADS += 1
        await t.wait_for_ready_state("complete", timeout=30)
        t_ready = time.time() - t0
        seen = {}
        while time.time() - t0 < 20 and len(seen) < len(PROBES):
            for k, js in PROBES.items():
                if k not in seen and await t.evaluate(js):
                    seen[k] = round(time.time() - t0, 2)
            await asyncio.sleep(0.1)
        res["timing"] = {"ready_complete_s": round(t_ready, 2), "appeared_s_since_nav": seen,
                         "missing_after_20s": [k for k in PROBES if k not in seen]}
    finally:
        await stop(b)
    res["github_loads"] = common.LOADS
    (OUT / "a_extra.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2)); print("leftover:", [p.pid for p in leftover_spike_chromes()])

asyncio.run(main())
