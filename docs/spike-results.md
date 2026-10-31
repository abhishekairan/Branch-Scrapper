# Phase 0 spike results: Zendriver 0.17.1

Run on 2026-11-01, Windows 11, Python 3.12 venv, Chrome 156 (per-user install), zendriver 0.17.1.
Scripts are in `spikes/` (shared helpers in `spikes/common.py`); raw JSON in `spikes/out/`.
GitHub page loads made in total: **13** (budget ~20), 3-6 s random pauses between consecutive loads in each script.
No blocks / rate-limit pages seen (`detect_block` returned None on every load). Every spike Chrome was stopped with
`await browser.stop()`; a psutil scan for the spike's `ghspike_*` profile dirs after each script and at the end found none.
Temp profiles were deleted.

Not tested: the plan's "50 repo pages + block rate" (phase-0 item (a) in the plan) was capped by the 20-load budget of this spike.
Not tested: Linux/macOS. Not tested: logging in to GitHub. All RAM numbers are single runs (no repeats), so differences under ~10% are noise.

## (a) Scraping (`spikes/a_scrape.py`, `spikes/a_scrape_extra.py`)

Headed Chrome, window at -32000,-32000, 1280x800, 3 anti-throttle flags. `tab.get_content()` then the existing parsers.

| Item | Result |
|---|---|
| Search `web scraper` | `parse_search_page(html, 1)` -> source `json`, 10 results, page_count 100, total 66782 |
| Repo pages | scrapy/scrapy and psf/requests: **all 26 fields filled**, including `branches` (42 / 7), `contributors` (642 / 659), `languages` ({Python 98.1, HTML 1.6, Other 0.3} / {Python 99.4, Makefile 0.6}), `last_commit_at` (2026-10-30 / 2026-10-26) |
| Needed wait? | No special selector. Probe loop (1 extra load of scrapy/scrapy, polling every 100 ms): `branches`, `contributors`, `languages` (`span[role=progressbar][aria-label*="%"]`), `last_commit_at` (`relative-time[datetime]`) were all present at the first poll after `readyState == complete` (1.45 s after navigation start). The ~3 s settle in the scripts is more than needed; `wait_for_ready_state("complete")` alone sufficed in this one sample. |

UA / webdriver (evaluated on `http://127.0.0.1` so `navigator.userAgentData` exists; `about:blank` is not a secure context and lacks it):

