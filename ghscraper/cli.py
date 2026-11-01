"""Interactive CLI (questionary + rich) with a non-interactive flag mode.

UX follows clig.dev: the current search plan is always visible, every prompt has
a flag equivalent (named like `gh search repos`), input is validated before a
long run starts, expensive runs show an estimate and ask first, and a cheap
"preview counts" dry run lets you tune filters before scraping.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

import questionary
from questionary import Choice, Separator
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__
from .browser.backend import plan_chromes
from .config import (BROWSER_CHOICES, CONFIG_PATH, MAX_BROWSERS, SORT_CHOICES, SORT_OPTIONS, Config,
                     parse_queries)
from .exporters import export
from .filters import (FILTERS, LICENSE_HINTS, MATCH_FIELDS, SPECS, check_query, describe, normalize,
                      to_qualifiers)
from .scraper import GitHubScraper

console = Console()
GITHUB_RESULT_CAP = 1000  # GitHub search never returns more than this per query


# ------------------------------------------------------------------ prompt helpers
def _int(min_v: int = 0, max_v: int | None = None):
    def check(v: str):
        try:
            n = int(v)
        except ValueError:
            return "Enter a whole number"
        if n < min_v or (max_v is not None and n > max_v):
            return f"Must be between {min_v} and {max_v}" if max_v else f"Must be >= {min_v}"
        return True
    return check


def _float(v: str):
    try:
        return True if float(v) >= 0 else "Must be >= 0"
    except ValueError:
        return "Enter a number"


def _ask(q):
    """questionary returns None on Ctrl+C; treat that as 'back'."""
    ans = q.ask()
    if ans is None:
        raise KeyboardInterrupt
    return ans


def _valid(key: str):
    def check(v: str):
        try:
            normalize(key, v)
            return True
        except ValueError as e:
            return str(e)
    return check


def read_queries(text: str) -> list[str]:
    """'a, b; c' -> [a, b, c]. '@file.txt' reads one query per line (# = comment)."""
    text = text.strip()
    if text.startswith("@"):
        lines = Path(text[1:].strip().strip('"')).read_text(encoding="utf-8").splitlines()
        text = "\n".join(l for l in lines if not l.strip().startswith("#"))
    return parse_queries(text)


def _validate_queries(v: str):
    try:
        return bool(read_queries(v)) or "Enter at least one keyword"
    except OSError as e:
        return f"Can't read file: {e.strerror}"


# ------------------------------------------------------------------ plan / views
def _filter_summary(cfg: Config) -> str:
    parts = [f"[cyan]{SPECS[k].label}[/] {describe(k, v)}" for k, v in cfg.filters.items()]
    return "  ·  ".join(parts) if parts else "[dim]none[/]"


def _query_warnings(cfg: Config) -> list[str]:
    return [f"'{q}': {w}" for q in cfg.queries for w in check_query(q)]


def estimate(cfg: Config) -> tuple[int, float, float]:
    """(page loads, low seconds, high seconds) - rough upper bound before dedupe."""
    per_query = min(cfg.max_results, GITHUB_RESULT_CAP)
    pages = len(cfg.queries) * math.ceil(per_query / 10)
    if cfg.deep_scrape:
        pages += len(cfg.queries) * per_query
    workers = cfg.worker_count()
    lo = pages * (cfg.min_delay + 2) / workers
    hi = pages * (cfg.max_delay + 5) / workers
    return pages, lo, hi


def _fmt_dur(s: float) -> str:
    return f"{s / 60:.0f} min" if s >= 90 else f"{s:.0f} s"


def plan_panel(cfg: Config) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold magenta", no_wrap=True)
    t.add_column()
    qs = cfg.queries
    shown = "  ·  ".join(qs[:5]) + (f"  [dim]+{len(qs) - 5} more[/]" if len(qs) > 5 else "")
    t.add_row(f"Queries ({len(qs)})", shown or "[red]none[/]")
    t.add_row("Filters", _filter_summary(cfg))
    t.add_row("Sort · limit", f"{cfg.sort_label()} · up to {cfg.max_results} per query")
    collect = "deep scrape" + (" + manifests" if cfg.fetch_manifests else "") if cfg.deep_scrape \
        else "listing data only"
    workers = cfg.worker_count()
    chromes = len(plan_chromes(workers, cfg.workers_per_chrome, cfg.isolate_workers))
    t.add_row("Sessions", f"{workers} worker(s) in {chromes} {cfg.browser} process(es) · "
                          f"{'headless' if cfg.headless else 'headed, off-screen'}"
                          f"{' · no images/fonts' if cfg.block_resources else ''} · {collect}")
    t.add_row("Output", f"{cfg.output_dir} · {', '.join(cfg.formats)}")
    if qs:
        t.add_row("Sent to GitHub", f"[green]{cfg.search_query(qs[0])}[/]"
                                    + ("  [dim](first query)[/]" if len(qs) > 1 else ""))
    pages, lo, hi = estimate(cfg)
    t.add_row("Estimate", f"≤ {pages} page loads · roughly {_fmt_dur(lo)}–{_fmt_dur(hi)}")
    for w in _query_warnings(cfg):
        t.add_row("[yellow]Warning[/]", f"[yellow]{w}[/]")
    return Panel(t, title=f"Branch Scrapper v{__version__} · search plan",
                 subtitle=f"[dim]{CONFIG_PATH}[/]", border_style="magenta", expand=False)


def show_results(results: list[dict[str, Any]]) -> None:
    t = Table(title=f"{len(results)} repositories", header_style="bold cyan", show_lines=False)
    t.add_column("#", justify="right", style="dim")
    t.add_column("Query", max_width=18, style="dim")
    t.add_column("Repository", style="bold")
    t.add_column("★ Stars", justify="right")
    t.add_column("Forks", justify="right")
    t.add_column("Branches", justify="right")
    t.add_column("Tech stack", max_width=40)
    t.add_column("Website", max_width=30, overflow="fold")
    for i, r in enumerate(results, 1):
        t.add_row(
            str(i),
            ", ".join(r.get("matched_queries") or [r.get("query", "")]),
            f"[link={r['url']}]{r['full_name']}[/link]" + (" [red](err)[/]" if r.get("error") else ""),
            f"{r['stars']:,}" if isinstance(r.get("stars"), int) else "-",
            f"{r['forks']:,}" if isinstance(r.get("forks"), int) else "-",
            str(r.get("branches") or "-"),
            ", ".join(r.get("tech_stack") or [])[:120] or "-",
            r.get("website") or "-",
        )
    console.print(t)


def show_totals(cfg: Config, totals: dict[str, int | None], results: list[dict[str, Any]] | None) -> None:
    t = Table(title="Per query", header_style="bold cyan")
    t.add_column("Query")
    t.add_column("GitHub matches", justify="right")
    if results is not None:
        t.add_column("Collected", justify="right")
    t.add_column("Note", style="dim")
    found = {}
    for r in results or []:
        for q in r.get("matched_queries", []):
            found[q] = found.get(q, 0) + 1
    for q in cfg.queries:
        total = totals.get(q)
        note = ""
        if total == 0:
            note = "[yellow]no matches - loosen filters or check qualifier values[/]"
        elif total is not None and total > GITHUB_RESULT_CAP and cfg.max_results > GITHUB_RESULT_CAP:
            note = f"GitHub serves only the first {GITHUB_RESULT_CAP}"
        elif total is None:
            note = "not searched (stopped or failed)"
        row = [q, "-" if total is None else f"{total:,}"]
        if results is not None:
            row.append(str(found.get(q, 0)))
        t.add_row(*row, note)
    console.print(t)


# ------------------------------------------------------------------ runs
PER_QUERY_BARS = 8  # beyond this many queries, show only the aggregate bar


def _live_run(cfg: Config, count_only: bool) -> tuple[GitHubScraper, bool]:
    n_workers = cfg.worker_count(searches_only=count_only)
    statuses = {i: "[dim]waiting[/]" for i in range(n_workers)}
    progress = Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                        MofNCompleteColumn(), TimeElapsedColumn())

    class View:  # re-rendered by Live on every refresh
        def __rich__(self):
            t = Table.grid(padding=(0, 2))
            for i, s in statuses.items():
                t.add_row(f"[magenta]Browser {i + 1}[/]", s)
            return Group(Panel(t, title="sessions", border_style="dim", expand=False), progress)

    t_search = progress.add_task("Searches", total=len(cfg.queries))
    per_query = {q: progress.add_task(f"  {q[:30]}", total=cfg.max_results)
                 for q in cfg.queries} if len(cfg.queries) <= PER_QUERY_BARS and not count_only else {}
    t_repo = progress.add_task("Repo pages", total=None, visible=cfg.deep_scrape and not count_only)
    finished: set[str] = set()

    def on_status(worker: int, text: str) -> None:
        statuses[worker] = text

    def on_listing(query: str, found: int, target: int) -> None:
        if query in per_query:
            progress.update(per_query[query], completed=found)
        if found >= target and query not in finished:
            finished.add(query)
            progress.advance(t_search)

    def on_repo(repo: dict[str, Any], done: int, total: int) -> None:
        progress.update(t_repo, completed=done, total=total)

    interrupted = False
    with Live(View(), console=console, refresh_per_second=4) as live:
        scraper = GitHubScraper(cfg, log=live.console.print, on_status=on_status,
                                on_listing=on_listing, on_repo=on_repo, count_only=count_only)
        try:
            scraper.run()
        except KeyboardInterrupt:
            interrupted = True
        except Exception as e:
            live.console.print(f"[bold red]Run failed:[/] {type(e).__name__}: {e}")
    return scraper, interrupted


