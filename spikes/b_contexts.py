"""(b) 3 browser contexts in one Chrome, own windows, simultaneous screencast while off-screen."""
import asyncio, base64, json, time, urllib.parse
from common import *

ANIM = """<html><body style='margin:0'><div id=d style='width:100vw;height:100vh;font:80px sans-serif'></div>
<script>window.n=0;function f(t){n++;document.getElementById('d').style.background='hsl('+(t/5%360)+',80%,60%)';
document.getElementById('d').textContent='CTX @@ n='+n;requestAnimationFrame(f)}requestAnimationFrame(f)</script></body></html>"""
SCROLL = """<html><body style='margin:0'><div style='height:20000px;background:linear-gradient(red,blue,green,yellow);font:60px sans-serif'>CTX @@ scroll</div>
<script>window.n=0;setInterval(()=>{n++;scrollBy(0,40)},50)</script></body></html>"""

def du(html): return "data:text/html," + urllib.parse.quote(html)

async def main():
    res = {}
    b = await start(headless=False)
    try:
        pages = [du(ANIM.replace("@@","A")), du(ANIM.replace("@@","B")), du(SCROLL.replace("@@","C"))]
        tabs = []
        for u in pages:
            tabs.append(await b.create_context(u, new_window=True))
        await asyncio.sleep(1.5)
        # windows
        wins = {}
        for i, t in enumerate(tabs):
            w = await t.send(cdp.browser.get_window_for_target(t.target_id))
            wins[f"ctx{i}"] = {"window_id": int(w[0]), "left": w[1].left, "top": w[1].top, "w": w[1].width, "h": w[1].height, "state": str(w[1].window_state)}
        main_tab = b.main_tab
        w = await main_tab.send(cdp.browser.get_window_for_target(main_tab.target_id))
        wins["default_tab"] = {"window_id": int(w[0]), "left": w[1].left, "top": w[1].top, "w": w[1].width, "h": w[1].height}
        res["windows"] = wins
        res["distinct_window_ids"] = len({v["window_id"] for v in wins.values()})
        ctx_ids = [str(t.target.browser_context_id) for t in tabs]
        res["distinct_contexts"] = len(set(ctx_ids))

        counts = [0, 0, 0]; first = [None, None, None]; last = [None] * 3; saved = [False] * 3; bytes_ = [0, 0, 0]
        def mk(i, tab):
            async def h(ev):
                counts[i] += 1
                now = time.time(); first[i] = first[i] or now; last[i] = now
                raw = base64.b64decode(ev.data); bytes_[i] += len(raw)
                if counts[i] == 20 and not saved[i]:
                    (OUT / f"ctx{i}.jpg").write_bytes(raw); saved[i] = True
                await tab.send(cdp.page.screencast_frame_ack(ev.session_id))
            return h
        hs = [mk(i, t) for i, t in enumerate(tabs)]
        for i, t in enumerate(tabs):
            t.add_handler(cdp.page.ScreencastFrame, hs[i])
        n0 = [await t.evaluate("window.n") for t in tabs]
        t_start = time.time()
        for t in tabs:
            await t.send(cdp.page.start_screencast(format_="jpeg", quality=50, max_width=480, every_nth_frame=1))
        await asyncio.sleep(10)
        dur = time.time() - t_start
        snap = list(counts)
        n1 = [await t.evaluate("window.n") for t in tabs]
        res["screencast_10s"] = {f"ctx{i}": {"frames": snap[i], "fps": round(snap[i] / dur, 1),
                                 "avg_kb": round(bytes_[i] / max(snap[i], 1) / 1024, 1),
                                 "page_ticks_per_s": round((n1[i] - n0[i]) / dur, 1)} for i in range(3)}
        res["duration_s"] = round(dur, 2)
        for t in tabs:
            await t.send(cdp.page.stop_screencast())
        await asyncio.sleep(1.0)
        after_stop_base = list(counts)
        await asyncio.sleep(3.0)
        res["frames_after_stop_3s"] = [counts[i] - after_stop_base[i] for i in range(3)]
        res["saved_jpegs"] = [str(OUT / f"ctx{i}.jpg") for i in range(3) if saved[i]]
    finally:
        await stop(b)
    print(json.dumps(res, indent=2)); (OUT / "b_results.json").write_text(json.dumps(res, indent=2))
    print("leftover:", [p.pid for p in leftover_spike_chromes()])

asyncio.run(main())
