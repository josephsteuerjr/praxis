"""Opt-in derived first-input continuation for source-complete group chat epochs.

Canonical messages stay raw. This private, disposable projection retains the
exact first model input of a chat turn, never its intermediate tool transcript.
Only a source-proved append can reuse it. Fresh system and tools are mandatory;
KEAT/frame-v6 adapters retain their existing path until separately integrated.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import dialogue_projection
import frame_epoch
import frame_layout
import frame_serve

_BOUND = ContextVar('frame_continuation', default=None)
SCHEMA = 'praxis.frame-continuation.v1'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _generation(selection):
    return _sha({k: selection[k] for k in
                 ('chat', 'n', 'boundary', 'scope', 'flip_id', 'summary_policy')})


def _row_hashes(rows):
    return [_sha(row) for row in rows]


def _load(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (value['schema'] == SCHEMA and
                value['checksum'] == _sha({k: v for k, v in value.items() if k != 'checksum'})):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _path(chat_id):
    return frame_epoch.STORE.parent / 'frame_continuation' / (_sha(str(chat_id)) + '.json')


@dataclass
class Plan:
    chat: str
    path: Path
    generation: str
    binding: str
    rows: list
    messages: list
    message_hash: str
    request_hash: str
    reused: int
    observed: bool = False


def prepare(*, ctx, selection, history, user_msg, messages, system, tools,
            kind, model_key, prefix_messages, capture_state=None):
    """Return a projection or None; an optimization failure cannot stop a turn."""
    try:
        if (os.getenv('PRAXIS_FRAME_CONTINUATION') != 'on' or kind != 'chat_turn'
                or ctx.is_dm or ctx.chat_id is None or not selection
                or capture_state is not None or frame_serve.enabled(ctx)
                or os.getenv('PRAXIS_KEAT') == 'serve'
                or os.getenv('PRAXIS_KEAT_CAPTURE') == 'on'
                or not isinstance(user_msg, str)):
            return None
        frame_epoch.require_servable(selection)
        if str(selection['chat']) != str(ctx.chat_id):
            return None
        rows = selection['rows']
        # Service-only metadata can straddle a projection frontier. Leave its
        # original renderer authoritative rather than guessing a new attachment.
        if not rows or any(row.get('service') for row in rows):
            return None
        expected_history, expected_current = dialogue_projection.epoch_dialogue(rows)
        if not expected_history or history != expected_history or user_msg != expected_current:
            return None
        if (not prefix_messages or
                messages[:len(prefix_messages)] != prefix_messages):
            return None
        generation = _generation(selection)
        binding = _sha(dict(
            system=system, tools=tools, model=model_key, epoch=prefix_messages,
            room=str(ctx.room_chat_id), scope=ctx.scope,
            owner=ctx.owner, known=ctx.known, family=ctx.family,
            praxis_self=ctx.praxis_self, hide_identity_load=ctx.hide_identity_load,
            render={k: v for k, v in os.environ.items()
                    if k.startswith('PRAXIS_FRAME_') or k == 'PRAXIS_DIALOGUE_ROLES'}))
        hashes = _row_hashes(rows)
        projected = copy.deepcopy(messages)
        reused = 0
        path = _path(ctx.chat_id)
        with frame_epoch.tape_store_guard(ctx.chat_id):
            saved = _load(path)
        if (saved and saved['generation'] == generation and saved['binding'] == binding
                and type(saved['rows']) is list and saved['rows']
                and len(saved['rows']) < len(hashes)
                and hashes[:len(saved['rows'])] == saved['rows']):
            # Re-render ONLY newly admitted source rows. Requiring an accepted
            # assistant occurrence avoids promoting an undelivered draft or
            # duplicating the old pending input after a silent/failed turn.
            suffix = rows[len(saved['rows']):]
            appended_history, current = dialogue_projection.epoch_dialogue(suffix)
            if appended_history and current == expected_current:
                projected = (copy.deepcopy(saved['messages'])
                             + frame_layout.tape(appended_history)
                             + copy.deepcopy(messages[-1:]))
                reused = len(saved['messages'])
        return Plan(str(ctx.chat_id), path, generation, binding, hashes, projected,
                    _sha(projected), _sha([system, projected, tools]), reused)
    except Exception:
        return None


@contextmanager
def bind(plan):
    token = _BOUND.set(plan)
    try:
        yield
    finally:
        _BOUND.reset(token)


def receipt():
    plan = _BOUND.get()
    if plan is None:
        return None
    return dict(schema=SCHEMA, status='continued' if plan.reused else 'seed',
                reused_messages=plan.reused, source_rows=len(plan.rows),
                input_utf8_bytes=len(_json(plan.messages).encode('utf-8')))


def observe(*, system, messages, tools, critical=False):
    """Commit only a successfully dispatched FIRST exact request of this turn.

    Called after llm.chat returns, before tools can append or prune anything.
    Later tool iterations, unrelated calls and substituted provider projections
    cannot update this checkpoint. A stale wake cannot roll a newer one back.
    """
    plan = _BOUND.get()
    if plan is None or plan.observed:
        return
    plan.observed = True
    try:
        if (critical or os.getenv('PRAXIS_FRAME_CONTINUATION') != 'on'
                or messages is not plan.messages or _sha(messages) != plan.message_hash
                or _sha([system, messages, tools]) != plan.request_hash):
            return
        with frame_epoch.tape_store_guard(plan.chat):
            current = (frame_epoch.load(plan.chat) or {}).get('tape')
            if (not current or _generation(dict(current, chat=plan.chat)) != plan.generation
                    or _row_hashes(current['rows'][:len(plan.rows)]) != plan.rows):
                return
            previous = _load(plan.path)
            if (previous and previous['generation'] == plan.generation
                    and len(previous['rows']) >= len(plan.rows)):
                return
            value = dict(schema=SCHEMA, generation=plan.generation,
                         binding=plan.binding, rows=plan.rows, messages=messages)
            value['checksum'] = _sha(value)
            plan.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = plan.path.with_name(plan.path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(_json(value))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, plan.path)
            finally:
                tmp.unlink(missing_ok=True)
    except Exception:
        pass  # no private contents or exception text in diagnostics