| Mode | `navigator.userAgent` | `navigator.webdriver` | `userAgentData.brands` |
|---|---|---|---|
| headed off-screen | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/156.0.0.0 Safari/537.36` | `false` | `[Not:A-Brand 8, Chromium 156, Google Chrome 156]` |
| `headless=True` | identical UA string (no "HeadlessChrome") | `false` | **`[]` (empty) - the Client Hints leak the plan mentions is real** |

**Go**: headed-offscreen scraping works with the unmodified parsers; headless works for parsing too (see e.5) but leaks via empty brands, so keep headed-offscreen as default.
Caveat: 3 repo pages + 1 search is a small sample; the 50-page block-rate test still needs doing.

## (b) Contexts + screencast (`spikes/b_contexts.py`)

One Chrome, three `create_context(url, new_window=True)` tabs (local `data:` pages, no GitHub loads): A and B run a `requestAnimationFrame` animation, C auto-scrolls a long page. All windows off-screen.

| Check | Result |
|---|---|
| Own window per context | **Yes.** `Browser.getWindowForTarget` gave 4 distinct window ids (3 contexts + the default tab) and 3 distinct `browser_context_id`s. All at left/top = -32000, 1280x800, state NORMAL (the flags/position propagate to new windows). |
| Screencast (jpeg q50, max_width 480, every_nth_frame 1), 9.98 s, all three simultaneously, each frame acked | ctx A: 995 frames = **99.7 fps**, 2.5 KB/frame avg. ctx B: 994 = **99.6 fps**, 2.5 KB. ctx C (scroll tick 20/s): 200 = **20.0 fps**, 1.3 KB |
| Page not throttled while off-screen | page rAF ticks/s: 164.9 / 164.7 / 20.0 (the 20 is the setInterval 50 ms; the 165 matches the monitor refresh, so no background throttling) |
| Stop | `Page.stopScreencast` on all 3: **0 frames in the following 3 s** (after a 1 s drain) |
| Content | Saved `spikes/out/ctx0.jpg`, `ctx1.jpg`, `ctx2.jpg` (480 px wide); inspected ctx2: real rendered gradient, not blank |

Takeaway: off-screen windows are not throttled and render. With `every_nth_frame=1` the stream is as fast as the page paints (~100 fps here), so the planned 1-2 fps tile / 10-15 fps single view must be enforced by `every_nth_frame` or by dropping frames server-side (still ack every frame).
Not tested: frame rate on a mostly-static page (Chrome only sends frames on visual change), and a context in a window that is minimised.

**Go.**

## (c) Input forwarding (`spikes/c_input.py`)

Local `data:` page: button (`onclick` sets `document.title`) and a text input. Run on the default-context tab and on a `create_context` tab, windows at -32000,-32000. Verified with `tab.evaluate`.

| Step | Default tab | create_context tab |
|---|---|---|
| mouseMoved + mousePressed + mouseReleased on the input | focused (`activeElement.id == "i"`) | same |
| keyDown (with `text=`) + keyUp for "a","b","c" | value `abc` | `abc` |
| `insert_text("XYZ")` | `abcXYZ` | `abcXYZ` |
| Backspace keyDown/keyUp (vk 8) | `abcXY` | `abcXY` |
| click on button | title `clicked` | `clicked` |

**Go.** Exact names that worked: `cdp.input_.dispatch_mouse_event("mousePressed", x=, y=, button=cdp.input_.MouseButton.LEFT, buttons=1, click_count=1)`, `cdp.input_.dispatch_key_event("keyDown", key=, code=, text=, windows_virtual_key_code=)`, `cdp.input_.insert_text(text)`. The module is `input_` (trailing underscore); parameters take `type_` positional first.
Not tested: real GitHub login page, modifier keys, scroll wheel (`delta_y`), IME/paste, coordinate scaling from a downsized screencast tile.

## (d) Kill-on-exit (`spikes/d_jobobject.py`)

Parent script launches a child Python that starts Chrome (own unique `ghspike_*` profile), then the parent `taskkill /F /PID <child>` (no `/T`), then polls psutil for Chrome processes whose cmdline contains that profile dir. Four variants, 12-13 Chrome processes each:

| Variant | In job? | Chrome procs before kill | Survivors after hard kill |
|---|---|---|---|
| control: no job | n/a | 12 | **12 (all survive)** - they were killed manually by marker afterwards; shows the test is valid |
| `root`: AssignProcessToJobObject on `browser._process_pid` after launch | root + children spawned after assignment: yes; the 9 children that existed before assignment: **no** | 13 | 0 (all gone within 0.5 s) |
| `root+existing`: also assign each pre-existing child | root yes; pre-existing children: **`Access denied` (error 5) for 8 of 9**, 1 succeeded | 12 | 0 (0.5 s) |
| `self`: put the sidecar process itself in the job BEFORE launching Chrome | **all 12 in job** (inherited) | 12 | 0 (0.6 s) |

Findings:
- `browser._process_pid` works (int, set after `zd.start`). It is `None` after `stop()`.
- Assigning the Chrome root after launch does not cover children that already exist (GPU, utility, crashpad etc. were not in the job). In the `root` run they still died within 0.5 s, presumably because Chrome child processes exit when the browser process dies (inference; not separately verified). Do not rely on that.
- Assigning pre-existing children fails with Access Denied (Chrome's sandboxed children are restricted-token processes).
- The robust approach is the `self` variant: call `AssignProcessToJobObject(GetCurrentProcess())` once at sidecar start, before spawning any Chrome; every descendant is covered. Works on nested jobs here (the harness's own job did not interfere); not tested inside a Tauri-spawned process, which may itself live in a job.
- ctypes details that mattered: `restype/argtypes` set with `wintypes.HANDLE` (64-bit handles), info class 9 = `JobObjectExtendedLimitInformation`, flag `0x2000`.
- The user's own Chrome processes were not touched (matching was only by profile dir).

**Go**, using the `self` variant (or launching Chrome after putting the sidecar in the job).

## (e) RAM (`spikes/e_ram.py`)

psutil over the process tree rooted at `browser._process_pid`, after the page loaded + 3 s + 5 s idle. RSS counts shared pages in every process, so **USS is the honest figure**. Single run each. Pages: scrapy/scrapy, psf/requests, pallets/flask (real GitHub loads, 9 total).

| # | Config | Procs | RSS MB | USS MB |
|---|---|---|---|---|
| 0 | baseline: 1 Chrome headed off-screen, about:blank only | 12 | 636.9 | 180.1 |
| 1 | 1 Chrome, 1 tab on scrapy/scrapy (headed off-screen) | 12 | 821.3 | 320.3 |
| 2 | 1 Chrome, 3 contexts each on a GitHub repo page | 15 | 1390.3 | 629.5 |
| 3 | 3 Chromes, 1 tab each (same 3 pages) | 42 | 2441.4 | 940.4 |
| 4 | like 1, with `Network.setBlockedURLs` (png/jpg/jpeg/gif/svg/webp/ico/woff/woff2/ttf/mp4/webm) | 12 | 805.3 | 299.5 |
| 5 | like 1, `headless=True` | 12 | 794.8 | 301.1 |

Derived (USS): first Chrome with a page ~320 MB; each extra context ~155 MB (630 vs 320 for 3 pages); each extra Chrome ~310 MB (940 vs 320). So shared contexts cost about half of separate Chromes: 3 workers = 630 MB vs 940 MB (-33%).
- Blocking: -21 MB USS (-6.5%), -16 MB RSS from one sample. Within the noise I would expect from single runs; treat as "small / unproven benefit". **Parsed output identical to #1 for all 16 compared fields** (stars, forks, branches, contributors, languages, last_commit_at, commits, issues, PRs, license, releases, tags, description, topics, root_files, default_branch). Parsers unaffected. The block request count and the effect on block-rate were not measured.
- Headless: -19 MB USS (-6%), same single-sample caveat, parse identical to #1 (`5_diff_keys == []`).
- The 12-process baseline for an idle Chrome is surprisingly large; headless idle was not separately measured.
- Plan targets ("each extra worker in shared mode well under one full Chrome"): met in this sample (155 vs 310 MB USS).

**Go** for contexts-per-Chrome (`workers_per_chrome` default 3). Blocking: optional, safe for parsing, little RAM gain.

## API gotchas

- `zd.start(headless=..., browser_args=[...], user_data_dir=..., browser="chrome", browser_executable_path=CHROME)` worked. Passing an explicit unique `user_data_dir` makes processes identifiable by cmdline; I delete it after `stop()`.
- `tab.get(url)` returns once the navigation starts; use `await tab.wait_for_ready_state("complete", timeout=30)` and then optional sleep. `tab.get_content()` returns the live DOM HTML, so JS-rendered values are present.
- `b.create_context(url, new_window=True)` returns a Tab; `tab.target.browser_context_id` identifies the context. A fresh `zd.start` also opens a default about:blank window in addition to the contexts (4 windows for 3 contexts).
- Window query: `await tab.send(cdp.browser.get_window_for_target(tab.target_id))` returns `(window_id, Bounds)`.
- Screencast: `cdp.page.start_screencast(format_="jpeg", quality=50, max_width=480, every_nth_frame=1)`; handler via `tab.add_handler(cdp.page.ScreencastFrame, async_fn)`; ack with `cdp.page.screencast_frame_ack(ev.session_id)`; `ev.data` is base64. Zendriver dispatches async handlers with `create_task`, so awaiting `tab.send` inside the handler did not deadlock. Stop: `cdp.page.stop_screencast()`.
- Blocking: `cdp.network.enable()` then `cdp.network.set_blocked_ur_ls(urls=[...])` (note the odd name `ur_ls`; `urls` is marked DEPRECATED in favour of `url_patterns`, but works in Chrome 156).
- `navigator.userAgentData` is undefined on `about:blank` / `data:` pages (insecure context); test brands on `http://127.0.0.1`.
- Module-level counters imported with `from common import *` are copied: read `common.LOADS`, not the starred name (bug I hit in `a_scrape.py`; its printed `github_loads: 0` is wrong, true loads were 3 there).
- `psutil` `memory_full_info().uss` worked on this Windows account for all Chrome processes.

## Note on repo state

`git status` also shows modified `ghscraper/config.py`, `ghscraper/exporters.py` and untracked `ghscraper/store/`, `tests/test_exporters.py`, `tests/test_store.py`. I did not touch these; they predate or are unrelated to the spike.

## Reproduce

`.venv/Scripts/python.exe spikes/a_scrape.py` (3 loads), `a_scrape_extra.py` (1), `b_contexts.py`, `c_input.py`, `d_jobobject.py`, `e_ram.py` (9 loads). Only a/a_extra/e touch GitHub.