def preview_counts(cfg: Config) -> None:
    console.print(f"\nChecking how many repos match each query "
                  f"(page 1 only, {cfg.worker_count(searches_only=True)} browser(s))...\n")
    scraper, _ = _live_run(cfg, count_only=True)
    show_totals(cfg, scraper.totals, None)
    console.print("[dim]Tip: tighten filters until the counts look right, then run the scrape.[/]")


def run_scrape(cfg: Config, confirm: bool = False) -> None:
    if confirm:
        pages, lo, hi = estimate(cfg)
        console.print(f"\nThis run loads up to [bold]{pages}[/] pages (roughly {_fmt_dur(lo)}–{_fmt_dur(hi)} "
                      f"with {cfg.worker_count()} browser(s)). Duplicates across queries cut that down.")
        if not _ask(questionary.confirm("Start scraping?", default=True)):
            return
    console.print()
    scraper, interrupted = _live_run(cfg, count_only=False)
    results = scraper.results
    if interrupted:
        console.print(f"[yellow]Interrupted - keeping {len(results)} repos scraped so far.[/]")
    show_totals(cfg, scraper.totals, results)
    if not results:
        console.print("[yellow]No repositories collected.[/] Try Preview counts to see which queries match.")
        return
    show_results(results)
    for p in export(results, cfg.queries, cfg.output_dir, cfg.formats):
        console.print(f"[green]Saved[/] {p.resolve()}")


