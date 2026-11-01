"""RunManager: owns at most one active run (or count-only preview) and turns it into events.

Everything here runs on the event-loop thread; blocking work (SQLite, psutil) goes through
``asyncio.to_thread``. Store writes are serialised by an asyncio lock so rows land in the
order they were scraped.

Events are delivered to ``listeners`` as ``(name, params)``; the sidecar wires them to the
WebSocket hub (and to the screen relay).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..api.protocol import (BAD_WORKER, INVALID_PARAMS, NO_RUN, NOT_PAUSED, RUN_ACTIVE, RpcError,
                            plain_log, strip_markup, worker_state)
from ..browser import PoolFactory, default_pool_factory
from ..browser.backend import plan_chromes
from ..config import BROWSER_CHOICES, CONFIG_PATH, MAX_BROWSERS, SORT_OPTIONS, Config
from ..filters import SPECS, check_query, describe, normalize, split_queries
from ..store import Store
from .scheduler import Scheduler

log = logging.getLogger("ghscraper.run")

GITHUB_RESULT_CAP = 1000   # GitHub search returns at most this many results per query
_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

Listener = Callable[[str, dict], None]


# ------------------------------------------------------------------ config handling
def load_or_init_config(path: Path, seed: Path | None = CONFIG_PATH) -> Config:
    """Load ``path``; if missing, start from ``seed`` (the repo-local config.json) or defaults."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        if seed is not None and Path(seed).is_file():
            try:
                shutil.copyfile(seed, path)
                log.info("seeded %s from %s", path, seed)
            except OSError as e:
                log.warning("could not copy %s: %s", seed, e)
    try:
        return Config.load(path)      # creates the file from defaults if still missing
    except (ValueError, OSError, TypeError) as e:
        log.error("config %s is unusable (%s); using defaults in memory, file left untouched", path, e)
        return Config()


_CONFIG_FIELDS = {f.name: f for f in dataclasses.fields(Config)}
_INT_FIELDS = {"max_results": 1, "browsers": 1, "workers_per_chrome": 1, "max_retries": 0, "page_timeout": 1}
_FLOAT_FIELDS = {"min_delay": 0.0, "max_delay": 0.0, "typing_delay": 0.0}
_BOOL_FIELDS = {"deep_scrape", "fetch_manifests", "isolate_workers", "block_resources", "headless",
                "human_search", "check_updates"}
_STR_FIELDS = {"browser_path", "proxy", "user_data_dir", "output_dir"}


def apply_overrides(base: Config, overrides: dict | None) -> tuple[Config, list[dict]]:
    """`base` overlaid with `overrides`. Returns (config, errors); errors are {field, message}.
    A key with an error keeps its base value."""
    errors: list[dict] = []
    values: dict[str, Any] = {}

    def err(field_: str, msg: str) -> None:
        errors.append({"field": field_, "message": msg})

    if overrides is not None and not isinstance(overrides, dict):
        return base, [{"field": "overrides", "message": "expected an object"}]
    for key, val in (overrides or {}).items():
        if key not in _CONFIG_FIELDS:
            err(key, "unknown setting")
        elif key == "queries":
            if isinstance(val, str):
                val = split_queries(val)
            if not isinstance(val, list) or not all(isinstance(q, str) for q in val):
                err(key, "expected a list of strings")
            else:
                values[key] = [q for q in dict.fromkeys(" ".join(q.split()) for q in val) if q]
        elif key == "filters":
            if not isinstance(val, dict):
                err(key, "expected an object")
                continue
            clean: dict[str, Any] = {}
            for fk, fv in val.items():
                if fk not in SPECS:
                    err(f"filters.{fk}", "unknown filter")
                    continue
                try:
                    nv = normalize(fk, fv)
                except ValueError as e:
                    err(f"filters.{fk}", str(e))
                    continue
                if nv is not None:
                    clean[fk] = nv
            values[key] = clean
        elif key in _INT_FIELDS:
            if isinstance(val, bool) or not isinstance(val, int) or val < _INT_FIELDS[key]:
                err(key, f"expected an integer >= {_INT_FIELDS[key]}")
            elif key == "browsers" and val > MAX_BROWSERS:
                err(key, f"at most {MAX_BROWSERS} parallel browsers")
            else:
                values[key] = val
        elif key in _FLOAT_FIELDS:
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val < _FLOAT_FIELDS[key]:
                err(key, "expected a number >= 0")
            else:
                values[key] = float(val)
        elif key in _BOOL_FIELDS:
            if not isinstance(val, bool):
                err(key, "expected true or false")
            else:
                values[key] = val
        elif key in _STR_FIELDS:
            if not isinstance(val, str):
                err(key, "expected a string")
            else:
                values[key] = val.strip()
        elif key == "sort":
            if val not in SORT_OPTIONS:
                err(key, f"use one of {', '.join(SORT_OPTIONS)}")
            else:
                values[key] = val
        elif key == "order":
            if val not in ("asc", "desc"):
                err(key, "use asc or desc")
            else:
                values[key] = val
        elif key == "browser":
            if val not in BROWSER_CHOICES:
                err(key, f"use one of {', '.join(BROWSER_CHOICES)}")
            else:
                values[key] = val
        elif key == "ui_theme":
            if val not in ("system", "light", "dark"):
                err(key, "use system, light or dark")
            else:
                values[key] = val
        elif key == "formats":
            if not isinstance(val, list) or not all(v in ("json", "csv") for v in val):
                err(key, "expected a list containing json and/or csv")
            else:
                values[key] = list(dict.fromkeys(val))
    cfg = dataclasses.replace(base, **values)
    if cfg.max_delay < cfg.min_delay:
        err("max_delay", "must be >= min_delay")
    return cfg, errors


