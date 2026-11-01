"""Receipt-derived whole-batch retirement with genuine synthetic health time."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import t_first_start_3010 as fixture
from localharness import first_start as start, first_start_assets as assets
from localharness import portable_retirement as retirement, portable_switch as switch
from localharness import windows_first_start, windows_hooks, vision_policy

_RUN = subprocess.run
_POPEN = subprocess.Popen


class _Socket:
    def __enter__(self):return self
    def __exit__(self, *args):return False
    def bind(self, address):pass
    def getsockname(self):return ('127.0.0.1', 32123)


class PortableRetirement(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.FirstStart()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.data = self.fx.places.data
        self._put(self.data/'memory/groups/current.jsonl', b'canonical conversation must survive\n')
        self._put(self.data/'memory/.control/profile-identity.json', b'{"id":"current-owner","agent_name":"Current agent"}\n')
        self._put(self.data/'memory/.control/desk_inbox/current.md', b'canonical pending turn must survive\n')
        self._put(self.data/'memory/.control/autonomy-paused.json', b'{"paused":true}\n')
        self._put(self.data/'workspace/project/Cargo.lock', b'owner dependency resolution\n')
        self._put(self.data/'memory/.state/personal.lock', b'owner state file\n')
        self._put(self.fx.root/'tree/merged.py', b'base=True\nauthored=True\nupstream=True\n')
        self._put(self.fx.root/'app/current.py', b'current reviewed app code\n')
        self.fx.backend.registered = lambda _places: []
        self.enterContext(patch.object(start.socket, 'socket', return_value=_Socket()))
        forbidden = AssertionError('Real Windows/process authority is forbidden in this test')
        for name in ('kernel', 'terminate', 'restart_holders', 'user_sid', 'Backend'):
            self.enterContext(patch.object(windows_first_start, name, side_effect=forbidden))
        self.enterContext(patch.object(windows_hooks, 'retire', side_effect=forbidden))
        def private_fixture_file(name):
            if not Path(name).resolve().is_relative_to(self.fx.base.resolve()):
                raise AssertionError('Private write leaves this synthetic fixture')
        self.enterContext(patch.object(vision_policy, '_protect_private_temp', side_effect=private_fixture_file))
        for name in ('run', 'Popen'):
            self.enterContext(patch.object(subprocess, name, side_effect=forbidden))
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

    def _document(self, path, value):
        self._put(path, (json.dumps(value, ensure_ascii=False)+'\n').encode('utf-8'))

    def _admit(self, program):
        self._document(start.receipt(program), {'format': start.FORMAT, 'state': 'completed',
            'completed_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'data': str(self.data)})

    def _protected_copy(self, base, name):
        program, data = self.fx.old(base=base, name=name)
        shutil.rmtree(data)
        config = start.load(program/'helene.json')
        config.update(tree=str(self.data), code='tree')
        self._document(program/'helene.json', config)
        self._put(program/'tree/authored.py', b'old authored source retained during trial\n')
        self._admit(program)
        return program

    def _snapshot(self, directory):
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob('*') if path.is_file()}

    def test_unknown_memory_arriving_after_capture_blocks_every_backend_effect(self):
        scenario=self._scenario();old=scenario.programs[0]
        expected=b'late private profile must not disappear\n'
        self._put(old/'memory/helene-profile.json',expected)
        self._assert_batch_blocked(scenario)
        self.assertEqual((old/'memory/helene-profile.json').read_bytes(),expected)

    def test_unknown_memory_arriving_during_retire_is_preserved_before_remove(self):
        scenario=self._scenario();first=Path(scenario.value['targets'][0]['root'])
        expected=b'private data arriving across a slow retirement boundary\n'
        original=self.fx.backend.retire
        def arrival(path):
            original(path)
            if path==first:self._put(first/'memory/helene-profile.json',expected)
        with patch.object(self.fx.backend,'retire',side_effect=arrival):
            with self.assertRaises(ValueError):self._commit(scenario.value)
        self.assertEqual((first/'memory/helene-profile.json').read_bytes(),expected)
        self.assertNotIn(('remove',first),self.fx.backend.actions)
        self.assertEqual(start.load(retirement.path(self.fx.root))['state'],'partial')

    def _baseline(self):
        return {'config': (self.fx.root/'helene.json').read_bytes(),
            'tree': self._snapshot(self.fx.root/'tree'), 'app': self._snapshot(self.fx.root/'app'),
            'runtime': self._snapshot(self.fx.root/'runtime'), 'data': self._snapshot(self.data)}

    def _assert_current(self, before):
        self.assertEqual((self.fx.root/'helene.json').read_bytes(), before['config'])
        for name in ('tree', 'app', 'runtime'):
            self.assertEqual(self._snapshot(self.fx.root/name), before[name], name)
        for relative, payload in before['data'].items():
            with self.subTest(current_data=relative):
                self.assertTrue((self.data/relative).is_file(), relative)
                if relative=='memory/.state/imported-profiles.json':
                    # Retirement may append provenance records; it must retain
                    # every already-custodied record without rewriting it.
                    previous = json.loads(payload)
                    current = start.load(self.data/relative)
                    self.assertEqual(current['format'], previous['format'])
                    for identity, record in previous['profiles'].items():
                        self.assertEqual(current['profiles'].get(identity), record)
                    continue
                self.assertEqual((self.data/relative).read_bytes(), payload)

    def _assert_archived_asset_refs_survive_retirement(self):
        imported = start.load(self.data/'memory/.state/imported-profiles.json')
        found = False
        for record in imported['profiles'].values():
            config = record['config']
            raw = (config.get('voice') or {}).get('tts_reference')
            if not raw:continue
            found = True
            home = Path(record['program'])/Path(record['relative']).parent
            reference = Path(raw)
            reference = (reference if reference.is_absolute() else home/reference).resolve()
            directory = Path(config['backup']['dir'])
            directory = (directory if directory.is_absolute() else home/directory).resolve()
            self.assertTrue(directory.is_relative_to(self.data.resolve()))
            self.assertEqual((directory/'backup.bin').read_bytes(), b'owner backup\x00\xff\n')
            self.assertEqual(reference, directory/'reference.wav')
            self.assertEqual(reference.read_bytes(), b'RIFF-owner-reference\x00\xfe\n')
        self.assertTrue(found)

    def _scenario(self, *, archive=False):
        programs = [
            self._protected_copy(self.fx.local, 'Programs/Helene old local'),
            self._protected_copy(self.fx.pf, 'Installed/Helene old global'),
            self._protected_copy(self.fx.downloads, 'Helene old portable'),
            self._protected_copy(self.fx.downloads, 'Helene old wrapper/Helene'),
        ]
        self.fx.backend.registered = lambda _places: [programs[1]]
        self._put(programs[-1].parent/'owner-note.txt', b'old wrapper material\n')
        source = programs[0]/'owner-backups'
        self._put(source/'backup.bin', b'owner backup\x00\xff\n')
        self._put(source/'reference.wav', b'RIFF-owner-reference\x00\xfe\n')
        config = start.load(programs[0]/'helene.json')
        config['backup'] = {'enabled': True, 'dir': 'owner-backups'}
        config['voice'] = {'tts_reference': 'owner-backups/reference.wav'}
        self._document(programs[0]/'helene.json', config)
        containers = [*programs[:-1], programs[-1].parent]
        before = {root: self._snapshot(root) for root in containers}
        if archive:
            data_before = self._snapshot(self.data)
            source_before = {name: self._snapshot(self.fx.root/name) for name in ('tree', 'app', 'runtime')}
            self.fx.prepare()
            value = start.load(retirement.path(self.fx.root))
            for name, snapshot in source_before.items():self.assertEqual(self._snapshot(self.fx.root/name), snapshot)
            for relative, payload in data_before.items():self.assertEqual((self.data/relative).read_bytes(), payload)
        else:
            self._document(self.fx.root/'helene.json', {
                'mode': 'local', 'tree': str(self.data), 'code': 'tree', 'setup_complete': True,
                'model': {'framework': 'openai', 'model': 'current-fixture', 'key': 'current-private-fixture'},
                'agent': {'name': 'Current merged agent'}, 'relay': {'enabled': False},
                'autonomy': {'paused': True},
            })
            self._admit(self.fx.root)
            plan = start.legacy_plan(self.fx.root, self.fx.places, self.fx.backend)
            profiles = start.configurations(plan['retained'], self.fx.places)
            retained = assets.prepare(self.data, plan['retained'], [self.data], profiles, {}, None)
            assets.retain(retained, keep_sources=plan['retained'])
            value = retirement.capture(self.fx.root, plan, self.fx.places, retained, mode='update')
            retirement.seal(self.fx.root, value)
        for root, snapshot in before.items():self.assertEqual(self._snapshot(root), snapshot)
        self.assertTrue(start.ready(self.fx.root, self.fx.places))
        self.assertTrue(value['assets'])
        retirement.validate(self.fx.root, value, self.fx.places)
        self.fx.backend.actions.clear()
        return SimpleNamespace(value=value, programs=programs, containers=containers, source=source)

    def _window(self, identity='healthy-generation'):
        window = switch.HealthWindow()
        for now in range(0, switch.TRIAL_SECONDS, 5):
            self.assertFalse(window.observe(identity, float(now)))
        return window

    def _commit(self, value, *, window=None, now=300.0, identity='healthy-generation'):
        window = self._window() if window is None else window
        with patch.object(switch, 'health', return_value=identity), \
             patch.object(retirement.time, 'monotonic', return_value=now):
            return retirement.commit(self.fx.root, value, window,
                                     places=self.fx.places, backend=self.fx.backend)

    def _assert_batch_blocked(self, scenario, *, value=None):
        value = scenario.value if value is None else value
        before = {root: self._snapshot(root) for root in scenario.containers}
        current = self._baseline()

        with self.assertRaises(ValueError):self._commit(value)

        self.assertEqual(self.fx.backend.actions, [])
        for root, snapshot in before.items():
            self.assertTrue(root.is_dir())
            self.assertEqual(self._snapshot(root), snapshot)
        self._assert_current(current)

    def test_archive_prepare_keeps_every_old_copy_and_asset_source_until_true_300_seconds(self):
        self.assertEqual(switch.TRIAL_SECONDS, 300)
        scenario = self._scenario(archive=True)
        self.assertEqual(scenario.value['mode'], 'archive')
        current = self._baseline()
        snapshots = {root: self._snapshot(root) for root in scenario.containers}
        window = self._window()

        with self.assertRaises(ValueError):self._commit(scenario.value, window=window, now=299.999)
        self.assertEqual(self.fx.backend.actions, [])
        for root, snapshot in snapshots.items():self.assertEqual(self._snapshot(root), snapshot)
        self._assert_current(current)

        self._commit(scenario.value, window=window, now=300.0)

        for root in scenario.containers:self.assertFalse(root.exists())
        self._assert_current(current)
        saved = start.load(retirement.path(self.fx.root))
        self.assertEqual(saved['state'], 'completed')
        self.assertEqual(set(saved['removed']), {row['root'] for row in saved['targets']})
        for row in saved['assets']:
            self.assertEqual(assets.snapshot(row['destination']), row['snapshot'])
        self._assert_archived_asset_refs_survive_retirement()

    def test_changed_health_generation_does_not_borrow_the_previous_300_second_interval(self):
        scenario = self._scenario()
        window = self._window()
        before = {root: self._snapshot(root) for root in scenario.containers}

        with self.assertRaises(ValueError):self._commit(scenario.value, window=window, identity='different-generation')

        self.assertEqual(self.fx.backend.actions, [])
        for root, snapshot in before.items():self.assertEqual(self._snapshot(root), snapshot)
        self.assertEqual(window.identity, 'different-generation')
        self.assertEqual(window.since, 300.0)

    def test_old_app_change_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        self._put(scenario.programs[-1]/'app/old.py', b'new owner app edit during trial\n')
        self._assert_batch_blocked(scenario)

    def test_old_source_change_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        self._put(scenario.programs[-1]/'tree/authored.py', b'new authored source during trial\n')
        self._assert_batch_blocked(scenario)

    def test_changed_new_app_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        self._put(self.fx.root/'app/current.py', b'unreviewed new app bytes during trial\n')
        self._assert_batch_blocked(scenario)

    def test_changed_new_runtime_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        self._put(self.fx.root/'runtime/python.exe', b'changed declared runtime during trial\n')
        self._assert_batch_blocked(scenario)

    def test_new_runtime_import_hook_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        self._put(self.fx.root/'runtime/Lib/site-packages/unadmitted.pth', b'import unadmitted_hook\n')
        self._assert_batch_blocked(scenario)

    def test_changed_new_transition_plan_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        self._document(self.fx.root/'code-transition-plan.json', {'unreviewed': 'new transition authority'})
        self._assert_batch_blocked(scenario)

    def test_old_receipt_change_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        record = start.load(start.receipt(scenario.programs[-1]))
        record['owner_note'] = 'receipt changed during trial'
        self._document(start.receipt(scenario.programs[-1]), record)
        self._assert_batch_blocked(scenario)

    def test_replaced_directory_generation_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        root = scenario.containers[-1]
        previous = root.stat()
        moved = root.with_name('preserved different directory generation')
        root.rename(moved)
        shutil.copytree(moved, root)
        self.assertNotEqual((root.stat().st_dev, root.stat().st_ino), (previous.st_dev, previous.st_ino))
        self._assert_batch_blocked(scenario)

    def test_changed_asset_source_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        self._put(scenario.source/'reference.wav', b'changed original owner voice\n')
        self._assert_batch_blocked(scenario)

    def test_changed_asset_copy_blocks_the_whole_batch_before_backend_effects(self):
        scenario = self._scenario()
        copied = Path(scenario.value['assets'][0]['destination'])
        self._put(copied/'reference.wav', b'changed copied voice\n')
        self._assert_batch_blocked(scenario)

    def test_tampered_journal_authority_cannot_change_mode_targets_or_asset_scope(self):
        scenario = self._scenario()
        original = copy.deepcopy(scenario.value)
        for kind in ('mode', 'targets', 'assets'):
            with self.subTest(tampered=kind):
                changed = copy.deepcopy(original)
                if kind=='mode':changed['mode']='archive'
                elif kind=='targets':changed['targets']=changed['targets'][:-1]
                else:changed['assets'][0]['destination']=str(self.fx.base/'unadmitted asset destination')
                self._document(retirement.path(self.fx.root), changed)
                self._assert_batch_blocked(scenario, value=start.load(retirement.path(self.fx.root)))

    def test_tampered_incoming_admission_seal_blocks_the_whole_batch(self):
        scenario = self._scenario()
        record = start.load(start.receipt(self.fx.root))
        record['retirement']['sha256'] = '0'*64
        self._document(start.receipt(self.fx.root), record)
        self._assert_batch_blocked(scenario)

    def test_forged_removed_record_cannot_authorize_a_still_existing_target(self):
        scenario = self._scenario()
        changed = copy.deepcopy(scenario.value)
        changed['removed'] = [changed['targets'][0]['root']]
        self._assert_batch_blocked(scenario, value=changed)

    def test_partial_failure_is_durable_and_retry_never_discovers_additional_targets(self):
        scenario = self._scenario()
        original_targets = copy.deepcopy(scenario.value['targets'])
        blocked = Path(original_targets[1]['root'])
        original_remove = self.fx.backend.remove
        failures = []
        def fail_once(path):
            if path==blocked and not failures:
                failures.append(path)
                raise OSError('synthetic retirement removal failure')
            return original_remove(path)
        current = self._baseline()

        with patch.object(self.fx.backend, 'remove', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'synthetic retirement removal failure'):
                self._commit(scenario.value)

        self.assertTrue(failures)
        saved = start.load(retirement.path(self.fx.root))
        self.assertEqual(saved['state'], 'partial')
        self.assertTrue(saved['removed'])
        self.assertNotEqual(set(saved['removed']), {row['root'] for row in original_targets})
        self.assertEqual(saved['targets'], original_targets)
        self._assert_current(current)
        late = self._protected_copy(self.fx.downloads, 'Helene discovered after failure')
        late_before = self._snapshot(late)
        self.fx.backend.actions.clear()

        self._commit(saved, window=self._window())

        self.assertEqual(saved['state'], 'completed')
        self.assertEqual(saved['targets'], original_targets)
        for row in original_targets:self.assertFalse(Path(row['root']).exists())
        self.assertEqual(self._snapshot(late), late_before)
        self.assertTrue(start.ready(late, self.fx.places))
        self._assert_current(current)

    def test_post_delete_pre_journal_crash_reconciles_only_the_captured_deleting_target(self):
        class AbruptExit(BaseException):pass
        scenario = self._scenario()
        original_targets = copy.deepcopy(scenario.value['targets'])
        first = Path(original_targets[0]['root'])
        journal = retirement.path(self.fx.root)
        original_write = start._write
        current = self._baseline()
        def crash_before_completed_delete_record(path, value, **kwargs):
            if Path(path)==journal and value.get('removed') and not value.get('deleting'):
                raise AbruptExit('synthetic process loss before completed-delete journal write')
            return original_write(path, value, **kwargs)

        with patch.object(start, '_write', side_effect=crash_before_completed_delete_record):
            with self.assertRaises(AbruptExit):self._commit(scenario.value)

        self.assertFalse(first.exists())
        saved = start.load(journal)
        self.assertNotEqual(saved['state'], 'completed')
        self.assertEqual(saved['deleting'], str(first))
        self.assertNotIn(str(first), saved['removed'])
        self.assertEqual(saved['targets'], original_targets)
        late = self._protected_copy(self.fx.downloads, 'Helene unrelated after journal crash')
        late_before = self._snapshot(late)
        self.fx.backend.actions.clear()

        self._commit(saved, window=self._window())

        self.assertEqual(saved['state'], 'completed')
        self.assertNotIn('deleting', saved)
        self.assertEqual(saved['targets'], original_targets)
        self.assertEqual(set(saved['removed']), {row['root'] for row in original_targets})
        for row in original_targets:self.assertFalse(Path(row['root']).exists())
        self.assertNotIn(('remove', first), self.fx.backend.actions)
        self.assertEqual(self._snapshot(late), late_before)
        self._assert_current(current)

    def test_helper_direct_script_help_works_without_site_or_import_hooks(self):
        helper = Path(retirement.__file__).resolve()
        command = [sys.executable, '-I', '-S', str(helper), '--help']
        def exact_helper_only(arguments, *args, **kwargs):
            self.assertEqual(arguments, command)
            return _POPEN(arguments, *args, **kwargs)
        with patch.object(subprocess, 'Popen', side_effect=exact_helper_only):
            result = _RUN(command, cwd=self.fx.base, capture_output=True, text=True,
                          timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--program', result.stdout)
        self.assertIn('--identity', result.stdout)
        self.assertIn('--sha256', result.stdout)
        self.assertEqual(self.fx.backend.actions, [])


if __name__=='__main__':unittest.main()