# ------------------------------------------------------------------ editors
def edit_queries(cfg: Config) -> None:
    action = _ask(questionary.select("Queries", choices=[
        Choice("Replace all (type or @file)", "replace"),
        Choice("Add more", "add"),
        Choice("Remove some", "remove", disabled=None if cfg.queries else "no queries"),
        Choice("Back", "back"),
    ]))
    hint = ("Separate with ; or , (commas inside qualifiers like in:name,description are kept). "
            "Inline qualifiers work per query, e.g. cli tool topic:rust")
    if action == "replace":
        cfg.queries = read_queries(_ask(questionary.text(
            "Queries:", default="; ".join(cfg.queries), instruction=hint, validate=_validate_queries)))
    elif action == "add":
        new = read_queries(_ask(questionary.text("Add queries:", instruction=hint, validate=_validate_queries)))
        cfg.queries += [q for q in new if q not in cfg.queries]
    elif action == "remove":
        drop = _ask(questionary.checkbox("Select queries to remove:", choices=cfg.queries))
        cfg.queries = [q for q in cfg.queries if q not in drop]
    for w in _query_warnings(cfg):
        console.print(f"[yellow]Warning:[/] {w}")


def _edit_range(spec) -> Any:
    mode = _ask(questionary.select(f"{spec.label}:", choices=[
        Choice("At least (≥)", ">="), Choice("At most (≤)", "<="), Choice("More than (>)", ">"),
        Choice("Less than (<)", "<"), Choice("Exactly", ""), Choice("Between", ".."),
        Choice("Type an expression (100+, 10..50, 1.5k)", "expr"), Choice("Clear", "clear")]))
    if mode == "clear":
        return None
    if mode == "expr":
        return _ask(questionary.text(f"{spec.label}:", validate=_valid(spec.key)))
    if mode == "..":
        lo = _ask(questionary.text("From:", validate=_int(0)))
        hi = _ask(questionary.text("To:", validate=_int(int(lo))))
        return f"{lo}..{hi}"
    return mode + _ask(questionary.text("Number (1k / 2.5k allowed):", validate=_valid(spec.key)))


