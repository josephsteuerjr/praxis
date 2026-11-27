"""Windowless update entries, including rollback to an older application."""
import os
from .windows_paths import Path


def background_flags():
    # CREATE_NEW_PROCESS_GROUP alone creates a visible console when the parent
    # has none. CREATE_NO_WINDOW also prevents console-close events reaching us.
    return (0x00000200 | 0x08000000) if os.name == 'nt' else 0


def permanent_environment():
    """A one-time migration runtime must not become the next update's default."""
    result=dict(os.environ)
    result.pop('HELENE_SOURCE_UPDATE_RUNTIME',None)
    return result


def interpreter_for(root):
    root = Path(root)
    if os.name != 'nt':
        return root / 'runtime/bin/python3'
    windowless = root / 'runtime/pythonw.exe'
    # Old/custom packets may lack pythonw. Every caller also suppresses console
    # creation, so fallback retains diagnostics without creating a terminal.
    return windowless if windowless.is_file() else root / 'runtime/python.exe'


def python_launch(argv, cwd):
    """All updater entries receive absolute program, script and data arguments.

    CreateProcess rejects a long current directory even with an extended prefix.
    Use the system directory for that one launch. Carry the actual executable
    and script as extended absolute paths so the original program owns the job.
    """
    if os.name != 'nt':
        return argv, cwd
    argv=list(argv)
    for i, arg in enumerate(argv[:2]):
        if os.path.isabs(arg):
            argv[i]=os.fspath(Path(arg))
    if len(str(cwd).encode('utf-16-le')) >= 500:
        import ctypes
        buf=ctypes.create_unicode_buffer(32768)
        count=ctypes.windll.kernel32.GetSystemDirectoryW(buf,len(buf))
        if not 0 < count < len(buf):
            raise OSError('Windows system directory unavailable for update startup')
        cwd=buf.value
    return argv, cwd
