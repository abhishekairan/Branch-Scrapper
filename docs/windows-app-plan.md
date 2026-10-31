# Plan: GitHub Ghost Scraper → cross-platform desktop app (Tauri)

> Product renamed to Branch Scrapper in v0.1.1.

Status: **draft v2 for review** (2026-11-01). Nothing here is implemented yet.
Snapshot of the CLI before this work: branch `snapshot/cli-v1` (commit `3011c29`).

Changes since v1: shell is **Tauri 2** (Windows first, Linux and macOS supported); Selenium/SeleniumBase is
replaced by a **CDP-direct driver**; browsers run **invisible** and are only rendered when the user views them.

## 1. Goals

1. **See every running browser** — one or many at once — inside the app window.
2. **Browsers run without a visible window (headless-like) when not being viewed**, and cost no rendering/streaming then.
3. **Stay lightweight**: the app must cost little; the browsers are the expensive part.
4. **Browse the data** with advanced search and filters, and save named datasets.
5. **Export** any data (whole dataset or current filtered view) to **JSON** and **CSV for Excel**.
6. **Cross-platform**: Windows first, then Linux and macOS from the same codebase.

## 2. What the current code gives us (read from the repo)

| Module | Fate | Notes |
|---|---|---|
| `parsers.py`, `filters.py`, `techstack.py` | Keep as is | Pure Python, covered by `tests/`. Parsers work on HTML strings, so they don't care which driver produced the HTML. |
| `scraper.py` `GitHubScraper` | Port to asyncio | Queue/dedupe/priority logic carries over. Threads → asyncio tasks, because the new driver is async. Callbacks (`on_status`, `on_listing`, `on_repo`) become events sent to the UI. |
| `browser.py` `StealthBrowser` | Rewrite | Same public surface (`open`, `pause`, `human_scroll`, `type_search`, shared per-IP cooldown, `detect_block`), new driver underneath. |
| `exporters.py` | Extend | CSV already writes UTF-8 with BOM (Excel-safe). Needs arbitrary row list + column selection. |
| `config.py` | Small change | Move `config.json` and data to the per-OS app-data dir. Resolve `MAX_BROWSERS = 5` vs README "1–8". |
| `cli.py` | Keep | Stays as a second entry point on top of the same core. |

## 3. Architecture

```
┌──────────── Tauri 2 app (Rust shell, system webview) ────────────┐
│  UI: Svelte + Vite static build                                  │
│   ├─ Tauri commands/events ──► Rust: spawn/supervise sidecar,    │
│   │                            file dialogs, app-data paths      │
│   └─ WebSocket 127.0.0.1:<random port>, per-launch token ──┐     │
└────────────────────────────────────────────────────────────┼─────┘
                                                             │
┌──────────── Python sidecar (PyInstaller, one per OS) ──────▼─────┐
│  asyncio core: RunManager · Scraper · Store (SQLite+FTS5)        │
│  StealthBrowser ×N (zendriver, CDP) ──► chrome ×N (no window)     │
│  ScreenRelay: CDP screencast for *viewed* browsers only           │
│  Exporters: JSON / CSV / optional XLSX                            │
└───────────────────────────────────────────────────────────────────┘
```

### Why a Python sidecar (and not Rust or Node)