def _edit_date(spec) -> Any:
    hint = "YYYY-MM-DD, or relative: 30d, 2w, 6m, 1y (counted back from the run date)"
    mode = _ask(questionary.select(f"{spec.label}:", choices=[
        Choice("Before…", "before"), Choice("After…", "after"),
        Choice("On or after (since)…", "since"), Choice("On or before (until)…", "until"),
        Choice("Between…", "between"), Choice("Within the last… (e.g. 30d, 6m)", "within"),
        Choice("Clear", "clear")]))
    if mode == "clear":
        return None
    check = lambda v: _valid(spec.key)(f"on {v}")  # noqa: E731
    if mode == "between":
        a = _ask(questionary.text("From:", instruction=hint, validate=check))
        b = _ask(questionary.text("To:", instruction=hint, validate=check))
        return f"{a}..{b}"
    if mode == "within":
        return "within " + _ask(questionary.text("How long?", instruction="e.g. 30d, 2w, 6m, 1y",
                                                 validate=check))
    return f"{mode} " + _ask(questionary.text("Date:", instruction=hint, validate=check))


def edit_filter(cfg: Config, key: str) -> None:
    spec = SPECS[key]
    current = cfg.filters.get(key)
    if spec.kind == "list":
        hint = {"license": "Comma-separated. Common: " + ", ".join(LICENSE_HINTS),
                "topic": "Comma-separated; spaces become hyphens. Blank to clear"}.get(
            spec.qualifier, "Comma-separated. Blank to clear")
        value = _ask(questionary.text(f"{spec.label}:", default=", ".join(current or []),
                                      instruction=hint, validate=_valid(key)))
    elif spec.kind == "match":
        value = _ask(questionary.checkbox("Keywords must appear in (none = GitHub default):",
                                          choices=[Choice(f, checked=f in (current or [])) for f in MATCH_FIELDS]))
    elif spec.kind == "range":
        value = _edit_range(spec)
    elif spec.kind == "date":
        value = _edit_date(spec)
    elif spec.kind == "choice":
        labels = {"archived": {"false": "Exclude archived", "true": "Only archived"},
                  "include_forks": {"true": "Include forks", "only": "Only forks"}}.get(key, {})
        value = _ask(questionary.select(f"{spec.label}:", choices=[
            Choice("Any (no filter)", "any"),
            *[Choice(labels.get(v, f"{spec.label}: {v}"), v) for v, _ in spec.choices]],
            default=current or "any"))
    elif spec.kind == "flag":
        value = _ask(questionary.select(f"{spec.label}:", choices=[
            Choice("Require", True), Choice("Any (no filter)", False)]))
    else:  # raw
        value = _ask(questionary.text("Raw qualifiers:", default=current or "",
                                      instruction="GitHub syntax, e.g. -language:php topics:>3 repo:a/b"))
        for w in check_query(value):
            console.print(f"[yellow]Warning:[/] {w}")
    try:
        nv = normalize(key, value)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return
    if nv is None:
        cfg.filters.pop(key, None)
        console.print(f"[dim]{spec.label} cleared.[/]")
    else:
        cfg.filters[key] = nv
        console.print(f"[green]✓[/] {spec.label}: {describe(key, nv)}  "
                      f"[dim]→ {' '.join(to_qualifiers({key: nv}))}[/]")


