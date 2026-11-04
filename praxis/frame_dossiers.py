"""Append admitted dossiers to the room accumulator until its explicit boundary.

Stored material is evidence, never a Telegram message or a refreshed state packet.
Its bytes and insertion point survive later turns, restarts and roster changes.
The existing epoch JSON and writer lock own this state; no parallel memory store.
"""
from __future__ import annotations

import copy
import hashlib
import json

import frame_epoch

OPEN = '<praxis_context_evidence>\n# Dossier snapshot in room accumulator\n'


def newly_admitted_messages(previous, current):
    """Carry new document admissions into exact-input append continuation.

    These envelopes are emitted only by the dossier builder. Ordinary chat text
    is quoted by frame_layout and cannot masquerade as an admission envelope.
    """
    seen = {_sha(m) for m in previous}
    result = []
    for message in current[:-1]:
        content = message.get('content')
        identity = _sha(message)
        if (message.get('role') == 'user' and isinstance(content, str)
                and content.startswith(OPEN) and identity not in seen):
            result.append(copy.deepcopy(message));seen.add(identity)
    return result


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def generation(selection):
    return {k: selection.get(k) for k in
            ('n', 'boundary', 'scope', 'flip_id', 'summary_policy')}


def project(*, ctx, messages, additions, private_in_rooms):
    """Insert previously admitted evidence at its original append position.

    Only the source-complete group accumulator participates. The last current
    situation packet stays replaceable. Dossier material does not live in it.
    Older detached wakes can read their frontier but cannot roll storage back.
    """
    selected = frame_epoch.bound_tape(ctx.chat_id)
    if ctx.is_dm or not selected or len(messages) < 2:
        return messages
    frame_epoch.require_servable(selected)
    key = dict(generation=generation(selected), private_in_rooms=bool(private_in_rooms))
    history = messages[1:-1]
    frontier = len(selected['rows'])
    with frame_epoch.tape_store_guard(ctx.chat_id):
        saved = frame_epoch.load(ctx.chat_id) or {}
        current = saved.get('tape') or {}
        existing = saved.get('dossier_accumulator') or {}
        state = (copy.deepcopy(existing) if existing.get('key') == key else
                 dict(version=1, key=key, entries=[]))
        # A wake from a prior boundary must use its detached material snapshot.
        if generation(current) != generation(selected):
            detached = selected.get('dossier_accumulator') or {}
            state = (copy.deepcopy(detached) if detached.get('key') == key else
                     dict(version=1, key=key, entries=[]))
        visible = [e for e in state['entries'] if e['frontier'] <= frontier]
        seen = {e['identity'] for e in visible}
        for addition in additions:
            identity = _sha([addition['source'], addition['body_sha256']])
            if identity in seen:
                continue
            entry = dict(identity=identity, source=addition['source'],
                         body_sha256=addition['body_sha256'],
                         message=copy.deepcopy(addition['message']),
                         at=len(history), frontier=frontier)
            state['entries'].append(entry)
            visible.append(entry)
            seen.add(identity)
        # Only a current frontier can extend the canonical accumulator. Failed
        # writes propagate: silently losing durable knowledge is not a fallback.
        writable = (generation(current) == generation(selected)
                    and all(e['frontier'] <= frontier for e in existing.get('entries', [])
                            if existing.get('key') == key))
        if writable and state != existing:
            saved['dossier_accumulator'] = state
            frame_epoch._write_atomic(frame_epoch.path_for(ctx.chat_id),
                                      json.dumps(saved, ensure_ascii=False, indent=1))
    buckets = {}
    for entry in visible:
        # If canonical repair shortens history, preserve all admitted knowledge;
        # its provenance is the file snapshot, never a fabricated chat event.
        buckets.setdefault(min(entry['at'], len(history)), []).append(entry['message'])
    projected = [copy.deepcopy(messages[0])]
    for i in range(len(history) + 1):
        projected.extend(copy.deepcopy(buckets.get(i, [])))
        if i < len(history):
            projected.append(copy.deepcopy(history[i]))
    projected.append(copy.deepcopy(messages[-1]))
    return projected
