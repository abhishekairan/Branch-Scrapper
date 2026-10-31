# Plan: GitHub Ghost Scraper desktop app — Tauri 2 + Zendriver

> Product renamed to Branch Scrapper in v0.1.1.

Status: **draft v3 for review** (2026-11-01). Nothing here is implemented yet.
Supersedes `docs/windows-app-plan.md`. CLI snapshot: branch `snapshot/cli-v1` (commit `3011c29`).

Decided so far: **Tauri 2** shell (Windows first, then Linux/macOS) · **Zendriver** as the browser driver ·
**Chrome for every worker** (2026-11-01).

Considered and rejected: a hybrid where plain HTTP workers do the bulk scraping. A test on 2026-11-01 showed the
existing parsers work on raw HTML (search and most repo fields; `branches`, `contributors`, `languages`,
`last_commit_at` were missing). It was rejected because plain HTTP clients get blocked in long sessions, and HTTP
workers can't be watched live. Lighter engines (Lightpanda, headless-shell) were rejected for detectability and no
live view. RAM is instead kept down by sharing contexts inside Chrome (§5) and the levers in §8.

Browser binary: `Config(browser=...)` accepts `chrome`, `msedge` and `brave`. Default `chrome`; `msedge` is offered
as a fallback on Windows, where it is preinstalled (same Chromium engine and weight, so stealth needs a quick
re-check in the spike).

Facts about Zendriver below come from its docs (zendriver.dev, release 0.17.1 of 2026-10-01). Anything marked
**(verify)** is an assumption about its API that Phase 0 must confirm before code depends on it.

---

## 1. Goals

1. See every running browser, one or several at once, inside the app.
2. Browsers produce no visible OS window and no streaming cost unless the user is looking at them.
3. Lightweight: minimal app overhead, and fewer/cheaper Chrome processes.
4. Advanced search/filter over all scraped data, with saved datasets.
5. Export any view to JSON and CSV for Excel.

## 2. Why Zendriver, and what it costs

- **CDP-direct:** Zendriver talks to Chrome over the DevTools Protocol. There is no Selenium and no chromedriver
  process, so there's one process tree less per browser. There's also no UC-mode "disconnect while the page loads",
  which in today's code would freeze a live view.
- **Stealth:** an independent Sept 2026 test (Chrome 152, Windows 11) found Zendriver indistinguishable from real
  Chrome when run headed. In headless mode it leaked through missing Client Hints. Plan: run headed but invisible
  by default (§6).
- **Async Python:** fits "N browsers + live screencasts in one process" without threads.
- **Raw CDP:** `tab.send(cdp.<domain>.<command>(...))` and `tab.add_handler(cdp.<domain>.<Event>, fn)`
  (documented in its CDP tutorial). Everything Zendriver doesn't wrap (screencast, input, resource blocking,
  window bounds) goes through this.
- **Per-context proxies (0.17.0):** `Browser.create_context()` opens a tab in a new browser context with its own
  proxy, including authenticated HTTP/HTTPS/SOCKS5. That makes the worker model in §5 possible.
- **Costs:**
  - **AGPL-3.0.** Assumed: the app is for personal/internal use. If it's ever shipped to others, the app's
    source must be offered under AGPL. **Please confirm.**
  - The `Config` has no `proxy` parameter. Proxies go through `create_context()`, or `--proxy-server` in
    `browser_args` for unauthenticated ones.
  - Young project (0.x). API changes between minor versions are likely, so pin the exact version and wrap it
    behind our own interface.

## 3. Architecture