def edit_filters(cfg: Config) -> None:
    last = None
    while True:
        choices: list[Any] = []
        group = None
        for spec in FILTERS:
            if spec.group != group:
                group = spec.group
                choices.append(Separator(f"── {group} ──"))
            val = cfg.filters.get(spec.key)
            shown = describe(spec.key, val) if val is not None else "·"
            choices.append(Choice(f"{spec.label:<22} {shown}", spec.key, description=spec.help or None))
        choices += [Separator("──────────"),
                    Choice("Clear all filters", "__clear", disabled=None if cfg.filters else "none set"),
                    Choice("Done", "__done")]
        console.print(f"[dim]Applied to every query:[/] {' '.join(to_qualifiers(cfg.filters)) or 'none'}")
        key = _ask(questionary.select("Filters - pick one to edit (type to search):", choices=choices,
                                      default=last, use_search_filter=True, use_jk_keys=False))
        if key == "__done":
            return
        if key == "__clear":
            if _ask(questionary.confirm("Remove all filters?", default=False)):
                cfg.filters.clear()
            continue
        last = key
        try:
            edit_filter(cfg, key)
        except KeyboardInterrupt:
            console.print("[dim]Unchanged.[/]")


def edit_sort(cfg: Config) -> None:
    current = next((label for label, s, o in SORT_CHOICES if s == cfg.sort and o == cfg.order), "Best match")
    label = _ask(questionary.select("Sort by (same options as GitHub's menu):",
                                    choices=[c[0] for c in SORT_CHOICES], default=current))
    _, cfg.sort, cfg.order = next(c for c in SORT_CHOICES if c[0] == label)
    cfg.max_results = int(_ask(questionary.text(
        f"Max repositories per query (1-{GITHUB_RESULT_CAP}; GitHub serves at most {GITHUB_RESULT_CAP}):",
        default=str(cfg.max_results), validate=_int(1, GITHUB_RESULT_CAP))))


def edit_data(cfg: Config) -> None:
    cfg.deep_scrape = _ask(questionary.confirm(
        "Deep scrape (open each repo: website, branches, forks, languages, contributors...)?",
        default=cfg.deep_scrape))
    if cfg.deep_scrape:
        cfg.fetch_manifests = _ask(questionary.confirm(
            "Read package.json / pyproject / Cargo.toml etc. for a detailed tech stack?",
            default=cfg.fetch_manifests))


def edit_browser(cfg: Config) -> None:
    cfg.browsers = int(_ask(questionary.text(f"Parallel workers (1-{MAX_BROWSERS}):",
                                             default=str(min(cfg.browsers, MAX_BROWSERS)),
                                             validate=_int(1, MAX_BROWSERS))))
    cfg.workers_per_chrome = int(_ask(questionary.text(
        "Workers sharing one Chrome process (each gets its own context; fewer Chromes = less RAM):",
        default=str(cfg.workers_per_chrome), validate=_int(1, MAX_BROWSERS))))
    cfg.isolate_workers = _ask(questionary.confirm(
        "Isolate workers (one Chrome per worker: more RAM, no shared crashes)?", default=cfg.isolate_workers))
    cfg.block_resources = _ask(questionary.confirm(
        "Block images, fonts and media (the parsers only need HTML)?", default=cfg.block_resources))
    cfg.browser = _ask(questionary.select("Browser:", choices=list(BROWSER_CHOICES),
                                          default=cfg.browser if cfg.browser in BROWSER_CHOICES else "chrome"))
    cfg.browser_path = _ask(questionary.text(
        "Browser executable path (blank = auto-detect):", default=cfg.browser_path)).strip().strip('"')
    cfg.headless = _ask(questionary.confirm(
        "Run headless? (Chrome is otherwise headed but parked off-screen; headless is easier to detect)",
        default=cfg.headless))
    if sys.platform == "win32":
        cfg.hide_taskbar_icons = _ask(questionary.confirm(
            "Hide the scraper's Chrome windows from the taskbar and Alt-Tab (Windows)?",
            default=cfg.hide_taskbar_icons))
    cfg.human_search = _ask(questionary.confirm("Type the query into GitHub's search box (more human)?",
                                                default=cfg.human_search))
    cfg.min_delay = float(_ask(questionary.text("Min delay between pages (s):", default=str(cfg.min_delay),
                                                validate=_float)))
    cfg.max_delay = float(_ask(questionary.text("Max delay between pages (s):", default=str(cfg.max_delay),
                                                validate=_float)))
    cfg.typing_delay = float(_ask(questionary.text("Mean typing delay per key (s):",
                                                   default=str(cfg.typing_delay), validate=_float)))
    cfg.max_retries = int(_ask(questionary.text("Max retries on rate-limit:", default=str(cfg.max_retries),
                                                validate=_int(1, 10))))
    cfg.proxy = _ask(questionary.text(
        "Proxy (blank = none; host:port or user:pass@host:port; comma-separate to rotate per browser):",
        default=cfg.proxy)).strip()
    cfg.user_data_dir = _ask(questionary.text(
        "Persistent Chrome profile dir (blank = fresh profile each run):", default=cfg.user_data_dir)).strip()


