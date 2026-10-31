"""Sidecar entry point: ``python -m ghscraper.sidecar``.

The desktop shell spawns this process, passes a secret in GHOSTSCRAPER_TOKEN and reads one
line from stdout: ``READY {"port": N, "pid": P, "version": "V"}``. Everything else goes to
stderr and to <data dir>/logs/sidecar.log. The process exits when its stdin closes (the
parent died), on SIGINT/SIGTERM, or on the ``app.shutdown`` RPC.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .browser import procguard
from .config import CONFIG_PATH, app_data_dir, migrate_legacy_data_dir

FROZEN = bool(getattr(sys, "frozen", False))     # True inside the PyInstaller build
MIN_TOKEN_LEN = 32
TOKEN_ENV = "GHOSTSCRAPER_TOKEN"
FAKE_POOL_ENV = "GHOSTSCRAPER_FAKE_POOL"

log = logging.getLogger("ghscraper.sidecar")


class _DeferredLog:
    """Collects log calls made before the log file exists (the data dir migration runs first)."""

    def __init__(self) -> None:
        self.records: list[tuple[int, str, tuple]] = []

    def info(self, msg: str, *args) -> None:
        self.records.append((logging.INFO, msg, args))

    def warning(self, msg: str, *args) -> None:
        self.records.append((logging.WARNING, msg, args))

    def error(self, msg: str, *args) -> None:
        self.records.append((logging.ERROR, msg, args))

    def replay(self, logger: logging.Logger) -> None:
        for level, msg, args in self.records:
            logger.log(level, msg, *args)
        self.records.clear()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m ghscraper.sidecar", description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=0, help="TCP port on 127.0.0.1 (default 0 = random)")
    ap.add_argument("--data-dir", type=Path, default=None, help="override the app data directory")
    ap.add_argument("--test-mode", action="store_true",
                    help="allow test-only switches (e.g. %s=1 for an offline fake browser pool)" % FAKE_POOL_ENV)
    args = ap.parse_args(argv)
    if not 0 <= args.port <= 65535:
        ap.error("--port must be 0..65535")
    return args


def setup_logging(data_dir: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        (data_dir / "logs").mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            data_dir / "logs" / "sidecar.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError as e:
        print(f"cannot open log file: {e}", file=sys.stderr)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _watch_stdin(loop: asyncio.AbstractEventLoop, shutdown: asyncio.Event) -> None:
    """Set `shutdown` when stdin reaches EOF (the parent closed it or died)."""
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        log.warning("no stdin: parent-death detection is disabled")
        return

    def run() -> None:
        try:
            while os.read(fd, 4096):
                pass
        except OSError as e:
            log.warning("stdin read failed (%s); treating as EOF", e)
        log.info("stdin closed; shutting down")
        loop.call_soon_threadsafe(shutdown.set)

    threading.Thread(target=run, name="stdin-watch", daemon=True).start()


def _install_signals(loop: asyncio.AbstractEventLoop, shutdown: asyncio.Event) -> None:
    def handler(signum, frame) -> None:
        loop.call_soon_threadsafe(shutdown.set)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


async def serve(args: argparse.Namespace, token: str, data_dir: Path, migration: str = "none") -> None:
    # Imported here so `--help` and the token check stay fast.
    from .api.methods import Api
    from .api.screen import ScreenRelay
    from .api.server import ApiServer, Hub
    from .core.runmanager import RunManager
    from .store import Store

    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    _install_signals(loop, shutdown)

    pool_factory = None
    if os.environ.get(FAKE_POOL_ENV) == "1":
        if args.test_mode:
            from .api.testing import fake_pool_factory
            pool_factory = fake_pool_factory()
            log.warning("TEST MODE: using the offline fake browser pool")
        else:
            log.warning("%s ignored: it is only honoured together with --test-mode", FAKE_POOL_ENV)

    db_path = data_dir / "data.db"
    store = Store(db_path)
    # Frozen (installed app): there is no repo-local config.json to seed from.
    seed = None if FROZEN else CONFIG_PATH
    manager = RunManager(store, data_dir / "config.json", pool_factory=pool_factory, seed_config=seed)
    hub = Hub()
    relay = ScreenRelay(manager, hub)
    manager.listeners.append(hub.broadcast)
    api = Api(manager=manager, store=store, relay=relay, data_dir=data_dir, db_path=db_path,
              request_shutdown=shutdown.set, test_mode=bool(pool_factory), migration=migration)
    server = ApiServer(hub, api, token, port=args.port,
                       hello={"version": __version__, "pid": os.getpid()})
    try:
        await server.start()
        print("READY " + json.dumps({"port": server.port, "pid": os.getpid(), "version": __version__}),
              flush=True)
        log.info("listening on 127.0.0.1:%d, data dir %s", server.port, data_dir)
        _watch_stdin(loop, shutdown)
        await shutdown.wait()
    finally:
        log.info("shutting down")
        await manager.shutdown()
        await server.close()
        store.close()
        log.info("bye")


def main(argv: list[str] | None = None) -> int:
    procguard.guard_current_process()      # Chrome must die with us: do this before anything else
    args = parse_args(argv)
    if args.test_mode and FROZEN:       # the fake pool is not part of the packaged build
        print("--test-mode is not available in the packaged app", file=sys.stderr)
        return 2
    token = os.environ.pop(TOKEN_ENV, "")  # pop: Chrome children must not inherit the secret
    if len(token) < MIN_TOKEN_LEN:
        print(f"{TOKEN_ENV} must be set to a secret of at least {MIN_TOKEN_LEN} characters", file=sys.stderr)
        return 2
    # Before anything opens the DB, config or log files in the new location: bring over the data of the
    # pre-rename app (Ghost Scraper). Skipped for an explicit --data-dir / GHOSTSCRAPER_HOME.
    deferred = _DeferredLog()
    if args.data_dir:
        data_dir, migration = args.data_dir, "none"
    else:
        data_dir = app_data_dir()
        migration = migrate_legacy_data_dir(data_dir, deferred)
    data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(data_dir)
    deferred.replay(log)
    log.info("Branch Scrapper sidecar %s, data dir %s, migration: %s", __version__, data_dir, migration)
    try:
        asyncio.run(serve(args, token, data_dir, migration))
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("sidecar crashed")
        return 1
    return 0


if __name__ == "__main__":
    code = main()
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)      # a daemon thread may still be blocked reading stdin
