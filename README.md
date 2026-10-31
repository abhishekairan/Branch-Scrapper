# GitHub Ghost Scraper

Stealth GitHub repository scraper built on **Zendriver** (drives Chrome directly over the DevTools
Protocol, asyncio-based).
Search by keyword and get, per repo: link, description, website, stars, forks, watchers,
branches, tags, releases, commits, contributors, open issues/PRs, languages (%),
detected tech stack, topics, license, default branch, created / last-commit dates.

## Setup (isolated venv — never global Python)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Requires Google Chrome installed (or Microsoft Edge / Brave: `--browser msedge|brave`, or `browser_path`
in `config.json` for a custom executable). There is no chromedriver: Zendriver talks to the browser
over the DevTools Protocol, so nothing else is downloaded on first run.

## Usage

```powershell
# Interactive menu (search, headless toggle, settings editor)
.\.venv\Scripts\python.exe main.py

# One-shot, flags override config.json for this run only
.\.venv\Scripts\python.exe main.py -q "web scraper" -n 50 --language python --sort stars --headless

# Several queries (comma/semicolon list, repeated -q, or a file) across 4 browsers
.\.venv\Scripts\python.exe main.py -q "web scraper, selenium stealth" -q "cli tool" -n 20 -b 4 --headless
.\.venv\Scripts\python.exe main.py --queries-file queries.txt -b 3
.\.venv\Scripts\python.exe main.py -h
```

In the interactive menu, type keywords as `web scraper; cli tool; rust http` or `@queries.txt`.
Commas also separate queries, except inside a qualifier (`in:name,description` stays one query).

## Interactive menu

The **search plan** panel is always on screen: queries, filters, sort, sessions, output, the exact
string sent to GitHub, a rough time estimate, and warnings (e.g. `topc:` → did you mean `topic:`?).
Shortcut keys: **r** run · **p** preview counts · **q** queries · **f** filters · **s** sort & limit ·
**h** headless · **b** browser & stealth · **d** data · **o** output · **x** exit.
**Preview counts** loads only page 1 of each search and shows GitHub's match count, so you can
tune filters before a long run. A full run shows its estimate and asks before starting.

## Filters

Filters apply to every query, are saved in `config.json` (`filters`), and use the same names as
`gh search repos`. You can also write qualifiers inline in a query (`shop topic:ecommerce stars:>20`).

| Flag | GitHub qualifier | Example |
|---|---|---|
| `--topic` / `--exclude-topic` | `topic:` / `-topic:` (all required) | `--topic nextjs,gsap` |
| `--language` / `--exclude-language` | `language:` | `--language python` |
| `--owner` | `user:` | `--owner vercel` |
| `--license` | `license:` | `--license mit,apache-2.0` |
| `--match` | `in:` | `--match name,description` |
| `--stars` `--forks` `--followers` `--size` (KB) `--number-topics` `--good-first-issues` `--help-wanted-issues` | numeric | `100`, `100+`, `>=100`, `<50`, `10..50`, `1.5k` |
| `--created`, `--pushed` (alias `--updated`) | dates | `2024-01-01`, `"before 2024-01-01"`, `"after 30d"`, `"since 6m"`, `"within 1y"`, `"between 2024-01-01 and 2024-06-30"` |
| `--archived true\|false` | `archived:` | |
| `--include-forks false\|true\|only` | `fork:` (GitHub hides forks by default) | |
| `--template`, `--mirror` | `template:`, `mirror:` | |
| `--sponsorable`, `--funding-file` | `is:sponsorable`, `has:funding-file` | |
| `--qualifier` | anything else, raw | `--qualifier "-language:php topics:>3"` |

Sort (`--sort stars|forks|updated --order desc|asc`) matches GitHub's own sort menu.
`--count` (alias `--dry-run`) previews match counts only. `--clear-filters` ignores saved filters.
`--save` stores the flags' settings in `config.json`.

- Relative dates (`30d`, `2w`, `6m`, `1y`) are kept relative in the config and resolved on each run.
- Prefer the word forms (`before …`) on Windows: `<` and `>` are redirects in cmd and need quoting.
- **GitHub never reports a bad qualifier value**: `stars:abc` or `created:notadate` silently return
  0 results. The scraper validates every filter before running and flags queries that match 0.

## Multiple browsers & queries

- `browsers` (1–5, hard cap `MAX_BROWSERS` in `config.py`; `-b N`) parallel workers share **one task queue**.
  Searches (one per query) are queued first; each finished search pushes its repo pages back
  onto the queue, so detail scraping is spread over every worker, even with a single query.
