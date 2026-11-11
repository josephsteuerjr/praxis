"""Cleanup of a previous installation never asks for administrator rights (11.11.2026).

Yegor: «очистка предыдущей установки приложения не должна запрашивать административные права.
Если старые файлы защищены, новая установка продолжается, остатки явно видны пользователю.»

Custody is not cleanup: stopping the old programme and detaching what could start it again as a
second data writer (its Windows service, a scheduled task bound to it) keep their existing rules.
Files, links and the cosmetic «Apps» entry are removed with the user's own rights; whatever Windows
protects stays in place and becomes a message for the person. Any other remainder still fails.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import migration_windows as windows  # noqa: E402
from localharness import first_start  # noqa: E402

NO_UAC = AssertionError('cleanup must not ask for administrator rights')


def refuse(path_to_refuse):
    """Path.unlink that Windows refuses for one exact file."""
    original = Path.unlink

    def unlink(self, *args, **kwargs):
        if Path(self) == Path(path_to_refuse):
            raise PermissionError(13, 'Access is denied', str(self))
        return original(self, *args, **kwargs)
    return unlink


class ProgramFiles(unittest.TestCase):
    def note(self, old, data):
        return {'startup_proof': {'tree': str(data)}, 'data_paths': [str(data)], 'old_data_paths': []}

    def test_protected_file_stays_named_and_nothing_is_elevated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old = root / 'old'; new = root / 'new'; data = root / 'data'
            for folder in (old / 'app', old / 'runtime', new, data):
                folder.mkdir(parents=True)
            (old / 'helene.exe').write_bytes(b'launcher')
            (old / 'app/python_app.py').write_text('app')
            (old / 'runtime/python.dll').write_bytes(b'dll')
            (old / 'helene.json').write_text('{}')
            locked = old / 'runtime/python.dll'
            with patch.object(windows, 'system_action', side_effect=NO_UAC), \
                    patch.object(windows, 'run_privileged', side_effect=NO_UAC), \
                    patch.object(Path, 'unlink', refuse(locked)):
                result = windows.remove_program(old, new, self.note(old, data))
            self.assertFalse(result['ok'])
            self.assertEqual(result['retained_count'], 1)
            self.assertEqual(result['retained_files'], ['runtime/python.dll'])
            self.assertIn('без прав администратора', result['message'])
            self.assertTrue(locked.exists())
            self.assertFalse((old / 'helene.exe').exists(), 'everything else is still removed')
            self.assertTrue((old / 'helene.json').exists(), 'settings stay as before')

    def test_launcher_left_behind_is_called_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old = root / 'old'; new = root / 'new'; data = root / 'data'
            for folder in (old, new, data):
                folder.mkdir()
            (old / 'helene.exe').write_bytes(b'launcher')
            with patch.object(windows, 'system_action', side_effect=NO_UAC), \
                    patch.object(Path, 'unlink', refuse(old / 'helene.exe')):
                result = windows.remove_program(old, new, self.note(old, data))
            self.assertIn('helene.exe', result['retained_program_entries'])
            self.assertIn('не запускай', result['message'])


class Links(unittest.TestCase):
    def test_protected_shortcut_is_reported_not_elevated(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / 'Helene.lnk'; link.write_bytes(b'lnk')
            retained = []
            with patch.object(windows, 'owned_shortcuts', return_value=[link]), \
                    patch.object(windows, 'unpin_shortcut'), \
                    patch.object(windows, 'run_privileged', side_effect=NO_UAC), \
                    patch.object(Path, 'unlink', refuse(link)):
                removed = windows.remove_shortcuts(Path(tmp) / 'old', retained=retained)
            self.assertEqual(removed, [])
            self.assertEqual(len(retained), 1)
            self.assertIn(str(link), retained[0])


class SystemHooks(unittest.TestCase):
    def test_uninstall_entry_tries_user_rights_then_becomes_a_leftover(self):
        calls = []

        def action(old, name, files=None, *, elevate=True):
            calls.append((name, elevate))
            if name == 'registration':
                raise PermissionError(5,'access denied')
        retained = []
        with patch.object(windows, 'system_registrations', return_value={'uninstall'}), \
                patch.object(windows, 'service', return_value=None), \
                patch.object(windows, 'system_action', side_effect=action):
            windows.unhook(Path('C:/old'), retained)
        self.assertEqual(calls, [('registration', False)])
        self.assertEqual(retained, [windows.REGISTRATION_LEFTOVER])

    def test_task_and_service_retirement_also_uses_current_rights(self):
        for found, svc in (({'task'}, None), (set(), {'state': 'Stopped'})):
            calls = []
            with patch.object(windows, 'system_registrations', return_value=found), \
                    patch.object(windows, 'service', return_value=svc), \
                    patch.object(windows, 'system_action', side_effect=lambda old, name, files=None, *, elevate=True: calls.append((name, elevate))):
                windows.unhook(Path('C:/old'), [])
            self.assertEqual(calls, [('custody', False)], (found, svc))

    def test_registration_action_without_elevation_never_reaches_uac(self):
        with patch.object(windows, 'os', type('nt', (), {**{k: getattr(os, k) for k in dir(os) if not k.startswith('__')}, 'name': 'nt'})), \
                patch.object(windows, 'admin', return_value=False), \
                patch.object(windows, 'powershell', side_effect=RuntimeError('access denied')), \
                patch.object(windows, 'run_privileged', side_effect=NO_UAC):
            with self.assertRaises(RuntimeError):
                windows.system_action(Path('C:/old'), 'registration', elevate=False)


@unittest.skipUnless(os.name == 'nt', 'Windows first-start backend')
class FirstStartBackend(unittest.TestCase):
    def test_protected_file_inside_a_folder_leaves_only_itself(self):
        from localharness.windows_first_start import Backend
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'old'; (folder / 'sub').mkdir(parents=True)
            keep = folder / 'sub/locked.dll'; keep.write_bytes(b'x')
            (folder / 'free.txt').write_text('y'); (folder / 'sub/free.dll').write_bytes(b'z')
            real_unlink = os.unlink

            def unlink(path, *args, **kwargs):
                if Path(path) == keep:
                    raise PermissionError(13, 'Access is denied', str(path))
                return real_unlink(path, *args, **kwargs)
            with patch('os.unlink', unlink):
                kept = Backend._remove_what_windows_allows(folder)
            self.assertEqual(kept, [keep])
            self.assertTrue(keep.exists())
            self.assertFalse((folder / 'free.txt').exists())
            self.assertFalse((folder / 'sub/free.dll').exists())

    def test_backend_cleanup_paths_have_no_elevation(self):
        import inspect
        from localharness import windows_first_start
        for name in ('prune', '_remove_captured', 'retire', 'shortcuts'):
            fragment = inspect.getsource(getattr(windows_first_start.Backend, name))
            self.assertNotIn("privileged('remove'", fragment, name)
            self.assertNotIn("privileged('prune'", fragment, name)
            self.assertNotIn('run_privileged', fragment, name)
            self.assertNotIn("system_action(old,'cleanup')", fragment, name)
        for name in ('stop','release','data_access','hooks'):
            self.assertNotIn('self.privileged(',inspect.getsource(getattr(windows_first_start.Backend,name)),name)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() != 0,
                     'POSIX permission refusal needs a non-root user')
class PosixProgramFiles(unittest.TestCase):
    def test_file_in_a_read_only_folder_stays_named(self):
        from localharness import migration_posix as posix
        import stat
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old = root / 'old'; new = root / 'new'; data = root / 'data'
            for folder in (old / 'app', old / 'runtime', new, data):
                folder.mkdir(parents=True)
            (old / 'app/python_app.py').write_text('app')
            (old / 'runtime/lib.so').write_bytes(b'lib')
            (old / 'runtime').chmod(stat.S_IRUSR | stat.S_IXUSR)
            try:
                with patch.object(posix, '_run', side_effect=NO_UAC):
                    result = posix.remove_program(old, new, {'startup_proof': {'tree': str(data)},
                                                             'data_paths': [str(data)]})
            finally:
                (old / 'runtime').chmod(stat.S_IRWXU)
            self.assertFalse(result['ok'])
            self.assertEqual(result['retained_files'], ['runtime/lib.so'])
            self.assertIn('без прав администратора', result['message'])
            self.assertFalse((old / 'app/python_app.py').exists())
            self.assertTrue((old / 'runtime/lib.so').exists())


class Leftovers(unittest.TestCase):
    class Stub:
        def __init__(self, leftovers=(), notes=()):
            self.leftovers = list(leftovers); self.notes = list(notes)

    def test_remainder_explained_by_protected_paths_is_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / 'old'; old.mkdir(); (old / 'locked.dll').write_bytes(b'x')
            backend = self.Stub([old / 'locked.dll'], ['note about a link'])
            messages = first_start.cleanup_leftovers(backend, [old])
            self.assertEqual(messages[0], 'note about a link')
            self.assertIn(str(old), messages[1])
            self.assertIn('не запускай', messages[1])

    def test_unexplained_remainder_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / 'old'; old.mkdir(); (old / 'x').write_text('x')
            with self.assertRaises(RuntimeError):
                first_start.cleanup_leftovers(self.Stub(), [old])

    def test_removed_roots_need_no_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(first_start.cleanup_leftovers(self.Stub(), [Path(tmp) / 'gone']), [])


class Finish(unittest.TestCase):
    def test_leftovers_do_not_block_retirement_and_are_visible(self):
        from localharness import windows_installation as install
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old = root / 'old'; new = root / 'new'
            old.mkdir(); new.mkdir()
            note = {'state': 'new_app_started', 'startup_proof': {'tree': str(root / 'data')},
                    'old_root': str(old), 'data_paths': [str(root / 'data')]}
            calls = []
            lifecycle = type('L', (), {
                'cleanup_installation': staticmethod(lambda o, n: {'retained': ['link left']}),
                'remove_program': staticmethod(lambda o, n, nt: calls.append('remove') or
                                               {'ok': False, 'retained_count': 2, 'message': 'two files left'}),
                'install_shortcuts': staticmethod(lambda n, desktop_only=True: [])})
            with patch.object(install, 'migration_windows', lifecycle), \
                    patch.object(install.app_migration, 'write_note'):
                result = install.finish(new, note, remove_old=True, progress=lambda *_: None)
            self.assertEqual(calls, ['remove'], 'a leftover link does not stop file removal')
            self.assertEqual(result['state'], 'retirement_incomplete')
            self.assertEqual(result['cleanup_errors'], ['link left', 'two files left'])


class DataCopy(unittest.TestCase):
    def test_protected_original_data_is_reported_not_elevated(self):
        from localharness import data_relocation as relocation
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'src'; target = Path(tmp) / 'dst'
            source.mkdir(); target.mkdir()
            (source / 'a.txt').write_text('a'); (source / 'b.txt').write_text('b')
            files, dirs = relocation._inventory(source)
            info = target.stat()
            transfer = {'destination': str(target.resolve()), 'roots': [{
                'source': str(source), 'destination': str(target), 'source_signatures': files,
                'directories': dirs, 'destination_identity': [info.st_dev, info.st_ino]}]}
            with patch('localharness.migration_windows.system_action', side_effect=NO_UAC), \
                    patch.object(Path, 'unlink', refuse(source / 'b.txt')):
                result = relocation.retire_sources(transfer, startup_proof={'tree': str(target)})
            self.assertEqual(result['state'], 'source_retained')
            self.assertIn('без прав администратора', result['errors'][0])
            self.assertTrue((source / 'b.txt').exists())
            self.assertFalse((source / 'a.txt').exists())


if __name__ == '__main__':
    unittest.main()
