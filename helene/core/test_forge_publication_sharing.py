"""Exercise journal publishers only: no Forge command/agent/process is started."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forge_process
import forge_verify
import forge_swarm
import forge_runtime

PUBLISHERS = (forge_process._write, forge_verify._write, forge_swarm._atomic)


class PublicationSharing(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows sharing handles')
    def test_real_deny_delete_reader_can_close_before_each_publication_finishes(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        for publish in PUBLISHERS:
            with self.subTest(publisher=publish.__module__), tempfile.TemporaryDirectory() as raw:
                path = Path(raw)/'state.json'
                path.write_text('{"state":"old"}', encoding='utf-8')
                handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0, None)
                self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
                released = threading.Event()
                def close():
                    kernel.CloseHandle(handle)
                    released.set()
                timer = threading.Timer(.08, close)
                timer.start()
                try:
                    publish(path, {'state':'complete', 'text':'Проба'})
                    self.assertTrue(released.is_set())
                    self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'state':'complete', 'text':'Проба'})
                finally:
                    timer.join()
                self.assertEqual(list(Path(raw).glob('*.tmp')), [])

    def test_persistent_sharing_denial_keeps_original_and_removes_staging(self):
        error = PermissionError('synthetic sharing denial'); error.winerror = 32
        for publish in PUBLISHERS:
            with self.subTest(publisher=publish.__module__), tempfile.TemporaryDirectory() as raw:
                path = Path(raw)/'state.json'; original = b'{"state":"old"}'
                path.write_bytes(original)
                with patch.object(forge_runtime.os, 'replace', side_effect=error), \
                     patch.object(forge_runtime.time, 'monotonic', side_effect=(0, 3)):
                    with self.assertRaises(PermissionError):
                        publish(path, {'state':'new'})
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(Path(raw).glob('*.tmp')), [])

    def test_unrelated_error_propagates_without_retry(self):
        for publish in PUBLISHERS:
            with self.subTest(publisher=publish.__module__), tempfile.TemporaryDirectory() as raw:
                path = Path(raw)/'state.json'; path.write_bytes(b'old')
                with patch.object(forge_runtime.os, 'replace', side_effect=OSError('disk failure')) as replace:
                    with self.assertRaises(OSError):
                        publish(path, {'state':'new'})
                self.assertEqual(replace.call_count, 1)
                self.assertEqual(path.read_bytes(), b'old')
                self.assertEqual(list(Path(raw).glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
