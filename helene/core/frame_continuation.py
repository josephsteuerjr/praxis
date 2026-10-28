"""Private, source-proved FIRST-input continuation, never canonical history.

The complete successful first request is kept before tool iterations mutate it.
Only new accepted canonical conversation/evidence is appended next turn. Growth
has no overhead reset: an authored fold or explicit incompatible source/route/
audience boundary starts a new seed and records why. KEAT retains its own path.
"""
from __future__ import annotations
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import uuid
import frame_layout

_SOURCE = ContextVar('helene_dialogue_source', default=None)
_PLAN = ContextVar('helene_first_input', default=None)
_REASON = ContextVar('helene_first_input_unavailable', default='')
_LOCK = threading.RLock()
SCHEMA = 'helene.first-input.v2'
_LOG = logging.getLogger(__name__)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def dialogue(records):
    rows = [(row.get('direction') == 'out', str(row.get('line') or ''))
            for row in records if str(row.get('line') or '').strip()]
    last = next((i for i in range(len(rows)-1, -1, -1) if rows[i][0]), -1)
    if last < 0:
        return [], '\n'.join(text for _, text in rows)
    history, lines, role = [], [], ''
    for outgoing, text in rows[:last+1]:
        current_role = 'assistant' if outgoing else 'user'
        if current_role != role:
            if lines: history.append({'role': role, 'content': '\n\n'.join(lines).strip()})
            role, lines = current_role, []
        lines.append(text)
    if lines: history.append({'role': role, 'content': '\n\n'.join(lines).strip()})
    current = '\n'.join(text for _, text in rows[last+1:])
    return (history, current) if current.strip() else ([], '')


@contextmanager
def bind_source(source, refresh, *, reason=''):
    token = _SOURCE.set((copy.deepcopy(source), refresh) if source else None)
    reason_token = _REASON.set(str(reason))
    try:
        yield
    finally:
        _SOURCE.reset(token)
        _REASON.reset(reason_token)


def source_bound():
    return (_SOURCE.get() is not None and os.getenv('PRAXIS_FRAME_CONTINUATION', 'on') == 'on'
            and os.getenv('PRAXIS_KEAT') != 'serve' and os.getenv('PRAXIS_KEAT_CAPTURE') != 'on')


def source_selection():
    bound = _SOURCE.get()
    return copy.deepcopy(bound[0]) if bound else None


def audience_key(ctx):
    """Frozen admission identity, not authority granted by remembered material."""
    return {key: getattr(ctx, key, None) for key in
            ('chat_id', 'room_chat_id', 'is_dm', 'scope', 'owner', 'known', 'family',
             'principal_id', 'praxis_self', 'hide_identity_load', 'owner_audience')}


def _source_audience(ctx, source):
    return {'chat': str(ctx.chat_id), 'is_dm': bool(ctx.is_dm),
            'owner': bool(ctx.owner_audience), 'installation': _sha(source.get('admission'))}


def admit_bound(ctx, history, user_msg):
    """Re-admit raw source occurrences before compiling any prompt material.

    This records selection admission, not a new claim about the message author.
    Exact source IDs survive edits/restart. A later public scope cannot re-import
    previously owner-admitted raw words just by rebuilding a fresh packet.
    """
    bound = _SOURCE.get()
    if not source_bound() or bound is None or not isinstance(user_msg, str):
        return history, user_msg
    source, refresh = bound
    if dialogue(source['rows']) != (history, user_msg):
        return history, user_msg
    audience = _source_audience(ctx, source)
    if source.get('admission_projection') == _sha(audience):
        return history, user_msg
    path = Path(source['cache_root']) / (_sha(source['chat']) + '.admissions.json')
    with _LOCK:
        try:
            admissions = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            admissions = {'schema': 'helene.source-admissions.v1', 'rows': {}}
        for row in source['rows']:
            identity = str(row.get('source_id') or row.get('id') or '')
            if identity:
                tag = (row.get('meta') or {}).get('source_audience') or row.get('source_audience')
                captured = dict(audience)
                if isinstance(tag, dict):
                    captured.update(chat=str(tag.get('chat') or source['chat']),
                                    is_dm=bool(tag.get('is_dm', True)), owner=bool(tag.get('owner', True)))
                    if tag.get('owner_id') and tag['owner_id'] != str((source.get('admission') or {}).get('owner') or ''):
                        captured['installation'] = 'previous-owner'
                admissions['rows'].setdefault(identity, captured)
        _write(path, {k:v for k,v in admissions.items() if k != 'checksum'})

    def project(fresh):
        current = _source_audience(ctx, fresh)
        def visible(row):
            identity = str(row.get('source_id') or row.get('id') or '')
            admitted = admissions['rows'].get(identity, current)
            return (admitted['chat'] == current['chat']
                    and admitted['installation'] == current['installation']
                    and (current['owner'] or (not admitted['owner']
                         and admitted['is_dm'] == current['is_dm'])))
        return dict(fresh, rows=[copy.deepcopy(r) for r in fresh['rows'] if visible(r)],
                    admission_projection=_sha(current))
    admitted = project(source)
    _SOURCE.set((admitted, lambda: project(refresh())))
    admitted_history, admitted_current = dialogue(admitted['rows'])
    if not admitted_current.strip():
        raise ValueError('source audience changed; a newly admitted input is required')
    return admitted_history, admitted_current


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


