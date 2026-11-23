"""Win32 extended paths at installer/updater I/O boundaries; receipts keep DOS spelling."""
import os
from pathlib import Path as StandardPath, WindowsPath, PureWindowsPath


def logical(path):
    text = str(path)
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
