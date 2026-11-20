"""Local client and executable adapter for the owner's Telegram service."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import uuid


DEFAULT_SOCKET = "/run/praxis-owner-telegram/telegram.sock"
MAX_FRAME = 8 * 1024 * 1024
HEADER = struct.Struct("!I")
READ_ONLY = frozenset({"status", "next_event", "yegor_read", "yegor_search"})


def mutates(method: str, arguments: dict) -> bool:
    if method == "yegor_mtproto":
        return arguments.get("action") == "call"
    if method == "yegor_wake_rules":
        return arguments.get("action") == "set"
    if method == "yegor_media":
        return arguments.get("action") == "send"
    return method not in READ_ONLY


def encode_frame(value: dict) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if not 0 < len(payload) <= MAX_FRAME:
        raise ValueError("Telegram RPC frame is too large")
    return HEADER.pack(len(payload)) + payload


def frame_size(header: bytes) -> int:
    size, = HEADER.unpack(header)
    if not 0 < size <= MAX_FRAME:
        raise ValueError("invalid Telegram RPC frame size")
    return size


def decode_payload(payload: bytes) -> dict:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Telegram RPC expects a JSON object")
    return value


def _receive(sock: socket.socket, size: int) -> bytes:
    data = bytearray(size)
    view = memoryview(data)
    while view:
        read = sock.recv_into(view)
        if read == 0:
            raise EOFError("Telegram RPC disconnected")
        view = view[read:]
    return bytes(data)


def request(method: str, arguments: dict | None = None, *, execution: dict | None = None,
            path: str | os.PathLike | None = None, timeout: float = 90) -> dict:
    arguments = arguments or {}
    address = os.fspath(path or os.getenv("PRAXIS_OWNER_TELEGRAM_SOCKET") or DEFAULT_SOCKET)
    request_id = uuid.uuid4().hex
    payload = encode_frame({"id": request_id, "method": method, "arguments": arguments,
                            "execution": execution or {}})
    submitted = False
    try:
        # On Windows this can also address a Unix socket in an isolated local test.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(address)
            submitted = True
            sock.sendall(payload)
            reply = decode_payload(_receive(sock, frame_size(_receive(sock, HEADER.size))))
            if reply.get("id") != request_id or not isinstance(reply.get("result"), dict):
                raise ValueError("unexpected Telegram RPC response")
            return reply["result"]
    except (OSError, EOFError, ValueError) as exc:
        return {"status": "in_doubt" if submitted and mutates(method, arguments) else "unavailable",
                "reason": type(exc).__name__}


def run_tool_script(method: str, arguments: dict, execution: dict) -> dict:
    """Execute a fixed, short-lived adapter and retain the service's structured result."""
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), method],
            input=json.dumps({"arguments": arguments, "execution": execution}, ensure_ascii=False),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8", timeout=100,
            check=True,
        )
        return decode_payload(completed.stdout)
    except OSError as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}
    except (subprocess.SubprocessError, ValueError) as exc:
        return {"status": "in_doubt" if mutates(method, arguments) else "unavailable",
                "reason": type(exc).__name__}


def main() -> None:
    method, = sys.argv[1:]
    data = sys.stdin.buffer.read(MAX_FRAME + 1)
    if len(data) > MAX_FRAME:
        raise ValueError("Telegram RPC input is too large")
    payload = decode_payload(data)
    result = request(method, payload["arguments"], execution=payload["execution"])
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
