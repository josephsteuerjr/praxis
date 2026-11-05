"""Private, same-volume atomic writes without account-name or shell dependencies.

No secret bytes enter staging until its descriptor has been checked. Windows
creates the file with a protected DACL and uses the effective token's user SID;
POSIX creates a 0700 staging directory and a 0600 file. A failed replacement
leaves the previous target intact. Callers may replay their own journal after
an interruption; this module never makes a plaintext backup or retries a write.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import uuid


class PrivateFileError(OSError):
    """Safe to display: stable stage and numeric status, never paths or bytes."""

    def __init__(self, operation: str, *, winerror: int = 0, errno: int = 0):
        self.code = "private_file_refused"
        self.operation = operation
        super().__init__(f"{self.code}: {operation} (winerror={winerror or 0}, errno={errno or 0})")
        self.winerror = winerror or 0
        self.errno = errno or 0


class _Windows:
    """Small ctypes boundary; do not cache identity across impersonation changes."""

    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.k = c.WinDLL("kernel32", use_last_error=True)
        self.a = c.WinDLL("advapi32", use_last_error=True)
        P = c.c_void_p
        signatures = {
            "OpenThreadToken": ([w.HANDLE, w.DWORD, w.BOOL, c.POINTER(w.HANDLE)], w.BOOL),
            "OpenProcessToken": ([w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
            "GetTokenInformation": ([w.HANDLE, c.c_int, P, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
            "ConvertSidToStringSidW": ([P, c.POINTER(P)], w.BOOL),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": ([w.LPCWSTR, w.DWORD, c.POINTER(P), P], w.BOOL),
            "GetSecurityDescriptorDacl": ([P, c.POINTER(w.BOOL), c.POINTER(P), c.POINTER(w.BOOL)], w.BOOL),
            "GetSecurityDescriptorControl": ([P, c.POINTER(w.WORD), c.POINTER(w.DWORD)], w.BOOL),
            "GetSecurityInfo": ([w.HANDLE, c.c_int, w.DWORD, P, P, c.POINTER(P), P, c.POINTER(P)], w.DWORD),
            "SetSecurityInfo": ([w.HANDLE, c.c_int, w.DWORD, P, P, P, P], w.DWORD),
            "GetAclInformation": ([P, P, w.DWORD, c.c_int], w.BOOL),
            "GetAce": ([P, w.DWORD, c.POINTER(P)], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.a, name)
            fn.argtypes, fn.restype = args, result
        for name, args, result in (
            ("GetCurrentThread", [], w.HANDLE), ("GetCurrentProcess", [], w.HANDLE),
            ("CloseHandle", [w.HANDLE], w.BOOL), ("LocalFree", [P], P),
            ("CreateDirectoryW", [w.LPCWSTR, P], w.BOOL),
            ("CreateFileW", [w.LPCWSTR, w.DWORD, w.DWORD, P, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
        ):
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result
        self.principals = tuple(dict.fromkeys((self._user(), "S-1-5-18", "S-1-5-32-544")))

    def _check(self, value, stage):
        if not value:
            raise PrivateFileError(stage, winerror=self.c.get_last_error())

    def _sid(self, pointer):
        value = self.c.c_void_p()
        self._check(self.a.ConvertSidToStringSidW(pointer, self.c.byref(value)), "sid")
        try:
            return self.c.wstring_at(value)
        finally:
            self.k.LocalFree(value)

    def _user(self):
        c, w = self.c, self.w
        token = w.HANDLE()
        if not self.a.OpenThreadToken(self.k.GetCurrentThread(), 8, True, c.byref(token)):
            # Only ERROR_NO_TOKEN permits process-token fallback. A refused or
            # anonymous thread token must never silently acquire another identity.
            if c.get_last_error() != 1008:
                raise PrivateFileError("thread-token", winerror=c.get_last_error())
            self._check(self.a.OpenProcessToken(self.k.GetCurrentProcess(), 8, c.byref(token)), "process-token")
        try:
            size = w.DWORD()
            self.a.GetTokenInformation(token, 1, None, 0, c.byref(size))
            if not size.value:
                raise PrivateFileError("token-size", winerror=c.get_last_error())
            buffer = c.create_string_buffer(size.value)
            self._check(self.a.GetTokenInformation(token, 1, buffer, size, c.byref(size)), "token-user")
            return self._sid(c.cast(buffer, c.POINTER(c.c_void_p))[0])
        finally:
            self.k.CloseHandle(token)

    def _descriptor(self):
        descriptor = self.c.c_void_p()
        sddl = "D:P" + "".join(f"(A;;FA;;;{sid})" for sid in self.principals)
        self._check(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, self.c.byref(descriptor), None), "descriptor")
        return descriptor

    def open(self, path: Path, *, create=False, protect=False, directory=False):
        c, w = self.c, self.w
        class SecurityAttributes(c.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]
        descriptor = self._descriptor() if create else None
        try:
            attributes = SecurityAttributes(c.sizeof(SecurityAttributes), descriptor, False)
            access = 0xC0020000 if create else (0x60000 if protect else 0x20000)
            if directory and create:
                self._check(self.k.CreateDirectoryW(str(path), c.byref(attributes)), "create-directory")
            handle = self.k.CreateFileW(str(path), access if not directory else (0x60000 if protect else 0x20000), 7,
                c.byref(attributes) if create else None, 1 if create and not directory else 3,
                0x00200000 | (0x02000000 if directory else 0), None)  # OPEN_REPARSE_POINT
            if handle == c.c_void_p(-1).value:
                raise PrivateFileError("create" if create else "open", winerror=c.get_last_error())
            return handle
        finally:
            if descriptor:
                self.k.LocalFree(descriptor)

    def protect(self, handle):
        c, w = self.c, self.w
        descriptor = self._descriptor()
        try:
            present, defaulted, dacl = w.BOOL(), w.BOOL(), c.c_void_p()
            self._check(self.a.GetSecurityDescriptorDacl(descriptor, c.byref(present), c.byref(dacl), c.byref(defaulted)), "descriptor-dacl")
            result = self.a.SetSecurityInfo(handle, 1, 4 | 0x80000000, None, None, dacl, None)
            if result:
                raise PrivateFileError("protect", winerror=result)
        finally:
            self.k.LocalFree(descriptor)

    def verify(self, handle):
        c, w = self.c, self.w
        descriptor, dacl = c.c_void_p(), c.c_void_p()
        result = self.a.GetSecurityInfo(handle, 1, 4, None, None, c.byref(dacl), None, c.byref(descriptor))
        if result:
            raise PrivateFileError("read-dacl", winerror=result)
        try:
            control, revision = w.WORD(), w.DWORD()
            self._check(self.a.GetSecurityDescriptorControl(descriptor, c.byref(control), c.byref(revision)), "dacl-control")
            if not control.value & 0x1000 or not dacl:
                raise PrivateFileError("unprotected-dacl")
            class AclSize(c.Structure):
                _fields_ = [("count", w.DWORD), ("used", w.DWORD), ("free", w.DWORD)]
            info = AclSize()
            self._check(self.a.GetAclInformation(dacl, c.byref(info), c.sizeof(info), 2), "dacl-size")
            entries = []
            for index in range(info.count):
                ace = c.c_void_p()
                self._check(self.a.GetAce(dacl, index, c.byref(ace)), "dacl-ace")
                # ACCESS_ALLOWED_ACE: header(4), mask(4), variable SID.
                header = c.string_at(ace, 4)
                mask = c.cast(ace.value + 4, c.POINTER(w.DWORD))[0]
                if header[0] != 0 or header[1] != 0 or mask != 0x1F01FF:
                    raise PrivateFileError("unexpected-ace")
                entries.append(self._sid(ace.value + 8))
            if len(entries) != len(self.principals) or set(entries) != set(self.principals):
                raise PrivateFileError("unexpected-principals")
        finally:
            self.k.LocalFree(descriptor)


def _verify(fd: int, windows=None) -> None:
    value = os.fstat(fd)
    if not stat.S_ISREG(value.st_mode):
        raise PrivateFileError("not-regular")
    if windows:
        import msvcrt
        windows.verify(msvcrt.get_osfhandle(fd))
    elif stat.S_IMODE(value.st_mode) != 0o600 or value.st_uid != os.geteuid():
        raise PrivateFileError("file-mode")


def protect_private_temp(name: str) -> None:
    """Compatibility protection of an existing EMPTY file; fail before any bytes."""
    path = Path(name)
    stage = "protect-empty"
    try:
        if path.is_symlink() or path.stat().st_size:
            raise PrivateFileError(stage)
        if os.name == "nt":
            windows = _Windows()
            handle = windows.open(path, protect=True)
            try:
                windows.protect(handle)
                windows.verify(handle)
            finally:
                windows.k.CloseHandle(handle)
        else:
            fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fchmod(fd, 0o600)
                _verify(fd)
            finally:
                os.close(fd)
    except PrivateFileError:
        raise
    except OSError as exc:
        raise PrivateFileError(stage, winerror=getattr(exc, "winerror", 0), errno=exc.errno) from None


def write_private(path: Path, data: bytes) -> None:
    """Publish private bytes atomically; raise only sanitized PrivateFileError."""
    _atomic_write(Path(path), data)


def _atomic_write(path: Path, data: bytes, *, before_write=None) -> None:
    # before_write exists solely for vision_policy's historical protection hook.
    # It cannot bypass the descriptor checks on the actual write handle.
    fd, staging, directory, windows = None, None, None, None
    stage = "prepare"
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "nt":
            import msvcrt
            windows = _Windows()
            candidate = path.parent / (".private-" + uuid.uuid4().hex)
            handle = windows.open(candidate, create=True, directory=True)
            directory = candidate  # cleanup owns only names we actually created
            try:
                windows.verify(handle)
            finally:
                windows.k.CloseHandle(handle)
            candidate = directory / 'content'
            handle = windows.open(candidate, create=True)
            staging = candidate
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            except BaseException:
                windows.k.CloseHandle(handle)
                raise
        else:
            directory = Path(tempfile.mkdtemp(prefix=".private-", dir=path.parent))
            # A server role directory may carry setgid. mkdtemp inherits that
            # bit even with mode0700; remove it on this new EMPTY staging dir.
            directory.chmod(0o700)
            mode = directory.stat()
            if stat.S_IMODE(mode.st_mode) != 0o700 or mode.st_uid != os.geteuid():
                raise PrivateFileError("directory-mode")
            candidate = directory / "content"
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            staging = candidate
        _verify(fd, windows)
        if before_write:
            before_write(str(staging))
        _verify(fd, windows)
        stage = "write"
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
        _verify(fd, windows)
        stage = "replace"
        os.replace(staging, path)
        stage = "verify-replaced"
        # The same open object must now be the final path, with the protected
        # descriptor intact. Replacement never inherits the old target's ACL.
        if path.is_symlink() or not os.path.samestat(os.fstat(fd), path.stat()):
            raise PrivateFileError(stage)
        _verify(fd, windows)
        if windows:
            handle = windows.open(path)
            try:
                windows.verify(handle)
            finally:
                windows.k.CloseHandle(handle)
        else:
            parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    except PrivateFileError:
        raise
    except OSError as exc:
        raise PrivateFileError(stage, winerror=getattr(exc, "winerror", 0), errno=exc.errno) from None
    finally:
        failed = sys.exc_info()[0] is not None
        try:
            if fd is not None:
                os.close(fd)
            if staging is not None:
                staging.unlink(missing_ok=True)
            if directory is not None:
                directory.rmdir()
        except OSError as exc:
            if not failed:
                raise PrivateFileError('cleanup', winerror=getattr(exc, 'winerror', 0), errno=exc.errno) from None