def edit_output(cfg: Config) -> None:
    cfg.output_dir = _ask(questionary.text("Output folder:", default=cfg.output_dir)).strip() or "output"
    cfg.formats = _ask(questionary.checkbox("Formats:", choices=[
        Choice("json", checked="json" in cfg.formats),
        Choice("csv", checked="csv" in cfg.formats),
    ], validate=lambda v: bool(v) or "Pick at least one"))


def interactive() -> None:
    try:
        cfg = Config.load()
    except ValueError as e:
        console.print(f"[red]config.json has an invalid filter:[/] {e}\nFix or delete {CONFIG_PATH}.")
        return
    while True:
        console.print(plan_panel(cfg))
        try:
            choice = _ask(questionary.select("What next?", use_shortcuts=True, choices=[
                Choice("Run scrape", "run", shortcut_key="r"),
                Choice("Preview match counts (fast dry run)", "preview", shortcut_key="p"),
                Separator(),
                Choice("Queries", "queries", shortcut_key="q"),
                Choice(f"Filters ({len(cfg.filters)} set)", "filters", shortcut_key="f"),
                Choice(f"Sort & limit ({cfg.sort_label()}, {cfg.max_results}/query)", "sort", shortcut_key="s"),
                Choice(f"Toggle headless (now {'ON' if cfg.headless else 'OFF'})", "headless", shortcut_key="h"),
                Choice("Browser sessions & stealth", "browser", shortcut_key="b"),
                Choice("Data collection", "data", shortcut_key="d"),
                Choice("Output", "output", shortcut_key="o"),
                Choice("Reset all settings", "reset", shortcut_key="z"),
                Choice("Exit", "exit", shortcut_key="x"),
            ]))
        except KeyboardInterrupt:
            break
        if choice == "exit":
            break
        try:
            if choice == "run":
                if not cfg.queries:
                    console.print("[red]Add at least one query first.[/]")
                    continue
                run_scrape(cfg, confirm=True)
                continue
            if choice == "preview":
                preview_counts(cfg)
                continue
            if choice == "reset":
                if _ask(questionary.confirm("Reset ALL settings (queries, filters, sessions) to defaults?",
                                            default=False)):
                    cfg = Config()
            elif choice == "headless":
                cfg.headless = not cfg.headless
            else:
                {"queries": edit_queries, "filters": edit_filters, "sort": edit_sort, "browser": edit_browser,
                 "data": edit_data, "output": edit_output}[choice](cfg)
        except KeyboardInterrupt:
            console.print("[dim]Back to menu.[/]")
        cfg.save()  # edits persist; runs never change settings
    console.print("Bye.")


