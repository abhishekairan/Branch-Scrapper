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

from .. import __version__
from ..browser.detect import browser_status
from ..config import MAX_BROWSERS, SORT_CHOICES, Config
from ..core.runmanager import RunManager, apply_overrides, validate_config
from ..exporters import export_rows
from ..filters import FILTERS
from ..store import Store, importer, query
from .protocol import (BROWSER_ERROR, EXPORT_FAILED, INVALID_PARAMS, METHOD_NOT_FOUND, NOT_FOUND, RpcError)
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
                 test_mode: bool = False, migration: str = "none"):
        self.manager, self.store, self.relay = manager, store, relay
        self.migration = migration     # result of the Ghost Scraper -> Branch Scrapper data copy at startup
        self.data_dir, self.db_path = data_dir, db_path
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
        }

    # ------------------------------------------------------------------ dispatcher protocol
    async def handle(self, client: Client, method: str, params: dict) -> Any:
        fn = self._methods.get(method)
        if fn is None:
            raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")
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
                "browser": browser, "migration": self.migration}

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
    async def run_start(self, client: Client, p: dict) -> dict:
        return await self.manager.start(_param(p, "overrides", dict, {}, nullable=True))

    async def run_preview(self, client: Client, p: dict) -> dict:
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
