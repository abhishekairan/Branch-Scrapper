"""Orchestration: N browser workers share one task queue.

Tasks are either a *search* (one per query) or a *repo* page visit. Searches are
queued first; each finished search pushes its repos back onto the same queue, so
detail pages - the slow part - spread over every browser even for one query.
Repos matched by several queries are scraped once (see `matched_queries`).
"""
from __future__ import annotations

import itertools
import queue
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

from .browser import Cancelled, StealthBrowser
from .config import SORT_OPTIONS, Config
from .parsers import parse_repo_page, parse_search_page
from .techstack import build_tech_stack

MAX_SEARCH_PAGES = 100  # GitHub caps search at 1000 results / 100 pages
_SEARCH, _REPO = 0, 1   # queue priorities: searches before repo pages


def search_url(cfg: Config, query: str, page: int = 1) -> str:
    params = {"q": cfg.search_query(query), "type": "repositories"}
    sort = SORT_OPTIONS.get(cfg.sort)
    if sort:
        params["s"] = sort
        params["o"] = cfg.order
    if page > 1:
        params["p"] = page
    return "https://github.com/search?" + urlencode(params)


def _is_target_search(url: str, cfg: Config, query: str) -> bool:
    qs = parse_qs(urlparse(url).query)
    return (qs.get("type", [""])[0] == "repositories"
            and qs.get("q", [""])[0].strip() == cfg.search_query(query)
            and qs.get("s", [None])[0] == SORT_OPTIONS.get(cfg.sort))


@dataclass(order=True)
class _Task:
    priority: int
    seq: int
    kind: str = field(compare=False)
    payload: Any = field(compare=False)


