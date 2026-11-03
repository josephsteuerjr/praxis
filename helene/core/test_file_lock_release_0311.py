"""A transient Windows reader must not turn a committed write into failure."""
import ctypes
import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import self_model


def sharing_error(code=32):
    error = PermissionError(errno.EACCES, 'controlled Windows sharing refusal')
    error.winerror = code
    return error


class Release(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'writer.lock'
        self.lock = self_model.FileLock(self.path).__enter__()
        self.addCleanup(self.lock.__exit__, None, None, None)

    def test_transient_reader_refusals_do_not_fail_the_completed_write(self):
        original = Path.unlink
        refusals = iter((32, 33))
        def remove(path, *args, **kwargs):
            code = next(refusals, None) if path == self.path else None
            if code is not None:
                raise sharing_error(code)
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', new=remove):
            self.lock.__exit__(None, None, None)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.lock.held)
        self.assertFalse(self.lock.lost_to_takeover)

    def test_retry_preserves_the_replacement_owners_lock(self):
        original = Path.unlink
        other = {'pid': os.getpid(), 'token': 'another-owner'}
        def remove(path, *args, **kwargs):
            if path == self.path:
                self.path.write_text(json.dumps(other), encoding='utf8')
                raise sharing_error()
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', new=remove):
            self.lock.__exit__(None, None, None)
        self.assertEqual(json.loads(self.path.read_text()), other)
        self.assertTrue(self.lock.lost_to_takeover)
        self.assertFalse(self.lock.held)

    def test_permanent_permission_error_is_not_hidden(self):
        error = sharing_error(5)
        with mock.patch.object(Path, 'unlink', side_effect=error):
            with self.assertRaises(PermissionError) as result:
                self.lock.__exit__(None, None, None)
        self.assertIs(result.exception, error)
        self.assertTrue(self.path.exists())
        self.assertFalse(self.lock.held)
        self.assertIsNone(self_model._held_here(self.path))

    def test_exhausted_reader_refusal_remains_an_error(self):
        error = sharing_error()
        with mock.patch.object(Path, 'unlink', side_effect=error), \
                mock.patch.object(self_model.time, 'monotonic', side_effect=(0., 10.)):
            with self.assertRaises(PermissionError) as result:
                self.lock.__exit__(None, None, None)
        self.assertIs(result.exception, error)
        self.assertTrue(self.path.exists())
        self.assertFalse(self.lock.held)
        self.assertIsNone(self_model._held_here(self.path))

    @unittest.skipUnless(os.name == 'nt', 'native Windows FILE_SHARE_DELETE semantics')
    def test_real_windows_reader_can_close_after_the_first_delete_refusal(self):
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        api.CloseHandle.restype = wintypes.BOOL
        handle = api.CreateFileW(str(self.path), 0x80000000, 1 | 2, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        original = Path.unlink
        collisions = []
        def close_reader():
            nonlocal handle
            if handle is not None:
                self.assertTrue(api.CloseHandle(handle))
                handle = None
        def remove(path, *args, **kwargs):
            try:
                return original(path, *args, **kwargs)
            except PermissionError as error:
                if path == self.path:
                    collisions.append(error.winerror)
                    close_reader()
                raise
        try:
            with mock.patch.object(Path, 'unlink', new=remove):
                self.lock.__exit__(None, None, None)
        finally:
            close_reader()
        self.assertEqual(collisions, [32])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.lock.held)
        self.assertFalse(self.lock.lost_to_takeover)


if __name__ == '__main__':
    unittest.main()
