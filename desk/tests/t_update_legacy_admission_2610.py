"""Legacy durable admission and its mechanical 15s consumer; no child processes."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import python_source_updater as updater, python_updates as updates
from localharness import update_progress, update_window, update_overlay, app_migration
from localharness import migration_lifecycle, desktop_supervisor, code_transition
from localharness import desktop_update_request as jobs, owner_stop
from localharness import update_source

# Frozen original main: the OLD admission timeout is reproduced by its actual
# await_admission consumer, not a test's inferred time threshold. This is the
# original pre-observer prepare block, with parser/custody delegated to fixture.
PRIOR_HANDOFF = '''def prior_handoff(args):
    prepared = prepared_install_path(args.new)
    if not prepared.is_file() and (args.new / 'python-app.json').is_file():
        from localharness.update_progress import Progress
        operation=args.staging.name.removeprefix('.helene-update-')
        progress=Progress(args.staging.with_name(args.staging.name+'.progress.json'),operation,args.old)
        prepare_install(args.old,args.new,progress=progress)
    if prepared.is_file():
        from localharness.update_window import run
        import threading
        args.observer_ready = threading.Event()
        receipt = json.loads(prepared.read_text(encoding='utf-8')).get('progress')
        if not receipt:raise ValueError('Prepared update has no progress receipt')
        return run(lambda: perform(args), receipt, args.observer_ready, browser=args.browser)
    return perform(args)
'''


def put(root, name, content=b'fixture'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class LegacyAdmission(unittest.TestCase):
    def setUp(self):
        # All synthetic effects are confined to this test-owned temporary tree.
        folder = Path(__file__).resolve().parent
        temporary = tempfile.TemporaryDirectory(prefix='legacy-admission-', dir=folder)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.old = self.root / 'application'
        self.staging = self.root / '.helene-update-legacy'
        self.new = self.staging / 'Helene'
        for name in ('a', 'b'):
            put(self.old, name, b'old-' + name.encode())
            put(self.new, name, b'new-' + name.encode())
        put(self.old, 'python-app.json', b'{"version":"2.1.1","delivery":"portable"}')
        put(self.new, 'python-app.json', b'{"version":"2.2.0","delivery":"portable"}')
        put(self.new, 'app/python_app.py', b'# synthetic entry\n')
        put(self.new, 'tree/main.py', b'# sealed synthetic incoming source\n')
        code_transition.seal(self.new)
        self.tree = self.root / 'profile'
        put(self.tree, 'memory/identity.md', b'synthetic retained identity\n')
        put(self.old, 'helene.json', json.dumps({'mode': 'remote', 'tree': str(self.root / 'profile'),
             'setup_complete': False}).encode('utf-8'))
        update_source.select(self.old, self.new, 'preserve', by='updater')
        # Redirect both real Stop readers to disjoint synthetic latches, rather
        # than consulting or modifying the owner's installed profile.
        for name in ('seed_file', 'client_file'):
            redirect = patch.object(owner_stop, name, return_value=self.root / (name + '.stop'))
            redirect.start()
            self.addCleanup(redirect.stop)
        self.args = SimpleNamespace(old=self.old, new=self.new, pid=42, staging=self.staging,
                                    archive=[], cleanup=False, browser=False)
        self.clock = 0.0
        self.owner_exited = False
        self.admitted = None
        self.child = SimpleNamespace(pid=os.getpid(), poll=Mock(return_value=None),
                                     terminate=Mock(), wait=Mock())
        self.original_bytes = {p.relative_to(self.old): p.read_bytes() for p in self.old.rglob('*') if p.is_file()}

    def await_old(self, expired=False):
        start = self.clock
        times = iter((0.0, self.clock)) if expired else None
        with patch.object(updates.time, 'monotonic', side_effect=(lambda: next(times)) if times else lambda: self.clock), \
             patch.object(updates.time, 'sleep'):
            return updates.await_admission(self.child, self.staging, self.old, timeout=15)

    def wait_old(self, pid, stopped=lambda: False):
        self.assertEqual(pid, 42)
        self.admitted = self.await_old()
        self.assertLess(self.clock, 15)
        self.owner_exited = True

    def observer(self, transaction, receipt, ready, **kwargs):
        self.assertFalse(self.owner_exited)
        self.assertFalse((self.staging / 'updater-admitted.json').exists())
        self.assertFalse(updater.prepared_install_path(self.new).exists())
        value = json.loads(Path(receipt).read_text(encoding='utf-8'))
        self.assertFalse(value['preparation_completed'])
        self.assertNotIn('prepared', [r['stage'] for r in value['stages']])
        self.clock = 3.5  # actual native paint measured separately by parent
        ready.set()
        return transaction()

    def run_mechanical(self, *, observer=None, **patches):
        # Unprepared main now admits an agent-owned migration job. These
        # historical timing/journal cases still exercise the real mechanical
        # consumer perform(), not a mock of the retired main preparation branch.
        import threading
        prepared = updater.prepared_install_path(self.new)
        receipt = (json.loads(prepared.read_text(encoding='utf-8'))['progress'] if prepared.is_file()
                   else str(self.staging.with_name(self.staging.name + '.progress.json')))
        if not prepared.is_file():
            update_progress.Progress(Path(receipt), 'legacy', self.old).phase('handoff', preparation_completed=False)
        args = SimpleNamespace(**vars(self.args), observer_ready=threading.Event(), progress_path=receipt)
        with ExitStack() as stack:
            stack.enter_context(patch.object(updater, 'wait_owner', side_effect=self.wait_old))
            for name, value in patches.items():
                stack.enter_context(patch.object(updater, name, side_effect=value))
            # Already-prepared work still uses the actual CLI main and real
            # installation lease; only its unprepared predecessor is direct.
            transaction = self.run_entry if prepared.is_file() else lambda: updater.perform(args)
            return (observer or self.observer)(transaction, receipt, args.observer_ready)

    def assert_old_unchanged(self):
        self.assertEqual(self.original_bytes, {p.relative_to(self.old): p.read_bytes()
                         for p in self.old.rglob('*') if p.is_file()})

    def run_entry(self):
        argv = ['python_source_updater.py', '--old', str(self.old), '--new', str(self.new),
                '--staging', str(self.staging), '--pid', '42']
        with patch.object(sys, 'argv', argv):
            return updater.main()

    def test_current_main_admits_real_durable_job_without_wizard_or_preparation(self):
        import psutil
        spawned = SimpleNamespace(pid=os.getpid(), handle=object(), poll=Mock(return_value=None),
                                  release=Mock(), stop=Mock())
        def spawn(command, **kwargs):
            self.assertEqual(Path(command[3]), self.new / 'app/localharness/session_host.py')
            self.assertEqual(Path(kwargs['cwd']), self.new)
            self.assertEqual(command[command.index('--control-tree') + 1], str(self.tree))
            value = jobs.status(self.tree)
            self.assertEqual(value['state'], 'admitting')
            self.assertEqual(value['confirmed_by'], 'legacy-update-invocation')
            self.assertEqual(value['migration']['data'], str(self.tree))
            self.assertEqual(value['migration']['source_mode'], 'preserve')
            value.update(state='running', admission={'pid':os.getpid(),
                         'birth':psutil.Process().create_time(), 'generation':'synthetic-host'})
            jobs._save(self.tree, value)
            return spawned
        before = (self.tree / 'memory/identity.md').read_bytes()
        with patch.object(desktop_supervisor.JobChild, 'spawn', side_effect=spawn) as start, \
             patch.object(update_window, 'run') as window, \
             patch.object(updater, 'prepare_install') as prepare, \
             patch.object(updater, 'wait_owner') as wait, \
             patch.object(updater.subprocess, 'Popen') as reopen:
            self.assertEqual(self.run_entry(), 0)
            # An exact repeated invocation rejoins the real persisted admission,
            # rather than emitting another work item or spawning a second host.
            self.assertEqual(self.run_entry(), 0)
            start.assert_called_once()
            window.assert_not_called(); prepare.assert_not_called()
            wait.assert_not_called(); reopen.assert_not_called()
        spawned.release.assert_called_once(); spawned.stop.assert_not_called()
        value = jobs.status(self.tree)
        admission = json.loads((self.staging / 'updater-admitted.json').read_text(encoding='utf-8'))
        self.assertEqual(admission['job_id'], value['id'])
        self.assertEqual(admission['durable_owner'], value['admission'])
        self.assertEqual(admission['job_path'], str(jobs.path(self.tree)))
        self.assertTrue(admission['process_exit_allowed'])
        self.assertFalse(admission['preparation_completed'])
        legacy = json.loads((self.new / ('.python-migrator-' + str(os.getpid()) + '.json')).read_text(encoding='utf-8'))
        self.assertFalse(legacy['wizard_opened'])
        self.assertEqual(legacy['admission_kind'], 'durable-agent-job')
        self.assertFalse(updater.prepared_install_path(self.new).exists())
        self.assertEqual(before, (self.tree / 'memory/identity.md').read_bytes())
        self.assert_old_unchanged()

    def test_current_main_missing_data_and_owner_stop_refuse_before_host_spawn(self):
        missing = self.root / 'missing-profile'
        put(self.old, 'helene.json', json.dumps({'mode':'remote', 'tree':str(missing)}).encode())
        with patch.object(desktop_supervisor.JobChild, 'spawn') as spawn:
            with self.assertRaisesRegex(ValueError, 'existing data folder'):
                self.run_entry()
            put(self.old, 'helene.json', self.original_bytes[Path('helene.json')])
            put(self.root, 'seed_file.stop', b'synthetic owner Stop')
            with self.assertRaisesRegex(RuntimeError, 'Owner Stop forbids migration admission'):
                self.run_entry()
            spawn.assert_not_called()
        self.assertEqual(jobs.status(self.tree), {'state':'none'})
        self.assertFalse((self.staging / 'updater-admitted.json').exists())
        self.assert_old_unchanged()

    def test_prior_order_exceeds_actual_old_15s_admission_and_is_terminated(self):
        def slow_prepare(*args, **kwargs):
            self.clock = 16
            self.await_old(expired=True)
            self.fail('old owner must not accept a missing admission')
        with patch.object(updater, 'prepare_install', side_effect=slow_prepare), \
             patch.object(update_window, 'run') as observer:
            namespace = dict(vars(updater))
            exec(PRIOR_HANDOFF, namespace)
            with self.assertRaisesRegex(TimeoutError, 'admission was not confirmed'):
                namespace['prior_handoff'](self.args)
            observer.assert_not_called()
        self.child.terminate.assert_called_once()
        self.assertFalse(self.owner_exited)
        self.assert_old_unchanged()

    def test_legacy_admitted_before_slow_prepare_and_complete_journal_precedes_first_write(self):
        real_prepare, real_copy = updater.prepare_install, update_overlay.copy_verified
        observed = []
        def prepare(*args, **kwargs):
            self.assertTrue(self.owner_exited)
            self.assertFalse(self.admitted['preparation_completed'])
            self.clock += 16  # same work that defeated the prior 15s protocol
            self.child.terminate.assert_not_called()
            return real_prepare(*args, **kwargs)
        def copy(source, target, sha, progress=None, category='copy'):
            if category == 'publish_copy':
                self.assertTrue(self.owner_exited)
                value = json.loads(updater.prepared_install_path(self.new).read_text(encoding='utf-8'))
                recovery = Path(value['recovery'])
                rows = json.loads((recovery / 'update-files.json').read_text(encoding='utf-8'))
                for row in rows:
                    if row['existed']:
                        self.assertTrue((recovery / row['path']).is_file())
                self.assertTrue(update_progress.status(self.old)['preparation_completed'])
                observed.append(Path(source).name)
            return real_copy(source, target, sha, progress, category)
        def spawn(*args, **kwargs):
            path = self.old / 'python-migration.json'
            note = json.loads(path.read_text(encoding='utf-8'))
            note['state'] = 'new_app_started'
            path.write_text(json.dumps(note), encoding='utf-8')
            return SimpleNamespace(pid=99, handle=object(), poll=lambda: None, release=Mock())
        with patch.object(update_overlay, 'copy_verified', side_effect=copy), \
             patch.object(desktop_supervisor.JobChild, 'spawn', side_effect=spawn), \
             patch.object(updater.subprocess, 'Popen') as cleanup:
            self.assertEqual(self.run_mechanical(prepare_install=prepare), 0)
            cleanup.assert_called_once()  # command captured, never executed
        self.assertGreater(self.clock, 15)
        self.assertIn('a', observed)
        self.assertEqual((self.old / 'a').read_bytes(), b'new-a')
        self.assertFalse(updater.active_update(self.old).exists())
        self.assertEqual(update_progress.status(self.old)['stage'], 'completed')

    def test_unpainted_observer_refuses_admission_and_does_not_prepare(self):
        class NotPainted:
            def wait(self, timeout):
                self_timeout = timeout
                assert self_timeout == 15
                return False
        def observer(transaction, receipt, ready, **kwargs):
            # Exercise perform's real wait, without spending real 15 seconds.
            return updater.perform(SimpleNamespace(**vars(self.args), observer_ready=NotPainted(), progress_path=receipt))
        with patch.object(updater, 'prepare_install') as prepare, \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(TimeoutError, 'progress window did not become ready'):
                self.run_mechanical(observer=observer)
            prepare.assert_not_called()
            launch.assert_not_called()
        self.assertFalse((self.staging / 'updater-admitted.json').exists())
        self.assertFalse(self.owner_exited)
        self.assert_old_unchanged()

    def test_invalid_staging_and_archive_refuse_before_observer_or_admission(self):
        for kind in ('staging', 'archive'):
            with self.subTest(kind=kind), patch.object(update_window, 'run') as observer, \
                 patch.object(updater, 'prepare_install') as prepare:
                args = SimpleNamespace(**vars(self.args))
                if kind == 'staging':args.new = self.root / 'unbound'
                else:args.archive = [self.root.parent / 'unbound.zip']
                with self.assertRaisesRegex(ValueError, 'staging directory|archive must be beside'):
                    updater.perform(args)
                observer.assert_not_called()
                prepare.assert_not_called()
        self.assertFalse((self.staging / 'updater-admitted.json').exists())
        self.assert_old_unchanged()

    def test_old_exit_timeout_never_prepares_or_reopens_over_live_owner(self):
        def still_alive(pid, stopped=lambda: False):
            raise TimeoutError('old owner still alive')
        with patch.object(updater, 'prepare_install') as prepare, \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(TimeoutError, 'old owner still alive'):
                self.run_mechanical(wait_owner=still_alive)
            prepare.assert_not_called()
            launch.assert_not_called()
        self.assertFalse(self.owner_exited)
        self.assert_old_unchanged()

    def test_unrecovered_marker_refuses_before_old_exit_and_is_never_blessed(self):
        pending = updater.active_update(self.old)
        pending.write_text('{"prior":"unresolved"}', encoding='utf-8')
        with patch.object(updater, 'prepare_install') as prepare, \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'startup recovery'):
                self.run_mechanical()
            prepare.assert_not_called()
            launch.assert_not_called()
        self.assertFalse(self.owner_exited)
        self.assertEqual(pending.read_text(encoding='utf-8'), '{"prior":"unresolved"}')
        self.assert_old_unchanged()

    def test_live_profile_owner_refuses_preparation_and_duplicate_reopen(self):
        tree = self.root / 'profile'
        local = {'tree': str(tree), 'value': {'mode': 'local'}}
        with patch.object(app_migration, 'discover', return_value={'agents': [local]}), \
             patch.object(migration_lifecycle, 'owner_lease_available', return_value=False) as lease, \
             patch.object(updater, 'prepare_install') as prepare, \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'still holds its data lease'):
                self.run_mechanical()
            lease.assert_called_once_with(tree / '.desktop-owner.lock')
            prepare.assert_not_called()
            launch.assert_not_called()
        self.assertTrue(self.owner_exited)
        self.assert_old_unchanged()

    def test_failed_preparation_reopens_only_intact_old_program(self):
        with patch.object(update_overlay, 'journal', side_effect=OSError('preimage journal failure')), \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(OSError, 'preimage journal failure'):
                self.run_mechanical()
            launch.assert_called_once()
        self.assertTrue(self.owner_exited)
        self.assertFalse(updater.active_update(self.old).exists())
        for name, content in self.original_bytes.items():
            self.assertEqual((self.old / name).read_bytes(), content)
        self.assertTrue(update_progress.status(self.old)['previous_reopen_requested'])

    def test_legacy_double_fault_retains_active_journal_and_never_reopens(self):
        real_copy = update_overlay.copy_verified
        def copy(source, target, sha, progress=None, category='copy'):
            if category == 'publish_copy' and Path(source).name == 'b':
                raise OSError('publication failed')
            if category == 'restore_copy':raise OSError('restore failed')
            return real_copy(source, target, sha, progress, category)
        with patch.object(update_overlay, 'copy_verified', side_effect=copy), \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(update_overlay.RestorationIncomplete, 'restoration is incomplete'):
                self.run_mechanical()
            launch.assert_not_called()
        self.assertEqual((self.old / 'a').read_bytes(), b'new-a')
        self.assertTrue(updater.active_update(self.old).is_file())
        record = json.loads(updater.active_update(self.old).read_text(encoding='utf-8'))
        recovery = Path(record['recovery'])
        self.assertTrue((recovery / 'update-files.json').is_file())
        self.assertEqual((recovery / 'a').read_bytes(), b'old-a')
        self.assertTrue(update_progress.status(self.old)['recovery_required'])

    def test_prepared_wrong_target_or_recovery_refuses_before_admission(self):
        progress = update_progress.Progress(self.root / '.helene-update-legacy.progress.json', 'legacy', self.old)
        updater.prepare_install(self.old, self.new, progress=progress)
        path = updater.prepared_install_path(self.new)
        original = json.loads(path.read_text(encoding='utf-8'))
        def observer(transaction, receipt, ready, **kwargs):
            ready.set()
            return transaction()
        for field in ('old', 'new', 'recovery'):
            with self.subTest(field=field):
                changed = dict(original, **{field: str(self.root / 'wrong-target')})
                path.write_text(json.dumps(changed), encoding='utf-8')
                with patch.object(updater, 'prepare_install') as prepare, \
                     patch.object(updater.subprocess, 'Popen') as launch:
                    with self.assertRaisesRegex(ValueError, 'Prepared update (target|recovery) differs'):
                        self.run_mechanical(observer=observer)
                    prepare.assert_not_called()
                    launch.assert_not_called()
                self.assertFalse((self.staging / 'updater-admitted.json').exists())
                self.assertFalse(self.owner_exited)
        self.assert_old_unchanged()

    def test_prepared_22_admits_before_slow_final_validation_without_reprepare(self):
        progress = update_progress.Progress(self.root / '.helene-update-legacy.progress.json', 'legacy', self.old)
        updater.prepare_install(self.old, self.new, progress=progress)
        prepared = json.loads(updater.prepared_install_path(self.new).read_text(encoding='utf-8'))
        real_validate, real_install = updater.validate_prepared, updater.install
        calls = []
        def validate(*args, **kwargs):
            # Realistic full-package validation can exceed the acknowledgement
            # budget; it is still mandatory after old exit and before writes.
            self.assertTrue(self.owner_exited)
            self.clock += 16
            calls.append('validate')
            return real_validate(*args, **kwargs)
        def observer(transaction, receipt, ready, **kwargs):
            self.assertTrue(updater.prepared_install_path(self.new).is_file())
            ready.set()
            return transaction()
        def install(*args, **kwargs):
            self.assertTrue(self.owner_exited)
            self.assertTrue(self.admitted['preparation_completed'])
            self.assertEqual(calls, [])
            real_install(*args, **kwargs)
            self.assertEqual(calls, ['validate'])
            self.assertEqual((self.old / 'a').read_bytes(), b'new-a')
            raise OSError('stop after prepared validation and overlay')
        with patch.object(updater, 'validate_prepared', side_effect=validate), \
             patch.object(updater, 'prepare_install') as prepare, \
             patch.object(updater.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(OSError, 'stop after prepared validation and overlay'):
                self.run_mechanical(observer=observer, install=install)
            prepare.assert_not_called()
            launch.assert_not_called()  # active journal prevents a mixed reopen
        self.assertGreater(self.clock, 15)
        self.child.terminate.assert_not_called()
        self.assertEqual(prepared, json.loads(updater.prepared_install_path(self.new).read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
