"""Private XTTS IPC contract shared by the application client and owned worker.

A ready file alone grants nothing: callers also observe the live process, bind
one generation, and renew a short request lease. Old generations never replay.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import time
import uuid


def selection(value,tree):
    reference=str(value.get('reference') or '').strip()
    if reference:
        path=Path(reference).expanduser()
        reference=str((path if path.is_absolute() else Path(tree)/path).resolve())
    return {'speaker':str(value.get('speaker') or 'Ana Florence'),
        'language':str(value.get('language') or 'ru'),'reference':reference}


def selection_key(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()


def reference_stamp(value):
    if not value.get('reference'):return None
    try:
        info=Path(value['reference']).stat()
        return {key:getattr(info,key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}
    except OSError:return {'unavailable':True}


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
        _publish(lambda: os.replace(temporary, path))
    finally:
        temporary.unlink(missing_ok=True)


def _publish(operation):
    # CPython file reads on Windows can briefly deny delete-sharing. The
    # ready/receipt reader must not crash its writer during an atomic replace.
    # Retry only these concrete sharing/access failures, never hide a lasting
    # denial or retry synthesis/model effects.
    for attempt in range(21):
        try:
            return operation()
        except PermissionError as exc:
            if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 20:
                raise
            time.sleep(.01)


def unlink(path: Path):
    return _publish(lambda: path.unlink(missing_ok=True))


def _queue_lock(home):
    # Reuse the checked cross-process file lock. Its permanent sidecar lives
    # in this private IPC home, never in the programme's roster directory.
    if __package__:
        from .agents import _default_writer_lock
    else:
        from agents import _default_writer_lock
    return _default_writer_lock(home)


def enqueue(path: Path, request: dict):
    """Allocate and atomically publish one FIFO ticket across client processes."""
    path=Path(path)
    if path.suffix!='.json' or not identity(path.stem):raise ValueError('Invalid speech request identity')
    home=path.parent.parent;home.mkdir(parents=True,exist_ok=True)
    order=home/'queue-order.json'
    with _queue_lock(home):
        previous=read(order)
        if order.exists() and (previous.get('schema')!=1 or type(previous.get('last')) is not int or previous['last']<0):
            raise ValueError('Speech queue order is unconfirmed')
        ticket=max(time.time_ns(),previous.get('last',0)+1)
        # Gaps after a failed publication are harmless; reusing a ticket is not.
        write(order,{'schema':1,'last':ticket})
        write(path,dict(request,enqueued_ns=ticket))
        return ticket


def queued(incoming: Path):
    """Snapshot publications under their writer lock, then order valid IDs.

    Old clients have no ticket: retain their filesystem-time fallback, with
    no claim to reconstruct FIFO between legacy files with equal timestamps.
    """
    incoming=Path(incoming)
    with _queue_lock(incoming.parent):
        paths=[]
        for path in incoming.glob('*.json'):
            if not identity(path.stem):continue
            try:stamp=path.stat().st_mtime_ns
            except FileNotFoundError:continue
            ticket=read(path).get('enqueued_ns')
            paths.append((ticket if type(ticket) is int and ticket>0 else stamp,path.name,path))
        return [row[2] for row in sorted(paths)]


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