class GitHubScraper:
    def __init__(self, cfg: Config, log: Callable[[str], None] = print,
                 on_status: Callable[[int, str], None] | None = None,
                 on_listing: Callable[[str, int, int], None] | None = None,
                 on_repo: Callable[[dict[str, Any], int, int], None] | None = None,
                 count_only: bool = False):
        self.cfg = cfg
        # count_only: load page 1 of each search to read GitHub's total, scrape nothing.
        self.count_only = count_only
        self.totals: dict[str, int | None] = {}   # query -> GitHub's reported match count
        self.log = log
        self.on_status = on_status or (lambda worker, text: None)
        self.on_listing = on_listing or (lambda query, found, target: None)
        self.on_repo = on_repo or (lambda repo, done, total: None)
        # Filled progressively so a caller can export partial results on Ctrl+C.
        self.results: list[dict[str, Any]] = []
        self.stop = threading.Event()

        self._lock = threading.Lock()
        self._queue: queue.PriorityQueue[_Task] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._pending = 0                       # queued + in-flight tasks
        self._repos: dict[str, dict[str, Any]] = {}  # full_name -> record (dedupe)
        self._repo_total = 0
        self._repo_done = 0
        self._browsers: list[StealthBrowser] = []

    # ------------------------------------------------------------ public
    def run(self) -> list[dict[str, Any]]:
        queries = self.cfg.queries
        if not queries:
            raise ValueError("No search queries configured")
        for q in queries:
            self._put(_SEARCH, "search", q)

        n = self.cfg.worker_count(searches_only=self.count_only)
        self.log(f"Launching [bold]{n}[/] browser session(s) for [bold]{len(queries)}[/] query(ies).")
        threads = [threading.Thread(target=self._worker, args=(i,), name=f"browser-{i + 1}", daemon=True)
                   for i in range(n)]
        for t in threads:
            t.start()
        try:
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(0.3)  # short joins keep Ctrl+C responsive
        except KeyboardInterrupt:
            self.shutdown()
            raise
        if self._pending and not self.stop.is_set():
            self.log(f"[red]{self._pending} task(s) left undone - every browser session failed or exited.[/]")
        return self.results

    def shutdown(self) -> None:
        """Stop all workers: wake their sleeps and kill their browsers."""
        self.stop.set()
        for b in list(self._browsers):
            b.close()

    # ------------------------------------------------------------ queue
    def _put(self, priority: int, kind: str, payload: Any) -> None:
        with self._lock:
            self._pending += 1
        self._queue.put(_Task(priority, next(self._seq), kind, payload))

    def _worker(self, index: int) -> None:
        status = lambda text: self.on_status(index, text)  # noqa: E731
        status("starting browser...")
        browser = StealthBrowser(self.cfg, self.log, index=index, stop=self.stop)
        try:
            browser.start()
        except Exception as e:
            status(f"[red]failed to start: {type(e).__name__}[/]")
            self.log(f"[red][B{index + 1}] Browser failed to start: {type(e).__name__}: {e}[/]")
            return
        with self._lock:
            self._browsers.append(browser)
        try:
            while not self.stop.is_set():
                try:
                    task = self._queue.get(timeout=0.5)
                except queue.Empty:
                    with self._lock:
                        if self._pending == 0:
                            break  # nothing queued or in flight anywhere: done
                    status("[dim]idle (waiting for searches)[/]")
                    continue
                try:
                    if task.kind == "search":
                        status(f"searching [bold]{task.payload}[/]")
                        self._do_search(browser, task.payload)
                    else:
                        status(f"repo {task.payload['full_name']}")
                        self._do_repo(browser, task.payload)
                except Cancelled:
                    break
                except Exception as e:
                    if self.stop.is_set():
                        break
                    self.log(f"[red][B{index + 1}] {task.kind} task failed: {type(e).__name__}: {e}[/]")
                finally:
                    with self._lock:
                        self._pending -= 1
            status("[green]done[/]" if not self.stop.is_set() else "[yellow]stopped[/]")
        finally:
            browser.close()

    # ------------------------------------------------------------ search
    def _do_search(self, browser: StealthBrowser, query: str) -> None:
        if self.count_only:
            html = self._open_search(browser, search_url(self.cfg, query))
            self.totals[query] = parse_search_page(html, expected_page=1).get("total")
            self.on_listing(query, 1, 1)
            return
        listings = self._collect_listings(browser, query)
        for item in listings:
            with self._lock:
                existing = self._repos.get(item["full_name"])
                if existing is not None:
                    if query not in existing["matched_queries"]:
                        existing["matched_queries"].append(query)
                    continue
                item["query"] = query
                item["matched_queries"] = [query]
                self._repos[item["full_name"]] = item
                self._repo_total += 1
            if self.cfg.deep_scrape:
                self._put(_REPO, "repo", item)
            else:
                item["tech_stack"] = [item["language"]] if item.get("language") else []
                item["scraped_at"] = _now()
                self._record(item)

    def _collect_listings(self, browser: StealthBrowser, query: str) -> list[dict[str, Any]]:
        cfg = self.cfg
        full_query = cfg.search_query(query)
        target = max(1, cfg.max_results)
        seen: dict[str, dict[str, Any]] = {}
        page = 1

        html = None
        if cfg.human_search:
            self.log(f"[B{browser.index + 1}] Typing [bold]{full_query}[/] into GitHub's search box...")
            if browser.type_search(full_query):
                if _is_target_search(browser.url, cfg, query):
                    html = browser.html
                else:
                    # Landed on search; apply sort/type like clicking the sort menu.
                    browser.pause(0.6)
        if html is None:
            html = self._open_search(browser, search_url(cfg, query, page))

        while not self.stop.is_set():
            # SPA navigation can leave stale embedded JSON; only trust page 1 JSON
            # if it really is page 1, otherwise parser falls back to the DOM.
            res = parse_search_page(html, expected_page=page)
            if page == 1:
                total = res.get("total")
                self.totals[query] = total
                self.log(f"[B{browser.index + 1}] '{query}': GitHub reports "
                         f"[bold]{total if total is not None else 'unknown'}[/] repositories "
                         f"(parsed via {res['source']}).")
                if total == 0:
                    self.log(f"[yellow]'{query}' matched nothing. GitHub returns 0 instead of an error "
                             f"for bad qualifier values, so check: {full_query}[/]")
            new = 0
            for item in res["results"]:
                if item["full_name"] not in seen and len(seen) < target:
                    seen[item["full_name"]] = item
                    new += 1
            self.on_listing(query, len(seen), target)
            if len(seen) >= target or not res["results"] or not res.get("has_next") \
                    or page >= MAX_SEARCH_PAGES:
                break
            if new == 0:
                self.log(f"[yellow]'{query}': no new results on page {page}; stopping pagination.[/]")
                break
            page += 1
            browser.pause()
            html = self._open_search(browser, search_url(cfg, query, page))
        self.on_listing(query, target, target)  # mark finished even if fewer exist
        return list(seen.values())

    @staticmethod
    def _open_search(browser: StealthBrowser, url: str) -> str:
        """Open a search URL and wait until its results data has rendered."""
        html = browser.open(url)
        return browser.html if browser.wait_for_search() else html

    # ------------------------------------------------------------ details
    def _do_repo(self, browser: StealthBrowser, item: dict[str, Any]) -> None:
        browser.pause()
        # Occasionally take a longer "reading" break, like a human would.
        if random.random() < 0.08:
            browser.pause(3)
        try:
            html = browser.open(item["url"])
            details = parse_repo_page(html, item["full_name"])
        except Cancelled:
            raise
        except Exception as e:  # keep going; record the failure on the row
            if self.stop.is_set():
                raise Cancelled from e
            self.log(f"[red]Failed to scrape {item['full_name']}: {type(e).__name__}: {e}[/]")
            item["error"] = f"{type(e).__name__}: {e}"
            item["tech_stack"] = [item["language"]] if item.get("language") else []
            item["scraped_at"] = _now()
            self._record(item)
            return
        search_language = item.get("language")
        # Update in place: the dict is shared with the dedupe map, so queries
        # matched later still land in this record's matched_queries.
        item.update({k: v for k, v in details.items() if v not in (None, "", [], {})})
        item["language"] = search_language or details.get("language", "")
        try:
            item["tech_stack"] = build_tech_stack(item, self.cfg.fetch_manifests)
        except Exception as e:
            self.log(f"[yellow]Tech-stack detection failed for {item['full_name']}: {e}[/]")
            item["tech_stack"] = [item["language"]] if item.get("language") else []
        item["scraped_at"] = _now()
        self._record(item)

    def _record(self, item: dict[str, Any]) -> None:
        with self._lock:
            self.results.append(item)
            self._repo_done += 1
            done, total = self._repo_done, self._repo_total
        self.on_repo(item, done, total)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