def _write(path, value):
    value = {k: copy.deepcopy(v) for k, v in value.items() if k != 'checksum'}
    value['checksum'] = _sha(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as sink:
            sink.write(_json(value)); sink.flush(); os.fsync(sink.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass
class Plan:
    path: Path
    generation: str
    binding: dict
    rows: list
    messages: list
    request_sha: str
    refresh: object
    reused: int = 0
    observed: bool = False
    reason: str = 'first'
    dispatched: object = None
    source_ids: object = None
    embedded: object = None
    expected_provider: object = None
    prepared_length: int = 0
    admission_sha: str = ''

    def __post_init__(self):
        self.prepared_length = len(self.messages)


def prepare(*, ctx, history, user_msg, messages, system, tools, model_key,
            prefix_messages, hands, render, capture_state=None, expected_provider=None,
            render_current=None):
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
        binding = {key: _sha(value) for key, value in dict(
            system=system, tools=tools, model=model_key, epoch=prefix_messages,
            audience=audience_key(ctx), admission=source.get('admission'), hands=hands,
            dossier_revocations=frame_layout.snapshot().get('dossier_revocations') or [],
            render={k:v for k,v in os.environ.items()
                    if k.startswith('PRAXIS_FRAME_') or k in
                    ('PRAXIS_DIALOGUE_ROLES', 'PRAXIS_DOSSIER_PRIVATE_IN_ROOMS')}).items()}
        path = Path(source['cache_root']) / (_sha(source['chat']) + '.json')
        generation = _sha([source['source'], source['generation']])
        projected, reused, reason = copy.deepcopy(messages), 0, 'first'
        with _LOCK:
            saved = _load(path)
        if saved:
            if saved['generation'] != generation:
                reason = 'source_generation_changed'
            elif (expected_provider is not None and saved.get('actual_provider') is not None
                  and saved['actual_provider'] != expected_provider):
                reason = 'provider_route_changed'
            elif saved['binding'] != binding:
                changed = sorted(k for k in binding if saved['binding'].get(k) != binding[k])
                reason = 'binding_changed:' + ','.join(changed)
            elif hashes[:len(saved['rows'])] != saved['rows']:
                reason = 'source_edited_deleted_or_reordered'
            elif len(saved['rows']) >= len(hashes):
                reason = 'no_new_canonical_reply'
            else:
                suffix = source['rows'][len(saved['rows']):]
                embedded = saved.get('embedded') or {}
                changed = any(str(r.get('source_id') or r.get('id')) in embedded
                              and _sha(r) != embedded[str(r.get('source_id') or r.get('id'))]
                              for r in suffix)
                if changed:
                    return Plan(path, generation, binding, hashes, projected,
                                _sha([system, projected, tools]), refresh,
                                reason='embedded_source_edited', source_ids=[str(r.get('source_id') or r.get('id')) for r in source['rows']])
                suffix = [r for r in suffix if str(r.get('source_id') or r.get('id')) not in embedded]
                suffix_history, suffix_current = dialogue(suffix)
                if ((suffix_history and suffix_current == user_msg)
                        or (not suffix_history and suffix_current and callable(render_current))):
                    import frame_dossiers
                    current_message = (render_current(suffix_current) if callable(render_current)
                                       else copy.deepcopy(messages[-1]))
                    projected = (copy.deepcopy(saved['messages'])
                                 + render(suffix_history, hands=hands)
                                 + frame_dossiers.newly_admitted_messages(saved['messages'], messages)
                                 + [current_message])
                    reused, reason = len(saved['messages']), 'continued'
                else:
                    reason = 'accepted_reply_not_in_suffix'
        # No 2x/size heuristic may discard a successful FIRST input. Capacity is
        # handled by an authored fold or an explicit provider limit, not guessed.
        return Plan(path, generation, binding, hashes, projected,
                    _sha([system, projected, tools]), refresh, reused, reason=reason,
                    source_ids=[str(r.get('source_id') or r.get('id')) for r in source['rows']],
                    expected_provider=expected_provider, admission_sha=_sha(source.get('admission')))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        _LOG.warning('FIRST input preparation unavailable; current canonical assembly retained')
        return None


@contextmanager
def bind(plan):
    token = _PLAN.set(plan)
    try:
        yield
    finally:
        _PLAN.reset(token)


def receipt():
    plan = _PLAN.get()
    if plan is None and _REASON.get():
        return {'schema': SCHEMA, 'status': 'not_applied:' + _REASON.get(),
                'reused_messages': 0, 'source_rows': 0}
    return ({'schema': SCHEMA, 'status': plan.reason, 'reused_messages': plan.reused,
             'source_rows': len(plan.rows), 'input_utf8_bytes': len(_json(plan.messages).encode('utf-8'))}
            if plan else None)


def dispatch(*, system, messages, tools, critical=False):
    """Freeze the real first transport input, including admitted boundary FIFO.

    Called immediately before the provider. Subsequent tool requests cannot
    replace this snapshot. No synthetic tool trace can be imported next turn.
    """
    plan = _PLAN.get()
    if plan is None or plan.observed or plan.dispatched is not None:
        return
    if (critical or messages is not plan.messages
            or _sha([system, messages[:plan.prepared_length], tools]) != plan.request_sha):
        plan.observed = True
        return
    # A boundary callback may append admitted user inputs before the first call.
    # Original messages may not be edited/replaced by another projection.
    plan.dispatched = (_sha([system, messages, tools]), copy.deepcopy(messages))
    import addressed_replies
    extra_ids = addressed_replies.staged_ids() - set(plan.source_ids or [])
    if extra_ids:
        try:
            fresh = plan.refresh()
            plan.embedded = {str(r.get('source_id') or r.get('id')): _sha(r)
                             for r in fresh['rows'] if str(r.get('source_id') or r.get('id')) in extra_ids}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            plan.observed = True
            _LOG.warning('FIRST boundary source proof unavailable; checkpoint not replaced')


def observe(*, system, messages, tools, critical=False, provider_route=None):
    plan = _PLAN.get()
    if plan is None or plan.observed:
        return
    plan.observed = True
    try:
        digest = _sha([system, messages, tools])
        if (critical or messages is not plan.messages
                or digest != (plan.dispatched[0] if plan.dispatched else plan.request_sha)):
            return
        with _LOCK:
            fresh = plan.refresh()
            if (_sha([fresh['source'], fresh['generation']]) != plan.generation
                    or (plan.admission_sha and _sha(fresh.get('admission')) != plan.admission_sha)
                    or _hashes(fresh)[:len(plan.rows)] != plan.rows):
                return
            previous = _load(plan.path)
            if (previous and previous['generation'] == plan.generation
                    and previous['binding'] == plan.binding
                    and previous['rows'] == _hashes(fresh)[:len(previous['rows'])]
                    and len(previous['rows']) >= len(plan.rows)):
                return
            kept = plan.dispatched[1] if plan.dispatched else messages
            _write(plan.path, {'schema': SCHEMA, 'generation': plan.generation,
                              'binding': plan.binding, 'rows': plan.rows,
                              'messages': copy.deepcopy(kept), 'reason': plan.reason,
                              'embedded': plan.embedded or {}, 'actual_provider': provider_route})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        _LOG.warning('FIRST input checkpoint unavailable; canonical source retained')