```
┌──────────── Tauri 2 (Rust shell, system webview) ────────────┐
│ UI (Svelte + Vite)                                            │
│  ├─ Tauri commands: start/stop sidecar, dialogs, paths        │
│  └─ WebSocket ws://127.0.0.1:<random>  (token auth) ──┐       │
└────────────────────────────────────────────────────────┼──────┘
┌──────────── Python sidecar: ghscraper (PyInstaller) ───▼──────┐
│ asyncio event loop                                            │
│  api/       WebSocket server: JSON-RPC commands + events,     │
│             binary frames for screencast                      │
│  core/      RunManager · Scheduler · Worker                   │
│  browser/   BrowserPool (Zendriver) · ScreenRelay · Input     │
│  store/     SQLite + FTS5 · filter→SQL · migrations           │
│  parsers.py filters.py techstack.py exporters.py   (kept)     │
└───────────────────────────────────────────────────────────────┘
          │ CDP websocket (Zendriver)
          ▼
   chrome ×1–N (headed, window hidden)  ── contexts/tabs = workers
```

- The **sidecar is the only thing that talks to Chrome**. The UI never gets a CDP endpoint.
- The **WebSocket** binds to `127.0.0.1` on a random port and requires a token that Tauri generates and passes to
  both sides at launch. Without that, any local process or web page could drive the browsers.
- **Messages:** JSON-RPC 2.0 for commands (`run.start`, `run.stop`, `browser.view`, `browser.input`,
  `data.query`, `export.write`…) and events (`worker.status`, `run.progress`, `repo.saved`, `log`). Screencast
  frames go as binary messages: `[browser id][frame no][jpeg bytes]`.
- **Supervision:** Tauri spawns the sidecar and kills it on exit. The sidecar also exits if its stdin closes.

## 4. Code layout (sidecar)

```
ghscraper/
  parsers.py  filters.py  techstack.py  exporters.py     # unchanged / extended
  config.py                                              # + app-data paths, + new browser options
  core/
    runmanager.py   # one active run; start/stop/pause; emits events
    scheduler.py    # today's priority queue + dedupe logic, asyncio version
    worker.py       # takes tasks, drives one BrowserSlot
  browser/
    backend.py      # BrowserBackend / BrowserSlot protocols (our interface)
    zendriver_backend.py
    stealth.py      # pauses, human typing/scrolling, block detection + shared per-IP cooldown
    screen.py       # ScreenRelay: screencast start/stop/ack, fps policy
    input.py        # forward mouse/keyboard from UI
    procguard.py    # kill-on-exit per OS (Job Object / PDEATHSIG / pgroup)
  store/
    db.py  schema.sql  migrations/  query.py  importer.py
  api/
    server.py  protocol.py
  cli.py            # kept, runs on the async core via asyncio.run()
```

### The interface we own

```python
class BrowserSlot(Protocol):          # one worker's browsing surface
    id: str
    async def open(self, url: str) -> str              # navigate, wait, block-check, return HTML
    async def html(self) -> str
    async def type_search(self, query: str) -> bool
    async def pause(self, scale: float = 1.0) -> None
    async def start_screencast(self, fps: int, max_w: int) -> None
    async def stop_screencast(self) -> None
    async def dispatch_input(self, event: dict) -> None
    async def close(self) -> None
```

The scheduler and parsers only see `BrowserSlot`. A fake implementation (like today's `FakeBrowser` in
`tests/test_scheduler.py`) keeps scheduler tests offline. Swapping to Patchright later means one new backend file.

### Mapping today's `browser.py` to Zendriver

| Today (SeleniumBase) | Zendriver equivalent |
|---|---|
| `Driver(uc=True, headless, user_data_dir, proxy, window_size)` | `zd.start(Config(headless=…, user_data_dir=…, browser_args=[…], lang="en-US"))` · proxy via `create_context()` |
| `uc_open_with_reconnect(url)` | `await tab.get(url)` **(verify)**, then wait for `document.readyState` via `tab.evaluate` |
| `driver.page_source` | `await tab.get_content()` **(verify)** |
| `find_element(By.CSS_SELECTOR)` / `.click()` | `await tab.select(css)` / `await el.click()` |
| `send_keys` per char (human typing) | `el.send_keys(ch)` per char with the same jitter, or CDP `Input.insertText` / `dispatchKeyEvent` |
| `execute_script` (scroll, readyState) | `await tab.evaluate(js)` |
| `set_window_position` | `--window-position` arg / CDP `Browser.setWindowBounds` |
| `detect_block(html, title)` | unchanged (parsers.py) |
| Shared per-proxy cooldown dict + lock | same idea, `asyncio.Lock` |

