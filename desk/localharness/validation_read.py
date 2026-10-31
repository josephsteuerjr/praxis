"""Cooperative cancellation confined to one owned, read-only validation scope."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sys
import threading
import types


class _ReadCancelled(BaseException):
    """The caller revoked a read; never an accepted validation result."""


# Older helpers can import a sibling by its bare name. Both import namespaces
# must share the exact thread-local carrier and exception identity.
_candidate = types.ModuleType('_helene_validation_read_state')
_candidate.local = threading.local()
_candidate.ReadCancelled = _ReadCancelled
_state = sys.modules.setdefault('_helene_validation_read_state', _candidate)
ReadCancelled = _state.ReadCancelled


def current():
    return getattr(_state.local, 'check', None)


def checkpoint(check=None):
    callback = check if check is not None else current()
    if callback is not None:
        callback()


@contextmanager
def scope(check):
    previous = current()
    _state.local.check = check
    try:
        yield
    finally:
        _state.local.check = previous


def digest(stream, check=None):
    callback = check if check is not None else current()
    value = hashlib.sha256()
    while True:
        checkpoint(callback)
        block = stream.read(1024 * 1024)
        checkpoint(callback)
        if not block:
            return value.hexdigest()
        value.update(block)


def parallel(function, items, *, workers):
    """Cancel queued reads and join active readers on every outcome."""
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        return list(pool.map(function, items))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
