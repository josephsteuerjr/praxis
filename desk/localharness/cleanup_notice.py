"""Read the selected application's cleanup receipt without touching old files."""
from pathlib import Path
import hashlib
import json


def status(program):
    path = Path(program) / 'python-migration.json'
    try:
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return {'messages': []}
        value = json.loads(raw)
        if not isinstance(value, dict):
            return {'messages': []}
    except (OSError, ValueError):
        return {'messages': []}
    messages = value.get('cleanup_errors')
    if not isinstance(messages, list):
        messages = []
    messages = list(dict.fromkeys(x[:2000] for x in messages if isinstance(x, str) and x.strip()))[:50]
    if not messages and value.get('state') == 'retirement_incomplete':
        messages = ['Часть прежней установки сохранена. Подробности — в ' + str(path)]
    return {'messages': messages, 'receipt': str(path),
            'id': hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest() if messages else ''}
