"""Local-runner first-input cache, never the canonical conversation or tool journal.

A source-proved append can keep the previous successful pre-tools input verbatim.
Source edits, audience/model/tool/render changes and unavailable proof use normal
assembly. The runner supplies the selection and its fresh read; no server epoch
APIs, native message IDs inferred from text, or automatic memory summaries.
"""
from __future__ import annotations
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

_SOURCE = ContextVar('helene_dialogue_source', default=None)
_PLAN = ContextVar('helene_first_input', default=None)
_LOCK = threading.RLock()
SCHEMA = 'helene.first-input.v1'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def dialogue(records):
    rows = [(row.get('direction') == 'out', str(row.get('line') or ''))
            for row in records if str(row.get('line') or '').strip()]
    last = next((i for i in range(len(rows)-1, -1, -1) if rows[i][0]), -1)
    if last < 0:
        return [], ''
    history, lines, role = [], [], ''
    for outgoing, text in rows[:last+1]:
        current_role = 'assistant' if outgoing else 'user'
        if current_role != role:
            if lines: history.append({'role': role, 'content': '\n\n'.join(lines).strip()})
            role, lines = current_role, []
        lines.append(text)
    if lines: history.append({'role': role, 'content': '\n\n'.join(lines).strip()})
    current = '\n'.join(text for _, text in rows[last+1:])
    return (history, current) if history and current.strip() else ([], '')


@contextmanager
def bind_source(source, refresh):
    token = _SOURCE.set((copy.deepcopy(source), refresh) if source else None)
    try:
        yield
    finally:
        _SOURCE.reset(token)


def source_bound():
    return _SOURCE.get() is not None


def _hashes(source):
    rows = source['rows']
    if not rows or any(not (row.get('source_id') or row.get('id')) for row in rows):
        raise ValueError('source identities unavailable')
    return [_sha(row) for row in rows]


def _load(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if value['schema'] == SCHEMA and value['checksum'] == _sha({k:v for k,v in value.items() if k != 'checksum'}):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


@dataclass
class Plan:
    path: Path
    generation: str
    binding: str
    rows: list
    messages: list
    request_sha: str
    refresh: object
    reused: int = 0
    observed: bool = False


def prepare(*, ctx, history, user_msg, messages, system, tools, model_key,
            prefix_messages, hands, render, capture_state=None):
    bound = _SOURCE.get()
    try:
        if (bound is None or os.getenv('PRAXIS_FRAME_CONTINUATION', 'on') != 'on'
                or capture_state is not None or not isinstance(user_msg, str)
                or os.getenv('PRAXIS_KEAT') == 'serve'
                or os.getenv('PRAXIS_KEAT_CAPTURE') == 'on'):
            return None
        source, refresh = bound
        if str(ctx.chat_id) != source['chat'] or dialogue(source['rows']) != (history, user_msg):
            return None
        hashes = _hashes(source)
        facets = {key: getattr(ctx, key, None) for key in
                  ('chat_id', 'room_chat_id', 'is_dm', 'scope', 'owner', 'known', 'family',
                   'praxis_self', 'hide_identity_load', 'owner_audience')}
        binding = _sha({'system': system, 'tools': tools, 'model': model_key,
                        'epoch': prefix_messages, 'audience': facets, 'hands': hands,
                        'render': {k:v for k,v in os.environ.items()
                                   if k.startswith('PRAXIS_FRAME_') or k == 'PRAXIS_DIALOGUE_ROLES'}})
        path = Path(source['cache_root']) / (_sha(source['chat']) + '.json')
        generation = _sha([source['source'], source['generation']])
        projected, reused = copy.deepcopy(messages), 0
        with _LOCK:
            saved = _load(path)
        if (saved and saved['generation'] == generation and saved['binding'] == binding
                and 0 < len(saved['rows']) < len(hashes)
                and hashes[:len(saved['rows'])] == saved['rows']):
            suffix_history, suffix_current = dialogue(source['rows'][len(saved['rows']):])
            if suffix_history and suffix_current == user_msg:
                projected = copy.deepcopy(saved['messages']) + render(suffix_history, hands=hands) + copy.deepcopy(messages[-1:])
                reused = len(saved['messages'])
        # Bound only derived-envelope amplification, never canonical history.
        # Repeated fresh memory/runtime envelopes must not grow without limit
        # while the selected conversation remains small. This is an overhead
        # budget (2x normal assembly), NOT an assertion of provider capacity.
        if len(_json([system, projected, tools])) > 2 * len(_json([system, messages, tools])):
            projected, reused = copy.deepcopy(messages), 0
        return Plan(path, generation, binding, hashes, projected,
                    _sha([system, projected, tools]), refresh, reused)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None  # cache failure never consumes or narrows a conversation


@contextmanager
def bind(plan):
    token = _PLAN.set(plan)
    try:
        yield
    finally:
        _PLAN.reset(token)


def receipt():
    plan = _PLAN.get()
    return ({'schema': SCHEMA, 'reused_messages': plan.reused,
             'source_rows': len(plan.rows)} if plan else None)


def observe(*, system, messages, tools, critical=False):
    plan = _PLAN.get()
    if plan is None or plan.observed:
        return
    plan.observed = True
    try:
        if (critical or messages is not plan.messages
                or _sha([system, messages, tools]) != plan.request_sha):
            return
        with _LOCK:
            fresh = plan.refresh()
            if (_sha([fresh['source'], fresh['generation']]) != plan.generation
                    or _hashes(fresh)[:len(plan.rows)] != plan.rows):
                return
            previous = _load(plan.path)
            if (previous and previous['generation'] == plan.generation
                    and len(previous['rows']) >= len(plan.rows)):
                return
            value = {'schema': SCHEMA, 'generation': plan.generation, 'binding': plan.binding,
                     'rows': plan.rows, 'messages': copy.deepcopy(messages)}
            value['checksum'] = _sha(value)
            plan.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = plan.path.with_name(plan.path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as sink:
                    sink.write(_json(value)); sink.flush(); os.fsync(sink.fileno())
                os.replace(tmp, plan.path)
            finally:
                tmp.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # derived private cache only; canonical source remains authoritative
