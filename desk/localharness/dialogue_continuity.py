"""Recover conversation roles from its canonical archive when the hot mirror lags.

Archive imports can preserve old answers without rebuilding the derived hot
state. Reconcile in the read path; keep the original files and folded episodes.
"""
import datetime
import json
import re

BIRTH_PREFIX = 'Это твой первый запуск'


def birth_note(row):
    text = str(row.get('line') or '')
    actor = str(row.get('actor') or '')
    return row.get('direction') != 'out' and actor == 'Hélène' and (
        text.startswith(BIRTH_PREFIX) or text.startswith('Hélène: ' + BIRTH_PREFIX))


def reconcile(rows, lines, *, is_room=False, is_dm=True, birth=False):
    live = list(rows)
    archived = []
    for line in lines:
        try: row = json.loads(line)
        except (TypeError, ValueError): continue
        if not isinstance(row, dict) or row.get('system') or row.get('deleted'): continue
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
            'ts': stamp, 'source_id': row.get('source_message_id') or row.get('source_id')})
    recent = next((r for r in reversed(archived) if r['direction'] == 'out'), None)
    hot = next((r for r in reversed(live) if r.get('direction') == 'out'), None)
    if recent and (hot is None or (recent['ts'] > float(hot.get('ts') or 0)
            and recent['line'] != hot.get('line'))):
        live = archived
    return live if birth else [r for r in live if not birth_note(r)]


def safe_chat(chat):
    return re.fullmatch(r'(?:-?\d+(?:__topic__\d+)?|window(?:-[0-9a-f]+)?|pult)', str(chat)) is not None
