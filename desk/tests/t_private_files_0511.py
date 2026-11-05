"""Synthetic profiles only. Native Windows descriptor and atomicity acceptance."""
from pathlib import Path
import ctypes
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import private_files as pf


class PrivateFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='helene private synthetic ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'профиль с пробелами.json'
        self.target.write_bytes(b'previous synthetic profile')

    def test_replacement_and_wrong_or_missing_username(self):
        for username in ('definitely-not-the-token-user', ''):
            with self.subTest(username=bool(username)), mock.patch.dict(os.environ, {'USERNAME': username}):
                pf.write_private(self.target, b'synthetic new secret')
                self.assertEqual(self.target.read_bytes(), b'synthetic new secret')
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_verification_refusal_precedes_first_secret_write(self):
        before = self.target.read_bytes()
        with mock.patch.object(pf, '_verify', side_effect=pf.PrivateFileError('injected-descriptor-refusal')), \
             mock.patch.object(pf.os, 'fdopen', wraps=os.fdopen) as stream:
            with self.assertRaises(pf.PrivateFileError):
                pf.write_private(self.target, b'NEVER-WRITTEN-SYNTHETIC')
        stream.assert_not_called()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_replace_refusal_is_sanitized_and_original_is_recoverable(self):
        before = self.target.read_bytes()
        error = PermissionError(13, 'SYNTHETIC-SECRET-AND-PATH-MUST-NOT-LEAK', str(self.target))
        with mock.patch.object(pf.os, 'replace', side_effect=error):
            with self.assertRaises(pf.PrivateFileError) as caught:
                pf.write_private(self.target, b'synthetic replacement')
        self.assertEqual(caught.exception.operation, 'replace')
        self.assertEqual(caught.exception.errno, 13)
        self.assertNotIn('SYNTHETIC', str(caught.exception))
        self.assertNotIn(str(self.target), str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.target])
        pf.write_private(self.target, b'replayed synthetic replacement')
        self.assertEqual(self.target.read_bytes(), b'replayed synthetic replacement')

    @unittest.skipUnless(os.name == 'posix', 'native POSIX semantics; run on Linux/macOS gate')
    def test_posix_modes_before_write_and_after_replace(self):
        observed = []
        def empty(path):
            p = Path(path)
            observed.append(p.read_bytes())
            self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(p.stat().st_uid, os.geteuid())
        self.target.chmod(0o666)
        pf._atomic_write(self.target, b'synthetic', before_write=empty)
        self.assertEqual(observed, [b''])
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == 'nt', 'native Windows descriptor')
    def test_failed_exclusive_creation_never_cleans_an_existing_directory(self):
        from types import SimpleNamespace
        identity = 'a'*32
        existing = self.root / ('.private-' + identity)
        existing.mkdir()
        before = self.target.read_bytes()
        with mock.patch.object(pf.uuid, 'uuid4', return_value=SimpleNamespace(hex=identity)):
            with self.assertRaisesRegex(pf.PrivateFileError, 'create-directory'):
                pf.write_private(self.target, b'NEVER-WRITTEN-SYNTHETIC')
        self.assertTrue(existing.is_dir())
        self.assertEqual(self.target.read_bytes(), before)

    @unittest.skipUnless(os.name == 'posix', 'native setgid inheritance')
    def test_private_staging_under_setgid_server_directory(self):
        self.root.chmod(0o2770)
        observed = []
        def before(path):
            stage = Path(path)
            observed.append(stage.read_bytes())
            self.assertEqual(stat.S_IMODE(stage.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(stage.stat().st_mode), 0o600)
        pf._atomic_write(self.target, b'synthetic setgid replacement', before_write=before)
        self.assertEqual(observed, [b''])
        self.assertEqual(self.target.read_bytes(), b'synthetic setgid replacement')
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == 'nt', 'native Windows descriptor')
    def test_native_descriptor_is_exact_on_final_replacement(self):
        pf.write_private(self.target, b'synthetic')
        # Independent OS observer: expectation derives from the actual token,
        # with deduplication when the user itself is a service/well-known SID.
        ps = '''$ErrorActionPreference='Stop';
        $a=Get-Acl -LiteralPath $env:PRIVATE_FILE_PROBE;
        $expected=@(@([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18','S-1-5-32-544')|Select-Object -Unique);
        $entries=@($a.Access);$actual=@($entries|ForEach-Object {$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value});
        @{protected=$a.AreAccessRulesProtected; exact=(@(Compare-Object $expected $actual).Count -eq 0);
          count=$entries.Count;expected_count=$expected.Count;
          invalid=@($entries|Where-Object {$_.IsInherited -or $_.AccessControlType -ne 'Allow' -or $_.FileSystemRights -ne 'FullControl'}).Count}|ConvertTo-Json -Compress'''
        value = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True,
            env={**os.environ, 'PRIVATE_FILE_PROBE': str(self.target), 'PSModulePath': str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules')}, timeout=30)
        self.assertEqual(value.returncode, 0, value.stderr)
        facts = json.loads(value.stdout)
        self.assertTrue(facts['protected']); self.assertTrue(facts['exact'])
        self.assertEqual(facts['count'], facts['expected_count']); self.assertEqual(facts['invalid'], 0)

    @unittest.skipUnless(os.name == 'nt', 'native Windows descriptor refusal')
    def test_native_insecure_descriptor_refused_before_bytes(self):
        before = self.target.read_bytes()
        original = pf._Windows._descriptor
        # Give the actual empty FILE a broad descriptor, then let the real
        # GetSecurityInfo/GetAce verifier refuse it. The directory remains private.
        def insecure(windows):
            descriptor = original(windows)
            windows.k.LocalFree(descriptor)
            descriptor = ctypes.c_void_p()
            windows._check(windows.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                'D:P(A;;FA;;;WD)', 1, ctypes.byref(descriptor), None), 'test-descriptor')
            return descriptor
        original_open = pf._Windows.open
        def altered(windows, path, **kwargs):
            if kwargs.get('create') and not kwargs.get('directory'):
                with mock.patch.object(windows, '_descriptor', lambda: insecure(windows)):
                    return original_open(windows, path, **kwargs)
            return original_open(windows, path, **kwargs)
        with mock.patch.object(pf._Windows, 'open', altered), mock.patch.object(pf.os, 'fdopen') as stream:
            with self.assertRaisesRegex(pf.PrivateFileError, 'unexpected-principals'):
                pf.write_private(self.target, b'NEVER-WRITTEN-SYNTHETIC')
        stream.assert_not_called()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.target])

    @unittest.skipUnless(os.name == 'nt', 'native restricted effective token')
    def test_refused_thread_token_never_falls_back_to_process_identity(self):
        windows = pf._Windows()
        def refused(*args):
            ctypes.set_last_error(5)
            return False
        with mock.patch.object(windows.a, 'OpenThreadToken', side_effect=refused), \
             mock.patch.object(windows.a, 'OpenProcessToken') as process:
            with self.assertRaisesRegex(pf.PrivateFileError, 'thread-token.*winerror=5'):
                windows._user()
        process.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'native restricted effective token')
    def test_unprivileged_effective_token_with_false_username(self):
        from ctypes import wintypes as w
        windows = pf._Windows(); a, k = windows.a, windows.k
        # The synthetic fixture must itself belong to the token user. The host's
        # default temp DACL can grant only Administrators, which would test the
        # fixture's directory refusal instead of unprivileged private writing.
        handle = windows.open(self.root, protect=True, directory=True)
        try:
            windows.protect(handle)
        finally:
            k.CloseHandle(handle)
        pf.write_private(self.target, b'previous synthetic profile')
        class SidAndAttributes(ctypes.Structure):
            _fields_ = [('sid', ctypes.c_void_p), ('attributes', w.DWORD)]
        a.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        a.ConvertStringSidToSidW.restype = w.BOOL
        a.CreateRestrictedToken.argtypes = [w.HANDLE,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,ctypes.c_void_p,w.DWORD,ctypes.c_void_p,ctypes.POINTER(w.HANDLE)]
        a.CreateRestrictedToken.restype = w.BOOL
        a.ImpersonateLoggedOnUser.argtypes = [w.HANDLE]; a.ImpersonateLoggedOnUser.restype = w.BOOL
        a.CheckTokenMembership.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.POINTER(w.BOOL)]; a.CheckTokenMembership.restype = w.BOOL
        a.RevertToSelf.argtypes = []; a.RevertToSelf.restype = w.BOOL
        token, restricted, admin = w.HANDLE(), w.HANDLE(), ctypes.c_void_p()
        windows._check(a.OpenProcessToken(k.GetCurrentProcess(), 0xB, ctypes.byref(token)), 'test-token')
        try:
            windows._check(a.ConvertStringSidToSidW('S-1-5-32-544', ctypes.byref(admin)), 'test-admin-sid')
            disable = SidAndAttributes(admin, 0)
            windows._check(a.CreateRestrictedToken(token, 1, 1, ctypes.byref(disable), 0, None, 0, None, ctypes.byref(restricted)), 'test-restrict')
            windows._check(a.ImpersonateLoggedOnUser(restricted), 'test-impersonate')
            try:
                member = w.BOOL()
                windows._check(a.CheckTokenMembership(None, admin, ctypes.byref(member)), 'test-membership')
                self.assertFalse(member.value, 'test must not execute with enabled Administrators membership')
                with mock.patch.dict(os.environ, {'USERNAME':'wrong synthetic account'}):
                    pf.write_private(self.target, b'restricted synthetic secret')
                self.assertEqual(self.target.read_bytes(), b'restricted synthetic secret')
            finally:
                windows._check(a.RevertToSelf(), 'test-revert')
        finally:
            if restricted: k.CloseHandle(restricted)
            k.CloseHandle(token)
            if admin: k.LocalFree(admin)


if __name__ == '__main__':
    unittest.main(verbosity=2)
