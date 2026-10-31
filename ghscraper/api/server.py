"""WebSocket transport: auth, origin check, per-client outbound channel, JSON-RPC dispatch.

Outbound rules (see docs/sidecar-protocol.md): JSON messages (responses, events) are queued
without limit and never dropped; screencast frames keep only the newest frame per slot.
"""
from __future__ import annotations

import asyncio
import hmac
import http
import json
import logging
import re
from collections import deque
from typing import Any, Awaitable, Callable, Protocol

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .protocol import (CLOSE_AUTH, INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND,
                       PARSE_ERROR, RpcError, error_response, notification, response)

log = logging.getLogger("ghscraper.api")

AUTH_TIMEOUT = 5.0
MAX_MESSAGE_BYTES = 1024 * 1024      # inbound limit; larger messages close the socket with 1009

_ORIGIN_RE = re.compile(
    r"tauri://localhost|https?://tauri\.localhost|http://localhost:\d{1,5}")


def origin_allowed(origin: str | None) -> bool:
    """A missing Origin (non-browser client) passes here; the token still protects it."""
    if origin is None:
        return True
    return _ORIGIN_RE.fullmatch(origin) is not None


class Client:
    """One connection's outbound side. Transport-agnostic: `send` takes str (text) or bytes (binary)."""

    def __init__(self, send: Callable[[str | bytes], Awaitable[None]],
                 close: Callable[[int, str], Awaitable[None]] | None = None):
        self._send = send
        self._close = close
        self._json: deque[str] = deque()
        self._frames: dict[int, bytes] = {}          # slot index -> newest packed frame (size 1 per slot)
        self._wake = asyncio.Event()
        self._closed = False
        self.dropped_frames = 0
        self.views: dict[int, tuple[float, int]] = {}  # slot index -> (fps, max_width) this client watches
        self.tasks: set[asyncio.Task] = set()          # in-flight request handlers
        self._writer: asyncio.Task | None = None

    # -- producers (event-loop thread only)
    def send_json(self, text: str) -> None:
        if not self._closed:
            self._json.append(text)
            self._wake.set()

    def offer_frame(self, index: int, packed: bytes) -> None:
        if self._closed:
            return
        if index in self._frames:
            self.dropped_frames += 1                 # latest wins
        self._frames[index] = packed
        self._wake.set()

    # -- writer
    def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop(), name="client-writer")

    async def _write_loop(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while True:
                    if self._json:
                        await self._send(self._json.popleft())
                    elif self._frames:
                        idx = next(iter(self._frames))
                        await self._send(self._frames.pop(idx))
                    else:
                        break
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("client writer failed")
        finally:
            self._closed = True

    async def flush(self, timeout: float = 2.0) -> None:
        """Wait until queued JSON has been handed to the socket."""
        async def _wait():
            while self._json and not self._closed:
                await asyncio.sleep(0.01)
        try:
            await asyncio.wait_for(_wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def aclose(self, code: int = 1000, reason: str = "") -> None:
        await self.flush()
        self._closed = True
        if self._writer:
            self._writer.cancel()
            await asyncio.gather(self._writer, return_exceptions=True)
        for t in list(self.tasks):
            t.cancel()
        if self._close:
            try:
                await self._close(code, reason)
            except Exception:
                pass


class Hub:
    """The set of authenticated clients; fan-out for events and frames."""

    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def broadcast(self, method: str, params: Any) -> None:
        if not self.clients:
            return
        text = notification(method, params)
        for c in list(self.clients):
            c.send_json(text)

    def publish_frame(self, index: int, packed: bytes) -> None:
        for c in list(self.clients):
            if index in c.views:
                c.offer_frame(index, packed)


class Dispatcher(Protocol):
    async def handle(self, client: Client, method: str, params: dict) -> Any: ...
    async def client_gone(self, client: Client) -> None: ...


class ApiServer:
    def __init__(self, hub: Hub, dispatcher: Dispatcher, token: str, host: str = "127.0.0.1",
                 port: int = 0, auth_timeout: float = AUTH_TIMEOUT, hello: dict | None = None):
        if len(token) < 32:
            raise ValueError("token must be at least 32 characters")
        self.hub = hub
        self.dispatcher = dispatcher
        self._token = token.encode("utf-8")
        self.host, self._port, self.auth_timeout = host, port, auth_timeout
        self.hello = hello or {}
        self._server = None

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await serve(self._connection, self.host, self._port,
                                   process_request=self._process_request,
                                   max_size=MAX_MESSAGE_BYTES, compression=None,
                                   ping_interval=20, ping_timeout=20, logger=log)

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()                         # stop accepting
        for c in list(self.hub.clients):
            await c.aclose(1001, "server shutting down")
        self.hub.clients.clear()
        try:
            await asyncio.wait_for(self._server.wait_closed(), 5)
        except asyncio.TimeoutError:
            pass

    # -- handshake
    def _process_request(self, connection: ServerConnection, request):
        origin = request.headers.get("Origin")
        if not origin_allowed(origin):
            log.warning("rejected connection with Origin %r", origin)
            return connection.respond(http.HTTPStatus.FORBIDDEN, "Origin not allowed\n")
        return None

    # -- one connection
    async def _connection(self, ws: ServerConnection) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), self.auth_timeout)
        except asyncio.TimeoutError:
            await ws.close(CLOSE_AUTH, "auth timeout")
            return
        except ConnectionClosed:
            return
        if not self._token_ok(raw):
            await ws.close(CLOSE_AUTH, "unauthorized")
            return

        client = Client(ws.send, ws.close)
        client.start()
        self.hub.clients.add(client)
        client.send_json(notification("auth.ok", self.hello))
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    await ws.close(1003, "binary messages from the client are not supported")
                    break
                self._spawn(client, message)
        except ConnectionClosed:
            pass
        finally:
            self.hub.clients.discard(client)
            await client.aclose()
            try:
                await self.dispatcher.client_gone(client)
            except Exception:
                log.exception("client_gone failed")

    def _token_ok(self, raw: Any) -> bool:
        if not isinstance(raw, str):
            return False
        try:
            msg = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(msg, dict) or msg.get("type") != "auth" or not isinstance(msg.get("token"), str):
            return False
        return hmac.compare_digest(msg["token"].encode("utf-8"), self._token)

    # -- JSON-RPC
    def _spawn(self, client: Client, message: str) -> None:
        task = asyncio.create_task(self._handle_message(client, message))
        client.tasks.add(task)
        task.add_done_callback(client.tasks.discard)

    async def _handle_message(self, client: Client, message: str) -> None:
        try:
            req = json.loads(message)
        except ValueError:
            client.send_json(error_response(None, RpcError(PARSE_ERROR, "parse error")))
            return
        if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
            rid = req.get("id") if isinstance(req, dict) else None
            client.send_json(error_response(rid, RpcError(INVALID_REQUEST, "invalid request")))
            return
        has_id = "id" in req
        rid = req.get("id")
        if has_id and rid is not None and (isinstance(rid, bool) or not isinstance(rid, (str, int, float))):
            client.send_json(error_response(None, RpcError(INVALID_REQUEST, "invalid id")))
            return
        params = req.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if has_id:
                client.send_json(error_response(rid, RpcError(INVALID_PARAMS, "params must be an object")))
            return
        try:
            result = await self.dispatcher.handle(client, req["method"], params)
        except asyncio.CancelledError:
            raise
        except RpcError as e:
            if has_id:
                client.send_json(error_response(rid, e))
            return
        except Exception as e:
            log.exception("unhandled error in %s", req["method"])
            if has_id:
                client.send_json(error_response(rid, RpcError(INTERNAL_ERROR, f"{type(e).__name__}: {e}")))
            return
        if has_id:
            client.send_json(response(rid, result))


__all__ = ["ApiServer", "Client", "Hub", "origin_allowed", "METHOD_NOT_FOUND"]
