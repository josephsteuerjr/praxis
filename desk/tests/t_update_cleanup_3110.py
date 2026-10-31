"""Pre-trial legacy sweep around an admitted portable update; fake OS authority.

Receipt protection here applies before the health trial. Retirement after the
trial has separate authority and is outside this API's tests.
"""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import t_first_start_3010 as fixture
from localharness import first_start as start, windows_first_start, windows_hooks, vision_policy


class _Socket:
    def __enter__(self):return self
    def __exit__(self, *args):return False
    def bind(self, address):pass
    def getsockname(self):return ('127.0.0.1', 32123)


class UpdateCleanup(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.FirstStart()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.data = self.fx.places.data
        self._put(self.data/'memory/groups/current.jsonl', b'current canonical conversation\n')
        self._put(self.data/'memory/.control/profile-identity.json', b'{"id":"current-profile","agent_name":"Current agent"}\n')
        self._put(self.data/'memory/.control/desk_inbox/current.md', b'current pending owner turn\n')
        self._put(self.data/'memory/.control/autonomy-paused.json', b'{"paused":true}\n')
        self._put(self.data/'memory/.state/personal.lock', b'owner state lock\n')
        self._put(self.data/'workspace/project/Cargo.lock', b'owner cargo dependencies\n')
        self._put(self.data/'workspace/project/poetry.lock', b'owner poetry dependencies\n')
        self._put(self.data/'workspace/project/uv.lock', b'owner uv dependencies\n')
        self._put(self.data/'workspace/project/personal.lck', b'owner personal lock\n')
        self._put(self.fx.root/'tree/merged.py', b'base=True\nauthored=True\nupstream=True\n')
        self._put(self.fx.root/'app/authored.py', b'current reviewed app edit\n')
        self._write_config(self.fx.root, {
            'mode': 'local', 'tree': str(self.data), 'code': 'tree',
            'setup_complete': True, 'agent': {'name': 'Current merged agent'},
            'model': {'framework': 'openai', 'model': 'current-model', 'key': 'current-private-fixture'},
            'relay': {'enabled': False}, 'images': {'enabled': False},
            'autonomy': {'paused': True}, 'port': 32122,
        })
        self._admit(self.fx.root)
        self.fx.backend.registered = lambda _places: []
        self.progress = []
        self.enterContext(patch.object(start.socket, 'socket', return_value=_Socket()))
        forbidden = AssertionError('Real Windows authority is forbidden in this test')
        for name in ('kernel', 'terminate', 'restart_holders', 'user_sid', 'Backend'):
            self.enterContext(patch.object(windows_first_start, name, side_effect=forbidden))
        self.enterContext(patch.object(windows_hooks, 'retire', side_effect=forbidden))
        def private_fixture_file(name):
            if not Path(name).resolve().is_relative_to(self.fx.base.resolve()):
                raise AssertionError('Private write leaves this synthetic fixture')
        self.enterContext(patch.object(vision_policy, '_protect_private_temp', side_effect=private_fixture_file))
        for name in ('run', 'Popen'):
            self.enterContext(patch.object(windows_first_start.subprocess, name, side_effect=forbidden))
        fake_psutil = SimpleNamespace(
            process_iter=Mock(side_effect=forbidden), Process=Mock(side_effect=forbidden),
            pid_exists=Mock(side_effect=forbidden), pids=Mock(side_effect=forbidden),
            NoSuchProcess=type('NoSuchProcess', (Exception,), {}),
            AccessDenied=type('AccessDenied', (Exception,), {}))
        self.enterContext(patch.dict(sys.modules, {'psutil': fake_psutil}))

    def _put(self, path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def _write_config(self, root, value):
        self._put(root/'helene.json', (json.dumps(value, ensure_ascii=False)+'\n').encode('utf-8'))

    def _admit(self, program, *, data=None, state='completed'):
        value = {'format': start.FORMAT, 'state': state,
            'completed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'data': str(self.data if data is None else data)}
        self._put(start.receipt(program), (json.dumps(value)+'\n').encode('utf-8'))

    def _snapshot(self, directory):
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob('*') if path.is_file()}

    def _baseline(self):
        return {'config': (self.fx.root/'helene.json').read_bytes(),
            'tree': self._snapshot(self.fx.root/'tree'),
            'app': self._snapshot(self.fx.root/'app'),
            'runtime': self._snapshot(self.fx.root/'runtime'),
            'data': self._snapshot(self.data)}

    def _assert_current_unchanged(self, before):
        self.assertEqual((self.fx.root/'helene.json').read_bytes(), before['config'])
        for name in ('tree', 'app', 'runtime'):
            self.assertEqual(self._snapshot(self.fx.root/name), before[name], name)
        for relative, payload in before['data'].items():
            with self.subTest(current_data=relative):
                self.assertTrue((self.data/relative).is_file(), relative)
                self.assertEqual((self.data/relative).read_bytes(), payload)

    def _sweep(self):
        return start.sweep_legacy(self.fx.root, places=self.fx.places,
                                  backend=self.fx.backend, progress=self.progress.append)

    def _protected_copy(self, base, name):
        old, data = self.fx.old(base=base, name=name)
        shutil.rmtree(data)
        value = start.load(old/'helene.json')
        value['tree'] = str(self.data)
        self._write_config(old, value)
        self._put(old/'app/authored.py', b'protected portable owner edit\n')
        self._admit(old)
        return old

    def test_mixed_native_appdata_program_files_and_downloads_legacy_are_cleaned(self):
        roots = []
        history = {}
        for index, (base, name) in enumerate((
                (self.fx.local, 'Programs/Helene legacy local'),
                (self.fx.roaming, 'Vendor/Helene legacy roaming'),
                (self.fx.pf, 'Installed/Helene legacy global'),
                (self.fx.pf, 'Vendor/Registered native application'))):
            old, data = self.fx.old(base=base, name=name)
            roots.append(old)
            relative = 'memory/groups/legacy-'+str(index)+'.jsonl'
            payload = ('legacy conversation '+str(index)+'\n').encode()
            self._put(data/relative, payload)
            history[relative] = payload
        self.fx.backend.registered = lambda _places: [roots[-1]]
        debris = self.fx.downloads/'Helene obsolete portable'
        self._put(debris/'obsolete.bin', b'old programme fixture\n')
        self._put(debris/'helene.exe', b'MZ identified synthetic portable programme\n')
        archive = self.fx.downloads/'Helene-previous.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('Helene/python-app.json', json.dumps({'format': 'helene-python-app.v1', 'version': 'fixture'}))
        marker = self._put(self.fx.downloads/'update-job-ref-legacy.json', b'{}\n')
        personal = self._put(self.fx.downloads/'Other project/keep.txt', b'personal unrelated material\n')
        before = self._baseline()

        self._sweep()

        for path in (*roots, debris, archive, marker):
            with self.subTest(retired=path.name):self.assertFalse(path.exists())
        for relative, payload in history.items():self.assertEqual((self.data/relative).read_bytes(), payload)
        self.assertEqual(personal.read_bytes(), b'personal unrelated material\n')
        self._assert_current_unchanged(before)
        for relative in ('app', 'runtime', 'agent.py'):
            self.assertFalse((self.data/relative).exists(), relative)

    def test_receipted_copies_and_nested_outer_container_are_preserved_during_pretrial_sweep(self):
        protected = [
            self._protected_copy(self.fx.local, 'Programs/Helene normal local'),
            self._protected_copy(self.fx.pf, 'Installed/Helene normal global'),
            self._protected_copy(self.fx.downloads, 'Helene previous normal'),
            self._protected_copy(self.fx.downloads, 'Helene previous archive/Helene'),
        ]
        self.fx.backend.registered = lambda _places: [protected[1]]
        outer = protected[-1].parent
        self._put(outer/'owner-note.txt', b'outer archive container must survive\n')
        snapshots = {path: self._snapshot(path) for path in (*protected[:-1], outer)}
        legacy, _data = self.fx.old(name='Programs/Helene disposable legacy')
        before = self._baseline()

        self._sweep()

        self.assertFalse(legacy.exists())
        for path, snapshot in snapshots.items():
            with self.subTest(protected=path.name):self.assertEqual(self._snapshot(path), snapshot)
        for path in protected:self.assertTrue(start.ready(path, self.fx.places))
        self._assert_current_unchanged(before)

    def test_receipt_bound_to_a_different_profile_grants_no_legacy_retirement_exemption(self):
        old, data = self.fx.old(name='Programs/Helene wrong receipt')
        history = (data/'memory/groups/window.jsonl').read_bytes()
        self._admit(old, data=self.fx.base/'Other profile/data')
        self.assertFalse(start.ready(old, self.fx.places))
        before = self._baseline()

        self._sweep()

        self.assertFalse(old.exists())
        self.assertEqual((self.data/'memory/groups/window.jsonl').read_bytes(), history)
        self._assert_current_unchanged(before)

    def test_pretrial_stops_old_receipted_supervisors_but_protects_their_files(self):
        protected = self._protected_copy(self.fx.downloads, 'Helene prior running supervisor')
        self.fx.old(name='Programs/Helene legacy to retire')
        protected_before = self._snapshot(protected)
        before = self._baseline()
        observations = []
        def stop(roots, identities=()):
            observations.append((list(roots), list(identities), list(self.fx.backend.excluded),
                                 list(self.fx.backend.process_excluded)))
            self.fx.backend.actions.append(('stop', list(roots)))
        self.fx.backend.stop = stop

        self._sweep()

        self.assertTrue(observations)
        self.assertTrue(any(protected in roots for roots, _ids, _files, _processes in observations))
        for roots, identities, file_exclusions, process_exclusions in observations:
            self.assertNotIn(self.fx.root, roots)
            self.assertIn(protected, file_exclusions)
            self.assertIn(self.fx.root, file_exclusions)
            self.assertEqual(process_exclusions, [self.fx.root])
            self.assertFalse(any(path==self.data or path.is_relative_to(self.data) for path in identities))
        self.assertEqual(self._snapshot(protected), protected_before)
        self._assert_current_unchanged(before)

    def test_wrong_profile_receipt_cannot_hide_external_data_from_fail_closed_checks(self):
        old, data = self.fx.old(base=self.fx.downloads, name='Helene wrong external receipt')
        external = self.fx.base/'Different owner profile/data'
        external.parent.mkdir(parents=True)
        data.rename(external)
        config = start.load(old/'helene.json')
        config['tree'] = str(external)
        self._write_config(old, config)
        self._admit(old, data=external)
        self.assertFalse(start.ready(old, self.fx.places))
        old_before, external_before = self._snapshot(old), self._snapshot(external)
        before = self._baseline()

        with self.assertRaises(ValueError):self._sweep()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(old), old_before)
        self.assertEqual(self._snapshot(external), external_before)
        self._assert_current_unchanged(before)

    def test_legacy_backup_and_contained_voice_are_retained_with_archived_bound_paths(self):
        old, _data = self.fx.old(name='Programs/Helene legacy assets')
        backup, voice = b'legacy owner backup\x00\xff', b'RIFF-legacy-owner-reference\x00\xfe'
        self._put(old/'owner-backups/backup.bin', backup)
        self._put(old/'owner-backups/reference.wav', voice)
        config = start.load(old/'helene.json')
        config['backup'] = {'enabled': True, 'dir': 'owner-backups'}
        config['voice'] = {'tts_reference': 'owner-backups/reference.wav'}
        self._write_config(old, config)
        before = self._baseline()

        self._sweep()

        self.assertFalse(old.exists())
        self._assert_current_unchanged(before)
        imported = start.load(self.data/'memory/.state/imported-profiles.json')
        records = [row for row in imported['profiles'].values() if Path(row['program']).resolve()==old.resolve()]
        self.assertTrue(records)
        for record in records:
            saved = record['config']
            home = Path(record['program'])/Path(record['relative']).parent
            directory = Path(saved['backup']['dir'])
            directory = (directory if directory.is_absolute() else home/directory).resolve()
            reference = Path(saved['voice']['tts_reference'])
            reference = (reference if reference.is_absolute() else home/reference).resolve()
            self.assertTrue(directory.is_relative_to(self.data.resolve()))
            self.assertEqual((directory/'backup.bin').read_bytes(), backup)
            self.assertEqual(reference, directory/'reference.wav')
            self.assertEqual(reference.read_bytes(), voice)

    def test_canonical_history_conflict_is_recovered_without_overwriting_current_history(self):
        old, data = self.fx.old(name='Programs/Helene conflicting history')
        imported = b'conflicting legacy history must remain recoverable\n'
        self._put(data/'memory/groups/current.jsonl', imported)
        before = self._baseline()

        self._sweep()

        self.assertFalse(old.exists())
        self._assert_current_unchanged(before)
        alternatives = [path for path in self.data.rglob('current.jsonl')
                        if path!=self.data/'memory/groups/current.jsonl' and path.read_bytes()==imported]
        self.assertTrue(alternatives)
        self.assertTrue(all(path.resolve().is_relative_to(self.data.resolve()) for path in alternatives))

    def test_legacy_execution_is_not_replayed_and_owner_dependency_locks_are_kept(self):
        old, data = self.fx.old(name='Programs/Helene legacy execution')
        dependency_files = {
            'workspace/legacy-project/Cargo.lock': b'legacy owner cargo lock\n',
            'workspace/legacy-project/poetry.lock': b'legacy owner poetry lock\n',
            'workspace/legacy-project/uv.lock': b'legacy owner uv lock\n',
            'workspace/legacy-project/personal.lck': b'legacy personal lock\n',
            'memory/.state/personal.lock': b'old personal state lock preserved as conflict\n',
        }
        for relative, payload in dependency_files.items():self._put(data/relative, payload)
        before = self._baseline()

        self._sweep()

        self.assertFalse(old.exists())
        self._assert_current_unchanged(before)
        self.assertFalse((self.data/'memory/.control/desk_inbox/old.md').exists())
        self.assertFalse((self.data/'memory/.state/harness.lock').exists())
        for relative, payload in dependency_files.items():
            matches = [path for path in self.data.rglob(Path(relative).name) if path.is_file() and path.read_bytes()==payload]
            with self.subTest(retained=relative):self.assertTrue(matches)

    def test_unknown_downloads_data_refuses_before_even_a_valid_legacy_root_is_touched(self):
        safe, _data = self.fx.old(name='Programs/Helene safe legacy')
        unknown = self.fx.downloads/'Helene unknown data'
        self._put(unknown/'helene.exe', b'MZ identified synthetic legacy programme\n')
        self._put(unknown/'data/memory/groups/history.jsonl', b'unknown out-of-scope owner data\n')
        safe_before, unknown_before = self._snapshot(safe), self._snapshot(unknown)
        before = self._baseline()

        with self.assertRaises(ValueError):self._sweep()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(safe), safe_before)
        self.assertEqual(self._snapshot(unknown), unknown_before)
        self._assert_current_unchanged(before)

    def test_unidentified_branded_neighbour_and_program_files_owner_folders_are_preserved(self):
        unrelated = [self.fx.downloads/'Helene personal notes', self.fx.pf/'Owner/Helene private project']
        for directory in unrelated:
            self._put(directory/'memory/groups/history.jsonl', b'owner material without programme evidence\n')
            self._put(directory/'workspace/project/user.py', b'owner unrelated source\n')
        snapshots = {directory: self._snapshot(directory) for directory in unrelated}
        before = self._baseline()

        self._sweep()

        for directory, snapshot in snapshots.items():self.assertEqual(self._snapshot(directory), snapshot)
        self._assert_current_unchanged(before)

    def test_malformed_legacy_configuration_preflight_has_zero_backend_effects(self):
        safe, _safe_data = self.fx.old(name='Programs/Helene safe preflight')
        broken, _data = self.fx.old(base=self.fx.pf, name='Installed/Helene broken preflight')
        self._put(broken/'helene.exe', b'MZ identified synthetic legacy programme\n')
        self._put(broken/'helene.json', b'{malformed legacy fixture\n')
        snapshots = {path: self._snapshot(path) for path in (safe, broken)}
        before = self._baseline()

        with self.assertRaises(ValueError):self._sweep()

        self.assertEqual(self.fx.backend.actions, [])
        for path, snapshot in snapshots.items():self.assertEqual(self._snapshot(path), snapshot)
        self._assert_current_unchanged(before)

    def test_sweep_requires_an_already_ready_incoming_programme(self):
        old, _data = self.fx.old(name='Programs/Helene must not be touched')
        start.receipt(self.fx.root).unlink()
        old_before = self._snapshot(old)
        before = self._baseline()
        self.assertFalse(start.ready(self.fx.root, self.fx.places))

        with self.assertRaises(ValueError):self._sweep()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(old), old_before)
        self._assert_current_unchanged(before)

    def _assert_missing_canonical_agent_refuses(self, *, empty_directory):
        old, _data = self.fx.old(name='Programs/Helene must survive missing canonical agent')
        old_before = self._snapshot(old)
        before = self._baseline()
        shutil.rmtree(self.data)
        if empty_directory:self.data.mkdir(parents=True)
        self.assertTrue(start.ready(self.fx.root, self.fx.places))

        with self.assertRaises(ValueError):self._sweep()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(old), old_before)
        self.assertEqual((self.fx.root/'helene.json').read_bytes(), before['config'])
        for name in ('tree', 'app', 'runtime'):
            self.assertEqual(self._snapshot(self.fx.root/name), before[name])
        if empty_directory:self.assertEqual(list(self.data.iterdir()), [])
        else:self.assertFalse(self.data.exists())

    def test_sweep_refuses_a_missing_canonical_data_directory_before_effects(self):
        self._assert_missing_canonical_agent_refuses(empty_directory=False)

    def test_sweep_refuses_an_empty_canonical_data_directory_before_effects(self):
        self._assert_missing_canonical_agent_refuses(empty_directory=True)


if __name__=='__main__':unittest.main()