# ------------------------------------------------------------------ flags
EPILOG = """\
examples:
  main.py                                   interactive menu
  main.py -q "web scraper" --language python --stars 100+ --pushed "within 6m"
  main.py -q "agency site; landing page" --topic nextjs,gsap --created "after 2025-01-01" -b 3
  main.py -q "cli tool" --exclude-topic deprecated --archived false --sort stars --count
  main.py --queries-file queries.txt --license mit --include-forks true --save

ranges:  100  100+  >=100  <50  10..50  1.5k
dates:   2024-01-01  "before 2024-01-01"  "after 30d"  "since 6m"  "within 1y"
         "between 2024-01-01 and 2024-06-30"   (word forms avoid quoting < > in cmd/PowerShell)
Inline qualifiers inside a query also work: -q "shop topic:ecommerce stars:>20"
"""

# flag dest -> filter key, for the simple one-value / list filters
_FLAG_FILTERS = {
    "topic": "topic", "exclude_topic": "exclude_topic", "language": "language",
    "exclude_language": "exclude_language", "owner": "owner", "license": "license", "match": "match",
    "stars": "stars", "forks": "forks", "followers": "followers", "size": "size",
    "number_topics": "number_topics", "good_first_issues": "good_first_issues",
    "help_wanted_issues": "help_wanted_issues", "created": "created", "pushed": "pushed",
    "archived": "archived", "include_forks": "include_forks", "template": "template", "mirror": "mirror",
    "qualifier": "qualifiers",
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="main.py", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
        description="Stealth GitHub repository scraper. Run without arguments for the interactive menu. "
                    "Filter flags mirror `gh search repos`; they are added to saved filters unless "
                    "--clear-filters is given.")
    s = ap.add_argument_group("search")
    s.add_argument("-q", "--query", action="append",
                   help="keywords; repeat, or separate with ; or , for several queries")
    s.add_argument("--queries-file", help="text file with one query per line")
    s.add_argument("-n", "--limit", "--max", type=int, dest="max_results", metavar="N",
                   help=f"max repositories per query (GitHub caps at {GITHUB_RESULT_CAP})")
    s.add_argument("--sort", choices=list(SORT_OPTIONS))
    s.add_argument("--order", choices=["desc", "asc"], help="only with --sort")
    s.add_argument("--count", "--dry-run", action="store_true", dest="count",
                   help="only show how many repos each query matches; scrape nothing")

    f = ap.add_argument_group("filters (lists accept repeats or commas)")
    f.add_argument("--topic", action="append", help="repo must have this topic (all are required)")
    f.add_argument("--exclude-topic", action="append")
    f.add_argument("--language", action="append")
    f.add_argument("--exclude-language", action="append")
    f.add_argument("--owner", action="append", help="user or org")
    f.add_argument("--license", action="append", help="e.g. mit, apache-2.0")
    f.add_argument("--match", action="append", help=f"restrict keywords to: {', '.join(MATCH_FIELDS)}")
    for name, hlp in (("stars", None), ("forks", None), ("followers", None), ("size", "in KB"),
                      ("number-topics", None), ("good-first-issues", None), ("help-wanted-issues", None)):
        f.add_argument(f"--{name}", metavar="RANGE", help=hlp)
    f.add_argument("--min-stars", type=int, help=argparse.SUPPRESS)  # pre-filters alias for --stars N+
    f.add_argument("--created", metavar="DATE")
    f.add_argument("--pushed", "--updated", metavar="DATE", dest="pushed", help="last push date")
    f.add_argument("--archived", choices=["true", "false"])
    f.add_argument("--include-forks", choices=["false", "true", "only"])
    f.add_argument("--template", choices=["true", "false"])
    f.add_argument("--mirror", choices=["true", "false"])
    f.add_argument("--sponsorable", action="store_true", default=None, help="owner has GitHub Sponsors")
    f.add_argument("--funding-file", action="store_true", default=None, help="repo has FUNDING.yml")
    f.add_argument("--qualifier", action="append", metavar="RAW", help="raw GitHub qualifier(s)")
    f.add_argument("--clear-filters", action="store_true", help="ignore filters saved in config.json")

    b = ap.add_argument_group("browser & output")
    b.add_argument("-b", "--browsers", type=int, choices=range(1, MAX_BROWSERS + 1),
                   metavar=f"1-{MAX_BROWSERS}", help="parallel workers")
    b.add_argument("--workers-per-chrome", type=int, choices=range(1, MAX_BROWSERS + 1),
                   metavar=f"1-{MAX_BROWSERS}", dest="workers_per_chrome",
                   help="workers (browser contexts) sharing one Chrome process; default 3")
    b.add_argument("--isolate-workers", action="store_true", default=None,
                   help="one Chrome process per worker (more RAM, no shared crashes)")
    b.add_argument("--block-resources", action="store_true", default=None,
                   help="block images, fonts and media")
    b.add_argument("--browser", choices=list(BROWSER_CHOICES), help="browser to drive (default chrome)")
    hl = b.add_mutually_exclusive_group()
    hl.add_argument("--headless", action="store_true", default=None,
                    help="true headless (easier to detect); default is headed but off-screen")
    hl.add_argument("--visible", action="store_true", default=None, help="headed, off-screen (the default)")
    b.add_argument("--show-taskbar-icons", action="store_true", default=None,
                   help="Windows: keep Chrome's taskbar buttons (they are hidden by default)")
    b.add_argument("--no-deep", action="store_true", help="only collect search-listing data")
    b.add_argument("--no-manifests", action="store_true", help="skip manifest-based tech stack")
    b.add_argument("--proxy")
    b.add_argument("--out", dest="output_dir")
    b.add_argument("--save", action="store_true", help="also save these settings to config.json")
    return ap


