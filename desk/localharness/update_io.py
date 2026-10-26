"""Update byte receipts: freshness is metadata + change identity, copies prove content.

A raw path->SHA map is not proof. Receipts only elide an earlier source read;
publication still hashes the stream copied to its temporary destination. On
Windows ChangeTime (not st_ctime, which is creation time) invalidates rewrites.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat


def remove_created_file(path):
    """Rollback owns these newly installed files, including read-only Git blobs."""
    path = Path(path)
    try:path.unlink(missing_ok=True)
    except PermissionError:
        if os.name != 'nt' or path.stat().st_mode & stat.S_IWRITE:raise
        path.chmod(path.stat().st_mode | stat.S_IWRITE)
        path.unlink()


def _change_time(path):
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [(n, ctypes.c_longlong) for n in ('created', 'accessed', 'written', 'changed')] + [('attributes', wintypes.DWORD)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        value = Basic()
        if not kernel.GetFileInformationByHandleEx(handle, 0, ctypes.byref(value), ctypes.sizeof(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.changed
    finally:
        kernel.CloseHandle(handle)


def stamp(path):
    path = Path(path)
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Update file is a link: ' + str(path))
    value = path.stat()
    try:change = _change_time(path)
    except OSError:change = None  # unknown change identity disables receipt reuse
    return {'size': value.st_size, 'mtime_ns': value.st_mtime_ns,
            'ctime_ns': value.st_ctime_ns, 'dev': value.st_dev, 'ino': value.st_ino,
            'mode': value.st_mode & 0o777, 'change_time': change}


def count(progress, category, size):
    if progress is not None and hasattr(progress, 'add_read'):
        progress.add_read(category, size)


def measured_digest(path, progress=None, category='incoming'):
    path = Path(path)
    before = stamp(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            count(progress, category, len(block))
            digest.update(block)
    if stamp(path) != before:
        raise ValueError('File changed while hashing: ' + str(path))
    return {'sha256': digest.hexdigest(), 'stat': before}


def proof(path, receipt, progress=None, category='incoming'):
    """Reuse a structured receipt only with a measured change identity.

    POSIX stat timestamps can share a clock tick across same-size rewrites,
    even with nanosecond fields. Without a stronger change identity, rehash;
    actual copied streams are independently hashed on every platform.
    """
    current = stamp(path)
    if (isinstance(receipt, dict) and isinstance(receipt.get('sha256'), str)
            and len(receipt['sha256']) == 64 and receipt.get('stat') == current
            and current['change_time'] is not None):
        return receipt
    return measured_digest(path, progress, category)


def copy_verified(source, destination, expected, progress=None, category='copy'):
    """Hash the actual copied stream; never rename bytes certified by an old SHA."""
    source, destination = Path(source), Path(destination)
    before = stamp(source)
    digest = hashlib.sha256()
    with source.open('rb') as reader, destination.open('xb') as writer:
        while block := reader.read(1024 * 1024):
            count(progress, category, len(block))
            writer.write(block)
            digest.update(block)
        writer.flush()
        os.fsync(writer.fileno())
    if stamp(source) != before or digest.hexdigest() != expected:
        raise ValueError('Copied update stream changed: ' + str(source))
    shutil.copystat(source, destination)
    return {'sha256': digest.hexdigest(), 'stat': stamp(destination)}
