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
import uuid

OWNER_STOP_EXIT = 5
LOCK = threading.RLock()
_tree = None


def seed_file():
    if os.name != 'nt':
        if os.environ.get('HELENE_SUPERVISOR') == 'serverboot' and os.environ.get('HELENE_TREE'):
            return Path(os.environ['HELENE_TREE']) / '.owner-stop' / 'stop.json'
        # Linux (03.10, слово владельца): движок всегда идёт от имени владельца,
        # флаг лежит в его XDG-state — ВНЕ дерева данных, чтобы снос дерева не
        # снимал стоп. HOME, не $XDG_STATE_HOME: у службы systemd из юнита есть
        # HOME, а XDG-переменных сеанса нет — Rust и Python смотрят в одну точку
        # (common/owner_stop.rs: linux_owner_stop_dir).
        home = os.environ.get('HOME')
        if not home:
            return None
        return Path(home) / '.local' / 'state' / 'helene' / 'stop.json'
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
    return present(seed_file()) or (os.name == 'nt' and present(client_file()))


def client_file():
    """Writable user latch; use the same Known Folder as the native client."""
    if os.name != 'nt': return seed_file()
    selected=os.environ.get('HELENE_USER_DATA')
    if selected:
        if __package__:from .first_start import Places,normalized
        else:from first_start import Places,normalized
        expected=Places.current().data
        if normalized(selected)==normalized(expected):return expected/'owner-stop.json'
    buf = ctypes.create_unicode_buffer(32768)
    fn = ctypes.WinDLL('shell32').SHGetFolderPathW
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p]
    fn.restype = ctypes.c_long
    if fn(None, 28, None, 0, buf) < 0: return None  # CSIDL_LOCAL_APPDATA
    return Path(buf.value) / 'Helene' / 'owner-stop.json'


def latch_client(via, reason=''):
    path = client_file()
    if path is None: raise RuntimeError('Не определена папка состояния владельца.')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as target:
            json.dump({'at': time.time(), 'via': via, 'reason': reason[:200],
                       'by': 'agent' if via == 'agent' else 'owner'}, target)
            target.flush(); os.fsync(target.fileno())
    except FileExistsError: pass


def configure(tree):
    global _tree
    _tree = Path(tree)


def pause_file():
    return _tree / 'memory' / '.control' / 'autonomy-paused.json' if _tree else None


def pause_generation(tree):
    """Exact durable control transition; absence is the legacy protocol only."""
    path = Path(tree) / 'memory/.control/autonomy-generation.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError('Autonomy generation is unreadable') from exc
    identity = value.get('id') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('format') != 'helene-autonomy-generation.v1'
            or not isinstance(identity, str) or len(identity) != 32
            or any(c not in '0123456789abcdef' for c in identity)
            or type(value.get('paused')) is not bool):
        raise RuntimeError('Autonomy generation identity is unconfirmed')
    return value


def change_pause(tree, paused, *, reason='owner interrupt', via='owner'):
    """Publish a new private control epoch BEFORE mutating its legacy marker.

    The token is not a timestamp or a security boundary. Atomic private-state
    writes preserve rapid transitions for trial observers without new processes.
    """
    if type(paused) is not bool:
        raise ValueError('Autonomy pause must be a boolean')
    if __package__:
        from .package_notice import write
    else:
        from package_notice import write
    tree = Path(tree)
    with LOCK:
        value = {'format':'helene-autonomy-generation.v1','id':uuid.uuid4().hex,
                 'paused':paused,'at':time.time(),'via':str(via)[:40],'reason':str(reason)[:200]}
        write(tree/'memory/.control/autonomy-generation.json', value)
        path = tree/'memory/.control/autonomy-paused.json'
        if paused:
            write(path, {'at':value['at'],'via':value['via'],'reason':value['reason']})
        else:
            path.unlink(missing_ok=True)
        return value


def paused():
    if stopped() or present(pause_file()):
        return True
    if _tree is None:
        return False
    try:
        generation = pause_generation(_tree)
    except RuntimeError:
        return True
    return generation is not None and generation['paused']


def pause(reason='owner interrupt'):
    if _tree is None:
        raise RuntimeError('owner pause has no configured data root')
    return change_pause(_tree, True, reason=reason)


def resume():
    with LOCK:
        if stopped():
            raise RuntimeError('Use explicit native Resume to clear the seed stop first')
        if _tree is not None:
            return change_pause(_tree, False, reason='explicit owner resume')


def require_admission():
    if paused():
        raise RuntimeError('Autonomy paused by owner; pending source retained until explicit resume')
