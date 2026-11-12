"""Account/endpoint scoped model capabilities, retained across window restarts."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()


def _saved(value):
    if not isinstance(value, dict) or value.get('ok') is not True:
        return False
    models, fetched = value.get('models'), value.get('fetched_at')
    return (isinstance(models, list) and bool(models)
            and all(isinstance(model, str) and model.strip() for model in models)
            and isinstance(fetched, (int, float)) and math.isfinite(fetched)
            and isinstance(value.get('model_details', []), list))


def relay_revision(tree):
    """Private account changes invalidate capabilities without storing credentials."""
    from .python_relay import signature
    home = Path(tree) / 'relay'
    try: generation = (home/'local_auth/login-generation').read_text(encoding='ascii')
    except FileNotFoundError: generation = ''
    return hashlib.sha256(json.dumps([generation, signature(home), signature(home, 'secondary')]).encode()).hexdigest()


def read(path: Path, base: str, key: str, framework: str, fetch, *, refresh=False, revision=''):
    identity = hashlib.sha256(json.dumps([base.rstrip('/'), key, framework, revision]).encode()).hexdigest()
    with _LOCK:
        try:
            entries = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(entries, dict): entries = {}
        except (OSError, ValueError):
            entries = {}
        entries = {key: value for key, value in entries.items() if _saved(value)}
        old = entries.get(identity)
        if isinstance(old, dict) and old.get('models') and not refresh:
            return {**old, 'cached': True, 'live': False,
                    'note': 'Сохранённый каталог моделей. Нажми «Обновить модели», чтобы перечитать реле.'}
        try:
            result = fetch()
        except (OSError, ValueError):
            result = {'ok': False, 'note': 'Не удалось перечитать каталог моделей.'}
        if not isinstance(result, dict):
            result = {'ok': False, 'note': 'Не удалось прочитать ответ каталога моделей.'}
        if result.get('ok') and result.get('models'):
            # Only declared public capability fields; never persist errors, keys or URLs.
            saved = {name: result[name] for name in ('ok', 'models', 'model_details') if name in result}
            saved['fetched_at'] = time.time()
            entries[identity] = saved
            entries = dict(sorted(entries.items(), key=lambda item: item[1].get('fetched_at', 0))[-16:])
            from .package_notice import write
            try:
                write(path, entries)
            except OSError:
                return {**result, 'cached': False, 'live': True, 'persisted': False,
                        'note': 'Каталог получен, но сохранить его не удалось. Предыдущая копия оставлена.'}
            return {**result, 'cached': False, 'live': True, 'fetched_at': saved['fetched_at']}
        if isinstance(old, dict) and old.get('models'):
            return {**old, 'cached': True, 'live': False, 'refresh_failed': True,
                    'note': 'Реле не ответило. Сохранённый каталог оставлен; доступность моделей не подтверждена.'}
        return {**result, 'cached': False, 'live': False}
