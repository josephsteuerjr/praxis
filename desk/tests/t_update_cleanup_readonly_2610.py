"""Tiny owned staging cleanup consumers; no application/model/Forge launches."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import python_source_updater as updater, update_io


class CleanupReadonly(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cleanup-readonly-2610-', dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.old = self.root / 'application'
        self.old.mkdir()
        self.staging = self.root / '.helene-update-cleanup-fixture'
        self.new = self.staging / 'Helene'
        self.recovery = self.root / '.helene-recovery-cleanup-fixture'
        self.recovery.mkdir()
        self.script = self.recovery / 'cleanup-update.py'
        self.script.write_bytes(Path(updater.__file__).read_bytes())
        # The real CLI prepends this app path. There is deliberately no parent
        # PYTHONPATH fallback: cleanup must import before deleting its own source.
        package = self.new / 'app/localharness'
        package.mkdir(parents=True)
        (package / '__init__.py').write_bytes(b'')
        self.io_source = package / 'update_io.py'
        self.io_source.write_bytes(Path(update_io.__file__).read_bytes())
        self.object = self.new / 'code/authored/.git/objects/00/synthetic-object'
        self.object.parent.mkdir(parents=True)
        self.object.write_bytes(b'synthetic Git object, not a user repository')
        self.archives = [self.root / 'download.zip', self.root / 'download.sha256']
        for file in self.archives:file.write_bytes(b'owned disposable download')
        self.outside = {
            self.old / 'memory/identity.md': b'outside memory',
            self.old / 'code/.git/objects/00/retained': b'outside authored Git',
            self.recovery / 'preimage.bin': b'unique recovery bytes',
            self.root / 'unrelated.txt': b'not updater staging',
        }
        for file, data in self.outside.items():
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        self.args = SimpleNamespace(old=self.old, new=self.new, staging=self.staging,
                                    archive=self.archives, pid=0, cleanup=True, browser=False)

    def run_cli(self):
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        command = [sys.executable, '-B', str(self.script), '--old', str(self.old),
                   '--new', str(self.new), '--staging', str(self.staging),
                   '--pid', '0', '--cleanup']
        for file in self.archives:command.extend(['--archive', str(file)])
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=15)
        return result

    def assert_outside_unchanged(self):
        for file, data in self.outside.items():self.assertEqual(file.read_bytes(), data)

    def assert_removed(self):
        self.assertFalse(self.staging.exists())
        for file in self.archives:self.assertFalse(file.exists())
        self.assert_outside_unchanged()

    def test_actual_cli_ordinary_cleanup_removes_only_owned_staging_and_downloads(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_removed()

    @unittest.skipUnless(os.name == 'nt', 'Windows read-only deletion semantics')
    def test_old_rmtree_failure_then_actual_cli_removes_readonly_git_object(self):
        self.object.chmod(stat.S_IREAD)
        # Same CLI/fixture with only the former failing rmtree invocation.
        corrected = self.script.read_text(encoding='utf-8')
        call = 'shutil.rmtree(staging, onerror=remove_readonly_file)'
        self.assertEqual(corrected.count(call), 1)
        self.script.write_text(corrected.replace(call, 'shutil.rmtree(staging)'), encoding='utf-8')
        negative = self.run_cli()
        self.assertNotEqual(negative.returncode, 0)
        self.assertIn('WinError 5', negative.stderr)
        self.assertTrue(self.object.is_file())
        self.assertFalse(self.object.stat().st_mode & stat.S_IWRITE)
        for file in self.archives:self.assertTrue(file.is_file())
        self.assert_outside_unchanged()
        # Negative rmtree may already remove app/update_io; restore only this
        # fixture's app files, then run the unchanged corrected CLI.
        self.io_source.parent.mkdir(parents=True, exist_ok=True)
        (self.io_source.parent / '__init__.py').write_bytes(b'')
        self.io_source.write_bytes(Path(update_io.__file__).read_bytes())
        self.script.write_text(corrected, encoding='utf-8')
        positive = self.run_cli()
        self.assertEqual(positive.returncode, 0, positive.stderr)
        self.assert_removed()
        print(json.dumps({'consumer': '--cleanup', 'old_exit': negative.returncode,
                          'old_winerror': 5, 'corrected_exit': positive.returncode,
                          'outside_sha256': {str(p.relative_to(self.root)): hashlib.sha256(v).hexdigest()
                                             for p, v in self.outside.items()}}, sort_keys=True))

    @unittest.skipUnless(os.name == 'nt', 'Windows real deny-delete handle')
    def test_actual_cli_real_writable_sharing_failure_keeps_archives(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.assertTrue(self.object.stat().st_mode & stat.S_IWRITE)
        handle = kernel.CreateFileW(str(self.object), 0x80000000, 1, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            result = self.run_cli()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('PermissionError', result.stderr)
            self.assertTrue(self.object.is_file())
            for file in self.archives:self.assertTrue(file.is_file())
            self.assert_outside_unchanged()
        finally:
            self.assertTrue(kernel.CloseHandle(handle))

    def test_other_deletion_errors_propagate_exactly_without_deleting_archives(self):
        for function, error in ((os.rmdir, PermissionError('directory denied')),
                                (os.unlink, OSError('disk failure'))):
            with self.subTest(function=function.__name__, error=type(error).__name__):
                def fail(path, *, onerror):
                    onerror(function, str(self.object), (type(error), error, None))
                with patch.object(updater.shutil, 'rmtree', side_effect=fail), \
                     patch.object(update_io, 'remove_created_file') as remove:
                    with self.assertRaises(type(error)) as failure:updater.perform(self.args)
                self.assertIs(failure.exception, error)
                remove.assert_not_called()
                for file in self.archives:self.assertTrue(file.is_file())
                self.assert_outside_unchanged()

    def test_owner_wait_failure_precedes_any_removal(self):
        error = TimeoutError('owner still alive')
        with patch.object(updater, 'wait_owner', side_effect=error) as wait, \
             patch.object(updater.shutil, 'rmtree') as remove:
            with self.assertRaises(TimeoutError) as failure:updater.perform(self.args)
        self.assertIs(failure.exception, error)
        wait.assert_called_once_with(0)
        remove.assert_not_called()
        self.assertTrue(self.staging.is_dir())
        for file in self.archives:self.assertTrue(file.is_file())

    def test_invalid_staging_and_archive_refuse_before_wait_or_deletion(self):
        for change in ({'new': self.old}, {'archive': [self.old / 'nested.zip']}):
            args = SimpleNamespace(**(vars(self.args) | change))
            with self.subTest(change=change), patch.object(updater, 'wait_owner') as wait, \
                 patch.object(updater.shutil, 'rmtree') as remove:
                with self.assertRaises(ValueError):updater.perform(args)
                wait.assert_not_called()
                remove.assert_not_called()
                self.assertTrue(self.staging.exists())
                for file in self.archives:self.assertTrue(file.is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
