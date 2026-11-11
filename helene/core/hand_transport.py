"""Local invocation IPC and native peer identity for the desktop edition.

Windows uses named-pipe client PID plus open process handles. POSIX uses kernel
socket credentials. A caller must still pass the invocation token and descend
from the live agent; a supplied PID is never trusted.
"""
from __future__ import annotations

import contextlib
import os
import socket
import struct
import sys
import threading

MAX_FRAME = 16 * 1024 * 1024


@contextlib.contextmanager
def process_pin(pid):
    import psutil
    process = psutil.Process(int(pid))
    handle = None
    if os.name == 'nt':
        import ctypes as c
        from ctypes import wintypes as w
        api = c.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.CloseHandle.argtypes = [w.HANDLE]
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.WaitForSingleObject.restype = w.DWORD
        handle = api.OpenProcess(0x1000 | 0x100000, False, int(pid))
        if not handle:
            raise c.WinError(c.get_last_error())
    try:
        birth = process.create_time()
        def live():
            if handle and api.WaitForSingleObject(handle, 0) != 258:
                return False
            return process.is_running() and process.create_time() == birth and process.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
        if not live():
            raise ProcessLookupError(pid)
        yield process, birth, live
    finally:
        if handle:
            api.CloseHandle(handle)


def descendant(child, ancestor):
    """Observe a live birth-ordered ancestry, keeping Windows handles until done."""
    try:
        with contextlib.ExitStack() as pins:
            seen, checks, younger_birth = set(), [], float('inf')
            while child > 0 and child not in seen:
                seen.add(child)
                process, birth, live = pins.enter_context(process_pin(child))
                if birth > younger_birth:
                    return False
                checks.append(live)
                if child == ancestor:
                    return all(check() for check in checks)
                younger_birth, child = birth, process.ppid()
    except Exception:
        return False
    return False


@contextlib.contextmanager
def peer_identity(request):
    if getattr(request, 'is_pipe', False):
        import ctypes as c
        from ctypes import wintypes as w
        api = c.WinDLL('kernel32', use_last_error=True)
        fn = api.GetNamedPipeClientProcessId
        fn.argtypes = [w.HANDLE, c.POINTER(w.ULONG)]
        fn.restype = w.BOOL
        pid = w.ULONG()
        if not fn(request.fileno(), c.byref(pid)):
            raise c.WinError(c.get_last_error())
        with process_pin(pid.value) as (process, _, live):
            import psutil
            same_user = process.username().casefold() == psutil.Process().username().casefold()
            if not live():
                raise ProcessLookupError(pid.value)
            yield pid.value, same_user
        return
    if sys.platform == 'darwin':
        # xnu bsd/sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=2.
        pid = struct.unpack('i', request.getsockopt(0, 2, 4))[0]
        with process_pin(pid) as (process, _, live):
            yield pid, live() and process.uids().effective == os.geteuid()
        return
    pid, uid, _ = struct.unpack('3i', request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    with process_pin(pid) as (_, _, live):
        yield pid, live() and uid == os.getuid()


class PipeRequest:
    is_pipe = True

    def __init__(self, connection):
        self.connection, self.timeout = connection, 30

    def fileno(self):
        return self.connection.fileno()

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv_bytes(self, maximum=MAX_FRAME):
        if self.timeout is not None and not self.connection.poll(self.timeout):
            raise TimeoutError('hand handshake timed out')
        return self.connection.recv_bytes(maximum)

    def send_bytes(self, data):
        self.connection.send_bytes(data)


class PipeServer:
    def __init__(self, path, handler):
        from multiprocessing.connection import PipeListener
        from command_execution import home_pipe_sid
        self.path, self.handler = path, handler
        self.stopping = threading.Event()
        if home_pipe_sid:
            sid = home_pipe_sid
            class SandboxedListener(PipeListener):
                def _new_handle(self, first=False):
                    return _container_pipe(self._address, sid, first)
            self.listener = SandboxedListener(path)
        else:
            self.listener = PipeListener(path)

    def serve_forever(self, poll_interval=.05):
        while not self.stopping.is_set():
            try:
                connection = self.listener.accept()
            except (OSError, EOFError):
                if self.stopping.is_set():
                    break
                raise
            if self.stopping.is_set():
                connection.close()
                break
            def handle(connection=connection):
                try:
                    self.handler(PipeRequest(connection), None, self)
                finally:
                    connection.close()
            threading.Thread(target=handle, daemon=True).start()

    def shutdown(self):
        self.stopping.set()
        # Wake accept without closing a handle beneath a pending overlapped read.
        from multiprocessing.connection import Client
        try:
            Client(self.path, family='AF_PIPE', authkey=None).close()
        except OSError:
            pass

    def server_close(self):
        self.listener.close()


def _container_pipe(path, sid, first):
    """Grant this AppContainer only read/write, never new pipe-server instances."""
    import ctypes as c
    from ctypes import wintypes as w
    import re
    if not re.fullmatch(r'S-1-15-2-(?:\d+-)*\d+', sid):
        raise ValueError('Invalid AppContainer SID')
    class Attributes(c.Structure):
        _fields_ = [('length',w.DWORD),('descriptor',c.c_void_p),('inherit',w.BOOL)]
    adv = c.WinDLL('advapi32',use_last_error=True)
    kernel = c.WinDLL('kernel32',use_last_error=True)
    convert = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(c.c_void_p),c.POINTER(w.DWORD)]
    convert.restype=w.BOOL
    kernel.LocalFree.argtypes=[c.c_void_p]
    create=kernel.CreateNamedPipeW
    create.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,c.POINTER(Attributes)]
    create.restype=w.HANDLE
    sd=c.c_void_p()
    # GR/GW would also grant FILE_CREATE_PIPE_INSTANCE (0x4); use exact bits.
    sddl=f'D:(A;;GA;;;SY)(A;;GA;;;OW)(A;;0x12019b;;;{sid})S:(ML;;NW;;;LW)'
    if not convert(sddl,1,c.byref(sd),None):raise c.WinError(c.get_last_error())
    try:
        attributes=Attributes(c.sizeof(Attributes),sd,False)
        handle=create(path,3|0x40000000|(0x80000 if first else 0),6|8,255,65536,65536,0,c.byref(attributes))
        if handle==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
        return handle
    finally:kernel.LocalFree(sd)
