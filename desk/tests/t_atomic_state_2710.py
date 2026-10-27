"""Atomic state replacement survives Windows readers without losing old bytes."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from localharness import package_notice, self_review


class AtomicState(unittest.TestCase):
    def test_clean_transition_accepts_cached_previous_package_notice(self):
        # The running public updater keeps its old package_notice cached while
        # it points localharness.__path__ at the incoming app for validation.
        script = '''
import json, tempfile
from pathlib import Path
from unittest import mock
from localharness import package_notice, code_transition, update_source
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    package_notice.write(root / 'code-transition-plan.json',
                         {'format': 'helene-clean-code-plan.v1'})
    del package_notice.replace_with_retry
    with mock.patch.object(update_source, 'validate_clean', return_value='validated') as check:
        assert code_transition.validate(root) == 'validated'
        check.assert_called_once()
'''
        completed = subprocess.run([sys.executable, '-B', '-c', script],
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_transient_windows_denial_retries_both_writers(self):
        original = os.replace
        denial = PermissionError('sharing violation')
        denial.winerror = 32
        for writer in (package_notice.write, self_review._write):
            with self.subTest(writer=writer.__module__), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'request.json'
                writer(path, {'old': True})
                attempts = []
                def replace(source, target):
                    attempts.append(source)
                    if len(attempts) < 3:
                        self.assertEqual(json.loads(path.read_text()), {'old': True})
                        raise denial
                    return original(source, target)
                with mock.patch.object(os, 'replace', side_effect=replace):
                    writer(path, {'new': True})
                self.assertEqual(len(attempts), 3)
                self.assertEqual(json.loads(path.read_text()), {'new': True})
                self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_other_permission_error_is_immediate_and_preserves_old_bytes(self):
        for writer in (package_notice.write, self_review._write):
            with self.subTest(writer=writer.__module__), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'request.json'
                writer(path, {'old': True})
                before = path.read_bytes()
                with mock.patch.object(os, 'replace', side_effect=PermissionError('denied')) as replace:
                    with self.assertRaises(PermissionError):
                        writer(path, {'new': True})
                self.assertEqual(replace.call_count, 1)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_permanent_windows_denial_expires_without_truncation(self):
        denial = PermissionError('access denied'); denial.winerror = 5
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'request.json'; package_notice.write(path, {'old': True})
            before = path.read_bytes()
            with mock.patch.object(os, 'replace', side_effect=denial), \
                 mock.patch.object(package_notice.time, 'monotonic', side_effect=[0, 0, 4]), \
                 mock.patch.object(package_notice.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    package_notice.write(path, {'new': True})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    @unittest.skipUnless(os.name == 'nt', 'native Windows sharing semantics')
    def test_real_windows_reader_releases_then_write_completes(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        for writer in (package_notice.write, self_review._write):
            with self.subTest(writer=writer.__module__), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'request.json'; writer(path, {'old': True})
                handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0, None)
                self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
                timer = threading.Timer(.15, lambda: kernel.CloseHandle(handle)); timer.start()
                try:
                    writer(path, {'new': True})
                    self.assertEqual(json.loads(path.read_text()), {'new': True})
                finally:
                    timer.join()


if __name__ == '__main__':
    unittest.main()