def apply_args(cfg: Config, args: argparse.Namespace, queries: list[str]) -> None:
    """Merge flags into cfg. Raises ValueError with a user-facing message."""
    cfg.queries = queries
    if args.clear_filters:
        cfg.filters = {}
    if args.min_stars:
        cfg.filters["stars"] = normalize("stars", f">={args.min_stars}")
    for dest, key in _FLAG_FILTERS.items():
        raw = getattr(args, dest)
        if raw is None:
            continue
        if key == "qualifiers":
            raw = " ".join([cfg.filters.get("qualifiers", ""), *raw]).strip()
        elif key == "include_forks" and raw == "false":
            cfg.filters.pop(key, None)  # GitHub's default already excludes forks
            continue
        elif isinstance(raw, list):
            raw = [v for item in raw for v in item.split(",")]
        try:
            value = normalize(key, raw)
        except ValueError as e:
            raise ValueError(f"--{dest.replace('_', '-')}: {e}") from None
        if value is None:
            cfg.filters.pop(key, None)
        else:
            cfg.filters[key] = value
    for dest in ("sponsorable", "funding_file"):
        if getattr(args, dest):
            cfg.filters[dest] = True
    for key in ("max_results", "browsers", "sort", "order", "proxy", "output_dir", "workers_per_chrome",
                "browser"):
        if getattr(args, key) is not None:
            setattr(cfg, key, getattr(args, key))
    if args.isolate_workers:
        cfg.isolate_workers = True
    if args.block_resources:
        cfg.block_resources = True
    if cfg.max_results is not None and not 1 <= cfg.max_results <= GITHUB_RESULT_CAP:
        raise ValueError(f"--limit must be between 1 and {GITHUB_RESULT_CAP}")
    if args.headless:
        cfg.headless = True
    if args.visible:
        cfg.headless = False
    if args.show_taskbar_icons:
        cfg.hide_taskbar_icons = False
    if args.no_deep:
        cfg.deep_scrape = False
    if args.no_manifests:
        cfg.fetch_manifests = False


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)

    try:
        queries = parse_queries(";".join(args.query or []))
        if args.queries_file:
            queries += [q for q in read_queries("@" + args.queries_file) if q not in queries]
    except OSError as e:
        ap.error(f"--queries-file: {e.strerror}: {args.queries_file}")
    if not queries:
        if not sys.stdin.isatty():
            ap.error("no --query given and stdin is not a terminal, so the menu can't run. "
                     "Pass -q \"keywords\" (see --help).")
        interactive()
        return

    try:
        cfg = Config.load()
        apply_args(cfg, args, queries)
    except ValueError as e:
        ap.error(str(e))
    if args.save:
        cfg.save()
    console.print(plan_panel(cfg))
    if args.count:
        preview_counts(cfg)
    else:
        run_scrape(cfg)  # flags apply to this run only unless --save


if __name__ == "__main__":
    main(sys.argv[1:])
