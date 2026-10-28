"""Exact incoming membership bound to accepted replies, for Telegram and local rows.

This private disposable index is scheduling evidence, never canonical history or
an acknowledgement that every question was answered. IDs are opaque occurrences,
not numeric high-watermarks. Only real successful model requests and accepted
text publication can contribute; media requires its own admission, not a caption.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
from pathlib import Path
import threading
import uuid

_BOUND = ContextVar('helene_addressed_input', default=None)
_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)
BASE = Path(__import__('os').environ.get('PRAXIS_BASE') or Path(__file__).resolve().parent)


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8', newline='\n') as out:
            json.dump(value, out, ensure_ascii=False, sort_keys=True)
            out.flush()
            __import__('os').fsync(out.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _path(base, run_id):
    return Path(base) / 'memory/.state/addressed-replies' / (_sha(str(run_id)) + '.json')


@contextmanager
def bind(base, chat_id):
    state = {'base': str(base), 'chat': str(chat_id), 'staged': [], 'observed': set(), 'run': ''}
    token = _BOUND.set(state)
    try:
        yield state
    finally:
        _BOUND.reset(token)


def stage(messages, incoming_ids):
    """Only ingress adapters may associate opaque IDs with exact model blocks."""
    state = _BOUND.get()
    if state is None or not messages:
        return
    ids = {str(value) for value in incoming_ids if str(value)}
    if ids:
        state['staged'].append((tuple(_sha(m) for m in messages), ids, tuple(id(m) for m in messages)))


def stage_initial(messages, rows):
    ids = []
    for row in rows:
        meta = row.get('meta') or {}
        if (row.get('direction') != 'out'
                and not row.get('media') and not meta.get('media')
                and not meta.get('media_kind') and not row.get('media_kind')):
            identity = row.get('source_id') or row.get('id')
            if identity is not None:
                ids.append(str(identity))
    stage(messages, ids)


def staged_ids():
    state = _BOUND.get()
    return set().union(*(ids for _required, ids, _objects in state['staged'])) if state and state['staged'] else set()


def _position(messages, required, objects=None, at=None):
    hashes = [_sha(m) for m in messages]
    positions = [at] if at is not None else range(len(hashes)-len(required)+1)
    for i in positions:
        if tuple(hashes[i:i+len(required)]) != required:
            continue
        if objects is None or tuple(id(m) for m in messages[i:i+len(required)]) == objects:
            return i
    return None


def dispatch(run_id, messages):
    """Persist ID-to-request-hash associations for exact checkpoint recovery.

    This is merely intent; no observed/covered membership is granted here.
    """
    state = _BOUND.get()
    if state is None or not run_id:
        return
    with _LOCK:
        path = _path(state['base'], run_id)
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            value = {'schema': 'helene.addressed-replies.v1', 'run_id': str(run_id),
                     'chat_id': state['chat'], 'observed_ids': [], 'replies': []}
        if value['chat_id'] != state['chat']:
            raise ValueError('addressed source audience differs')
        value['staged'] = [{'hashes': list(hashes), 'ids': sorted(ids), 'at': at}
                           for hashes, ids, objects in state['staged']
                           if (at := _position(messages, hashes, objects)) is not None]
        _atomic(path, value)


def presented(run_id, messages):
    """Called after the actual provider returned, never at intent/checkpoint ACK."""
    state = _BOUND.get()
    if not run_id:
        return
    if state is None:
        # Resume has independently validated the original run authority. Only
        # exact checkpoint message hashes can recover its prior source binding.
        try:
            pending = json.loads(_path(BASE, run_id).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        state = {'base': str(BASE), 'chat': pending['chat_id'], 'observed': set(), 'staged': []}
        for row in pending.get('staged', []):
            if _position(messages, tuple(row['hashes']), at=row['at']) is not None:
                state['observed'].update(row['ids'])
    else:
        for required, ids, objects in state['staged']:
            # Equal text is not equal occurrence; live identity precedes the
            # positional+hash association retained for exact checkpoint recovery.
            if _position(messages, required, objects) is not None:
                state['observed'].update(ids)
    state['run'] = str(run_id)
    with _LOCK:
        path = _path(state['base'], run_id)
        try:
            previous = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            previous = {'schema': 'helene.addressed-replies.v1', 'run_id': str(run_id),
                        'chat_id': state['chat'], 'observed_ids': [], 'replies': []}
        if previous['chat_id'] != state['chat'] or previous['run_id'] != str(run_id):
            raise ValueError('addressed observation identity differs')
        previous['observed_ids'] = sorted(set(previous['observed_ids']) | state['observed'])
        _atomic(path, previous)


def accepted(base, run_id, chat_id, reply_id, *, delivery_key, complete=True, purpose='reply'):
    """Called only after full accepted text publication; no narration/silence/media."""
    if not run_id or not reply_id or not delivery_key or not complete or purpose != 'reply':
        return False
    with _LOCK:
        path = _path(base, run_id)
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return False
        if saved.get('chat_id') != str(chat_id) or saved.get('run_id') != str(run_id):
            return False
        observed = list(saved.get('observed_ids') or [])
        if not observed:
            return False
        receipt = {'reply_id': str(reply_id), 'delivery_key': str(delivery_key),
                   'observed_ids': observed}
        old = next((r for r in saved['replies'] if r['delivery_key'] == str(delivery_key)), None)
        if old is not None:
            return old == receipt
        saved['replies'].append(receipt)
        _atomic(path, saved)
        return True


def covered(base, run_id, incoming_id):
    try:
        saved = json.loads(_path(base, run_id).read_text(encoding='utf-8'))
        return any(str(incoming_id) in row['observed_ids'] for row in saved.get('replies', []))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def safe_presented(run_id, messages):
    # A disposable scheduling index does not veto the speech it observes.
    try:
        presented(run_id, messages)
    except (OSError, ValueError, TypeError, KeyError):
        _LOG.warning('addressed observation unavailable; no coverage claimed')


def safe_dispatch(run_id, messages):
    try:
        dispatch(run_id, messages)
    except (OSError, ValueError, TypeError, KeyError):
        _LOG.warning('addressed dispatch association unavailable; no recovery coverage claimed')


def safe_accepted(*args, **kwargs):
    try:
        return accepted(*args, **kwargs)
    except (OSError, ValueError, TypeError, KeyError):
        _LOG.warning('accepted reply index unavailable; publication retained')
        return False