- **Optimises:** reuse. ~1,100 lines of tested parsers/filters/tech-stack logic stay untouched.
- **Gives up:** a second runtime in the bundle (PyInstaller adds roughly 30–60 MB on disk per platform — estimate,
  not measured) and a per-OS build (PyInstaller can't cross-compile, so CI needs a Windows/Linux/macOS matrix).
- **Alternatives passed over:** a Rust driver (`chromiumoxide`, or `zendriver-rs` which has a stealth crate but is
  at 0.2.x) would remove Python entirely but means rewriting parsers and filters, and the Rust stealth ecosystem
  is much younger. A Node sidecar (Patchright/Puppeteer) has the same rewrite cost.
- **Reversibility:** the WebSocket protocol between UI and sidecar is the contract; a later Rust rewrite of the
  sidecar would not touch the UI.

### Why a local WebSocket for UI ⇄ sidecar

Screencast frames are a continuous binary stream. Relaying them sidecar → Rust → Tauri event → webview would
base64 and copy every frame twice. The webview connects straight to the sidecar instead. The socket binds to
`127.0.0.1` only, on a random port, and requires a token Tauri hands to both sides at launch — it controls
browsers, so it must not be reachable by other local apps or web pages.

Note: Tauri uses WebView2 on Windows, WKWebView on macOS and **WebKitGTK on Linux**. The UI has to be tested on
all three; WebKitGTK is the one most likely to need workarounds (grid performance, WebSocket/CSP quirks).

## 4. Browser driver: replacing Selenium

Researched 2026-11-01. The strongest evidence is an independent test from Sept 2026 (6 tools, Chrome 152,
Windows 11, headed and headless) — see Sources. Benchmarks in this space conflict and age fast, so Phase 0
re-tests on GitHub itself.

| Option | Engine / API | Headed stealth (Sept 2026 test) | Headless stealth | Live screencast | Licence | Notes |
|---|---|---|---|---|---|---|
| **Zendriver** (recommended) | Chrome, CDP-direct, async Python | Clean (matched real Chrome) | Leaks via missing Client Hints (`sec-ch-ua`) | Yes, raw CDP access (to verify in spike) | **AGPL-3.0** | Maintained fork of nodriver; no chromedriver process → lighter |
| Nodriver | Chrome, CDP-direct | Clean | Leaks `HeadlessChrome` UA | Yes | AGPL-3.0 (to verify) | Maintenance described as slowing |
| SeleniumBase CDP mode | Chrome, Selenium + CDP | Clean | Client Hints leak | Partly (same UC disconnect issue) | MIT | Smallest migration from today's code, keeps chromedriver overhead |
| Patchright | Chrome, Playwright API (Py/Node) | Near-clean | Leaks `HeadlessChrome` UA | Yes (CDP session) | Apache-2.0 (to verify) | Best if distribution licence matters |
| Camoufox | **Firefox**, Playwright API | Clean | **Only tool clean headless** | No CDP screencast — screenshot polling only | MPL-2.0 (to verify) | README: "may not be suitable for stable production use"; slow startup |

**Recommendation: Zendriver**, with the driver behind a small `BrowserBackend` interface so Patchright or
Camoufox can be swapped in per run.

- **Optimises:** stealth in headed mode, low overhead (Chrome talks CDP directly — no chromedriver process per
  browser, and no UC "disconnect during page load" problem that blocked live view in plan v1), async model
  that fits many browsers + screencast in one process.
- **Gives up:** **AGPL-3.0**. Fine for personal/internal use. If you distribute the app to others, AGPL
  obligations apply (provide source of the combined work). If that's not acceptable, Patchright is the swap.
  **This depends on your answer to decision 2 in §9.**

## 5. "Headless when not viewed" — how it actually works

**Constraint:** a running Chrome cannot switch between headless and headed. And per the test above, every
Chromium tool leaks something in true headless mode, while all of them pass headed. GitHub's own bot detection is
lighter than those test pages, but headless remains the weaker option.

So the plan separates *"has a visible OS window"* from *"is headless"*:

**Default — invisible headed (recommended):** Chrome runs headed (full real-browser fingerprint) but its window is
never shown on the user's desktop:

| OS | How the window is hidden |
|---|---|
| Linux | Private virtual display (Xvfb) per run — the standard approach, the same one Camoufox uses for `headless="virtual"` |
| Windows | Window placed off-screen (fallback to check in the spike: a separate hidden desktop via `CreateDesktop`) |
| macOS | Window placed off-screen; no virtual display equivalent, so this is the platform to test hardest |

Chrome throttles windows it thinks are hidden, which would slow scraping. Launch with
`--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding`,
`--disable-background-timer-throttling`.

**Optional — true headless** (`--headless=new`): per-run toggle for lower RAM on runs where detection isn't an
issue. Same viewing behaviour.

**Viewing, in both modes:**

- The **Browsers** panel shows one tile per browser: status (`starting / searching "x" / repo owner/name /
  backing off 40s / done / failed`), current URL, pages done, RAM.
- Layouts: **single** (one browser large), **grid** (2–8 tiles), **list** (status rows only, no video).
- Only tiles on screen stream, via CDP `Page.startScreencast` (JPEG): thumbnails ~1–2 fps, focused tile ~10–15
  fps. When a tile scrolls off-screen, the panel is closed, or the app is minimised, the sidecar sends
  `Page.stopScreencast` — the browser keeps scraping with zero streaming cost. That is the "headless when not in
  active view" behaviour.
- **Interact:** in single view, mouse/keyboard are forwarded with `Input.dispatchMouseEvent` / `dispatchKeyEvent`,
  so the user can log in to GitHub or clear a challenge from inside the app. Scraping for that browser pauses
  while the user has control.
- Optional per browser: "pop out real window" for debugging (moves the window on-screen; not available in true headless).

## 6. Lifecycle: no orphaned Chromes

If the app or sidecar dies, every Chrome must die too:

- **Tauri → sidecar:** Rust supervises the sidecar and kills it on exit; the sidecar also exits if its stdin closes (parent gone).
- **Sidecar → Chromes:**
  - Windows: a Job Object with `KILL_ON_JOB_CLOSE`, via `ctypes` (no `pywin32` needed).
  - Linux: own process group plus `PR_SET_PDEATHSIG`.
  - macOS: own process group, killed on exit. A crash-safe watchdog is a spike item, since macOS has no PDEATHSIG.
- Per-browser RAM from `psutil`; optional memory cap enforced by restarting the browser.

## 7. Staying lightweight

The app shell itself is small; the Chromes use most of the memory. Typical figures, **not measured here**:

| Component | Expected |
|---|---|
| Tauri shell + UI (Windows/WebView2) | ~50–120 MB RAM, installer ~10 MB before the sidecar |
| Python sidecar | ~60–120 MB RAM |
| Each Chrome (headed, default flags) | ~250–500 MB |

Levers, each benchmarked in Phase 0 because some may raise detection risk: block images/fonts/media
(`Network.setBlockedURLs`; the parsers only need HTML + embedded JSON), `--renderer-process-limit`, small fixed
viewport, restart each browser every N pages, stream only viewed tiles, true headless where acceptable.
UI side: virtualised grid, paged SQLite queries, no full dataset in memory. Live total-RAM meter with a warning before launching more browsers than the machine can hold.

## 8. Data: storage, search, filter, datasets

**SQLite + FTS5** in the sidecar (stdlib, single file in the app-data dir). Proposed schema — **this is the data model and needs your review before Phase 2**:

- `runs` — id, start/end, config snapshot (JSON), status, counts.
- `repos` — one row per `full_name`, all current CSV columns; list fields also stored as JSON.
- `repo_topics`, `repo_tech`, `repo_languages(repo, language, pct)` — indexed side tables for fast filters.
- `repo_runs(repo, run, query)` — which run/queries found a repo (replaces `matched_queries`).
- `repos_fts` — FTS5 over name, description, topics, website.
- `datasets` — saved views: filters + columns + sort.
- One-time import of existing `output/*.json`.

**Data view:** full-text search plus a filter sidebar: numeric ranges (stars, forks, watchers, contributors,
issues), language with min %, topics (any/all/none), tech stack, licence, created/last-commit dates,
archived/fork/template, has website, found-by query, found-in run. The grid is virtualised, sortable and resizable,
with a column chooser and a row-detail drawer. "Save as dataset" stores the current view. Filters compile to
parameterised SQL in Python, so no SQL is built from UI strings.

## 9. Export

- Scope: current filtered view, selected rows, or whole dataset.
- **JSON** (same shape as today) and **CSV for Excel** (existing BOM + flattening; visible columns in grid order;
  cells starting `= + - @` are prefixed with `'` to block formula injection from scraped descriptions).
- Optional **XLSX** via `openpyxl` (typed cells, frozen header, autofilter).
- Native save dialog through Tauri's dialog plugin.

## 10. Phases

| Phase | Work | Depends on | Done when |
|---|---|---|---|
| 0. Spike (~3–4 days) | Zendriver on GitHub search + repo pages (block rate, headed-invisible vs headless); screencast + input forwarding; hidden-window approach per OS (Windows first); kill-on-exit; RAM per flag set; minimal Tauri ↔ sidecar WebSocket | — | Written go/no-go with measured numbers |
| 1. Core port | `BrowserBackend` interface + zendriver impl; asyncio scraper; app-data paths; CLI on new core; tests green | 0 | CLI produces same output as `snapshot/cli-v1` on a fixed query set |
| 2. Storage | Schema + migrations, writer, FTS5, JSON import, filter→SQL builder + tests | 1, **schema sign-off** | Filter tests pass |
| 3. App: runs & browsers | Tauri shell, sidecar supervision, query/filter editor (reusing `filters.py` validation), preview counts, start/stop, Browsers panel (single/grid/list, live tiles, interact), log pane | 1 | End-to-end scrape from the app on Windows |
| 4. App: data & export | Grid, search/filter sidebar, datasets, detail drawer, JSON/CSV(/XLSX) export | 2 | Filtered export opens correctly in Excel |
| 5. Packaging | PyInstaller sidecar per OS; Tauri bundles (MSI/NSIS, AppImage/deb, dmg); CI matrix; first-run checks (Chrome installed; WebView2 on Windows) | 3, 4 | Clean install on fresh Windows 11, Ubuntu LTS and macOS |
| 6. Linux/macOS polish | Xvfb integration, macOS hidden-window and watchdog, WebKitGTK UI fixes | 5 | Same scrape passes on all three |

Phases 3 and 4 can run in parallel after 1 and 2.

## 11. Decisions

Decided:
- **Shell: Tauri 2** (2026-11-01).

Still needed:
1. **Driver:** Zendriver (recommended), Patchright, or SeleniumBase CDP mode (least change).
2. **Distribution / licence:** personal or internal use only, or shipped to others? AGPL (Zendriver) is fine for the first; for the second, Patchright (Apache-2.0) or open-sourcing the app. Code signing (Windows certificate, Apple Developer ID + notarisation) is also only needed for the second.
3. **New dependencies:** zendriver, psutil, PyInstaller, Rust toolchain + Tauri, Node/Vite/Svelte (dev-time), optional openpyxl; Xvfb on Linux.
4. **Data history:** latest scrape wins vs keep every snapshot (enables trends).
5. **Browser cap:** 5 or 8.

## 12. Risks

- **Stealth:** results from the test pages don't guarantee behaviour on GitHub; Phase 0 measures the real block rate. Screencasting and resource blocking are each switchable.
- **macOS hidden windows** are the least proven part of §5.
- **Three webview engines** mean three sets of UI quirks (WebKitGTK the main one).
- **Chrome must be installed** on the target machine (zendriver drives the system Chrome).
- **GitHub markup changes** break parsers regardless; fixture tests remain the safety net.
- **ToS:** an easier UI makes larger scrapes easier; the official API is still the sanctioned route for large or commercial use.

## Sources

- [I Tested 7 Stealth Browsers Against 6 Detection Pages (Sept 2026)](https://ijazurrahim.com/blog/stealth-browsers-tested-2026.html)
- [Zendriver repository](https://github.com/cdpdriver/zendriver)
- [Camoufox repository](https://github.com/daijro/camoufox)
- [Patchright repository](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
- [Best Stealth Browsers for Web Scraping in 2026 — Scrapfly](https://scrapfly.io/blog/posts/best-stealth-browsers)
- [zendriver-rs releases](https://github.com/turtiesocks/zendriver-rs/releases)
