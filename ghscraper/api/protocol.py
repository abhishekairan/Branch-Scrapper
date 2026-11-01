"""Wire-protocol helpers: error codes, JSON-RPC envelopes, binary frames, log/status text."""
from __future__ import annotations

import json
import re
import struct
from typing import Any

# --- JSON-RPC 2.0 standard codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# --- application codes (-32000..-32099 is the implementation-defined range)
RUN_ACTIVE = -32001        # a run / preview is already active
NO_RUN = -32002            # nothing is running
NOT_PAUSED = -32003        # browser.input on a worker the user has not paused
NOT_FOUND = -32004
EXPORT_FAILED = -32005
BAD_WORKER = -32006        # worker index out of range for the current run
BROWSER_ERROR = -32007     # the browser slot rejected / failed the operation
STORAGE_FAILED = -32008    # storage.move could not copy / verify / switch; nothing was changed

# --- WebSocket close codes
CLOSE_AUTH = 4401          # missing / wrong / late auth message

FRAME_VERSION = 1
_FRAME_HEADER = struct.Struct(">BHIII")   # version, slot index, seq, width, height
FRAME_HEADER_SIZE = _FRAME_HEADER.size    # 15


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_obj(self) -> dict:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def notification(method: str, params: Any) -> str:
    return dumps({"jsonrpc": "2.0", "method": method, "params": params})


def response(id_: Any, result: Any) -> str:
    return dumps({"jsonrpc": "2.0", "id": id_, "result": result})


def error_response(id_: Any, err: RpcError) -> str:
    return dumps({"jsonrpc": "2.0", "id": id_, "error": err.to_obj()})


def pack_frame(index: int, seq: int, width: int, height: int, jpeg: bytes) -> bytes:
    """`[u8 version=1][u16 slot index][u32 seq][u32 width][u32 height][jpeg]`, big-endian."""
    return _FRAME_HEADER.pack(FRAME_VERSION, index, seq & 0xFFFFFFFF,
                              max(0, int(width or 0)) & 0xFFFFFFFF,
                              max(0, int(height or 0)) & 0xFFFFFFFF) + jpeg


def unpack_frame(data: bytes) -> dict:
    """Inverse of `pack_frame` (used by tests and handy for other Python clients)."""
    version, index, seq, width, height = _FRAME_HEADER.unpack_from(data)
    return {"version": version, "index": index, "seq": seq, "width": width, "height": height,
            "jpeg": data[FRAME_HEADER_SIZE:]}


# ------------------------------------------------------------------ rich markup -> plain text
# Same rule rich uses: a tag starts with a lowercase letter, '#', '/' or '@', so labels
# such as "[B1]" stay literal.
_TAG_RE = re.compile(r"(\\*)\[([a-z#/@][^\[\]]*)\]")


def _unescape(m: re.Match) -> str:
    slashes = m.group(1)
    if len(slashes) % 2:                         # escaped tag: "\[bold]" -> literal "[bold]"
        return slashes[:-1] + f"[{m.group(2)}]"
    return slashes[: len(slashes) // 2]


def strip_markup(text: str) -> str:
    return _TAG_RE.sub(_unescape, text)


def log_level(text: str) -> str:
    """Infer a level from rich colour tags: red -> error, yellow -> warning, dim -> debug."""
    tags = " ".join(m.group(2) for m in _TAG_RE.finditer(text) if not len(m.group(1)) % 2 == 1)
    if "red" in tags:
        return "error"
    if "yellow" in tags:
        return "warning"
    if "dim" in tags:
        return "debug"
    return "info"


def plain_log(text: str) -> tuple[str, str]:
    """(level, plain text) for a rich-markup log line."""
    return log_level(text), strip_markup(text)


def worker_state(plain: str) -> str:
    """Coarse state of a worker from its plain status text."""
    t = plain.strip().lower()
    for prefix, state in (("starting", "starting"), ("searching", "searching"), ("repo ", "repo"),
                          ("idle", "idle"), ("paused", "paused"), ("recovering", "recovering"),
                          ("done", "done"), ("stopped", "stopped"), ("failed", "failed")):
        if t.startswith(prefix):
            return state
    return "other"
