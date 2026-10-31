"""Orchestration: N async workers share one task queue.

Tasks are either a *search* (one per query) or a *repo* page visit. Searches are
queued first; each finished search pushes its repos back onto the same queue, so
detail pages - the slow part - spread over every browser slot even for one query.
Repos matched by several queries are scraped once (see `matched_queries`).

The scheduler only sees `BrowserPool` / `BrowserSlot` (ghscraper.browser.backend), so it
runs offline against a fake. All methods must be called from the event-loop thread;
the sync facade in ghscraper.scraper handles cross-thread use.
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

from ..browser import Cancelled, PoolFactory, SlotCrashed, default_pool_factory
from ..browser.backend import BrowserPool, BrowserSlot
from ..config import SORT_OPTIONS, Config
from ..parsers import parse_repo_page, parse_search_page
from ..techstack import build_tech_stack

MAX_SEARCH_PAGES = 100  # GitHub caps search at 1000 results / 100 pages
MAX_REQUEUES = 2        # a task whose slot crashed is retried at most this many times
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
    requeues: int = field(default=0, compare=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Scheduler:
    def __init__(self, cfg: Config, log: Callable[[str], None] = print,
                 on_status: Callable[[int, str], Any] | None = None,
                 on_listing: Callable[[str, int, int], Any] | None = None,
                 on_repo: Callable[[dict[str, Any], int, int], Any] | None = None,
                 count_only: bool = False, pool_factory: PoolFactory | None = None):
        self.cfg = cfg
        # count_only: load page 1 of each search to read GitHub's total, scrape nothing.
        self.count_only = count_only
        self.totals: dict[str, int | None] = {}   # query -> GitHub's reported match count
        self.log = log
        # Callbacks may be plain functions or coroutine functions.
        self.on_status = on_status or (lambda worker, text: None)
        self.on_listing = on_listing or (lambda query, found, target: None)
        self.on_repo = on_repo or (lambda repo, done, total: None)
        # Filled progressively so a caller can export partial results after a stop / Ctrl+C.
        self.results: list[dict[str, Any]] = []
        self.stop_event = asyncio.Event()
        self.pool: BrowserPool | None = None
        self.slots: dict[int, BrowserSlot] = {}     # worker index -> slot (for live view / input)

        self._pool_factory = pool_factory or default_pool_factory
        self._queue: asyncio.PriorityQueue[_Task] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._pending = 0                           # queued + in-flight tasks
        self._repos: dict[str, dict[str, Any]] = {}  # full_name -> record (dedupe)
        self._repo_total = 0
        self._repo_done = 0
        self._resume: dict[int, asyncio.Event] = {}  # set = worker may take work
        self._worker_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------ public
    async def run(self) -> list[dict[str, Any]]:
        queries = self.cfg.queries
        if not queries:
            raise ValueError("No search queries configured")
        for q in queries:
            self._put(_SEARCH, "search", q)

        n = self.cfg.worker_count(searches_only=self.count_only)
        self.log(f"Launching [bold]{n}[/] browser session(s) for [bold]{len(queries)}[/] query(ies).")
        pool = self.pool = self._pool_factory(self.cfg, self.log, self.stop_event, n)
        try:
            await pool.start()
            self._worker_tasks = [asyncio.create_task(self._worker(i), name=f"worker-{i + 1}")
                                  for i in range(n)]
            if self.stop_event.is_set():
                self.stop()
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        except BaseException:   # includes the CancelledError asyncio.run raises on Ctrl+C
            self.stop()
            raise
        finally:
            for t in self._worker_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
            await pool.close()
        if self._pending and not self.stop_event.is_set():
            self.log(f"[red]{self._pending} task(s) left undone - every browser session failed or exited.[/]")
        return self.results

    def stop(self) -> None:
        """Stop the run: wake every sleep, release paused workers, cancel in-flight work."""
        self.stop_event.set()
        for ev in self._resume.values():
            ev.set()
        for t in self._worker_tasks:
            if not t.done():
                t.cancel()

    def pause_worker(self, index: int) -> None:
        """The worker finishes its current page/task, then waits until `resume_worker`."""
        self._resume_event(index).clear()

    def resume_worker(self, index: int) -> None:
        self._resume_event(index).set()

    def is_paused(self, index: int) -> bool:
        return not self._resume_event(index).is_set()

    # Read-only progress counters (for the sidecar's progress events).
    @property
    def repo_total(self) -> int:
        return self._repo_total

    @property
    def repo_done(self) -> int:
        return self._repo_done

    @property
    def pending(self) -> int:
        """Tasks still queued or in flight."""
        return self._pending

    # ------------------------------------------------------------ plumbing
    def _resume_event(self, index: int) -> asyncio.Event:
        ev = self._resume.get(index)
        if ev is None:
            ev = self._resume[index] = asyncio.Event()
            ev.set()
        return ev

    @staticmethod
    async def _emit(cb: Callable[..., Any], *args: Any) -> None:
        res = cb(*args)
        if inspect.isawaitable(res):
            await res

    async def _gate(self, index: int) -> None:
        """Block while the user has paused this worker."""
        ev = self._resume_event(index)
        if not ev.is_set():
            await self._emit(self.on_status, index, "[yellow]paused (you have control)[/]")
            await ev.wait()

    def _put(self, priority: int, kind: str, payload: Any, requeues: int = 0) -> None:
        self._pending += 1
        self._queue.put_nowait(_Task(priority, next(self._seq), kind, payload, requeues))

    # ------------------------------------------------------------ worker
    async def _worker(self, index: int) -> None:
        async def status(text: str) -> None:
            await self._emit(self.on_status, index, text)

        label = f"B{index + 1}"
        slot: BrowserSlot | None = None
        try:
            await status("starting browser...")
            try:
                slot = await self.pool.acquire(index)
            except Cancelled:
                return
            except Exception as e:
                await status(f"[red]failed to start: {type(e).__name__}[/]")
                self.log(f"[red][{label}] Browser failed to start: {type(e).__name__}: {e}[/]")
                return
            self.slots[index] = slot
            while not self.stop_event.is_set():
                await self._gate(index)
                if self.stop_event.is_set():
                    break
                try:
                    task = await asyncio.wait_for(self._queue.get(), 0.5)
                except asyncio.TimeoutError:
                    if self._pending == 0:
                        break  # nothing queued or in flight anywhere: done
                    await status("[dim]idle (waiting for searches)[/]")
                    continue
                crashed = False
                try:
                    if task.kind == "search":
                        await status(f"searching [bold]{task.payload}[/]")
                        await self._do_search(index, slot, task.payload)
                    else:
                        await status(f"repo {task.payload['full_name']}")
                        await self._do_repo(slot, task.payload)
                except Cancelled:
                    break
                except SlotCrashed as e:
                    if self.stop_event.is_set():
                        break
                    crashed = True
                    self.log(f"[yellow][{label}] browser slot crashed during {task.kind} task: {e}[/]")
                    await self._requeue(task, label)
                except Exception as e:
                    if self.stop_event.is_set():
                        break
                    self.log(f"[red][{label}] {task.kind} task failed: {type(e).__name__}: {e}[/]")
                finally:
                    self._pending -= 1
                if crashed:
                    await status("[yellow]recovering browser...[/]")
                    try:
                        await self.pool.recycle(slot)
                    except Cancelled:
                        break
                    except Exception as e:
                        await status(f"[red]failed to recover: {type(e).__name__}[/]")
                        self.log(f"[red][{label}] Could not recover the browser: {type(e).__name__}: {e}[/]")
                        return
            await status("[green]done[/]" if not self.stop_event.is_set() else "[yellow]stopped[/]")
        except asyncio.CancelledError:
            await self._safe_status(index, "[yellow]stopped[/]")
            raise
        except Exception as e:
            self.log(f"[red][{label}] worker crashed: {type(e).__name__}: {e}[/]")
        finally:
            if slot is not None:
                try:
                    await slot.close()
                except BaseException:
                    pass  # pool.close() stops the browsers anyway

    async def _safe_status(self, index: int, text: str) -> None:
        try:
            await self._emit(self.on_status, index, text)
        except BaseException:
            pass

    async def _requeue(self, task: _Task, label: str) -> None:
        if task.requeues < MAX_REQUEUES:
            self._put(task.priority, task.kind, task.payload, task.requeues + 1)
            self.log(f"[yellow][{label}] requeued {task.kind} task "
                     f"(retry {task.requeues + 1}/{MAX_REQUEUES}).[/]")
            return
        what = task.payload if task.kind == "search" else task.payload["full_name"]
        self.log(f"[red]Giving up on {task.kind} '{what}' after {MAX_REQUEUES} requeues.[/]")
        if task.kind == "repo":  # keep the row so counts add up, flagged as failed
            await self._record_failure(task.payload, "browser crashed repeatedly")

    # ------------------------------------------------------------ search
    async def _do_search(self, index: int, slot: BrowserSlot, query: str) -> None:
        if self.count_only:
            html = await self._open_search(slot, search_url(self.cfg, query))
            res = await asyncio.to_thread(parse_search_page, html, expected_page=1)
            self.totals[query] = res.get("total")
            await self._emit(self.on_listing, query, 1, 1)
            return
        listings = await self._collect_listings(index, slot, query)
        for item in listings:
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
                await self._record(item)

    async def _collect_listings(self, index: int, slot: BrowserSlot, query: str) -> list[dict[str, Any]]:
        cfg = self.cfg
        label = f"B{index + 1}"
        full_query = cfg.search_query(query)
        target = max(1, cfg.max_results)
        seen: dict[str, dict[str, Any]] = {}
        page = 1

        html = None
        if cfg.human_search:
            self.log(f"[{label}] Typing [bold]{full_query}[/] into GitHub's search box...")
            if await slot.type_search(full_query):
                if _is_target_search(await slot.url(), cfg, query):
                    html = await slot.html()
                else:
                    # Landed on search; apply sort/type like clicking the sort menu.
                    await slot.pause(0.6)
        if html is None:
            html = await self._open_search(slot, search_url(cfg, query, page))

        while not self.stop_event.is_set():
            # SPA navigation can leave stale embedded JSON; only trust page 1 JSON
            # if it really is page 1, otherwise parser falls back to the DOM.
            res = await asyncio.to_thread(parse_search_page, html, expected_page=page)
            if page == 1:
                total = res.get("total")
                self.totals[query] = total
                self.log(f"[{label}] '{query}': GitHub reports "
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
            await self._emit(self.on_listing, query, len(seen), target)
            if len(seen) >= target or not res["results"] or not res.get("has_next") \
                    or page >= MAX_SEARCH_PAGES:
                break
            if new == 0:
                self.log(f"[yellow]'{query}': no new results on page {page}; stopping pagination.[/]")
                break
            page += 1
            await self._gate(index)
            await slot.pause()
            html = await self._open_search(slot, search_url(cfg, query, page))
        await self._emit(self.on_listing, query, target, target)  # mark finished even if fewer exist
        return list(seen.values())

    @staticmethod
    async def _open_search(slot: BrowserSlot, url: str) -> str:
        """Open a search URL and wait until its results data has rendered."""
        html = await slot.open(url)
        return await slot.html() if await slot.wait_for_search() else html

    # ------------------------------------------------------------ details
    async def _do_repo(self, slot: BrowserSlot, item: dict[str, Any]) -> None:
        await slot.pause()
        # Occasionally take a longer "reading" break, like a human would.
        if random.random() < 0.08:
            await slot.pause(3)
        try:
            html = await slot.open(item["url"])
            details = await asyncio.to_thread(parse_repo_page, html, item["full_name"])  # keep the loop responsive
        except (Cancelled, SlotCrashed):
            raise
        except Exception as e:  # keep going; record the failure on the row
            if self.stop_event.is_set():
                raise Cancelled from e
            self.log(f"[red]Failed to scrape {item['full_name']}: {type(e).__name__}: {e}[/]")
            await self._record_failure(item, f"{type(e).__name__}: {e}")
            return
        search_language = item.get("language")
        # Update in place: the dict is shared with the dedupe map, so queries
        # matched later still land in this record's matched_queries.
        item.update({k: v for k, v in details.items() if v not in (None, "", [], {})})
        item["language"] = search_language or details.get("language", "")
        try:
            item["tech_stack"] = await asyncio.to_thread(build_tech_stack, item, self.cfg.fetch_manifests)
        except Exception as e:
            self.log(f"[yellow]Tech-stack detection failed for {item['full_name']}: {e}[/]")
            item["tech_stack"] = [item["language"]] if item.get("language") else []
        item["scraped_at"] = _now()
        await self._record(item)

    async def _record_failure(self, item: dict[str, Any], error: str) -> None:
        item["error"] = error
        item["tech_stack"] = [item["language"]] if item.get("language") else []
        item["scraped_at"] = _now()
        await self._record(item)

    async def _record(self, item: dict[str, Any]) -> None:
        self.results.append(item)
        self._repo_done += 1
        await self._emit(self.on_repo, item, self._repo_done, self._repo_total)
