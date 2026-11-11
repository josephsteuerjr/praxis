"""Bounded, shared HTTP view snapshots; never used for Forge control decisions."""
from collections import OrderedDict
import copy
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time

from . import forge_reader


@dataclass
class Entry:
    lock: object = field(default_factory=threading.Lock)
    value: object = None
    expires: float = 0


_lock = threading.Lock()
_entries = OrderedDict()
_epoch = 0
_LIMIT = 32


def invalidate():
    """A control request makes the next view read fresh, including during a read."""
    global _epoch
    with _lock:
        _epoch += 1
        _entries.clear()


def interval(page):
    for task in page.get('active', []):
        for unit in task.get('agents', []):
            if unit.get('status') in ('running', 'queued', 'pending'):
                return 1.0
            live = unit.get('liveness') or {}
            if live.get('state') == 'alive':
                try:
                    observed = forge_reader.dt.datetime.fromisoformat(str(live.get('observed_at')).replace('Z', '+00:00'))
                    age = (forge_reader.dt.datetime.now(forge_reader.dt.timezone.utc) - observed).total_seconds()
                    if -5 <= age < 15:return 1.0
                except (ValueError, TypeError):pass
    return 10.0


def activity(tree: Path, limit=20, cursor=''):
    # Retain the root boundary check even on hits; a redirected root is not a
    # valid way to retrieve a prior tree's cached view.
    tree = Path(tree).resolve()
    forge_reader._inside(tree, tree/'memory/.forge/tasks')
    key = (str(tree), int(limit), cursor)
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            entry = Entry()
            _entries[key] = entry
            while len(_entries) > _LIMIT:_entries.popitem(last=False)
        else:_entries.move_to_end(key)
        epoch = _epoch
    with entry.lock:
        now = time.monotonic()
        with _lock:current = epoch == _epoch
        if current and entry.value is not None and now < entry.expires:
            return copy.deepcopy(entry.value)
        value = forge_reader.activity(tree, limit, cursor)
        # Keep the original observed_at/liveness evidence; a cache hit does not
        # fabricate a fresh observation or remove unresolved participants.
        with _lock:
            if epoch == _epoch:
                entry.value = copy.deepcopy(value)
                entry.expires = time.monotonic() + interval(value)
        return value


def phone_activity(tree: Path, limit=20, cursor=''):
    from .phone_activity import project
    return project(activity(tree, limit, cursor))