def validate_config(cfg: Config, for_run: bool = True) -> dict:
    """Errors/warnings for a (type-checked) config, the GitHub query per search, and the estimate."""
    errors: list[dict] = []
    warnings: list[dict] = []
    if for_run and not cfg.queries:
        errors.append({"field": "queries", "message": "add at least one search query"})
    if cfg.max_delay < cfg.min_delay:
        errors.append({"field": "max_delay", "message": "must be >= min_delay"})
    if cfg.max_results > GITHUB_RESULT_CAP:
        warnings.append({"field": "max_results",
                         "message": f"GitHub returns at most {GITHUB_RESULT_CAP} results per query"})
    queries = []
    for q in cfg.queries:
        w = check_query(q)
        for text in w:
            warnings.append({"field": "queries", "query": q, "message": text})
        try:
            gh = cfg.search_query(q)
        except ValueError as e:
            gh = None
            errors.append({"field": "filters", "message": str(e)})
        queries.append({"query": q, "github_query": gh, "warnings": w})
    described = {}
    for k, v in cfg.filters.items():
        try:
            described[k] = describe(k, v)
        except Exception:
            described[k] = str(v)
    est = None
    if cfg.queries:
        from ..cli import estimate    # lazy: the CLI module pulls in rich/questionary
        pages, lo, hi = estimate(cfg)
        workers = cfg.worker_count()
        est = {"pages": pages, "seconds_low": lo, "seconds_high": hi, "workers": workers,
               "chromes": len(plan_chromes(workers, cfg.workers_per_chrome, cfg.isolate_workers))}
    return {"ok": not errors, "errors": errors, "warnings": warnings, "queries": queries,
            "filters_described": described, "estimate": est}


# ------------------------------------------------------------------ process memory
def measure_processes(pids: list[int], roots: list[int] | None = None) -> list[dict]:
    """Group `pids` (as returned by BrowserPool.processes) into process trees, one per root,
    and sum RSS / USS (bytes; USS is None when the OS denies it) for each.

    `roots` are the pool's known browser PIDs: with them, only those that are alive form a
    tree, and a process that can't be attributed to one is ignored. A short-lived Chrome child
    that exits between `processes()` and this call (its ppid lookup fails) must never count as
    a Chrome of its own."""
    import psutil
    procs: dict[int, Any] = {}
    for pid in dict.fromkeys(pids):
        try:
            procs[pid] = psutil.Process(pid)
        except psutil.Error:
            continue
    parent = {}
    for pid, p in list(procs.items()):
        try:
            parent[pid] = p.ppid()
        except psutil.Error:
            del procs[pid]          # exited meanwhile: not a process tree root
    known = {r for r in (roots or []) if r in procs}
    trees: dict[int, list[int]] = {}
    for pid in procs:
        root = pid
        seen = {root}
        while root not in known and parent.get(root) in procs and parent[root] not in seen:
            root = parent[root]
            seen.add(root)
        if roots is not None and root not in known:
            continue
        trees.setdefault(root, []).append(pid)
    out = []
    for root, members in trees.items():
        rss, uss = 0, 0
        uss_ok = True
        for pid in members:
            p = procs[pid]
            try:
                rss += p.memory_info().rss
            except psutil.Error:
                continue
            try:
                uss += p.memory_full_info().uss
            except (psutil.Error, AttributeError):
                uss_ok = False
        out.append({"pid": root, "processes": len(members), "rss": rss, "uss": uss if uss_ok else None})
    return out


