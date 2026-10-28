"""Recover conversation roles from its canonical archive when the hot mirror lags.

Archive imports can preserve old answers without rebuilding the derived hot
state. Reconcile in the read path; keep the original files and folded episodes.
"""
import datetime
import json
import math
import re

BIRTH_PREFIX = 'Это твой первый запуск'


def birth_note(row):
    text = str(row.get('line') or '')
    actor = str(row.get('actor') or '')
    return row.get('direction') != 'out' and actor == 'Hélène' and (
        text.startswith(BIRTH_PREFIX) or text.startswith('Hélène: ' + BIRTH_PREFIX))


def epoch(value):
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def identity(row):
    source = row.get('source_id')
    if source is not None:
        native, sep, _revision = str(source).partition(':')
        if sep and native.isdigit(): source = native
    return (str(source), row.get('direction')) if source is not None and str(source) else None


def reconcile(rows, lines, *, is_room=False, is_dm=True, birth=False, recover_missing=True):
    live = list(rows)
    archived = []
    deleted = set()
    for line in lines:
        try: row = json.loads(line)
        except (TypeError, ValueError): continue
        if not isinstance(row, dict) or row.get('system'): continue
        source = (row.get('source_message_id') or row.get('source_id')
                  or row.get('delivery_key') or row.get('message_id'))
        if row.get('deleted'):
            if source is not None: deleted.add(str(source))
            continue
        text = str(row.get('text') or '').strip()
        if not text: continue
        outgoing = row.get('outgoing') is True
        actor = str(row.get('sender_name') or '?')
        if is_room or (not outgoing and not is_dm): text = f'{actor}: {text}'
        # Group labels are already carried by the hot records. For recovery,
        # preserve the archived sender rather than inferring it from the text.
        try: stamp = datetime.datetime.fromisoformat(str(row.get('timestamp') or '').replace('Z', '+00:00')).timestamp()
        except (ValueError, OverflowError, OSError): stamp = 0
        archived.append({'line': text, 'actor': actor, 'direction': 'out' if outgoing else 'in',
            'ts': stamp, 'source_id': source,
            'meta': {key: row[key] for key in ('media_kind', 'media_path', 'media_source', 'source_audience',
                     'reply_to_message_id', 'reply_to_text', 'reply_to_sender', 'reply_to_quote') if key in row}})
    canonical = {identity(r): r for r in archived if identity(r)}
    reconciled = []
    for row in live:
        key = identity(row)
        if key and key[0] in deleted: continue
        saved = canonical.get(key)
        if saved and epoch(saved.get('ts')) >= epoch(row.get('ts')) and saved['line'] != row.get('line'):
            row = saved
        elif saved and saved.get('meta'):
            row = dict(row, meta=dict(row.get('meta') or {}, **saved['meta']))
        reconciled.append(row)
    live = reconciled
    recent = next((r for r in reversed(archived) if r['direction'] == 'out'), None)
    hot = next((r for r in reversed(live) if r.get('direction') == 'out'), None)
    recover = recent and (hot is None or recent['ts'] > epoch(hot.get('ts')) or
        (recent['ts'] == epoch(hot.get('ts')) and identity(recent) and identity(recent) != identity(hot)))
    if recover and recover_missing:
        # Canonical rows restore missing history, while hot may already contain
        # newer input or system notices that have not reached the archive yet.
        merged = list(archived)
        positions = {identity(r): i for i, r in enumerate(merged) if identity(r)}
        for row in live:
            key = identity(row)
            if key and key in positions:
                i = positions[key]
                if epoch(row.get('ts')) > epoch(merged[i].get('ts')):
                    merged[i] = row
            else:
                merged.append(row)
                if key: positions[key] = len(merged) - 1
        live = sorted(merged, key=lambda r: epoch(r.get('ts')))
    return live if birth else [r for r in live if not birth_note(r)]


def safe_chat(chat):
    return re.fullmatch(r'(?:-?\d+(?:__topic__\d+)?|window(?:-[0-9a-f]+)?|pult)', str(chat)) is not None
