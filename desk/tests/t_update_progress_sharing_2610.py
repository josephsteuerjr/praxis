"""Actual in-process Windows reader/rename ordering, no application/subprocesses."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import update_progress as progress, update_window


class ProgressSharing(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='progress-sharing-', dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / 'application'
        self.target.mkdir()
        self.path = self.root / '.helene-update-test.progress.json'
        self.report = progress.Progress(self.path, 'test', self.target)
        self.report.phase('prepared')

    def test_ordinary_success_complete_unicode_snapshot_and_bound_identity(self):
        self.report.phase('preimages', detail='Сохраняю прежние байты')
        self.assertEqual(progress.read_json(self.path)['detail'], 'Сохраняю прежние байты')
        self.assertEqual(progress.Progress(self.path).value, progress.status(self.target))
        pointer = progress.pointer(self.target)
        progress.atomic_json(pointer, {'receipt': str(self.path), 'id': 'other'})
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            progress.status(self.target)

    def test_publication_waits_for_reader_close_and_keeps_complete_snapshots(self):
        old = self.path.read_bytes()
        attempted = threading.Event()
        published = threading.Event()
        failures = []
        def publish():
            attempted.set()
            try:self.report.phase('preimages')
            except BaseException as exc:failures.append(exc)
            finally:published.set()
        with progress._open_reader(self.path) as reader:
            thread = threading.Thread(target=publish, name='progress-publish-test')
            thread.start()
            self.assertTrue(attempted.wait(3))
            if os.name == 'nt':self.assertFalse(published.wait(.05))
            self.assertEqual(json.load(reader), json.loads(old))
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(progress.read_json(self.path)['stage'], 'preimages')
        self.assertFalse(self.path.with_suffix('.json.tmp').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows deny-delete negative control')
    def test_plain_reader_reproduces_denied_replace_and_does_not_lose_old_bytes(self):
        old = self.path.read_bytes()
        # Hold the previous production Path.read_text opening mode at the actual
        # overlap rather than hoping a fast poll happens to hit the race.
        with self.path.open('r', encoding='utf-8'):
            with self.assertRaises(PermissionError) as failure:
                self.report.phase('preimages')
        self.assertIn(failure.exception.winerror, (5, 32, 33))
        self.assertEqual(self.path.read_bytes(), old)
        temporary = self.path.with_suffix('.json.tmp')
        self.assertEqual(json.loads(temporary.read_text(encoding='utf-8'))['stage'], 'preimages')
        self.report.save()
        self.assertEqual(progress.read_json(self.path)['stage'], 'preimages')

    def test_nonsharing_publication_errors_are_not_retried_or_swallowed(self):
        old = self.path.read_bytes()
        denied = PermissionError('ACL denied')
        denied.winerror = 5
        for error in (denied, OSError('disk failure')):
            with self.subTest(error=type(error).__name__):
                with patch.object(Path, 'replace', side_effect=error) as replace:
                    with self.assertRaises(type(error)) as failure:
                        self.report.phase('preimages')
                self.assertIs(failure.exception, error)
                replace.assert_called_once()
                self.assertEqual(self.path.read_bytes(), old)

    def test_read_failure_preserves_error_and_releases_reader(self):
        self.path.write_text('{not json', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            progress.read_json(self.path)
        # Exclusive open proves the failed reader closed on Windows too;
        # successful replacement alone would not prove closure with share-delete.
        if os.name == 'nt':
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.CreateFileW(str(self.path), 0x80000000, 0, None, 3, 0x80, None)
            self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
            kernel.CloseHandle(handle)
        with self.assertRaises(FileNotFoundError):
            progress.read_json(self.root / 'absent.json')

    def test_native_bridge_holds_old_snapshot_while_worker_publishes_new_one(self):
        ready = threading.Event()
        reader_open = threading.Event()
        published = threading.Event()
        attempted = threading.Event()
        active = threading.Event()
        windows = {}
        actual_open = progress._open_reader

        @contextmanager
        def held(path):
            with actual_open(path) as stream:
                if active.is_set() and threading.current_thread() is threading.main_thread():
                    reader_open.set()
                    self.assertTrue(attempted.wait(3))
                    if os.name == 'nt':self.assertFalse(published.wait(.05))
                yield stream

        class Window:
            def destroy(self):
                windows['destroyed'] = True

        def create(*args, **kwargs):
            windows.update(kwargs)
            return Window()

        def start(**kwargs):
            windows['js_api'].ready()
            kwargs['func']()
            active.set()
            self.assertEqual(windows['js_api'].status()['stage'], 'prepared')
            active.clear()
            self.assertTrue(published.wait(3))
            self.assertEqual(windows['js_api'].status()['stage'], 'completed')

        def transaction():
            self.assertTrue(ready.is_set())
            self.assertTrue(reader_open.wait(3), 'bridge did not use shared reader')
            attempted.set()
            try:
                self.report.phase('completed')
            finally:
                published.set()
            return 'complete'

        fake = SimpleNamespace(create_window=create, start=start)
        with patch.dict(sys.modules, {'webview': fake}), \
             patch.object(progress, '_open_reader', side_effect=held), \
             patch.object(update_window.time, 'sleep'):
            self.assertEqual(update_window.run(transaction, self.path, ready), 'complete')
        self.assertTrue(windows['destroyed'])
        self.assertFalse(any(t.name == 'update-transaction' and t.is_alive() for t in threading.enumerate()))

    @unittest.skipUnless(os.name == 'nt', 'Windows handle census')
    def test_repeated_successful_readers_release_handles(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        def count():
            value = wintypes.DWORD()
            self.assertTrue(kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(value)))
            return value.value
        progress.read_json(self.path)  # import/runtime initialization precedes census
        before = count()
        for _ in range(200):
            progress.read_json(self.path)
        self.assertLessEqual(count(), before + 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