# ------------------------------------------------------------------ active run
@dataclass
class _Active:
    kind: str                              # "run" | "preview"
    cfg: Config
    scheduler: Scheduler
    n_workers: int
    run_id: int | None = None
    started: float = field(default_factory=time.monotonic)
    stop_requested: bool = False
    task: asyncio.Task | None = None
    workers: dict[int, dict] = field(default_factory=dict)
    queries_done: set = field(default_factory=set)
    write_errors: int = 0
    result: dict | None = None
    slot_index: dict[str, int] = field(default_factory=dict)


class RunManager:
    def __init__(self, store: Store, config_path: Path, pool_factory: PoolFactory | None = None,
                 stats_interval: float = 2.0, seed_config: Path | None = CONFIG_PATH):
        self.store = store
        self.config_path = Path(config_path)
        self.config = load_or_init_config(self.config_path, seed_config)
        self.listeners: list[Listener] = []
        self.frame_sink: Callable[[int, bytes, dict], None] | None = None
        self.stats_interval = stats_interval
        self._pool_factory = pool_factory
        self._active: _Active | None = None
        self._last: dict | None = None
        self._write_lock = asyncio.Lock()

    # ---------------------------------------------------------- events
    def emit(self, name: str, params: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn(name, params)
            except Exception:
                log.exception("event listener failed on %s", name)

    def _log(self, message: str) -> None:
        level, text = plain_log(message)
        log.log(_LEVELS[level], "%s", text)
        self.emit("log", {"level": level, "text": text})

    async def write(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run a Store write in a worker thread; writes are serialised and keep their order."""
        async with self._write_lock:
            return await asyncio.to_thread(fn, *args)

    # ---------------------------------------------------------- config
    async def update_config(self, values: dict) -> Config:
        cfg, errors = apply_overrides(self.config, values)
        if errors:
            raise RpcError(INVALID_PARAMS, "invalid configuration", {"errors": errors})
        await asyncio.to_thread(self._save, cfg)
        self.config = cfg
        return cfg

    def _save(self, cfg: Config) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.save(self.config_path)

    def build_config(self, overrides: dict | None, for_run: bool = True) -> Config:
        cfg, errors = apply_overrides(self.config, overrides)
        if not errors:
            errors = validate_config(cfg, for_run)["errors"]
        if errors:
            raise RpcError(INVALID_PARAMS, "invalid configuration", {"errors": errors})
        return cfg

    # ---------------------------------------------------------- state
    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def slots(self) -> dict[int, Any]:
        """Live browser slots of the active run, by worker index."""
        return self._active.scheduler.slots if self._active else {}

    def status(self) -> dict:
        a = self._active
        if a is None:
            return {"active": False, "last": self._last}
        return {"active": True, "kind": a.kind, "run_id": a.run_id, "stopping": a.stop_requested,
                "elapsed": time.monotonic() - a.started,
                "workers": [dict(a.workers[i], paused=a.scheduler.is_paused(i)) for i in range(a.n_workers)],
                "progress": self._progress(a)}

    def _progress(self, a: _Active) -> dict:
        s = a.scheduler
        done, total = s.repo_done, s.repo_total
        elapsed = time.monotonic() - a.started
        eta = (elapsed / done) * (total - done) if a.kind == "run" and done >= 2 and total > done else None
        return {"run_id": a.run_id, "queries_done": len(a.queries_done), "queries_total": len(a.cfg.queries),
                "repos_done": done, "repos_total": total, "eta_seconds": eta, "elapsed": elapsed}

    def _require(self) -> _Active:
        if self._active is None:
            raise RpcError(NO_RUN, "no run is active")
        return self._active

    def _check_index(self, a: _Active, index: int) -> None:
        if not 0 <= index < a.n_workers:
            raise RpcError(BAD_WORKER, f"worker index {index} out of range (0..{a.n_workers - 1})")

    # ---------------------------------------------------------- start / stop
    async def start(self, overrides: dict | None) -> dict:
        cfg = self.build_config(overrides)
        a = self._begin("run", cfg)
        try:
            a.run_id = await asyncio.to_thread(self.store.start_run, dataclasses.asdict(cfg))
        except BaseException:
            self._active = None
            raise
        self._launch(a)
        return {"run_id": a.run_id, "workers": a.n_workers}

    async def preview_counts(self, overrides: dict | None) -> dict:
        """Load page 1 of every search and report GitHub's match counts. Nothing is stored."""
        cfg = self.build_config(overrides)
        a = self._begin("preview", cfg)
        self._launch(a)
        await asyncio.shield(a.task)
        return a.result or {"status": "failed", "totals": {}, "queries": []}

    def _begin(self, kind: str, cfg: Config) -> _Active:
        if self._active is not None:
            raise RpcError(RUN_ACTIVE, f"a {self._active.kind} is already active")
        count_only = kind == "preview"
        sched = Scheduler(cfg, log=self._log, count_only=count_only, pool_factory=self._wrap_factory)
        n = cfg.worker_count(searches_only=count_only)
        a = _Active(kind, cfg, sched, n)
        a.workers = {i: {"index": i, "text": "", "state": "pending"} for i in range(n)}
        sched.on_status = lambda idx, text, a=a: self._on_status(a, idx, text)
        sched.on_listing = lambda q, found, target, a=a: self._on_listing(a, q, found, target)
        sched.on_repo = lambda item, done, total, a=a: self._on_repo(a, item, done, total)
        self._active = a
        return a

    def _launch(self, a: _Active) -> None:
        self.emit("run.started", {"run_id": a.run_id, "kind": a.kind, "workers": a.n_workers,
                                  "queries": list(a.cfg.queries), "config": dataclasses.asdict(a.cfg)})
        a.task = asyncio.create_task(self._run(a), name=f"run-{a.run_id or a.kind}")

    def stop(self) -> dict:
        a = self._active
        if a is None:
            return {"stopping": False}
        a.stop_requested = True
        a.scheduler.stop()
        return {"stopping": True, "run_id": a.run_id}

    async def shutdown(self, timeout: float = 15.0) -> None:
        a = self._active
        if a is None or a.task is None:
            return
        self.stop()
        try:
            await asyncio.wait_for(asyncio.shield(a.task), timeout)
        except (asyncio.TimeoutError, Exception):
            a.task.cancel()
            await asyncio.gather(a.task, return_exceptions=True)

    def _wrap_factory(self, cfg, log_fn, stop, n):
        pool = (self._pool_factory or default_pool_factory)(cfg, log_fn, stop, n)
        pool.on_frame = self._on_frame
        return pool

    # ---------------------------------------------------------- pause / input
    def pause_worker(self, index: int) -> dict:
        a = self._require()
        self._check_index(a, index)
        a.scheduler.pause_worker(index)
        return {"index": index, "paused": True, "engaged": a.workers[index]["state"] == "paused"}

    def resume_worker(self, index: int) -> dict:
        a = self._require()
        self._check_index(a, index)
        a.scheduler.resume_worker(index)
        return {"index": index, "paused": False}

    def input_slot(self, index: int):
        """The slot to forward input to; only while the user has paused it and it has stopped working."""
        a = self._require()
        self._check_index(a, index)
        if not a.scheduler.is_paused(index):
            raise RpcError(NOT_PAUSED, "worker is not paused; call worker.pause first")
        if a.workers[index]["state"] != "paused":
            raise RpcError(NOT_PAUSED, "worker is still finishing its current task; wait for its "
                                       "'paused' status")
        slot = a.scheduler.slots.get(index)
        if slot is None:
            raise RpcError(BAD_WORKER, "this worker has no browser")
        return slot

    # ---------------------------------------------------------- scheduler callbacks
    def _on_status(self, a: _Active, index: int, text: str) -> None:
        plain = strip_markup(text)
        state = worker_state(plain)
        a.workers[index] = {"index": index, "text": plain, "state": state}
        self.emit("worker.status", {"index": index, "text": plain, "state": state})

    def _on_listing(self, a: _Active, query: str, found: int, target: int) -> None:
        if found >= target:
            a.queries_done.add(query)
        self.emit("run.progress", self._progress(a))

    async def _on_repo(self, a: _Active, item: dict, done: int, total: int) -> None:
        saved = False
        if a.kind == "run" and a.run_id is not None:
            try:
                await self.write(self.store.upsert_repo, item, a.run_id)
                saved = True
            except Exception as e:
                a.write_errors += 1
                self._log(f"[red]Could not save {item.get('full_name')}: {type(e).__name__}: {e}[/]")
        if not saved:
            return
        self.emit("repo.saved", {
            "run_id": a.run_id, "full_name": item.get("full_name"), "url": item.get("url"),
            "description": item.get("description"), "stars": item.get("stars"), "forks": item.get("forks"),
            "language": item.get("language"), "topics": item.get("topics") or [],
            "error": item.get("error"), "scraped_at": item.get("scraped_at")})
        self.emit("run.progress", self._progress(a))

    def _on_frame(self, slot_id: str, jpeg: bytes, meta: dict) -> None:
        a = self._active
        sink = self.frame_sink
        if a is None or sink is None:
            return
        idx = a.slot_index.get(slot_id)
        if idx is None or a.scheduler.slots.get(idx) is None or a.scheduler.slots[idx].id != slot_id:
            a.slot_index = {s.id: i for i, s in a.scheduler.slots.items()}
            idx = a.slot_index.get(slot_id)
        if idx is not None:
            sink(idx, jpeg, meta)

    # ---------------------------------------------------------- the run itself
    async def _run(self, a: _Active) -> None:
        sched = a.scheduler
        status, error = "done", None
        stats = asyncio.create_task(self._stats_loop(a), name="pool-stats")
        try:
            await sched.run()
            if a.stop_requested or sched.stop_event.is_set():
                status = "stopped"
            elif sched.pending:
                status, error = "failed", f"{sched.pending} task(s) left undone: every browser session failed"
        except asyncio.CancelledError:
            status = "stopped"
        except BaseException as e:
            status, error = "failed", f"{type(e).__name__}: {e}"
            log.exception("run failed")
            self._log(f"[red]Run failed: {type(e).__name__}: {e}[/]")
        finally:
            stats.cancel()
            await asyncio.gather(stats, return_exceptions=True)
            counts = {"repos": sched.repo_done, "repos_total": sched.repo_total,
                      "queries_done": len(a.queries_done), "queries_total": len(a.cfg.queries),
                      "errors": sum(1 for r in sched.results if r.get("error")),
                      "write_errors": a.write_errors, "elapsed": round(time.monotonic() - a.started, 1)}
            if a.kind == "run" and a.run_id is not None:
                try:
                    await asyncio.to_thread(self.store.finish_run, a.run_id, status, counts)
                except Exception:
                    log.exception("finish_run failed")
            payload = {"run_id": a.run_id, "kind": a.kind, "status": status, "error": error, "counts": counts}
            if a.kind == "preview":
                a.result = {"status": status, "totals": dict(sched.totals),
                            "queries": [{"query": q, "github_query": a.cfg.search_query(q),
                                         "total": sched.totals.get(q),
                                         "over_cap": (sched.totals.get(q) or 0) > GITHUB_RESULT_CAP}
                                        for q in a.cfg.queries]}
                payload["totals"] = a.result["totals"]
            self._last = payload
            self._active = None
            self.emit("run.finished", payload)

    async def _stats_loop(self, a: _Active) -> None:
        while True:
            await asyncio.sleep(self.stats_interval)
            pool = a.scheduler.pool
            if pool is None:
                continue
            try:
                pids = list(pool.processes())
                get_roots = getattr(pool, "roots", None)
                roots = list(get_roots()) if callable(get_roots) else None
                trees = await asyncio.to_thread(measure_processes, pids, roots)
            except Exception as e:
                log.debug("pool stats failed: %s", e)
                continue
            self.emit("pool.stats", self._stats_payload(a, pids, trees))

    def _stats_payload(self, a: _Active, pids: list[int], trees: list[dict]) -> dict:
        groups = plan_chromes(a.n_workers, a.cfg.workers_per_chrome, a.cfg.isolate_workers)
        mapped = len(trees) == len(groups)
        chromes = []
        slots = {i: {"index": i, "rss": None, "uss": None, "chrome": None} for i in range(a.n_workers)}
        for k, tree in enumerate(trees):
            workers = groups[k] if mapped else []
            chromes.append({**tree, "workers": workers})
            for w in workers:    # a shared Chrome's memory is split evenly between its workers
                slots[w].update(rss=tree["rss"] // len(workers),
                                uss=None if tree["uss"] is None else tree["uss"] // len(workers),
                                chrome=tree["pid"])
        uss_vals = [t["uss"] for t in trees]
        return {"run_id": a.run_id, "chrome_count": len(trees), "process_count": len(set(pids)),
                "total_rss": sum(t["rss"] for t in trees),
                "total_uss": sum(uss_vals) if uss_vals and None not in uss_vals else None,
                "chromes": chromes, "slots": list(slots.values())}