- Workers are browser **contexts** (own cookies, own window, own proxy) inside a few Chrome processes:
  `workers_per_chrome` (default 3, `--workers-per-chrome`) workers share one Chrome, so 3 workers run in
  one Chrome instead of three. A measured sample (single run, Chrome 156, Windows) put 3 workers at about
  630 MB (USS) in one Chrome vs about 940 MB in three. `isolate_workers` (`--isolate-workers`) goes back to
  one Chrome per worker for maximum isolation, at the price of RAM. If a Chrome or tab dies, the worker's
  context (or that Chrome) is recreated and its task is retried (at most 2 times).
- `block_resources` (`--block-resources`) blocks images, fonts and media; the parsers only need HTML.
- `max_results` is **per query**. A repo matched by several queries is scraped once;
  `matched_queries` lists all of them (`query` = the first that found it).
- With deep scrape off, no more browsers are launched than there are queries.
- Chrome processes launch one at a time with a short random gap. Windows are headed but parked
  off-screen (`--window-position=-32000,-32000`), so nothing appears on your desktop.
- A rate-limit hit pauses **every** session sharing that exit IP. Comma-separate proxies
  (`p1:port, p2:port`) to give each browser its own IP, round-robin.
- `user_data_dir`: Chrome locks a profile, so Chrome process 1 uses the dir as-is and processes 2+ use
  `<dir>-2`, `<dir>-3`…. Only the first worker of each Chrome runs inside that persistent profile
  (logged in); the other workers sharing that Chrome are fresh logged-out contexts. With
  `--isolate-workers` every worker has its own profile. A proxy for a profile worker can't use
  `user:pass@` credentials (use `host:port`).
- All sessions share your IP unless you use proxies: 4 browsers ≈ 4× the request rate, so
  raise `min_delay`/`max_delay` if you start seeing back-off messages.

Results go to `output/<query>_<timestamp>.json` and `.csv`. Settings persist in `config.json`
(created on first run, editable by hand or via the menu).

## How it extracts data (from analysing GitHub's markup)

GitHub's search and repo pages are React apps that ship their data as JSON in
`<script data-target="react-app.embeddedData">`. Class names are hashed CSS modules
(`Button__AyoG5z`) and change between deploys, so the scraper never relies on them.

| Data | Source |
|---|---|
| Search results, stars, language, topics | `payload.blackbirdSearchRoute.results[]` (DOM fallback: `[data-testid="results-list"]`) |
| Description, website, topics, stars, forks, watchers, license, releases, tags | `payload.sidebarAbout` |
| Default branch, created date, fork/archived | `payload.codeViewLayoutRoute.repo` |
| Root files (tech stack), commit count | `payload.codeViewRepoRoute.tree.items`, `.overview.commitCount` |
| Languages % | `span[role=progressbar][aria-label="Lang: N%"]` |
| Branches | `a[href="/{repo}/branches"]` text |
| Contributors | `h2 a[href$="/graphs/contributors"]` + `[data-component=CounterLabel]` |
| Issues / PRs | repo tab `a[href$="/issues"]` counter (hidden = 0) |

Tech stack = languages ≥1% + root-file signatures (Dockerfile, next.config.*, Cargo.toml…)
+ optional dependency scan of package.json / requirements.txt / pyproject.toml / Cargo.toml /
go.mod / composer.json fetched from `raw.githubusercontent.com`.

## Stealth measures

- Zendriver drives Chrome over CDP with no chromedriver and no webdriver binary; in a spike run,
  `navigator.webdriver` was `false` and the user agent / Client Hints matched a normal Chrome (headed).
- Chrome runs headed but off-screen by default. `--headless` works, but it is easier to detect: in
  that spike headless Chrome reported empty `userAgentData.brands` (missing Client Hints).
- Optional human typing into GitHub's header search box, randomised delays, scrolling, occasional long pauses.
- Rate-limit / abuse page detection with exponential back-off.
- Optional proxy and persistent Chrome profile (`user_data_dir`). The scraper's windows are off-screen,
  so to get higher logged-in search limits log in beforehand: start Chrome yourself with
  `--user-data-dir=<that dir>`, sign in to GitHub, close it, then run the scraper with the same dir.
  (Not tested end to end yet.)
- Chrome processes are placed in an OS job (Windows) or killed on exit (other platforms, best effort),
  so closing or killing the scraper does not leave browsers behind.

## Limits

- GitHub search returns at most 1000 results (100 pages × 10 when logged out).
- Deep scrape costs one page load per repo (~3–8 s each with default delays).
- Respect GitHub's Terms of Service; for large-scale or commercial collection the official
  REST/GraphQL API (5000 req/h with a token) is the sanctioned route.

## Tests

Offline parser tests against saved pages in `tests/fixtures` — re-capture them if GitHub changes its markup:

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```
