"""Independent first-start custody regressions; no live Windows authority.

The existing FirstStart is composed only as a synthetic-folder fixture. Its
test methods are not inherited or loaded by this module. Process inventory is
always an injected psutil module containing only the fake rows below.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import t_first_start_3010 as fixture
from localharness import first_start as start
from localharness import standalone_relay, windows_first_start, windows_hooks, vision_policy


class _Socket:
    """Allocate no OS socket while _bind prepares a private test config."""
    def __enter__(self):return self
    def __exit__(self, *args):return False
    def bind(self, address):pass
    def getsockname(self):return ('127.0.0.1', 32123)


class _Process:
    def __init__(self, pid, exe, argv, *, ppid=0, birth=100.0):
        self.info = {'pid': pid, 'ppid': ppid, 'exe': str(exe), 'create_time': birth}
        self._argv = [str(value) for value in argv]

    def cmdline(self):return list(self._argv)


class FirstStartAudit(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.FirstStart()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.enterContext(patch.object(start.socket, 'socket', return_value=_Socket()))
        # A later implementation change must fail this hermetic test rather
        # than acquire actual process, service, task or registry authority.
        for name in ('kernel', 'terminate', 'restart_holders', 'user_sid', 'Backend'):
            self.enterContext(patch.object(windows_first_start, name,
                side_effect=AssertionError('Real Windows authority is forbidden in this test')))
        self.enterContext(patch.object(windows_hooks, 'retire',
            side_effect=AssertionError('Real Windows hooks are forbidden in this test')))
        def private_fixture_file(name):
            if not Path(name).resolve().is_relative_to(self.fx.base.resolve()):
                raise AssertionError('Private write leaves this synthetic fixture')
        self.enterContext(patch.object(vision_policy, '_protect_private_temp', side_effect=private_fixture_file))
        for name in ('run', 'Popen'):
            self.enterContext(patch.object(windows_first_start.subprocess, name,
                side_effect=AssertionError('Real subprocess authority is forbidden in this test')))

    def _write(self, path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def _snapshot(self, directory):
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob('*') if path.is_file()}

    def _late_unknown_memory(self,*,sweep):
        if sweep:
            self.fx.old();self.fx.prepare()
        old=self.fx.local/'Helene final shutdown'
        self._write(old/'helene.exe',b'MZ private obsolete programme')
        expected=b'{"private":"last shutdown profile bytes"}\n'
        original=self.fx.backend.stop
        def flush(roots,protected=()):
            original(roots,protected)
            self._write(old/'memory/helene-profile.json',expected)
        with patch.object(self.fx.backend,'stop',side_effect=flush):
            with self.assertRaises(ValueError):
                if sweep:start.sweep_legacy(self.fx.root,places=self.fx.places,backend=self.fx.backend)
                else:self.fx.prepare()
        self.assertEqual((old/'memory/helene-profile.json').read_bytes(),expected)
        self.assertTrue(old.is_dir())

    def test_first_prepare_refuses_unknown_data_published_during_shutdown(self):
        self._late_unknown_memory(sweep=False)

    def test_update_sweep_refuses_unknown_data_published_during_shutdown(self):
        self._late_unknown_memory(sweep=True)

    def test_shutdown_backup_and_final_admitted_data_are_preserved(self):
        old,data=self.fx.old()
        expected=b'actual private last backup bytes\n';history=b'final shutdown history\n'
        original=self.fx.backend.stop
        def flush(roots,protected=()):
            original(roots,protected)
            self._write(old/'backups/final.zip',expected)
            self._write(data/'memory/groups/final.jsonl',history)
        with patch.object(self.fx.backend,'stop',side_effect=flush):self.fx.prepare()
        self.assertFalse(old.exists())
        self.assertEqual((self.fx.places.data/'memory/groups/final.jsonl').read_bytes(),history)
        matches=[p for p in (self.fx.places.data/'retained-assets').rglob('*') if p.is_file() and p.read_bytes()==expected]
        self.assertEqual(len(matches),1)

    def test_settings_changed_during_shutdown_require_a_fresh_preparation(self):
        old,data=self.fx.old();before=self._snapshot(data)
        original=self.fx.backend.stop
        def flush(roots,protected=()):
            original(roots,protected)
            config=start.load(old/'helene.json');config['agent']['name']='Final shutdown name'
            self._write(old/'helene.json',json.dumps(config).encode())
        with patch.object(self.fx.backend,'stop',side_effect=flush):
            with self.assertRaises(ValueError):self.fx.prepare()
        self.assertEqual(self._snapshot(data),before)
        self.assertEqual(start.load(old/'helene.json')['agent']['name'],'Final shutdown name')

    def test_new_settings_published_during_shutdown_are_not_deleted_unread(self):
        old=self.fx.local/'Helene late settings'
        self._write(old/'helene.exe',b'MZ private obsolete programme')
        expected=b'{"mode":"local","tree":"data","model":{"key":"private-final-fixture"}}'
        original=self.fx.backend.stop
        def flush(roots,protected=()):
            original(roots,protected)
            self._write(old/'helene.json',expected)
            self._write(old/'data/memory/groups/final.jsonl',b'final private history')
        with patch.object(self.fx.backend,'stop',side_effect=flush):
            with self.assertRaises(ValueError):self.fx.prepare()
        self.assertEqual((old/'helene.json').read_bytes(),expected)
        self.assertEqual((old/'data/memory/groups/final.jsonl').read_bytes(),b'final private history')

    def test_known_pf_data_waits_for_its_scoped_access_grant_before_listing(self):
        old,data=self.fx.old(base=self.fx.pf);granted=[]
        original=Path.iterdir
        def listing(path):
            if path==data and not granted:raise PermissionError('private SYSTEM-created data listing')
            return original(path)
        with patch.object(Path,'iterdir',listing),patch.object(self.fx.backend,'data_access',
                side_effect=lambda path:granted.append(path),create=True):
            self.fx.prepare()
        self.assertEqual(granted,[data])
        self.assertFalse(old.exists())
        self.assertTrue((self.fx.places.data/'memory/groups/window.jsonl').is_file())

    def test_unknown_unreadable_neighbour_data_never_gains_a_cleanup_grant(self):
        old=self.fx.root.parent/'Helene unknown ACL'
        self._write(old/'helene.exe',b'MZ identified private obsolete programme')
        self._write(old/'app/deskapp.py',b'private old programme marker')
        data=old/'data';self._write(data/'memory/groups/history.jsonl',b'private unknown history')
        original=Path.iterdir
        def listing(path):
            if path==data:raise PermissionError('unknown data must remain unreadable')
            return original(path)
        with patch.object(Path,'iterdir',listing):
            with self.assertRaises(PermissionError):self.fx.prepare()
        self.assertEqual(self.fx.backend.actions,[])
        self.assertEqual((data/'memory/groups/history.jsonl').read_bytes(),b'private unknown history')

    def test_neighbour_zip_with_an_agent_profile_is_preserved(self):
        import zipfile
        archive=self.fx.root.parent/'Helene saved profile.zip'
        with zipfile.ZipFile(archive,'w') as output:
            output.writestr('Helene/helene.exe',b'MZ old programme')
            output.writestr('Helene/data/memory/groups/history.jsonl',b'irreplaceable private archived conversation')
        before=archive.read_bytes()
        self.fx.prepare()
        self.assertTrue(archive.is_file())
        self.assertEqual(archive.read_bytes(),before)

    def test_explicit_neighbour_backup_zip_is_preserved_even_without_a_data_directory(self):
        import zipfile
        archive=self.fx.root.parent/'Helene-backup.zip'
        with zipfile.ZipFile(archive,'w') as output:
            output.writestr('Helene/python-app.json','{"format":"helene-python-app.v1"}')
            output.writestr('Helene/app/authored.py',b'authored recovery material')
        before=archive.read_bytes()
        self.fx.prepare()
        self.assertTrue(archive.is_file())
        self.assertEqual(archive.read_bytes(),before)

    def _owned(self, processes, roots, protected=()):
        inventory = Mock(return_value=iter(processes))
        inventory.cache_clear = Mock()
        fake_psutil = SimpleNamespace(process_iter=inventory,
            NoSuchProcess=type('NoSuchProcess', (Exception,), {}),
            AccessDenied=type('AccessDenied', (Exception,), {}))
        with patch.dict(sys.modules, {'psutil': fake_psutil}):
            return windows_first_start.owned(roots, protected)

    def test_reset_preserves_owner_lock_files_and_clears_only_runtime_state(self):
        data = self.fx.places.data
        keep = {
            'workspace/project/Cargo.lock': b'cargo dependency resolution\n',
            'workspace/project/poetry.lock': b'poetry dependency resolution\n',
            'workspace/project/uv.lock': b'uv dependency resolution\n',
            'workspace/project/owner.lck': b'owner project lock\n',
            'workspace/project/lock': b'project lock without suffix\n',
            'workspace/project/lockfile': b'project lockfile\n',
            'memory/journal/personal.lock': b'personal journal metadata\n',
            'memory/.state/personal.lock': b'owner state file\n',
            'attachments/portrait.lck': b'owner attachment metadata\n',
            'personal.lock': b'owner root file\n',
            'memory/groups/history.jsonl': b'{"text":"keep every conversation byte"}\n',
            'memory/.control/profile-identity.json': b'{"id":"owner-profile"}\n',
            'memory/.control/autonomy-paused.json': b'{"paused":true}\n',
            'memory/.control/autonomy-generation.json': b'{"format":"helene-autonomy-generation.v1","id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","paused":true}\n',
        }
        discard = (
            'memory/.state/harness.lock', '.desktop-owner.lock',
            'memory/.control/desk_inbox/stale.md',
            'memory/.state/notification-queue.json',
            'memory/queue/stale.json', 'pending/stale.json',
            'body/bridge/spool.db',
        )
        for relative, payload in keep.items():self._write(data/relative, payload)
        for relative in discard:self._write(data/relative, b'stale execution state\n')

        start.reset_execution(data)

        for relative, payload in keep.items():
            with self.subTest(retained=relative):
                self.assertTrue((data/relative).is_file(), relative)
                self.assertEqual((data/relative).read_bytes(), payload)
        for relative in discard:
            with self.subTest(discarded=relative):self.assertFalse((data/relative).exists())
        self.assertEqual(self.fx.backend.actions, [])

    def _assert_unconfigured_downloads_data_is_protected(self, *, malformed=False, wrapped=False):
        outer = self.fx.downloads/'Helene-old-unconfigured'
        old = outer/'Helene' if wrapped else outer
        data = old/'data'
        self._write(old/'helene.exe', b'MZ identified synthetic legacy programme\n')
        self._write(data/'memory/groups/history.jsonl', b'{"text":"irreplaceable old history"}\n')
        self._write(old/'app/deskapp.py', b'old programme fixture\n')
        if malformed:self._write(old/'helene.json', b'{not valid json\n')
        before = self._snapshot(outer)

        with self.assertRaises(ValueError):self.fx.prepare()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(outer), before)
        self.assertFalse((self.fx.root/'helene.json').exists())
        self.assertFalse(start.receipt(self.fx.root).exists())
        self.assertFalse(self.fx.places.data.exists())

    def test_downloads_data_without_config_fails_before_backend_actions(self):
        self._assert_unconfigured_downloads_data_is_protected()

    def test_downloads_data_with_malformed_config_fails_before_backend_actions(self):
        self._assert_unconfigured_downloads_data_is_protected(malformed=True)

    def test_wrapped_downloads_data_without_config_fails_before_backend_actions(self):
        self._assert_unconfigured_downloads_data_is_protected(wrapped=True)

    def test_wrapped_downloads_data_with_malformed_config_fails_before_backend_actions(self):
        self._assert_unconfigured_downloads_data_is_protected(malformed=True, wrapped=True)

    def test_data_guard_does_not_expand_into_unrelated_source_or_verification_trees(self):
        unrelated = self.fx.downloads/'Source fixtures'
        for relative in ('source/Helene-template/data/memory/groups/history.jsonl',
                         'verification/Helene-fixture/data/memory/groups/history.jsonl'):
            self._write(unrelated/relative, b'synthetic source fixture, not an installed agent\n')
        before = self._snapshot(unrelated)

        result = self.fx.prepare()

        self.assertFalse(result['agent'])
        self.assertEqual(self._snapshot(unrelated), before)
        self.assertTrue(start.ready(self.fx.root, self.fx.places))

    def _assert_unknown_direct_memory_is_preserved(self, base):
        old = base/'Vendor/Helene direct-memory'
        self._write(old/'helene.exe', b'MZ identified synthetic legacy programme\n')
        self._write(old/'memory/groups/history.jsonl', b'unknown direct-memory history\n')
        self._write(old/'workspace/project/user.py', b'owner source must remain exactly here\n')
        self._write(old/'app/old.py', b'unknown old app fixture\n')
        self._write(old/'agent.py', b'unknown old agent fixture\n')
        before = self._snapshot(old)

        with self.assertRaises(ValueError):self.fx.prepare()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(old), before)
        self.assertFalse((self.fx.root/'helene.json').exists())
        self.assertFalse(start.receipt(self.fx.root).exists())
        self.assertFalse(self.fx.places.data.exists())

    def test_unconfigured_direct_memory_in_local_appdata_fails_without_touching_files(self):
        self._assert_unknown_direct_memory_is_preserved(self.fx.local)

    def test_unconfigured_direct_memory_in_roaming_appdata_fails_without_touching_files(self):
        self._assert_unknown_direct_memory_is_preserved(self.fx.roaming)

    def test_unconfigured_direct_memory_in_program_files_fails_without_touching_files(self):
        self._assert_unknown_direct_memory_is_preserved(self.fx.pf)

    def _assert_minimal_profile_memory_is_preserved(self, relative, payload):
        old = self.fx.local/'Programs/Helene minimal-profile'
        self._write(old/'helene.exe', b'MZ identified synthetic legacy programme\n')
        self._write(old/relative, payload)
        before = self._snapshot(old)
        self.assertEqual(set(before), {relative, 'helene.exe'})

        with self.assertRaises(ValueError):self.fx.prepare()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(old), before)
        self.assertFalse((self.fx.root/'helene.json').exists())
        self.assertFalse(start.receipt(self.fx.root).exists())
        self.assertFalse(self.fx.places.data.exists())

    def test_unconfigured_saved_profile_without_history_is_owner_data(self):
        self._assert_minimal_profile_memory_is_preserved('memory/.state/helene-profile.json',
            b'{"model":{"framework":"openai","model":"fixture","key":"private-fixture"}}\n')

    def test_unconfigured_reconnect_record_without_history_is_owner_data(self):
        self._assert_minimal_profile_memory_is_preserved('memory/.control/installation-reconnect.json',
            b'{"program":"fixture-old-programme","reconnect":true}\n')

    def test_unconfigured_id_only_profile_without_history_is_owner_data(self):
        self._assert_minimal_profile_memory_is_preserved('memory/.control/profile-identity.json',
            b'{"id":"owner-identity-with-no-agent-name"}\n')

    def test_malformed_legacy_appdata_config_preserves_exact_document_and_data_before_effects(self):
        old, data = self.fx.old()
        self._write(old/'helene.exe', b'MZ identified synthetic legacy programme\n')
        original = (b'\xef\xbb\xbf{\r\n  "tree": "data",\r\n'
                    b'  "model": {"key":"private-fixture"},\r\n  malformed legacy content\r\n}\r\n')
        (old/'helene.json').write_bytes(original)
        before = self._snapshot(old)

        with self.assertRaises(ValueError):self.fx.prepare()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(old), before)
        self.assertEqual((old/'helene.json').read_bytes(), original)
        self.assertFalse((self.fx.root/'helene.json').exists())
        self.assertFalse(start.receipt(self.fx.root).exists())
        self.assertFalse(self.fx.places.data.exists())

    def test_valid_utf16_legacy_config_retains_exact_original_bytes_in_imported_archive(self):
        old, data = self.fx.old()
        history = (data/'memory/groups/window.jsonl').read_bytes()
        config = start.load(old/'helene.json')
        config['agent']['name'] = 'Сохранённый профиль — fixture'
        original = json.dumps(config, ensure_ascii=False, indent=2).replace('\n', '\r\n').encode('utf-16')
        (old/'helene.json').write_bytes(original)

        self.fx.prepare()

        self.assertFalse(old.exists())
        self.assertTrue(start.ready(self.fx.root, self.fx.places))
        self.assertEqual((self.fx.places.data/'memory/groups/window.jsonl').read_bytes(), history)
        imported = start.load(self.fx.places.data/'memory/.state/imported-profiles.json')
        records = [record for record in imported['profiles'].values()
                   if Path(record['program']).resolve()==old.resolve() and record['relative']=='helene.json']
        self.assertTrue(records)
        for record in records:
            self.assertEqual(base64.b64decode(record['raw_config'], validate=True), original)

    def test_configured_root_data_with_separate_tree_extracts_owner_work_but_no_programme_code(self):
        old, data = self.fx.old()
        history = (data/'memory/groups/window.jsonl').read_bytes()
        shutil.move(str(data/'memory'), str(old/'memory'))
        shutil.rmtree(data)
        owner_work = b'print("owner project source, not programme code")\n'
        self._write(old/'workspace/project/user.py', owner_work)
        self._write(old/'agent.py', b'old root executable agent\n')
        self._write(old/'runtime/python.exe', b'old interpreter fixture\n')
        self._write(old/'tree/agent.py', b'old separate tree agent\n')
        self._write(old/'tree/llm.py', b'old separate tree llm\n')
        self.assertFalse((old/'llm.py').exists())
        config = start.load(old/'helene.json')
        config.update(tree='.', code='tree')
        (old/'helene.json').write_text(json.dumps(config), encoding='utf-8')

        self.fx.prepare()

        target = self.fx.places.data
        self.assertFalse(old.exists())
        self.assertEqual((target/'memory/groups/window.jsonl').read_bytes(), history)
        self.assertEqual((target/'workspace/project/user.py').read_bytes(), owner_work)
        for relative in ('app', 'agent.py', 'runtime', 'tree'):
            with self.subTest(excluded_programme=relative):self.assertFalse((target/relative).exists())
        current = start.load(self.fx.root/'helene.json')
        selected = Path(current['tree'])
        self.assertEqual((selected if selected.is_absolute() else self.fx.root/selected).resolve(), target.resolve())

    def test_selected_candidate_walk_error_propagates_before_backend_effects(self):
        old, _data = self.fx.old()
        before = self._snapshot(old)
        def unreadable_walk(top, *args, onerror=None, **kwargs):
            if Path(top).resolve()==old.resolve() and onerror is not None:
                onerror(PermissionError('synthetic selected candidate unreadable'))
            return iter(())

        with patch.object(start.os, 'walk', side_effect=unreadable_walk):
            with self.assertRaisesRegex(PermissionError, 'synthetic selected candidate unreadable'):
                self.fx.prepare()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(old), before)
        self.assertFalse((self.fx.root/'helene.json').exists())
        self.assertFalse(start.receipt(self.fx.root).exists())
        self.assertFalse(self.fx.places.data.exists())

    def _assert_relay_failure_preserves_old_installation(self, *, images=False):
        old, data = self.fx.old()
        config = start.load(old/'helene.json')
        config['relay'] = {'enabled': not images}
        config['images'] = {'enabled': images}
        (old/'helene.json').write_text(json.dumps(config), encoding='utf-8')
        before = self._snapshot(old)
        self.assertFalse(standalone_relay.executable(self.fx.root).exists())

        with patch.object(standalone_relay, 'install', side_effect=OSError('offline')):
            with self.assertRaisesRegex(OSError, 'offline'):self.fx.prepare()

        self.assertEqual(self.fx.backend.actions, [])
        self.assertEqual(self._snapshot(old), before)
        self.assertEqual((data/'memory/groups/window.jsonl').read_bytes(),
                         b'old complete conversation\n')
        self.assertFalse(self.fx.places.data.exists())
        self.assertFalse((self.fx.root/'helene.json').exists())
        self.assertFalse(start.receipt(self.fx.root).exists())

    def test_relay_download_failure_precedes_old_hooks_and_process_retirement(self):
        self._assert_relay_failure_preserves_old_installation()

    def test_image_relay_download_failure_precedes_old_hooks_and_process_retirement(self):
        self._assert_relay_failure_preserves_old_installation(images=True)

    def _configured_path(self, config, home, block, key):
        value = config.get(block)
        self.assertIsInstance(value, dict, block)
        raw = value.get(key)
        self.assertIsInstance(raw, str, block+'.'+key)
        self.assertTrue(raw, block+'.'+key)
        path = Path(raw)
        return (path if path.is_absolute() else home/path).resolve()

    def _assert_internal_asset_paths(self, config, home, old, *, backup, reference=None):
        target = self.fx.places.data.resolve()
        backup_dir = self._configured_path(config, home, 'backup', 'dir')
        self.assertTrue(backup_dir==target or backup_dir.is_relative_to(target))
        self.assertFalse(backup_dir==old.resolve() or backup_dir.is_relative_to(old.resolve()))
        self.assertTrue((backup_dir/'backup.bin').is_file(), str(backup_dir/'backup.bin'))
        self.assertEqual((backup_dir/'backup.bin').read_bytes(), backup)
        if reference is not None:
            selected = self._configured_path(config, home, 'voice', 'tts_reference')
            self.assertTrue(selected.is_relative_to(target))
            self.assertFalse(selected.is_relative_to(old.resolve()))
            self.assertTrue(selected.is_file(), str(selected))
            self.assertEqual(selected.read_bytes(), reference)

    def _assert_archived_internal_asset_paths(self, old, *, backup, reference=None):
        archive = start.load(self.fx.places.data/'memory/.state/imported-profiles.json')
        records = list(archive['profiles'].values())
        self.assertTrue(records)
        for record in records:
            with self.subTest(archived_profile=record['relative']):
                home = Path(record['program'])/Path(record['relative']).parent
                self._assert_internal_asset_paths(record['config'], home, old,
                                                  backup=backup, reference=reference)

    def _old_with_explicit_programme_assets(self):
        old, data = self.fx.old()
        backup = b'\x00owner backup bytes\xff\n'
        reference = b'RIFF-owner-voice-reference\x00\xfe\n'
        self._write(old/'owner-backups/backup.bin', backup)
        self._write(old/'reference.wav', reference)
        config = start.load(old/'helene.json')
        config['backup'] = {'enabled': True, 'dir': 'owner-backups'}
        config['voice'] = {'tts_reference': 'reference.wav'}
        (old/'helene.json').write_text(json.dumps(config), encoding='utf-8')
        return old, data, backup, reference

    def test_explicit_programme_backup_and_voice_reference_survive_retirement(self):
        old, data, backup, reference = self._old_with_explicit_programme_assets()
        history = (data/'memory/groups/window.jsonl').read_bytes()

        self.fx.prepare()

        self.assertFalse(old.exists())
        self.assertEqual((self.fx.places.data/'memory/groups/window.jsonl').read_bytes(), history)
        config = start.load(self.fx.root/'helene.json')
        self._assert_internal_asset_paths(config, self.fx.root, old, backup=backup, reference=reference)
        self._assert_archived_internal_asset_paths(old, backup=backup, reference=reference)

    def test_implicit_programme_backups_are_preserved_and_bound_to_canonical_data(self):
        old, _data = self.fx.old()
        backup = b'implicit existing owner backup\x00\xff\n'
        self._write(old/'backups/backup.bin', backup)
        config = start.load(old/'helene.json')
        config['backup'] = {'enabled': True}
        (old/'helene.json').write_text(json.dumps(config), encoding='utf-8')

        self.fx.prepare()

        self.assertFalse(old.exists())
        config = start.load(self.fx.root/'helene.json')
        self.assertTrue(config['backup']['enabled'])
        self._assert_internal_asset_paths(config, self.fx.root, old, backup=backup)

    def test_independent_external_backup_and_voice_reference_keep_their_paths_and_bytes(self):
        old, _data = self.fx.old()
        external = self.fx.base/'Owner assets outside all programmes'
        backup = b'independent external backup\x00\xfe\n'
        reference = b'RIFF-independent-external-reference\x00\xff\n'
        self._write(external/'backups/backup.bin', backup)
        self._write(external/'reference.wav', reference)
        config = start.load(old/'helene.json')
        config['backup'] = {'enabled': True, 'dir': str(external/'backups')}
        config['voice'] = {'tts_reference': str(external/'reference.wav')}
        (old/'helene.json').write_text(json.dumps(config), encoding='utf-8')
        expected_backup = config['backup']['dir']
        expected_reference = config['voice']['tts_reference']
        before = self._snapshot(external)

        self.fx.prepare()

        self.assertFalse(old.exists())
        actual = start.load(self.fx.root/'helene.json')
        self.assertEqual(actual['backup']['dir'], expected_backup)
        self.assertEqual(actual['voice']['tts_reference'], expected_reference)
        self.assertEqual(self._snapshot(external), before)
        archive = start.load(self.fx.places.data/'memory/.state/imported-profiles.json')
        self.assertTrue(archive['profiles'])
        for record in archive['profiles'].values():
            self.assertEqual(record['config']['backup']['dir'], expected_backup)
            self.assertEqual(record['config']['voice']['tts_reference'], expected_reference)

    def test_late_cleanup_retry_keeps_moved_assets_and_rebound_profile_settings(self):
        old, _data, backup, reference = self._old_with_explicit_programme_assets()
        original_remove = self.fx.backend.remove
        failures = []
        def fail_once(path):
            if path==old and not failures:
                failures.append(path)
                raise OSError('synthetic late cleanup failure')
            return original_remove(path)

        with patch.object(self.fx.backend, 'remove', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'synthetic late cleanup failure'):self.fx.prepare()
        self.assertTrue(failures)
        self.assertFalse(start.ready(self.fx.root, self.fx.places))
        with self.subTest(custody='after failed cleanup'):
            pending = start.load(self.fx.root/'helene.json')
            self._assert_internal_asset_paths(pending, self.fx.root, old, backup=backup, reference=reference)

        self.fx.prepare()

        self.assertFalse(old.exists())
        self.assertTrue(start.ready(self.fx.root, self.fx.places))
        config = start.load(self.fx.root/'helene.json')
        self.assertEqual(config['agent']['name'], 'Original agent')
        self.assertEqual(config['model']['key'], 'private-fixture')
        self.assertTrue(config['autonomy']['paused'])
        self.assertTrue(config['backup']['enabled'])
        self._assert_internal_asset_paths(config, self.fx.root, old, backup=backup, reference=reference)
        self._assert_archived_internal_asset_paths(old, backup=backup, reference=reference)

    def _old_with_reference_inside_backup(self):
        old, data, backup, reference = self._old_with_explicit_programme_assets()
        (old/'reference.wav').rename(old/'owner-backups/reference.wav')
        config = start.load(old/'helene.json')
        config['voice']['tts_reference'] = 'owner-backups/reference.wav'
        (old/'helene.json').write_text(json.dumps(config), encoding='utf-8')
        return old, data, backup, reference

    def _assert_reference_stays_inside_retained_backup(self, old, *, backup, reference):
        current = start.load(self.fx.root/'helene.json')
        archive = start.load(self.fx.places.data/'memory/.state/imported-profiles.json')
        self.assertTrue(archive['profiles'])
        profiles = [(current, self.fx.root)]
        profiles.extend((record['config'], Path(record['program'])/Path(record['relative']).parent)
                        for record in archive['profiles'].values())
        for config, home in profiles:
            with self.subTest(profile_home=str(home)):
                self._assert_internal_asset_paths(config, home, old, backup=backup, reference=reference)
                directory = self._configured_path(config, home, 'backup', 'dir')
                selected = self._configured_path(config, home, 'voice', 'tts_reference')
                self.assertEqual(selected, directory/'reference.wav')

    def test_voice_reference_inside_backup_keeps_its_directory_relationship(self):
        old, _data, backup, reference = self._old_with_reference_inside_backup()

        self.fx.prepare()

        self.assertFalse(old.exists())
        self._assert_reference_stays_inside_retained_backup(old, backup=backup, reference=reference)

    def test_voice_reference_inside_backup_survives_late_cleanup_failure_and_retry(self):
        old, _data, backup, reference = self._old_with_reference_inside_backup()
        original_remove = self.fx.backend.remove
        failed = []
        def fail_once(path):
            if path==old and not failed:
                failed.append(path)
                raise OSError('synthetic nested-asset cleanup failure')
            return original_remove(path)

        with patch.object(self.fx.backend, 'remove', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'synthetic nested-asset cleanup failure'):self.fx.prepare()
        self.assertTrue(failed)
        self.assertFalse(start.ready(self.fx.root, self.fx.places))
        with self.subTest(custody='nested assets after failed cleanup'):
            self._assert_reference_stays_inside_retained_backup(old, backup=backup, reference=reference)

        self.fx.prepare()

        self.assertFalse(old.exists())
        self.assertTrue(start.ready(self.fx.root, self.fx.places))
        self._assert_reference_stays_inside_retained_backup(old, backup=backup, reference=reference)

    @unittest.skipUnless(os.name=='nt', 'Windows process affinity with native Windows paths')
    def test_python_argument_path_mentions_do_not_grant_process_ownership(self):
        old, data = self.fx.old()
        external = self.fx.base/'Independent tools'
        python = external/'python.exe'
        cases = [
            [python, external/'report.py', '--note', old/'app/deskapp.py'],
            [python, external/'report.py', '--output', old/'report.json'],
            [python, external/'report.py', '--data', old/'data'],
            [python, external/'report.py', '--root', old],
            [python, external/'report.py', '--tree='+str(old/'data')],
            [python, external/'report.py', '--data-dir', old/'data'],
            [python, external/'report.py', '--config', old/'helene.json'],
            [python, '-c', 'print("unrelated")', '--root', old],
            [python, '-m', 'unrelated_tool', '--root', old],
        ]
        processes = [_Process(os.getpid()+100+i, python, argv) for i, argv in enumerate(cases)]

        self.assertEqual(self._owned(processes, [old], [data]), [])

    @unittest.skipUnless(os.name=='nt', 'Windows process affinity with native Windows paths')
    def test_known_organs_need_supported_bindings_not_diagnostic_mentions(self):
        old, data = self.fx.old()
        external = self.fx.base/'Independent tools'
        cases = [
            ('helene-body.exe', ['--note', data]),
            ('helene-bridge.exe', ['--output', old/'report.json']),
            ('praxis-body.exe', ['--note='+str(old)]),
            ('praxis-bridge.exe', [data]),
        ]
        processes = [_Process(os.getpid()+200+i, external/name, [external/name, *args])
                     for i, (name, args) in enumerate(cases)]

        self.assertEqual(self._owned(processes, [old], [data]), [])

    @unittest.skipUnless(os.name=='nt', 'Windows process affinity with native Windows paths')
    def test_module_words_after_an_independent_script_are_only_script_arguments(self):
        old, data = self.fx.old()
        external = self.fx.base/'Independent tools'
        python = external/'python.exe'
        process = _Process(os.getpid()+250, python,
            [python, external/'independent.py', '-m', 'localharness.runner', '--root', old])

        self.assertEqual(self._owned([process], [old], [data]), [])

    @unittest.skipUnless(os.name=='nt', 'Windows process affinity with native Windows paths')
    def test_module_words_after_python_c_are_only_command_arguments(self):
        old, data = self.fx.old()
        external = self.fx.base/'Independent tools'
        python = external/'python.exe'
        process = _Process(os.getpid()+251, python,
            [python, '-c', 'print("independent command")', '-m', 'localharness.runner', '--root', old])

        self.assertEqual(self._owned([process], [old], [data]), [])

    @unittest.skipUnless(os.name=='nt', 'Windows process affinity with native Windows paths')
    def test_actual_old_executable_is_owned_when_argv_zero_is_abbreviated(self):
        old, data = self.fx.old()
        pid = os.getpid()+252
        process = _Process(pid, old/'runtime/python.exe', ['python'])

        self.assertEqual({row['pid'] for row in self._owned([process], [old], [data])}, {pid})

    @unittest.skipUnless(os.name=='nt', 'Windows process affinity with native Windows paths')
    def test_spoofed_argv_zero_cannot_make_an_independent_executable_owned(self):
        old, data = self.fx.old()
        external = self.fx.base/'Independent tools'
        process = _Process(os.getpid()+253, external/'python.exe',
            [old/'runtime/python.exe', external/'independent.py'])

        self.assertEqual(self._owned([process], [old], [data]), [])

    @unittest.skipUnless(os.name=='nt', 'Windows process affinity with native Windows paths')
    def test_actual_entry_script_executable_and_organ_data_bindings_are_owned(self):
        old, data = self.fx.old()
        external = self.fx.base/'Independent tools'
        python = external/'python.exe'
        processes = [
            _Process(os.getpid()+300, python,
                     [python, '-B', '-s', old/'app/python_app.py', '--gui']),
            _Process(os.getpid()+301, old/'runtime/old-desktop.exe',
                     [old/'runtime/old-desktop.exe']),
        ]
        for offset, flag in enumerate(('--root', '--data', '--tree', '--data-dir', '--config')):
            exe = external/('helene-body.exe' if offset%2 else 'helene-bridge.exe')
            binding = data/'body/config.json' if flag=='--config' else data
            arguments = [flag, str(binding)] if offset%2 else [flag+'='+str(binding)]
            processes.append(_Process(os.getpid()+310+offset, exe, [exe, *arguments]))

        actual = self._owned(processes, [old], [data])

        self.assertEqual({row['pid'] for row in actual}, {p.info['pid'] for p in processes})

    @unittest.skipUnless(os.name=='nt', 'Windows process affinity with native Windows paths')
    def test_desktop_session_descriptor_requires_exact_pid_birth_and_data_home(self):
        old, data = self.fx.old()
        external = self.fx.base/'Independent tools'
        python = external/'python.exe'
        pid = os.getpid()+400
        descriptor = data/'memory/.control/desktop-session.json'
        good = {'pid': pid, 'birth': 123.5, 'tree': str(data), 'program': str(old)}
        cases = (
            ('matching', good, {pid}),
            ('different pid', dict(good, pid=pid+1), set()),
            ('recycled pid', dict(good, birth=123.0), set()),
            ('different data', dict(good, tree=str(self.fx.base/'other-data')), set()),
        )
        for label, value, expected in cases:
            with self.subTest(identity=label):
                descriptor.write_text(json.dumps(value), encoding='utf-8')
                process = _Process(pid, python, [python, external/'session_host.py'], birth=123.5)
                rows = self._owned([process], [old], [data])
                self.assertEqual({row['pid'] for row in rows}, expected)


if __name__=='__main__':unittest.main()
