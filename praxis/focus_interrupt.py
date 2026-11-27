"""Owner's Telegram bell and a safe-point callback for an offline coding run."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from contextlib import contextmanager
from contextvars import ContextVar

from telegram_followups import priority_prefix

_CHECK = ContextVar("focus_interrupt", default=None)


def marked(text):
    marker = priority_prefix()
    return bool(marker and str(text or "").lstrip().startswith(marker))


def fingerprint(message):
    return [int(message.date.timestamp()), hashlib.sha256(
        (message.message or "").encode("utf-8")).hexdigest()]


def original(message, sender):
    return (message.sender_id == sender and not getattr(message, "fwd_from", None)
            and not getattr(message, "via_bot_id", None) and marked(message.message))


@contextmanager
def bind(callback):
    token = _CHECK.set(callback)
    try:
        yield
    finally:
        _CHECK.reset(token)


def active():
    return _CHECK.get() is not None


def checkpoint(current, iteration):
    callback = _CHECK.get()
    if callback is not None and current is not None and current.kind in {"coding_window", "task_window"}:
        callback(current.run_id, iteration)

def peer_file():
    base = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
    return base / "memory/.state/focus-telegram-peer"


def publish_peer(peer):
    if type(peer) is not int or peer <= 0:
        raise ValueError("invalid connected peer")
    path = peer_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        stream.write(str(peer))
        tmp = Path(stream.name)
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def connected_peer():
    try:
        return int(peer_file().read_text())
    except (OSError, ValueError):
        return None
