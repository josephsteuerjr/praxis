"""Seed stop and durable autonomy pause. Not a security boundary against an admin.

The seed owns the machine-wide stop; the edition owns pause under its data root.
Only an explicit resume removes pause. Pending alarms/events are never consumed.
"""
from pathlib import Path
import ctypes
import json
import os
import threading
import time

OWNER_STOP_EXIT = 5
LOCK = threading.RLock()
_tree = None


def seed_file():
    if os.name != 'nt':
        return None
    # CSIDL_COMMON_APPDATA is the same known folder as Rust's FOLDERID_ProgramData.
    buf = ctypes.create_unicode_buffer(32768)
    fn = ctypes.WinDLL('shell32').SHGetFolderPathW
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p]
    fn.restype = ctypes.c_long
    if fn(None, 35, None, 0, buf) < 0:
        return Path(r'C:\ProgramData\Helene\stop.json')
    return Path(buf.value) / 'Helene' / 'stop.json'


def present(path):
    if path is None:
        return False
    try:
        path.stat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def stopped():
    return present(seed_file())


def configure(tree):
    global _tree
    _tree = Path(tree)


def pause_file():
    return _tree / 'memory' / '.control' / 'autonomy-paused.json' if _tree else None


def paused():
    return stopped() or present(pause_file())


def pause(reason='owner interrupt'):
    with LOCK:
        path = pause_file()
        if path is None:
            raise RuntimeError('owner pause has no configured data root')
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'at': time.time(), 'reason': reason}), encoding='utf-8')
        os.replace(temp, path)


def resume():
    with LOCK:
        if stopped():
            raise RuntimeError('Use explicit native Resume to clear the seed stop first')
        path = pause_file()
        if path:
            path.unlink(missing_ok=True)


def require_admission():
    if paused():
        raise RuntimeError('Autonomy paused by owner; pending source retained until explicit resume')
