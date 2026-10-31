# GitHub Ghost Scraper

Stealth GitHub repository scraper built on **SeleniumBase UC mode** (undetected Chrome).
Search by keyword and get, per repo: link, description, website, stars, forks, watchers,
branches, tags, releases, commits, contributors, open issues/PRs, languages (%),
detected tech stack, topics, license, default branch, created / last-commit dates.

## Setup (isolated venv — never global Python)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Requires Google Chrome installed. SeleniumBase downloads a matching chromedriver on first run.

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

- `browsers` (1–8, hard cap `MAX_BROWSERS` in `config.py`) parallel sessions share **one task queue**.
  Searches (one per query) are queued first; each finished search pushes its repo pages back
  onto the queue, so detail scraping is spread over every browser, even with a single query.
- `max_results` is **per query**. A repo matched by several queries is scraped once;
  `matched_queries` lists all of them (`query` = the first that found it).
- With deep scrape off, no more browsers are launched than there are queries.
- Browsers launch one at a time (UC mode patches chromedriver on disk) and cascade their windows.
- A rate-limit hit pauses **every** session sharing that exit IP. Comma-separate proxies
  (`p1:port, p2:port`) to give each browser its own IP, round-robin.
- `user_data_dir`: Chrome locks a profile, so browser 1 uses the dir as-is and browsers 2+ use
  `<dir>-2`, `<dir>-3`… — log in to each separately if you want logged-in limits everywhere.
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

- UC mode: patched chromedriver, `navigator.webdriver` hidden, driver detached during page load.
- Optional human typing into GitHub's header search box, randomised delays, scrolling, occasional long pauses.
- Rate-limit / abuse page detection with exponential back-off.
- Optional proxy and persistent Chrome profile (`user_data_dir`) — log in once manually in a
  visible run to get higher logged-in search limits.
- Headless is more detectable than a visible window; use visible mode if you get blocked.

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
