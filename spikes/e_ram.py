"""(e) RAM of Chrome process tree under 5 configurations (+ idle baseline). 9 GitHub loads total."""
import asyncio, json
from common import *
import common
from ghscraper.parsers import parse_repo_page

REPOS = ["scrapy/scrapy", "psf/requests", "pallets/flask"]
BLOCK = ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp", "*.ico", "*.woff", "*.woff2", "*.ttf", "*.mp4", "*.webm"]
KEYS = ("stars","forks","branches","contributors","languages","last_commit_at","commits","open_issues","open_pull_requests","license","releases","tags","description","topics","root_files","default_branch")

async def settle():
    await asyncio.sleep(5)

def parsed(html, full):
    d = parse_repo_page(html, full)
    return {k: d[k] for k in KEYS}

async def load(tab, full, block=False):
    if block:
        await tab.send(cdp.network.enable())
        await tab.send(cdp.network.set_blocked_ur_ls(urls=BLOCK))
    await goto(tab, f"https://github.com/{full}", settle=3.0)
    return parsed(await tab.get_content(), full)

async def main():
    res = {}
    # 0 baseline idle about:blank (no GitHub load)
    b = await start(); await settle()
    res["0_idle_blank_1chrome"] = ram(tree(b)); await stop(b)

    # 1
    b = await start(); t = await b.get("about:blank")
    p1 = await load(t, REPOS[0]); await settle()
    res["1_one_chrome_one_tab"] = ram(tree(b)); await stop(b)
    await polite()

    # 2: 3 contexts
    b = await start(); tabs = []
    for r in REPOS:
        tab = await b.create_context("about:blank", new_window=True); tabs.append(tab)
    for tab, r in zip(tabs, REPOS):
        await load(tab, r); await polite()
    await settle()
    res["2_one_chrome_three_contexts"] = ram(tree(b)); await stop(b)

    # 3: 3 chromes
    bs = []
    for r in REPOS:
        bb = await start(); tt = await bb.get("about:blank"); await load(tt, r); bs.append(bb); await polite()
    await settle()
    allp = []
    for bb in bs: allp += tree(bb)
    res["3_three_chromes_one_tab"] = ram(allp)
    for bb in bs: await stop(bb)
    await polite()

    # 4: blocked
    b = await start(); t = await b.get("about:blank")
    p4 = await load(t, REPOS[0], block=True); await settle()
    res["4_one_chrome_blocked"] = ram(tree(b)); await stop(b)
    res["4_parse_identical_to_1"] = (json.dumps(p1, sort_keys=True, default=str) == json.dumps(p4, sort_keys=True, default=str))
    res["4_diff_keys"] = [k for k in KEYS if p1[k] != p4[k]]
    await polite()

    # 5: headless
    b = await start(headless=True); t = await b.get("about:blank")
    p5 = await load(t, REPOS[0]); await settle()
    res["5_headless_one_tab"] = ram(tree(b)); await stop(b)
    res["5_parse_identical_to_1"] = (json.dumps(p1, sort_keys=True, default=str) == json.dumps(p5, sort_keys=True, default=str))
    res["5_diff_keys"] = [k for k in KEYS if p1[k] != p5[k]]
    res["github_loads"] = common.LOADS
    print(json.dumps(res, indent=2, default=str)); (OUT / "e_results.json").write_text(json.dumps(res, indent=2, default=str))
    print("leftover:", [p.pid for p in leftover_spike_chromes()])
asyncio.run(main())
