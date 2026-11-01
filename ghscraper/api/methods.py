"""JSON-RPC method table. Parameter checking lives here; business logic in RunManager / Store."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import __version__, relocate
from ..browser.detect import browser_status
from ..config import MAX_BROWSERS, SORT_CHOICES, Config, Location, clear_location_pointer
from ..core.runmanager import RunManager, apply_overrides, validate_config
from ..exporters import export_rows
from ..filters import FILTERS
from ..store import Store, importer, query
from .protocol import (BROWSER_ERROR, EXPORT_FAILED, INVALID_PARAMS, METHOD_NOT_FOUND, NOT_FOUND, RUN_ACTIVE,
                       STORAGE_FAILED, RpcError)
from .screen import DEFAULT_FPS, DEFAULT_MAX_WIDTH, ScreenRelay
from .server import Client

log = logging.getLogger("ghscraper.api")

MAX_SEARCH_LIMIT = 1000
MAX_NAMES = 10000
MAX_FPS = 30
MIN_WIDTH, MAX_WIDTH = 160, 1920
INPUT_TYPES = ("mouse", "key", "text")

_REQUIRED: Any = object()


def _param(params: dict, name: str, kind: type | tuple, default: Any = _REQUIRED, *, nullable: bool = False) -> Any:
    if name not in params or (params[name] is None and nullable):
        if default is _REQUIRED:
            raise RpcError(INVALID_PARAMS, f"missing parameter '{name}'")
        return default
    v = params[name]
    kinds = kind if isinstance(kind, tuple) else (kind,)
    if (isinstance(v, bool) and bool not in kinds) or not isinstance(v, kinds):
        names = "/".join(k.__name__ for k in kinds)
        raise RpcError(INVALID_PARAMS, f"parameter '{name}' must be {names}")
    return v


def _int(params: dict, name: str, default: Any = _REQUIRED, lo: int | None = None, hi: int | None = None) -> int:
    v = _param(params, name, int, default)
    if lo is not None and v < lo or hi is not None and v > hi:
        raise RpcError(INVALID_PARAMS, f"parameter '{name}' must be between {lo} and {hi}")
    return v


def _str_list(params: dict, name: str, max_items: int = MAX_NAMES) -> list[str]:
    v = _param(params, name, list)
    if len(v) > max_items or not all(isinstance(x, str) for x in v):
        raise RpcError(INVALID_PARAMS, f"parameter '{name}' must be a list of up to {max_items} strings")
    return v


def _abs_path(value: Any, name: str = "path") -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise RpcError(INVALID_PARAMS, f"parameter '{name}' must be an absolute path")
    return Path(value)


class Api:
    def __init__(self, *, manager: RunManager, store: Store, relay: ScreenRelay, data_dir: Path,
                 db_path: Path, request_shutdown: Callable[[], None], pid: int | None = None,
                 test_mode: bool = False, migration: str = "none", location: Location | None = None):
        self.manager, self.store, self.relay = manager, store, relay
        self.migration = migration     # result of the Ghost Scraper -> Branch Scrapper data copy at startup
        self.data_dir, self.db_path = data_dir, db_path
        # How data_dir was chosen. None (tests, embedding) = fixed: storage.move / storage.reset are refused.
        self.location = location
        self._moving = False           # storage.move is copying: no run may start
        self._pending_dir: Path | None = None    # a move/reset was done; the sidecar must restart to use it
        self._request_shutdown = request_shutdown
        self.pid = pid if pid is not None else os.getpid()
        self.test_mode = test_mode
        self._methods: dict[str, Callable[[Client, dict], Awaitable[Any]]] = {
            "app.info": self.app_info, "app.shutdown": self.app_shutdown,
            "config.get": self.config_get, "config.update": self.config_update,
            "config.validate": self.config_validate,
            "run.start": self.run_start, "run.preview_counts": self.run_preview,
            "run.stop": self.run_stop, "run.status": self.run_status,
            "worker.pause": self.worker_pause, "worker.resume": self.worker_resume,
            "browser.view": self.browser_view, "browser.input": self.browser_input,
            "data.search": self.data_search, "data.facets": self.data_facets, "data.get": self.data_get,
            "data.delete": self.data_delete, "data.import_json": self.data_import,
            "datasets.list": self.datasets_list, "datasets.save": self.datasets_save,
            "datasets.delete": self.datasets_delete, "runs.list": self.runs_list,
            "export.write": self.export_write,
            "storage.move": self.storage_move, "storage.reset": self.storage_reset,
        }

    # ------------------------------------------------------------------ dispatcher protocol
    _WRITES_BLOCKED_AFTER_MOVE = frozenset({
        "config.update", "run.start", "run.preview_counts", "data.delete", "data.import_json",
        "datasets.save", "datasets.delete",
    })

    async def handle(self, client: Client, method: str, params: dict) -> Any:
        fn = self._methods.get(method)
        if fn is None:
            raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")
        if self._pending_dir is not None and method in self._WRITES_BLOCKED_AFTER_MOVE:
            # The data was copied/switched to another folder; anything written now would land in the old one
            # and be missing after the restart.
            raise RpcError(INVALID_PARAMS, "the data folder was changed; restart the backend before making changes")
        try:
            return await fn(client, params)
        except ValueError as e:      # Store / query validation errors
            raise RpcError(INVALID_PARAMS, str(e)) from e

    async def client_gone(self, client: Client) -> None:
        await self.relay.client_gone(client)

    # ------------------------------------------------------------------ app
    async def app_info(self, client: Client, p: dict) -> dict:
        schema = await asyncio.to_thread(self.store.schema_version)
        cfg = self.manager.config
        browser = await asyncio.to_thread(browser_status, cfg.browser, cfg.browser_path)
        return {"version": __version__, "pid": self.pid, "platform": sys.platform,
                "python": platform.python_version(), "data_dir": str(self.data_dir),
                "db_path": str(self.db_path), "config_path": str(self.manager.config_path),
                "schema_version": schema, "run_active": self.manager.active, "test_mode": self.test_mode,
                "max_browsers": MAX_BROWSERS, "frozen": bool(getattr(sys, "frozen", False)),
                "browser": browser, "migration": self.migration,
                "storage": await asyncio.to_thread(self._storage_info)}

    def _storage_info(self) -> dict:
        loc = self.location
        db_bytes = 0
        for suffix in ("", "-wal"):
            try:
                db_bytes += (self.db_path.parent / (self.db_path.name + suffix)).stat().st_size
            except OSError:
                pass
        default_dir = loc.default_dir if loc else self.data_dir
        custom = bool(loc and loc.source == "pointer")
        return {"source": loc.source if loc else "arg", "custom": custom,
                "can_change": bool(loc and loc.can_change), "default_dir": str(default_dir),
                "default_has_data": custom and (default_dir / "data.db").is_file(),
                "db_bytes": db_bytes, "problem": loc.problem if loc else None,
                "pending_restart": str(self._pending_dir) if self._pending_dir else None}

    async def app_shutdown(self, client: Client, p: dict) -> dict:
        asyncio.get_running_loop().call_later(0.1, self._request_shutdown)   # let the reply go out first
        return {"ok": True}

    # ------------------------------------------------------------------ config
    async def config_get(self, client: Client, p: dict) -> dict:
        return {
            "config": dataclasses.asdict(self.manager.config),
            "defaults": dataclasses.asdict(Config()),
            "meta": {
                "max_browsers": MAX_BROWSERS,
                "sort_choices": [{"label": l, "sort": s, "order": o} for l, s, o in SORT_CHOICES],
                "filters": [{"key": f.key, "label": f.label, "kind": f.kind, "qualifier": f.qualifier,
                             "help": f.help, "group": f.group, "choices": [list(c) for c in f.choices],
                             "negate": f.negate} for f in FILTERS],
            },
        }

    async def config_update(self, client: Client, p: dict) -> dict:
        cfg = await self.manager.update_config(_param(p, "values", dict))
        return {"config": dataclasses.asdict(cfg)}

    async def config_validate(self, client: Client, p: dict) -> dict:
        overrides = _param(p, "overrides", dict, {}, nullable=True)
        cfg, errors = apply_overrides(self.manager.config, overrides)
        result = validate_config(cfg)
        result["errors"] = errors + result["errors"]
        result["ok"] = not result["errors"]
        return result

    # ------------------------------------------------------------------ run
    def _not_moving(self) -> None:
        if self._moving:
            raise RpcError(RUN_ACTIVE, "the data folder is being moved; try again in a moment")

    async def run_start(self, client: Client, p: dict) -> dict:
        self._not_moving()
        return await self.manager.start(_param(p, "overrides", dict, {}, nullable=True))

    async def run_preview(self, client: Client, p: dict) -> dict:
        self._not_moving()
        return await self.manager.preview_counts(_param(p, "overrides", dict, {}, nullable=True))

    async def run_stop(self, client: Client, p: dict) -> dict:
        return self.manager.stop()

    async def run_status(self, client: Client, p: dict) -> dict:
        return self.manager.status()

    async def worker_pause(self, client: Client, p: dict) -> dict:
        return self.manager.pause_worker(_int(p, "index", lo=0))

    async def worker_resume(self, client: Client, p: dict) -> dict:
        return self.manager.resume_worker(_int(p, "index", lo=0))

    # ------------------------------------------------------------------ browsers
    async def browser_view(self, client: Client, p: dict) -> dict:
        raw = _param(p, "indices", list)
        if not all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i < MAX_BROWSERS for i in raw):
            raise RpcError(INVALID_PARAMS, f"'indices' must be integers in 0..{MAX_BROWSERS - 1}")
        indices = list(dict.fromkeys(raw))
        fps = _int(p, "fps", int(DEFAULT_FPS), 1, MAX_FPS)
        width = _int(p, "max_width", DEFAULT_MAX_WIDTH, MIN_WIDTH, MAX_WIDTH)
        return await self.relay.set_view(client, indices, fps, width)

    async def browser_input(self, client: Client, p: dict) -> dict:
        index = _int(p, "index", lo=0)
        event = _param(p, "event", dict)
        if event.get("type") not in INPUT_TYPES:
            raise RpcError(INVALID_PARAMS, f"event.type must be one of {', '.join(INPUT_TYPES)}")
        slot = self.manager.input_slot(index)     # NOT_PAUSED etc.
        try:
            await slot.dispatch_input(event)
        except (ValueError, KeyError, TypeError) as e:
            raise RpcError(INVALID_PARAMS, f"bad input event: {type(e).__name__}: {e}") from e
        except Exception as e:
            raise RpcError(BROWSER_ERROR, f"{type(e).__name__}: {e}") from e
        return {"ok": True}

    # ------------------------------------------------------------------ data
    @staticmethod
    def _sort(p: dict) -> list | None:
        return _param(p, "sort", list, None, nullable=True)

    async def data_search(self, client: Client, p: dict) -> dict:
        filters = _param(p, "filters", dict, None, nullable=True)
        limit = _int(p, "limit", 100, 0, MAX_SEARCH_LIMIT)
        offset = _int(p, "offset", 0, 0)
        columns = _param(p, "columns", list, None, nullable=True)
        sort = self._sort(p)
        return await asyncio.to_thread(query.search, self.store, filters, sort, limit, offset, columns)

    async def data_facets(self, client: Client, p: dict) -> dict:
        limit = _int(p, "limit", 0, 0)
        return await asyncio.to_thread(self.store.facets, limit or None)

    async def data_get(self, client: Client, p: dict) -> dict:
        name = _param(p, "full_name", str)
        rec = await asyncio.to_thread(self.store.get_repo, name)
        if rec is None:
            raise RpcError(NOT_FOUND, f"no repository {name!r}")
        return rec

    async def data_delete(self, client: Client, p: dict) -> dict:
        names = _str_list(p, "full_names")

        def work() -> int:
            return sum(1 for n in names if self.store.delete_repo(n))
        return {"deleted": await self.manager.write(work)}

    async def data_import(self, client: Client, p: dict) -> dict:
        paths = [_abs_path(x, "paths[]") for x in _str_list(p, "paths", 1000)]
        return await self.manager.write(importer.import_json_files, self.store, paths)

    async def datasets_list(self, client: Client, p: dict) -> dict:
        return {"datasets": await asyncio.to_thread(self.store.list_datasets)}

    async def datasets_save(self, client: Client, p: dict) -> dict:
        name = _param(p, "name", str)
        filters = _param(p, "filters", dict, {}, nullable=True)
        columns = _param(p, "columns", list, None, nullable=True)
        sort = self._sort(p)
        return {"id": await self.manager.write(self.store.save_dataset, name, filters, columns, sort)}

    async def datasets_delete(self, client: Client, p: dict) -> dict:
        return {"deleted": await self.manager.write(self.store.delete_dataset, _int(p, "id"))}

    async def runs_list(self, client: Client, p: dict) -> dict:
        return {"runs": await asyncio.to_thread(self.store.list_runs)}

    # ------------------------------------------------------------------ storage
    def _storage_guard(self) -> Location:
        """Common refusals of storage.move / storage.reset (nothing has been touched yet)."""
        loc = self.location
        if loc is None or not loc.can_change:
            why = {"arg": "--data-dir", "env": "GHOSTSCRAPER_HOME"}.get(loc.source if loc else "arg", "the host")
            raise RpcError(INVALID_PARAMS, f"the data folder is fixed by {why} and cannot be changed from the app")
        if self._pending_dir is not None:
            raise RpcError(INVALID_PARAMS, "a change of data folder is waiting for a restart of the backend")
        if self.manager.active or self._moving:
            raise RpcError(RUN_ACTIVE, "a run is active; stop it before changing the data folder")
        return loc

    @staticmethod
    def _move_rpc_error(e: relocate.MoveError) -> RpcError:
        return RpcError(INVALID_PARAMS if e.kind == "invalid" else STORAGE_FAILED, str(e))

    async def storage_move(self, client: Client, p: dict) -> dict:
        """Copy the data to another folder (or switch to data already there) and point the app at it.
        `dry_run: true` only validates and reports what would happen."""
        loc = self._storage_guard()
        path = _param(p, "path", str)
        dry_run = _param(p, "dry_run", bool, False)
        if dry_run:
            try:
                plan = await asyncio.to_thread(relocate.plan_move, self.data_dir, path, loc.default_dir)
            except relocate.MoveError as e:
                raise self._move_rpc_error(e) from e
            return {**plan.describe(), "dry_run": True}

        self._moving = True            # set before the first await: no run can slip in
        try:
            async with self.manager._write_lock:        # no data.delete / import while the files are copied
                if self.manager.active:
                    raise RpcError(RUN_ACTIVE, "a run is active; stop it before changing the data folder")
                try:
                    plan = await asyncio.to_thread(relocate.plan_move, self.data_dir, path, loc.default_dir)
                    result = await asyncio.to_thread(relocate.move, loc.default_dir, plan)
                except relocate.MoveError as e:
                    raise self._move_rpc_error(e) from e
        finally:
            self._moving = False
        self._pending_dir = result.plan.target
        log.info("storage.move: %s -> %s (%s)", result.plan.source, result.plan.target, result.plan.mode)
        return {"restart_required": True, **result.plan.describe(), "tables": result.tables or {},
                "warnings": result.warnings}

    async def storage_reset(self, client: Client, p: dict) -> dict:
        """Go back to the default data folder by removing the pointer file. Data at the custom folder is kept."""
        loc = self._storage_guard()
        if loc.source != "pointer" and not loc.problem:
            raise RpcError(INVALID_PARAMS, "the default data folder is already in use")
        try:
            await asyncio.to_thread(clear_location_pointer, loc.default_dir)
        except OSError as e:
            raise RpcError(STORAGE_FAILED, f"could not remove {loc.pointer_file}: {e}") from e
        self._pending_dir = loc.default_dir
        log.info("storage.reset: back to %s (kept %s)", loc.default_dir, self.data_dir)
        return {"restart_required": True, "data_dir": str(loc.default_dir), "kept": str(self.data_dir)}

    # ------------------------------------------------------------------ export
    async def export_write(self, client: Client, p: dict) -> dict:
        fmt = _param(p, "format", str)
        if fmt not in ("json", "csv"):
            raise RpcError(INVALID_PARAMS, "'format' must be 'json' or 'csv'")
        target = _abs_path(p.get("path"))
        if target.suffix.lower() != f".{fmt}":
            raise RpcError(INVALID_PARAMS, f"'path' must end in .{fmt}")
        if target.is_dir():
            raise RpcError(INVALID_PARAMS, "'path' is a directory")
        scopes = [k for k in ("filters", "full_names", "run_id") if p.get(k) is not None]
        if len(scopes) != 1:
            raise RpcError(INVALID_PARAMS, "give exactly one of 'filters', 'full_names', 'run_id'")
        columns = query.validate_columns(_param(p, "columns", list, None, nullable=True))
        sort = self._sort(p)
        names: list[str] | None = None
        filters: dict | None = None
        if scopes[0] == "full_names":
            names = _str_list(p, "full_names")
        elif scopes[0] == "run_id":
            filters = {"run_id": _int(p, "run_id")}
        else:
            filters = _param(p, "filters", dict)
        if filters is not None:               # fail fast, before touching the disk
            c = query.compile_filters(filters)
            query.validate_sort(sort, has_text=bool(c.fts))
        store = self.store

        def rows():
            if names is None:
                yield from query.iter_all(store, filters, sort, columns)
                return
            for i in range(0, len(names), 400):         # keep the caller's order
                chunk = names[i:i + 400]
                found = {r["full_name"].lower(): r for r in store.decode_rows(store.read(
                    f"SELECT * FROM repos WHERE full_name IN ({','.join('?' * len(chunk))})", chunk))}
                for n in chunk:
                    if n.lower() in found:
                        rec = found.pop(n.lower())
                        yield rec if columns is None else {k: rec.get(k) for k in columns}

        def work() -> int:
            count = 0

            def counted():
                nonlocal count
                for r in rows():
                    count += 1
                    yield r
            part = target.with_name(target.name + ".part")
            try:
                export_rows(counted(), part, fmt, columns)
                os.replace(part, target)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            return count

        try:
            n = await asyncio.to_thread(work)
        except OSError as e:
            raise RpcError(EXPORT_FAILED, f"could not write {target}: {e}") from e
        return {"rows": n, "path": str(target), "format": fmt}
