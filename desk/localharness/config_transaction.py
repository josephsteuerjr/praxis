"""One per-document kernel lock shared by desktop CLI and channel writers.

The config may be a read-only bind mount, so its lock lives in the OS temporary
directory, keyed by canonical path. Keep the lock inode after release.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .phone_tunnel.platform_support import acquire_file_lock, release_file_lock, PlatformSupportError


def read_snapshot(path):
    """Read a document and its receipt from one checked file generation."""
    path=Path(path)
    with path.open('rb') as source:
        before=os.fstat(source.fileno());raw=source.read();after=os.fstat(source.fileno())
    live=path.stat()
    if (before.st_mtime_ns!=after.st_mtime_ns or before.st_size!=after.st_size
            or (live.st_ino,live.st_mtime_ns)!=(after.st_ino,after.st_mtime_ns)):
        raise ValueError('файл изменился во время чтения — перечитай настройки')
    for encoding in ('utf-8-sig','utf-16','cp1251'):
        try:text=raw.decode(encoding)
        except (UnicodeDecodeError,LookupError):continue
        value=json.loads(text)
        if not isinstance(value,dict):raise ValueError('верхний уровень должен быть объектом {…}')
        return value,str(after.st_mtime_ns)
    raise ValueError('файл не читается ни как UTF-8, ни как UTF-16, ни как CP1251')


def replace_config(staged, target):
    """Replace under the caller's lock with a strictly newer CAS receipt.

    Windows may stamp consecutive files with the same clock tick. Advance the
    staging timestamp by the smallest supported quantum before replacement;
    failure leaves the old document intact. Readers keep the numeric mtime API.
    """
    staged,target=Path(staged),Path(target)
    try:previous=target.stat().st_mtime_ns
    except FileNotFoundError:previous=None
    if previous is not None and staged.stat().st_mtime_ns<=previous:
        for quantum in (1,100,1000,10000,1000000,1000000000,2000000000):
            value=staged.stat()
            os.utime(staged,ns=(value.st_atime_ns,previous+quantum))
            if staged.stat().st_mtime_ns>previous:break
        else:raise OSError('Filesystem cannot provide a fresh settings receipt')
    hidden=False
    if os.name=='nt' and target.exists():
        import ctypes
        flags=ctypes.windll.kernel32.GetFileAttributesW(str(target))
        hidden=flags!=-1 and bool(flags&2)
    os.replace(staged,target)
    if hidden:
        from .program_state import hidden as retain_hidden
        retain_hidden(target)


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
