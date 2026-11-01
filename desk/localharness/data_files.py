"""Bounded owner-data file aliases; programme paths still reject every link."""
from contextlib import contextmanager
from dataclasses import dataclass
import errno,hashlib,os,stat,tempfile
from pathlib import Path


def identity(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)


def unlink_regular(path):
    """Unlink a data entry without changing shared read-only blob attributes."""
    from .first_start import plain
    path=plain(path)
    try:path.unlink();return
    except PermissionError:
        before=path.stat()
        if (os.name!='nt' or not stat.S_ISREG(before.st_mode)
                or not getattr(before,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_READONLY):raise
    # Windows exposes per-entry deletion that ignores READONLY while retaining
    # the inode's attributes for every other hardlink. DELETE and attribute
    # access are still checked by the OS; no chmod or ACL override is used.
    # https://learn.microsoft.com/windows/win32/api/minwinbase/ne-minwinbase-file_info_by_handle_class
    import ctypes as c,msvcrt
    from ctypes import wintypes as w
    api=c.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    api.CreateFileW.restype=w.HANDLE
    api.SetFileInformationByHandle.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
    api.SetFileInformationByHandle.restype=w.BOOL
    api.CloseHandle.argtypes=[w.HANDLE];api.CloseHandle.restype=w.BOOL
    handle=api.CreateFileW(str(path),0x10000|0x80|0x100,7,None,3,0x00200000,None)
    if handle==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
    fd=None
    try:
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_NOINHERIT)
        if identity(os.fstat(fd))!=identity(before) or identity(plain(path).stat())!=identity(before):
            raise ValueError('Read-only data entry generation changed')
        flags=w.DWORD(0x1|0x2|0x10)  # DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE
        if not api.SetFileInformationByHandle(handle,21,c.byref(flags),c.sizeof(flags)):
            raise c.WinError(c.get_last_error())
    finally:
        if fd is None:api.CloseHandle(handle)
        else:os.close(fd)  # The descriptor owns the native handle.
    if os.path.lexists(path):raise OSError('Windows retained the read-only data entry')


@dataclass(frozen=True)
class Alias:
    path: Path
    target: Path
    generation: tuple
    spelling: str

    def verify(self):
        from .first_start import plain
        plain(self.path.parent)
        try:
            info=self.path.lstat()
            same=(stat.S_ISLNK(info.st_mode) and identity(info)==self.generation
                  and os.readlink(self.path)==self.spelling)
        except OSError:same=False
        if not same:raise ValueError('Data file alias changed: '+str(self.path))


def entry(path,root):
    """Resolve only a file alias ending inside this exact data directory."""
    from .first_start import plain
    root=plain(root);path=Path(os.path.abspath(path))
    plain(path.parent)
    if not path.is_relative_to(root):raise ValueError('Data file leaves its source directory: '+str(path))
    info=path.lstat()
    if not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400:
        if not stat.S_ISREG(info.st_mode):raise ValueError('Data entry is not a regular file: '+str(path))
        return path,None
    if not stat.S_ISLNK(info.st_mode):raise ValueError('Unsupported data reparse entry: '+str(path))
    spelling=os.readlink(path)
    try:target=plain(path.resolve(strict=True))
    except (OSError,RuntimeError) as error:raise ValueError('Broken data file alias: '+str(path)) from error
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError('Data file alias leaves its source directory or names a directory: '+str(path))
    alias=Alias(path,target,identity(info),spelling);alias.verify()
    return target,alias


def inventory(root):
    """Validate the whole tree before movement; never walk directory links."""
    from .first_start import plain
    from .validation_read import checkpoint
    root=plain(root);directories=['.'];files=[];aliases=[]
    def unreadable(error):raise error
    for here,dirs,names in os.walk(root,followlinks=False,onerror=unreadable):
        checkpoint()
        for name in dirs:
            child=plain(Path(here)/name)
            directories.append(child.relative_to(root).as_posix())
        for name in names:
            checkpoint();path=Path(here)/name;target,alias=entry(path,root)
            if alias:aliases.append(alias)
            else:files.append(path)
    admitted=set(files)
    if any(alias.target not in admitted for alias in aliases):
        raise ValueError('Data alias has no admitted regular target')
    return directories,files,aliases


def materialize(aliases):
    """Atomically replace each bounded alias with its regular-file bytes.

    The original data stays readable after interruption, even if a later
    transfer has already moved the alias's former target to another volume.
    Hardlinks retain one blob; unsupported filesystems get a verified copy.
    """
    from .first_start import plain
    from .update_io import copy_verified
    for alias in aliases:
        alias.verify();target=plain(alias.target);before=target.stat()
        descriptor,name=tempfile.mkstemp(prefix='.data-alias-',dir=alias.path.parent)
        os.close(descriptor);temporary=Path(name);temporary.unlink()
        try:
            try:os.link(target,temporary)
            except OSError as error:
                if error.errno not in (errno.EXDEV,errno.EPERM,errno.ENOSYS,errno.EOPNOTSUPP) and getattr(error,'winerror',None) not in (1,17,50,1314):raise
                with target.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                copy_verified(target,temporary,digest)
            if identity(target.stat())!=identity(before):raise ValueError('Data alias target changed')
            alias.verify();os.replace(temporary,alias.path)
        finally:
            if os.path.lexists(temporary):unlink_regular(temporary)


@contextmanager
def open_file(path,root):
    """Keep byte reads bound to the validated entry and opened generation."""
    from .first_start import plain
    target,alias=entry(path,root)
    before=target.stat()
    with plain(target).open('rb') as stream:
        if identity(os.fstat(stream.fileno()))!=identity(before):
            raise ValueError('Data file generation changed before reading')
        if alias:alias.verify()
        yield stream
        if (identity(os.fstat(stream.fileno()))!=identity(before)
                or identity(plain(target).stat())!=identity(before)):
            raise ValueError('Data file changed while reading')
        if alias:alias.verify()
