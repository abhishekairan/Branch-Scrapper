"""(c) Input forwarding to off-screen windows via raw CDP Input domain."""
import asyncio, json, urllib.parse
from common import *

PAGE = """<html><body style='margin:0'>
<button id=b style='position:absolute;left:300px;top:200px;width:160px;height:60px' onclick="document.title='clicked'">Go</button>
<input id=i style='position:absolute;left:300px;top:320px;width:300px;height:40px'>
<script>window.keys=[];document.getElementById('i').addEventListener('keydown',e=>keys.push(e.key));</script>
</body></html>"""

async def click(tab, x, y):
    await tab.send(cdp.input_.dispatch_mouse_event("mouseMoved", x=x, y=y))
    await tab.send(cdp.input_.dispatch_mouse_event("mousePressed", x=x, y=y, button=cdp.input_.MouseButton.LEFT, buttons=1, click_count=1))
    await tab.send(cdp.input_.dispatch_mouse_event("mouseReleased", x=x, y=y, button=cdp.input_.MouseButton.LEFT, buttons=0, click_count=1))

async def center(tab, sel):
    r = json.loads(await tab.evaluate(f"JSON.stringify((()=>{{const r=document.querySelector('{sel}').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]}})())"))
    return r

async def run(tab, label):
    out = {"label": label}
    out["title_before"] = await tab.evaluate("document.title")
    x, y = await center(tab, "#i"); await click(tab, x, y)
    out["input_focused"] = await tab.evaluate("document.activeElement.id")
    for ch in "abc":  # real key events (keyDown carries text -> produces char)
        await tab.send(cdp.input_.dispatch_key_event("keyDown", key=ch, code=f"Key{ch.upper()}", text=ch, windows_virtual_key_code=ord(ch.upper())))
        await tab.send(cdp.input_.dispatch_key_event("keyUp", key=ch, code=f"Key{ch.upper()}", windows_virtual_key_code=ord(ch.upper())))
    out["after_key_events"] = await tab.evaluate("document.getElementById('i').value")
    await tab.send(cdp.input_.insert_text("XYZ"))
    out["after_insert_text"] = await tab.evaluate("document.getElementById('i').value")
    await tab.send(cdp.input_.dispatch_key_event("keyDown", key="Backspace", code="Backspace", windows_virtual_key_code=8))
    await tab.send(cdp.input_.dispatch_key_event("keyUp", key="Backspace", code="Backspace", windows_virtual_key_code=8))
    out["after_backspace"] = await tab.evaluate("document.getElementById('i').value")
    out["keydown_events_seen"] = await tab.evaluate("JSON.stringify(keys)")
    x, y = await center(tab, "#b"); await click(tab, x, y)
    out["title_after_click"] = await tab.evaluate("document.title")
    w = await tab.send(cdp.browser.get_window_for_target(tab.target_id))
    out["window_left_top"] = [w[1].left, w[1].top]
    return out

async def main():
    res = []
    b = await start(headless=False)
    try:
        url = "data:text/html," + urllib.parse.quote(PAGE)
        t = await b.get(url); await t.wait_for_ready_state("complete")
        res.append(await run(t, "default-context tab, window off-screen"))
        t2 = await b.create_context(url, new_window=True); await asyncio.sleep(1)
        res.append(await run(t2, "create_context tab, window off-screen"))
    finally:
        await stop(b)
    print(json.dumps(res, indent=2)); (OUT / "c_results.json").write_text(json.dumps(res, indent=2))
    print("leftover:", [p.pid for p in leftover_spike_chromes()])
asyncio.run(main())
