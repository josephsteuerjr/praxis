"""Win32 extended paths at installer/updater I/O boundaries; receipts keep DOS spelling."""
from contextlib import contextmanager
import os
import tempfile
from pathlib import Path as StandardPath, WindowsPath, PurePath, PureWindowsPath


def logical(path):
    text = str(path) if isinstance(path,PurePath) else os.fspath(path)
    if not isinstance(text,str):raise TypeError('Expected a Windows text path')
    if text[:8].lower() == '\\\\?\\unc\\':
        return '\\\\' + text[8:]
    if text.startswith('\\\\?\\') and text[5:7] == ':\\':
        return text[4:]
    return text


def wide(path):
    if os.name != 'nt':
        return path
    text = os.path.abspath(logical(path))
    if text.startswith(('\\\\?\\', '\\\\.\\')):
        raise ValueError('Unexpected Windows device namespace')
    if text.startswith('\\\\'):
        return '\\\\?\\UNC\\' + text[2:]
    return '\\\\?\\' + text


class _WindowsFile(WindowsPath):
    """Comparable/persistable logical path with explicit extended-length filesystem access."""
    def __new__(cls, *parts):
        return super().__new__(cls, *(logical(p) for p in parts))

    def __init__(self, *parts):
        # pathlib moved its WindowsPath initialisation from __new__ to
        # __init__ in 3.12. Both the bundled and older tooling use this type.
        if WindowsPath.__init__ is not object.__init__:
            super().__init__(*(logical(p) for p in parts))

    def is_junction(self):
        if hasattr(os.path, 'isjunction'):
            return os.path.isjunction(self)
        try:
            return getattr(self.lstat(), 'st_reparse_tag', None) == 0xa0000003
        except FileNotFoundError:
            return False

    def __fspath__(self):
        text = str(self)
        # Keep relative names relative for subprocess, archives and dir_fd APIs.
        return wide(text) if len(text.encode('utf-16-le')) >= 460 and os.path.isabs(text) else text

    def resolve(self, strict=False):
        return type(self)(logical(os.path.realpath(wide(self), strict=strict)))

    def iterdir(self):
        with os.scandir(wide(self)) as entries:
            children = [self / entry.name for entry in entries]
        return iter(children)

    def glob(self, pattern, **options):
        root = StandardPath(wide(self))
        return (self / p.relative_to(root) for p in root.glob(pattern, **options))

    def rglob(self, pattern, **options):
        root = StandardPath(wide(self))
        return (self / p.relative_to(root) for p in root.rglob(pattern, **options))


Path = _WindowsFile if os.name == 'nt' else StandardPath


def reserved(name):
    if hasattr(os.path,'isreserved'):
        return os.path.isreserved(name)
    return PureWindowsPath(name).is_reserved() or name.endswith((' ','.')) or any(
        ch in '<>:"|?*' or ord(ch)<32 for ch in name)


def walk(root, *args, **options):
    for here, directories, files in os.walk(wide(root), *args, **options):
        yield logical(here), directories, files

def _tool_junction(alias, target):
    """Attach a freshly created temporary directory directly to a verified target."""
    import ctypes as c
    from ctypes import wintypes as w
    import struct
    kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype=w.HANDLE
    kernel.DeviceIoControl.argtypes=[w.HANDLE,w.DWORD,w.LPVOID,w.DWORD,w.LPVOID,w.DWORD,c.POINTER(w.DWORD),w.LPVOID]
    kernel.DeviceIoControl.restype=w.BOOL
    kernel.CloseHandle.argtypes=[w.HANDLE]
    # MOUNT_POINT_REPARSE_BUFFER, offsets in bytes from PathBuffer. Omit the
    # optional print name; it does not determine resolution of this junction.
    substitute=(chr(92)+'??'+chr(92)+wide(target)[4:]).encode('utf-16-le')
    names=struct.pack('<4H',0,len(substitute),len(substitute)+2,0)+substitute+bytes(4)
    data=struct.pack('<IHH',0xa0000003,len(names),0)+names
    alias.mkdir()
    handle=kernel.CreateFileW(str(alias),0x40000000,0,None,3,0x02000000|0x00200000,None)
    if handle==w.HANDLE(-1).value:raise c.WinError(c.get_last_error())
    try:
        count=w.DWORD()
        if not kernel.DeviceIoControl(handle,0x000900a4,c.create_string_buffer(data),len(data),None,0,c.byref(count),None):
            raise c.WinError(c.get_last_error())
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def child_directory(path):
    """Present a private short cwd to Win32 tools that reject a long cwd at startup."""
    path = Path(path)
    if os.name != 'nt' or len(str(path).encode('utf-16-le')) < 480:
        yield str(path)
        return
    temp = Path(tempfile.gettempdir())
    # Redirected TEMP may itself be long. Create only a uniquely named child.
    for base in dict.fromkeys((temp, *temp.parents, *path.parents)):
        if len(str(base).encode('utf-16-le')) > 390:
            continue
        try:
            folder = tempfile.TemporaryDirectory(prefix='helene-cwd-', dir=str(base))
            break
        except (PermissionError, FileNotFoundError):
            continue
    else:
        raise OSError('No writable short working directory for the update tool')
    with folder as temporary:
        alias = StandardPath(temporary)/'work'
        _tool_junction(alias, path)
        try:
            if not os.path.samefile(alias,wide(path)):
                raise OSError('Update working directory identity differs')
            yield str(alias)
        finally:
            alias.rmdir()
