"""(a) Scraping through headed-offscreen Chrome + UA/webdriver checks headed vs headless."""
import asyncio, json
from common import *
from ghscraper.parsers import parse_search_page, parse_repo_page, detect_block

UA_JS = "JSON.stringify({ua:navigator.userAgent, webdriver:navigator.webdriver, brands:JSON.stringify(navigator.userAgentData && navigator.userAgentData.brands)})"
LANG_JS = "!!document.querySelector('span[role=progressbar][aria-label^=\"Lang\"]') || !![...document.querySelectorAll('span[role=progressbar][aria-label]')].length"

async def ua(headless):
    b = await start(headless=headless, profile=new_profile("ua"))
    try:
        t = await b.get("about:blank")
        r = await t.evaluate(UA_JS)
        return json.loads(r)
    finally:
        await stop(b)

async def main():
    res = {}
    b = await start(headless=False)
    try:
        t = await b.get("about:blank")
        res["ua_headed"] = json.loads(await t.evaluate(UA_JS))
        await goto(t, "https://github.com/search?q=web+scraper&type=repositories")
        html = await t.get_content()
        title = await t.evaluate("document.title")
        res["search_block"] = detect_block(html, title)
        s = parse_search_page(html, 1)
        res["search"] = {"source": s["source"], "n": len(s["results"]), "page_count": s["page_count"],
                         "total": s["total"], "first": s["results"][0] if s["results"] else None}
        for full in ("scrapy/scrapy", "psf/requests"):
            await polite()
            await goto(t, f"https://github.com/{full}", settle=3.0)
            html = await t.get_content()
            title = await t.evaluate("document.title")
            d = parse_repo_page(html, full)
            res[full] = {"block": detect_block(html, title), "fields": fields(d),
                         "values": {k: d[k] for k in ("stars","forks","branches","contributors","languages","last_commit_at","commits","open_issues","open_pull_requests","license","releases","tags")}}
            # when would languages appear? poll selector
            res[full]["lang_selector_now"] = await t.evaluate(LANG_JS)
            (OUT / f"{full.replace('/','_')}.html").write_text(html, encoding="utf-8")
    finally:
        await stop(b)
    res["ua_headless"] = await ua(True)
    res["github_loads"] = LOADS
    (OUT / "a_results.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))
    print("leftover:", [p.pid for p in leftover_spike_chromes()])

asyncio.run(main())
