"""Private XTTS IPC contract shared by the application client and owned worker.

A ready file alone grants nothing: callers also observe the live process, bind
one generation, and renew a short request lease. Old generations never replay.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def identity(value) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(c in '0123456789abcdef' for c in value)


def blocked(tree: Path) -> bool:
    if __package__:
        from . import owner_stop
    else:
        import owner_stop
    return owner_stop.stopped() or owner_stop.present(Path(tree) / 'memory/.control/autonomy-paused.json')


def generation(home: Path, expected: str) -> bool:
    current = read(home / 'ready.json')
    return identity(expected) and current.get('generation') == expected and current.get('ready') is True
