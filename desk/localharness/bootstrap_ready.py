"""One-shot Windows launcher acknowledgement; kernel objects leave no files."""
import os,re,threading

_lock=threading.RLock()
_name=None


def attach():
    global _name
    value=os.environ.pop('HELENE_BOOTSTRAP_EVENT','')
    if not value:return
    if os.name!='nt' or not re.fullmatch(r'Local\\HeleneBootstrap-[a-f0-9]{32}',value):
        raise ValueError('Invalid bootstrap startup identity')
    with _lock:
        if _name is not None and _name!=value:raise ValueError('Bootstrap startup identity changed')
        _name=value


def signal(*,failed=False):
    global _name
    with _lock:
        if _name is None:
            attach()
        if _name is None:return
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenEventW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR];kernel.OpenEventW.restype=wintypes.HANDLE
        kernel.SetEvent.argtypes=[wintypes.HANDLE];kernel.SetEvent.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenEventW(2,False,_name+('-failed' if failed else ''))
        # A slow startup may outlive its launcher; it must remain usable.
        if not handle:
            error=ctypes.get_last_error()
            if error==2:_name=None;return
            raise ctypes.WinError(error)
        try:
            if not kernel.SetEvent(handle):raise ctypes.WinError(ctypes.get_last_error())
            _name=None
        finally:kernel.CloseHandle(handle)
