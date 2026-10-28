"""One per-document kernel lock shared by desktop CLI and channel writers.

The config may be a read-only bind mount, so its lock lives in the OS temporary
directory, keyed by canonical path. Keep the lock inode after release.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile

from .phone_tunnel.platform_support import acquire_file_lock, release_file_lock, PlatformSupportError


@contextmanager
def transaction(path):
    identity = os.path.normcase(str(Path(path).resolve()))
    name = 'helene-config-' + hashlib.sha256(identity.encode('utf-8')).hexdigest() + '.lock'
    try:
        fd = acquire_file_lock(Path(tempfile.gettempdir()) / name, timeout_sec=15)
    except PlatformSupportError as exc:
        raise OSError('Settings writer is busy or unavailable') from exc
    try:
        yield
    finally:
        release_file_lock(fd)