## 5. Worker model: contexts inside few Chromes (the main RAM lever)

Today: 1 worker = 1 Chrome = ~250–500 MB (typical figure, not measured here).

New: a **BrowserPool** runs `P` Chrome processes and gives each worker its own **browser context**
(`create_context()`): separate cookies/storage, optional own proxy, own tab. The Chrome browser and GPU
processes are shared, so each extra worker costs roughly one renderer instead of a whole browser.

| Setting | Default | Meaning |
|---|---|---|
| `workers` | 3 | parallel scraping slots (cap 5) |
| `workers_per_chrome` | 3 | contexts per Chrome process |
| `isolate_workers` | off | 1 worker = 1 Chrome (today's behaviour), for max isolation |

- **Optimises:** RAM and startup time. One Chrome launch instead of N; contexts open in well under a second.
- **Gives up:** fault isolation (a Chrome crash takes its contexts with it; the pool restarts it and requeues
  in-flight tasks). All contexts in one Chrome share a hardware/browser fingerprint. For GitHub that's fine,
  since rate limits are per IP and the same machine is behind every worker anyway.
- **Logged-in scraping:** a persistent `user_data_dir` belongs to the Chrome's default context, so a logged-in
  worker uses the default context of its own Chrome. Other contexts are logged out unless cookies are loaded
  (Zendriver supports saving and loading cookies).
- **Risk (verify):** a hidden window renders only its active tab. Each context must get **its own window**
  (CDP `Target.createTarget` with `newWindow`), or background tabs get throttled and produce no screencast frames.
  Whether `create_context()` opens a new window is the first spike question. If not, the backend calls CDP
  directly.

## 6. Invisible browsers, visible on demand

**Constraint:** Chrome can't switch between headless and headed while running, and headless leaks under Zendriver.
So by default Chrome runs **headed with its windows hidden**:

| OS | Hiding method |
|---|---|
| Windows (first) | `--window-position=-32000,-32000` plus small size; fallback to check: hidden desktop (`CreateDesktop`) |
| Linux | private Xvfb display per run (`DISPLAY=:N`) |
| macOS | off-screen window bounds; least proven, spike on real hardware |

Required flags so hidden windows aren't throttled: `--disable-backgrounding-occluded-windows`,
`--disable-renderer-backgrounding`, `--disable-background-timer-throttling`.
`headless=True` stays a per-run option for low-risk runs.

### ScreenRelay (live view)

- `browser.view {ids, mode}` from the UI → the sidecar calls `Page.startScreencast` (JPEG, `quality`,
  `maxWidth`, `everyNthFrame`) on those tabs only.
- Each `Page.screencastFrame` is acked (`Page.screencastFrameAck`; Chrome stops sending if frames aren't acked)
  and pushed to the UI as a binary message. Frames are dropped, not queued, if the socket is busy: latest frame wins.
- **FPS policy:** grid tile ~1–2 fps at ≤480 px wide; single view ~10–15 fps at window width; **off-screen tile,
  closed panel or minimised app → `Page.stopScreencast`** (zero cost while not viewed).
- **Interact (single view only):** pointer/keyboard events → `Input.dispatchMouseEvent` /
  `Input.dispatchKeyEvent` with coordinates scaled from tile to viewport. Taking control **pauses that worker's
  task loop**; a "Resume scraping" button gives it back. Typical uses: logging in to GitHub, clearing a challenge.
- "Pop out" (debug): move the real window on-screen with `Browser.setWindowBounds`.

### Browsers panel (UI)

- **Grid:** 1–8 tiles with status chip (`starting · searching "x" · repo owner/name · backing off 40s · paused
  by you · done · failed`), current URL, pages done, RAM.
- **Single:** one large interactive browser + its log lines.
- **List:** status rows only, no video (the cheapest view).
- Pool header: Chrome processes, total RAM, run progress (queries done, repos done/total, ETA from today's `estimate()`).

## 7. Lifecycle and safety

- `procguard.py` makes sure Chrome dies with the sidecar:
  - **Windows:** Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, via `ctypes`. Chrome's PID comes from
    Zendriver's process handle **(verify; may be private)**, or from `psutil` children of the sidecar.
  - **Linux:** launch in its own process group plus `PR_SET_PDEATHSIG`.
  - **macOS:** process group, killed on exit, plus a small watchdog.
- **Worker health:** per-page timeout, and a crashed tab or context is recreated with its task requeued (today's
  retry/back-off logic carries over).
- **Memory hygiene:** recycle a context every N pages (default 150); optional per-Chrome RAM ceiling triggers a recycle.

## 8. Lightweight checklist

- Block images, fonts and media with `Network.setBlockedURLs` (parsers need only HTML and embedded JSON). On by
  default if the spike shows no rise in blocks.
- Small viewport (e.g. 1280×800) and `--renderer-process-limit` tuned in the spike.
- Contexts instead of Chromes (§5); screencast only when viewed (§6).
- Sidecar: no pandas or other heavy deps. SQLite is stdlib; `psutil` and `zendriver` are the main additions.
- UI: virtualised grid, paged queries (never send the whole dataset to the webview).
- Targets to measure in Phase 0: idle app < 200 MB RAM in total; each extra worker in shared mode well under one full Chrome.

## 9. Data: storage, search, filters, datasets

SQLite + FTS5 in the app-data dir, WAL mode, a single writer task in the sidecar.
**Schema needs your sign-off before Phase 2** (data model, costly to change later):

- `runs(id, started_at, finished_at, status, config_json, counts…)`
- `repos(full_name PK, url, description, website, stars, forks, watchers, language, license, default_branch,
  branches, tags, releases, commits, contributors, open_issues, open_pull_requests, created_at, updated_at,
  last_commit_at, archived, is_fork, is_template, owner, error, scraped_at, raw_json)`
- `repo_topics(repo, topic)`, `repo_tech(repo, tech)`, `repo_languages(repo, language, pct)`: indexed
- `repo_runs(repo, run_id, query)`: provenance (replaces `matched_queries`)
- `repos_fts`: FTS5 over full_name, description, topics, website
- `datasets(id, name, filter_json, columns_json, sort_json)`
- Importer for existing `output/*.json`.

**UI:** search bar (FTS, prefix match) plus a filter sidebar: ranges (stars, forks, watchers, contributors, issues,
PRs), language with min %, topics any/all/none, tech stack, licence, created/last-commit dates, archived/fork/
template, has website, found-by query, run. The grid is virtualised, sortable and resizable, with a column chooser,
a row-detail drawer and an "open on GitHub" link. Saved datasets sit in the sidebar. `store/query.py` compiles filter
JSON to parameterised SQL; the UI never sends SQL.

## 10. Export

- Scope: current filtered view · selected rows · whole dataset · single run.
- **JSON:** array of objects, same field names as today.
- **CSV for Excel:** UTF-8 BOM, visible columns in grid order, lists/dicts flattened (today's `_cell`), cells
  starting with `= + - @` prefixed with `'` to block formula injection.
- Optional XLSX via `openpyxl` (decision 3).
- Files are written by the sidecar, to a path chosen in Tauri's native save dialog.

## 11. Phases

| # | Work | Depends on | Done when |
|---|---|---|---|
| 0 | **Zendriver spike** (~3–4 days): (a) GitHub search + 50 repo pages, headed-hidden vs headless, block rate; (b) `create_context()` + own window per context, screencast from 3 hidden contexts at once; (c) input forwarding (log in to GitHub through a tile); (d) Windows Job Object kill-on-exit; (e) RAM: isolated vs shared contexts, with/without resource blocking; (f) confirm every **(verify)** item | — | Written go/no-go with numbers |
| 1 | Core port: `BrowserSlot` + Zendriver backend, async scheduler/worker, stealth.py, procguard (Windows), app-data config, CLI on new core; port `test_scheduler.py` to a fake slot | 0 | All 25 existing tests pass (ported); CLI output matches `snapshot/cli-v1` on a fixed query set |
| 2 | Store: schema, migrations, writer, FTS, importer, query compiler + tests | 1, schema sign-off | Filter tests pass on imported real data |
| 3 | Sidecar API: WebSocket server, token auth, JSON-RPC, events, binary frames, ScreenRelay + input | 1 | Scripted client can run a scrape and receive frames |
| 4 | Tauri shell: sidecar spawn/supervise, token handoff, Run screen (queries, filters with `filters.py` validation, preview counts, start/stop), Browsers panel (grid/single/list, interact) | 3 | End-to-end scrape from the app on Windows |
| 5 | Data screen: grid, search/filters, datasets, detail drawer, export | 2, 3 | Filtered CSV opens correctly in Excel |
| 6 | Packaging (Windows): PyInstaller sidecar, Tauri NSIS/MSI, first-run checks (Chrome present, WebView2) | 4, 5 | Clean install on a fresh Windows 11 VM |
| 7 | Linux/macOS: Xvfb integration, macOS hiding + watchdog, WebKitGTK UI fixes, CI build matrix | 6 | Same scrape passes on Ubuntu LTS and macOS |

Phases 2 and 3 run in parallel after 1; phases 4 and 5 in parallel after 3.

## 12. Decisions (2026-11-01)

1. **Licence:** personal use only, so AGPL (Zendriver) is fine.
2. **Data history:** latest scrape wins (one row per repo). History can be added later with a migration.
3. **Dependencies:** zendriver pinned at 0.17.1 (installed in `.venv`); psutil already present. The Rust toolchain
   gets installed by Claude via winget when Phase 4 starts. Others (PyInstaller, Tauri, Vite, Svelte) come in at their phases.
4. **Worker cap:** 5 (keep `MAX_BROWSERS = 5`; fix README's "1–8").
5. **Default worker mode:** shared contexts, to be confirmed by the spike's RAM numbers.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Hidden headed windows still throttled, or contexts don't get their own window | Spike (b); CDP `Target.createTarget(newWindow)`; fall back to isolated mode |
| Zendriver API churn (0.x, AGPL) | Pin version; everything behind `BrowserSlot`; Patchright backend as escape hatch |
| GitHub blocks shared-IP workers faster | Shared per-IP cooldown kept; per-context proxies; conservative default delays |
| macOS window hiding unproven | Phase 7 on real hardware; headless fallback there |
| WebKitGTK UI quirks on Linux | Test early with a minimal Tauri build in Phase 4 |
| Parsers break when GitHub changes markup | Unchanged risk; fixture tests |
| ToS | Same as today: official API is the sanctioned route for large or commercial use |

## Sources

- [Zendriver docs](https://zendriver.dev/) · [Config reference](https://zendriver.dev/reference/core/config/) · [CDP tutorial](https://zendriver.dev/tutorials/cdp/) · [Release notes](https://zendriver.dev/release-notes/)
- [Zendriver repository](https://github.com/cdpdriver/zendriver)
- [I Tested 7 Stealth Browsers Against 6 Detection Pages (Sept 2026)](https://ijazurrahim.com/blog/stealth-browsers-tested-2026.html)
