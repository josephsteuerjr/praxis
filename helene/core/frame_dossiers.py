"""Durable, audience-bound admitted dossier versions for local FIRST inputs.

Selection is still the person's transport binding, not everybody in the archive.
Once admitted, a rendered version retains its bytes/order until a fold or an
incompatible admission boundary. Old private snapshots are never re-admitted by
loading this cache in a different audience. This store grants no permissions.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

import frame_continuation as continuation
import frame_layout

OPEN = '<helene_context_evidence>\n# Admitted dossier snapshot\n'


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def collect(source, body):
    """Called only after the existing dossier source-admission/rendering rules."""
    if not continuation.source_bound():
        return False
    additions = list(frame_layout.snapshot().get('dossier_additions') or [])
    additions.append({'source': str(source), 'body_sha256': _sha(body),
                      'message': {'role': 'user', 'content': OPEN + body + '\n</helene_context_evidence>'}})
    frame_layout.stash(dossier_additions=additions)
    return True


def newly_admitted_messages(previous, current):
    seen = {_sha(m) for m in previous}
    return [copy.deepcopy(m) for m in current[:-1]
            if m.get('role') == 'user' and isinstance(m.get('content'), str)
            and m['content'].startswith(OPEN) and _sha(m) not in seen]


def project(*, ctx, messages, additions, private_in_rooms):
    selected = continuation.source_selection()
    if not selected or not messages:
        return messages
    # Include policy and exact audience/principal, not only chat id. Re-admission
    # after restart/revocation starts solely from freshly permitted additions.
    key = {'source': selected['source'], 'generation': selected['generation'],
           'audience': continuation.audience_key(ctx),
           'admission': selected.get('admission'), 'private_in_rooms': bool(private_in_rooms)}
    # Separate immutable admission boundaries keep prior private snapshots
    # addressable without even loading them in a newly admitted audience.
    path = Path(selected['cache_root']) / (_sha(selected['chat']) + '.dossiers.' + _sha(key) + '.json')
    with continuation._LOCK:
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved.get('checksum') != continuation._sha({k:v for k,v in saved.items() if k != 'checksum'}):
                raise ValueError('dossier accumulator checksum mismatch')
        except FileNotFoundError:
            saved = {}
        # Corruption is not a new blank memory; leave the original and fail the
        # current preparation explicitly. No silent fallback destroys admissions.
        state = ({k: copy.deepcopy(v) for k, v in saved.items() if k != 'checksum'}
                 if saved.get('key') == key else {'schema': 'helene.dossiers.v1', 'key': key, 'entries': [], 'revoked_sources': []})
        revoked = [entry['source'] for entry in state['entries']
                   if entry.get('source_present') and not (Path(selected['source']) / entry['source']).is_file()]
        if revoked:
            state.setdefault('retired_entries', []).extend(dict(copy.deepcopy(entry), retired_reason='source_deleted')
                for entry in state['entries'] if entry['source'] in revoked)
            state['entries'] = [entry for entry in state['entries'] if entry['source'] not in revoked]
            state['revoked_sources'] = sorted(set(state.get('revoked_sources', [])) | set(revoked))
        frame_layout.stash(dossier_revocations=state.get('revoked_sources', []))
        seen = {entry['identity'] for entry in state['entries']}
        for addition in additions:
            identity = _sha([addition['source'], addition['body_sha256']])
            if identity not in seen:
                state['entries'].append(dict(copy.deepcopy(addition), identity=identity,
                    source_present=(Path(selected['source']) / addition['source']).is_file()))
                seen.add(identity)
        if state != {k:v for k,v in saved.items() if k != 'checksum'}:
            continuation._write(path, state)
    # The FIRST checkpoint owns insertion positions across turns. A fresh seed
    # uses admission order; continuation adds only genuinely new versions.
    return (copy.deepcopy(messages[:-1])
            + [copy.deepcopy(entry['message']) for entry in state['entries']]
            + copy.deepcopy(messages[-1:]))
