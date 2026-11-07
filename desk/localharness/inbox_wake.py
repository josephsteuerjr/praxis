"""Wake the Windows inbox consumer on writes, without repeated directory polling.

Notifications are hints to drain the existing durable inbox, never message
receipts. Unsupported filesystems/platforms retain the ordinary timed wait.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import threading
import time

_SIGNALED = 0
_TIMEOUT = 258
_FAILED = 0xFFFFFFFF
_FILTER = 0x00000001 | 0x00000010  # file names and last write


def _windows_api():
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
    api.FindFirstChangeNotificationW.restype = wintypes.HANDLE
    api.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
    api.FindNextChangeNotification.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
    api.FindCloseChangeNotification.restype = wintypes.BOOL
    return api


class InboxWake:
    """Create once before the consumer loop; call wait only when it is idle.

    The consumer still reads the durable inbox before waiting and after every
    wake/timeout. A notification may combine several writes or arrive while the
    consumer is busy, so it must not be interpreted as one particular message.
    """

    def __init__(self, directory, *, notes_only=False):
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._api = None
        self._handle = None
        self._directory = Path(directory)
        self._notes_only = notes_only
        self._notes = self._note_stamp() if notes_only else None
        try:
            import ctypes
            api = _windows_api()
            if api is not None:
                handle = api.FindFirstChangeNotificationW(str(Path(directory).resolve()), False, _FILTER)
                if handle not in (None, 0, -1, ctypes.c_void_p(-1).value):
                    self._api, self._handle = api, handle
        except (OSError, ValueError, AttributeError):
            # Missing/inaccessible inbox or an unsupported filesystem must not
            # break the existing queue consumer or turn its idle loop busy.
            pass

    @property
    def native(self):
        return self._handle is not None and not self._closed.is_set()

    def _note_stamp(self):
        # Status/heartbeat, attachment bookkeeping and atomic temp files share
        # this directory. Only published notes can wake its consumer early.
        try:
            with os.scandir(self._directory) as entries:
                return tuple(sorted((entry.name, info.st_ino, info.st_mtime_ns, info.st_size)
                    for entry in entries
                    if entry.name.endswith('.md') and not entry.name.startswith('.tmp-')
                    for info in (entry.stat(),)))
        except OSError:
            return None  # Timed consumer retry retains its normal error policy.

    def _retire(self):
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                self._api.FindCloseChangeNotification(handle)
            except (OSError, ValueError, AttributeError):
                pass

    def wait(self, timeout=1):
        """Return True for a directory signal, False for timeout/closed/fallback.

        FindNextChangeNotification is called ONLY after a signaled wait. Calling
        it after a timeout can lose notifications and exhaust kernel resources.
        """
        seconds = float(timeout)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Inbox wait timeout must be finite and nonnegative')
        deadline = time.monotonic() + seconds
        with self._lock:
            if self._closed.is_set():
                return False
            if self._handle is not None:
                try:
                    # Avoid INFINITE (0xffffffff), even for a very large input.
                    milliseconds = min(0xFFFFFFFE, math.ceil(seconds * 1000))
                    while True:
                        result = self._api.WaitForSingleObject(self._handle, milliseconds)
                        if result != _SIGNALED:
                            break
                        try:
                            rearmed = self._api.FindNextChangeNotification(self._handle)
                        except (OSError, ValueError, AttributeError):
                            rearmed = False
                        if not rearmed:
                            self._retire()
                        if not self._notes_only:
                            return True
                        stamp = self._note_stamp()
                        changed = stamp is not None and stamp != self._notes
                        self._notes = stamp
                        if changed:
                            return True
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return False
                        if not rearmed:
                            break
                        milliseconds = min(0xFFFFFFFE, math.ceil(remaining * 1000))
                    if result == _TIMEOUT:
                        return False
                    self._retire()
                except (OSError, ValueError, AttributeError):
                    self._retire()
        # On a failed native wait, preserve the remaining idle interval. The
        # close event gives the timed fallback a prompt, safe shutdown path.
        self._closed.wait(max(0, deadline - time.monotonic()))
        return False

    def close(self):
        self._closed.set()
        # WaitForSingleObject has undefined behavior if its handle is closed
        # concurrently. Serialize close with the bounded idle wait above.
        with self._lock:
            self._retire()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
