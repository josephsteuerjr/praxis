"""Keep the start of an accepted DM tape until its canonical source is folded.

Only row IDs and hashes persist. Each request renders today's canonical rows and
its own evidence, including after edits, deletions and provider changes.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import frame_continuation as frames
import frame_epoch
import memory_life

SCHEMA = "praxis.dm-continuation.v1"
_SOURCE = ContextVar("dm_continuation_source", default=None)


@dataclass
class Source:
    chat: str
    anchor: str
    previous: dict
    attempted: bool = False


def enabled():
    return (os.getenv("PRAXIS_FRAME_CONTINUATION") == "on"
            and os.getenv("PRAXIS_KEAT") != "serve"
            and os.getenv("PRAXIS_KEAT_CAPTURE") != "on")


def _path(chat):
    return frames._path(chat).with_suffix(".dm.json")


def select(chat, rows, seed_limit):
    previous = frames._load(_path(chat), SCHEMA) or {}
    anchor = previous.get("anchor")
    start = next((i for i, row in enumerate(rows) if anchor and row.get("id") == anchor), None)
    selected = (rows[start:] if start is not None else
                memory_life.tape_window(rows[-seed_limit:] if seed_limit > 0 else rows))
    if not selected or not selected[0].get("id"):
        return selected, None
    return selected, Source(str(chat), selected[0]["id"], previous)


@contextmanager
def bind(source):
    token = _SOURCE.set(source)
    try:
        yield
    finally:
        _SOURCE.reset(token)


def active(chat):
    source = _SOURCE.get()
    return source is not None and source.chat == str(chat)


def measure(chat, system, messages, tools, model):
    source = _SOURCE.get()
    if not active(chat) or source.attempted or not messages or not enabled():
        return None
    source.attempted = True
    binding = frames._sha([system, tools, model])
    prefix = [frames._sha(msg) for msg in messages[:-1]]
    reused = 0
    old = source.previous
    if old.get("binding") == binding and old.get("anchor") == source.anchor:
        for before, after in zip(old["prefix"], prefix):
            if before != after:
                break
            reused += 1
    value = dict(schema=SCHEMA, anchor=source.anchor, binding=binding, prefix=prefix)
    value["checksum"] = frames._sha(value)
    return value, dict(schema=SCHEMA, status="continued" if reused else "seed",
                       reused_messages=reused, history_messages=len(prefix))


def accept(value):
    source = _SOURCE.get()
    path = _path(source.chat)
    with frame_epoch.tape_store_guard(source.chat):
        current = frames._load(path, SCHEMA) or {}
        if current.get("checksum") != source.previous.get("checksum"):
            return
        frames.write_checkpoint(path, value)
