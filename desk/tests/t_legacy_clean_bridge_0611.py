"""Old 2.4 nested clean admission keeps its sender and runtime protocol intact."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import clean_update as clean, legacy_clean_bridge as bridge
from localharness import python_source_updater as updater, python_updates as updates
from localharness import migration_lifecycle, owner_stop, program_state, source_merge
from localharness import parallel_files, update_profile, source_admission, portable_switch
from localharness.package_notice import read, write
from localharness.update_source import choice_path


class LegacyCleanBridge(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='legacy-clean-bridge-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.old = self.base / 'old'
        self.stage = self.old / '.helene/updates/.helene-update-old24'
        self.source = self.stage / 'Helene'
        self.data = self.base / 'data'
        for root in (self.old, self.source):
            (root / 'app/localharness').mkdir(parents=True)
            (root / 'tree').mkdir()
            write(root / 'python-app.json', {'version': '2.4.0' if root == self.old else '2.5.0'})
            (root / 'app/python_app.py').write_bytes(b'# synthetic desktop entry\n')
        (self.source / 'app/localharness/python_source_updater.py').write_bytes(b'# synthetic helper entry\n')
        (self.source / 'tree/incoming.py').write_bytes(b'INCOMING = True\n')
        (self.data / 'memory').mkdir(parents=True)
        (self.data / 'memory/history').write_bytes(b'owner history must stay')
        write(self.old / 'helene.json', {'mode': 'remote', 'tree': str(self.data), 'setup_complete': True})
        recovery = program_state.recovery(self.old, 'old24-operation')
        recovery.mkdir(parents=True)
        self.prepared = {'format': 'helene-prepared-install.v1', 'old': str(self.old), 'new': str(self.source),
            'note': {'id': 'old24-operation', 'source_policy': 'clean'}, 'recovery': str(recovery),
            'progress': str(program_state.progress(self.old, 'old24-operation'))}
        write(updater.prepared_install_path(self.source), self.prepared)
        from localharness.update_progress import Progress
        Progress(self.prepared['progress'], 'old24-operation', self.old).phase('prepared')
        write(choice_path(self.source), {'format': 'helene-source-choice.v1', 'old': str(self.old),
            'new': str(self.source), 'mode': 'clean', 'by': 'updater'})
        self.args = SimpleNamespace(old=self.old, new=self.source, staging=self.stage,
            pid=42, cleanup=False, browser=False, archive=[], job=None, job_id='', admission_name='old24-admitted.json')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('seed_file', 'client_file'):
            self.stack.enter_context(patch.object(owner_stop, name, return_value=self.base / (name + '.stop')))
        self.stack.enter_context(patch('localharness.session_exit.requested', return_value=False))
        self.stack.enter_context(patch('localharness.portable_profile.default_data', return_value=self.base / 'permanent/data'))
        self.effects = []
        for name in ('stop', 'quiescent', 'cleanup_installation', 'install_shortcuts'):
            self.stack.enter_context(patch.object(migration_lifecycle, name,
                side_effect=lambda *a, _n=name, **k: self.effects.append((_n, a)) or {}))
        original = bridge.required
        # Exercise the pure old-POSIX route on every host without changing
        # global os.name or Path's host-specific implementation.
        self.stack.enter_context(patch.object(bridge, 'required',
            side_effect=lambda *a, **k: original(*a, posix=True)))
        for module, name in ((source_merge, 'inventory'), (update_profile, 'selection'),
                (source_admission, 'activate'), (portable_switch, 'payload'), (parallel_files, 'verify_pairs')):
            self.stack.enter_context(patch.object(module, name, side_effect=AssertionError('No programme validation: ' + name)))
        self.external = None
        self.original_admission = None
        self.original_returned = False

    def argv(self, args=None):
        args = args or self.args
        value = ['python_source_updater.py', '--old', str(args.old), '--new', str(args.new),
            '--staging', str(args.staging), '--pid', str(args.pid), '--source-mode', 'clean',
            '--admission-name', args.admission_name]
        if args.job:
            value.extend(['--job', str(args.job), '--job-id', args.job_id])
        return value

    def wait_original(self, pid, stopped=lambda: False):
        self.assertEqual(pid, 42)
        first = self.original_admission is None
        child = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
        self.original_admission = updates.await_admission(child, self.stage, self.old, receipt_name='old24-admitted.json')
        self.assertNotIn('helper_sha256', self.original_admission)
        self.assertTrue(self.source.is_dir())
        if first:self.assertFalse(bridge.journal(self.old, self.source).exists())

    def spawn_external(self, command, **kwargs):
        self.assertIsNotNone(self.original_admission)
        self.external = command
        self.assertEqual(command[command.index('--pid') + 1], str(os.getpid()))
        target = Path(command[command.index('--new') + 1])
        stage = Path(command[command.index('--staging') + 1])
        self.assertFalse(target.is_relative_to(self.old))
        self.assertTrue(self.source.is_dir())
        if self.args.job:
            current = read(self.args.job)
            self.assertEqual(current['staging'], str(self.stage))
            self.assertEqual(current['progress'], self.prepared['progress'])
        import psutil
        write(stage / command[command.index('--admission-name') + 1], {'pid': os.getpid(),
            'birth': psutil.Process().create_time(), 'target': str(self.old), 'preparation_completed': True})
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)

    def original_main(self):
        with patch.object(sys, 'argv', self.argv()), patch.object(updater, 'wait_owner', side_effect=self.wait_original), \
                patch.object(bridge.subprocess, 'Popen', side_effect=self.spawn_external):
            result = updater.main()
        self.original_returned = True
        return result

    def complete_external(self):
        target = Path(self.external[self.external.index('--new') + 1])
        def wait_bridge(pid, stopped=lambda: False):
            self.assertEqual(pid, os.getpid())
            self.assertTrue(self.original_returned)
        def start_new(*a, **k):
            note = read(target / 'python-migration.json')
            note.update(state='new_app_started', startup_proof={'tree': str(self.data), 'code': str(target / 'tree')})
            write(target / 'python-migration.json', note)
            return SimpleNamespace(poll=lambda: None)
        with patch.object(sys, 'argv', self.external[1:]), patch.object(updater, 'wait_owner', side_effect=wait_bridge), \
                patch.object(clean.subprocess, 'Popen', side_effect=start_new):
            self.assertEqual(updater.main(), 0)
        return target

    def test_normal_main_admits_old_sender_then_external_helper_owns_cutover(self):
        before = updater.prepared_install_path(self.source).read_bytes()
        with patch.object(clean, 'run', side_effect=AssertionError('Original runtime cannot retire itself')):
            self.assertEqual(self.original_main(), 0)
        self.assertEqual(updater.prepared_install_path(self.source).read_bytes(), before)
        self.assertEqual(read(self.stage / 'old24-admitted.json'), self.original_admission)
        self.assertTrue(self.old.is_dir())
        target = self.complete_external()
        self.assertFalse(self.old.exists())
        self.assertEqual(read(target / 'python-migration.json')['state'], 'completed')
        self.assertEqual((self.data / 'memory/history').read_bytes(), b'owner history must stay')

    def test_durable_job_and_incoming_reference_transfer_without_false_completion(self):
        from localharness import desktop_update_request as jobs
        self.args.job = jobs.path(self.data)
        self.args.job_id = 'durable-old24'
        write(self.args.job, {'id': self.args.job_id, 'tree': str(self.data), 'program': str(self.old),
            'state': 'handed_off', 'source_mode': 'clean', 'staging': str(self.stage), 'progress': self.prepared['progress']})
        self.assertEqual(self.original_main(), 0)
        target = Path(self.external[self.external.index('--new') + 1])
        current = read(self.args.job)
        self.assertEqual(current['state'], 'handed_off')
        self.assertEqual(current['staging'], str(target.parent))
        self.assertEqual(current['helper']['pid'], os.getpid())
        self.assertTrue(Path(current['progress']).is_relative_to(target))
        self.assertEqual(self.external[self.external.index('--job') + 1], str(self.args.job))
        self.assertEqual(self.external[self.external.index('--job-id') + 1], self.args.job_id)
        self.assertEqual(read(jobs.incoming_reference(target)), {'id': self.args.job_id, 'path': str(self.args.job),
            'program': str(self.old), 'incoming': str(target)})
        self.assertNotIn('result', current)
        self.complete_external()
        completed = read(self.args.job)
        self.assertEqual(completed['state'], 'completed')
        self.assertEqual(completed['result']['installed_root'], str(target))
        self.assertEqual(completed['result']['effect'], 'committed')
        self.assertFalse(self.old.exists())

    def test_durable_pre_admission_failure_retries_original_stage_without_copying_again(self):
        from localharness import desktop_update_request as jobs
        self.args.job = jobs.path(self.data)
        self.args.job_id = 'durable-retry-old24'
        write(self.args.job, {'id': self.args.job_id, 'tree': str(self.data), 'program': str(self.old),
            'state': 'handed_off', 'source_mode': 'clean', 'staging': str(self.stage), 'progress': self.prepared['progress']})
        with patch.object(sys, 'argv', self.argv()), patch.object(updater, 'wait_owner', side_effect=self.wait_original), \
                patch.object(bridge.subprocess, 'Popen', side_effect=OSError('external admission launch refused')):
            with self.assertRaisesRegex(OSError, 'external admission launch refused'):
                updater.main()
        failed = read(self.args.job)
        self.assertEqual(failed['state'], 'attention')
        self.assertEqual(failed['staging'], str(self.stage))
        self.assertEqual(failed['progress'], self.prepared['progress'])
        self.assertNotIn('result', failed)
        with patch.object(parallel_files, 'copytree', side_effect=AssertionError('Completed copy must be reused')):
            self.assertEqual(self.original_main(), 0)
        self.complete_external()
        self.assertEqual(read(self.args.job)['state'], 'completed')

    def test_durable_owner_cancel_after_original_admission_prevents_copy_and_new_launch(self):
        from localharness import desktop_update_request as jobs
        self.args.job = jobs.path(self.data)
        self.args.job_id = 'cancel-old24'
        write(self.args.job, {'id': self.args.job_id, 'tree': str(self.data), 'program': str(self.old),
            'state': 'handed_off', 'source_mode': 'clean', 'staging': str(self.stage), 'progress': self.prepared['progress']})
        def stopped_after_admission(pid, stopped=lambda: False):
            self.wait_original(pid, stopped)
            jobs.request(self.data, 'cancel', root=self.old, identity=self.args.job_id, by='window')
        with patch.object(sys, 'argv', self.argv()), patch.object(updater, 'wait_owner', side_effect=stopped_after_admission), \
                patch.object(parallel_files, 'copytree') as copier, patch.object(bridge.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(InterruptedError, 'Owner stopped'):
                updater.main()
            copier.assert_not_called(); spawn.assert_not_called()
        self.assertEqual(read(self.args.job)['state'], 'cancelled')
        self.assertTrue(self.source.is_dir())
        self.assertEqual((self.data / 'memory/history').read_bytes(), b'owner history must stay')

    def test_partial_copy_retry_removes_only_owned_target_and_reuses_completed_copy(self):
        original = parallel_files.copytree
        def partial(source, target, **kwargs):
            (target / 'partial-only').write_bytes(b'interrupted copy')
            raise OSError('controlled copy failure')
        with patch.object(parallel_files, 'copytree', side_effect=partial):
            with self.assertRaisesRegex(OSError, 'controlled copy'):
                bridge.materialize(self.old, self.source, self.prepared, lambda: False)
        target = bridge.location(self.old, self.source) / 'Helene'
        self.assertTrue((target / 'partial-only').exists())
        with patch.object(parallel_files, 'copytree', wraps=original) as copies:
            _, reused, _ = bridge.materialize(self.old, self.source, self.prepared, lambda: False)
            bridge.materialize(self.old, self.source, self.prepared, lambda: False)
        self.assertEqual(copies.call_count, 1)
        self.assertEqual(reused, target)
        self.assertFalse((target / 'partial-only').exists())
        self.assertEqual((target / 'tree/incoming.py').read_bytes(), b'INCOMING = True\n')
        self.assertTrue(self.source.is_dir())

    def test_replaced_partial_target_is_preserved(self):
        with patch.object(parallel_files, 'copytree', side_effect=OSError('controlled')):
            with self.assertRaises(OSError):
                bridge.materialize(self.old, self.source, self.prepared, lambda: False)
        target = bridge.location(self.old, self.source) / 'Helene'
        target.rename(target.with_name('original-partial'))
        target.mkdir(); (target / 'unrelated').write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'journal-owned'):
            bridge.materialize(self.old, self.source, self.prepared, lambda: False)
        self.assertEqual((target / 'unrelated').read_bytes(), b'keep')

    def test_stop_before_copy_and_after_copy_preserves_original_programme_and_data(self):
        with patch.object(parallel_files, 'copytree') as copier:
            with self.assertRaises(InterruptedError):
                bridge.materialize(self.old, self.source, self.prepared, lambda: True)
            copier.assert_not_called()
        original = parallel_files.copytree
        stopped = False
        def copied(*a, **k):
            nonlocal stopped
            original(*a, **k); stopped = True
        with patch.object(parallel_files, 'copytree', side_effect=copied), patch.object(clean, 'prepare') as prepare:
            with self.assertRaises(InterruptedError):
                bridge.materialize(self.old, self.source, self.prepared, lambda: stopped)
            prepare.assert_not_called()
        self.assertTrue(self.source.is_dir())
        self.assertEqual((self.data / 'memory/history').read_bytes(), b'owner history must stay')

    def test_existing_live_or_unknown_external_helper_never_spawns_a_duplicate(self):
        self.assertEqual(self.original_main(), 0)
        with patch.object(bridge.subprocess, 'Popen', side_effect=AssertionError('Duplicate helper')):
            self.assertEqual(bridge.handoff(self.args, self.prepared, lambda *a, **k: None, lambda: False), 0)
        self.assertFalse(bridge.can_reopen(self.old, self.source))
        with patch('localharness.session_host.process_state', return_value='unknown'), \
                patch.object(bridge.subprocess, 'Popen', side_effect=AssertionError('Uncertain helper replay')):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                bridge.handoff(self.args, self.prepared, lambda *a, **k: None, lambda: False)
            self.assertFalse(bridge.can_reopen(self.old, self.source))

    def test_launch_pending_without_a_process_receipt_fails_closed(self):
        bridge.materialize(self.old, self.source, self.prepared, lambda: False)
        record = bridge.journal(self.old, self.source)
        value = read(record); value['state'] = 'launch_pending'; write(record, value)
        with patch.object(bridge.subprocess, 'Popen', side_effect=AssertionError('Uncertain launch replay')):
            with self.assertRaisesRegex(RuntimeError, 'launch is still uncertain'):
                bridge.handoff(self.args, self.prepared, lambda *a, **k: None, lambda: False)
        self.assertFalse(bridge.can_reopen(self.old, self.source))

    def test_copy_failure_reopens_previous_only_when_data_leases_are_free(self):
        for available in (False, True):
            with self.subTest(available=available), patch.object(sys, 'argv', self.argv()), \
                    patch.object(updater, 'wait_owner', side_effect=self.wait_original), \
                    patch.object(parallel_files, 'copytree', side_effect=OSError('copy failure')), \
                    patch.object(migration_lifecycle, 'owner_lease_available', return_value=available), \
                    patch.object(updater.subprocess, 'Popen', return_value=SimpleNamespace(pid=1)) as spawn:
                with self.assertRaisesRegex(OSError, 'copy failure'):
                    updater.main()
                self.assertEqual(spawn.call_count, int(available))
                if available:
                    command = spawn.call_args.args[0]
                    self.assertIn('-c', command)
                    self.assertIn(str(self.old / 'app/python_app.py'), command)
            self.assertTrue(self.old.is_dir())

    def test_explicit_mechanical_and_preserve_records_never_enter_bridge(self):
        for change in ({'folder_switch': False}, {'note': {'id': 'old24-operation', 'source_policy': 'preserve'}}):
            record = dict(self.prepared, **change)
            self.assertFalse(bridge.required(self.old, self.source, record))
        external = bridge.location(self.old, self.source) / 'Helene'
        self.assertFalse(bridge.required(self.old, external, dict(self.prepared, new=str(external))))

    def test_helper_that_exits_before_birth_observation_allows_guarded_recovery(self):
        import psutil
        child = SimpleNamespace(pid=987654321, poll=lambda: 7, returncode=7)
        with patch.object(bridge.subprocess, 'Popen', return_value=child), \
                patch.object(psutil, 'Process', side_effect=psutil.NoSuchProcess(child.pid)):
            with self.assertRaisesRegex(RuntimeError, 'exited before admission'):
                bridge.handoff(self.args, self.prepared, lambda *a, **k: None, lambda: False)
        self.assertTrue(bridge.can_reopen(self.old, self.source))
        self.assertEqual(read(bridge.journal(self.old, self.source))['failed_launch_exit'], 7)

    def test_failure_never_reopens_an_old_process_that_is_still_live(self):
        stage, target, _ = bridge.materialize(self.old, self.source, self.prepared, lambda: False)
        (self.old / 'helene.json').unlink()  # no profile lease can prove GUI exit
        args = SimpleNamespace(**dict(vars(self.args), new=target, staging=stage))
        with patch.object(sys, 'argv', self.argv(args)), patch.object(updater, 'wait_owner'), \
                patch.object(clean, 'run', side_effect=ValueError('refused before stop')), \
                patch.object(migration_lifecycle, 'quiescent', side_effect=RuntimeError('old process alive')), \
                patch.object(updater.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'refused before stop'):
                updater.main()
            spawn.assert_not_called()

    def test_committed_new_with_retired_old_never_attempts_recovery(self):
        _, target, _ = bridge.materialize(self.old, self.source, self.prepared, lambda: False)
        note = read(target / 'python-migration.json')
        note.update(state='completed', startup_proof={'tree': str(self.data)})
        write(target / 'python-migration.json', note)
        shutil.rmtree(self.old)
        self.assertFalse(bridge.can_reopen(self.old, self.source))

    def test_damaged_old_configuration_does_not_mask_primary_switch_error(self):
        stage, target, _ = bridge.materialize(self.old, self.source, self.prepared, lambda: False)
        args = SimpleNamespace(**dict(vars(self.args), new=target, staging=stage))
        def refusal(*a, **k):
            (self.old / 'helene.json').write_bytes(b'{damaged')
            raise RuntimeError('primary cutover refusal')
        with patch.object(sys, 'argv', self.argv(args)), patch.object(updater, 'wait_owner'), \
                patch.object(clean, 'run', side_effect=refusal), patch.object(updater.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'primary cutover refusal'):
                updater.main()
            spawn.assert_not_called()

    def test_failed_external_start_keeps_old_and_requests_one_guarded_reopen(self):
        self.original_main()
        target = Path(self.external[self.external.index('--new') + 1])
        recovered = []
        def spawn(command, **kwargs):
            if '-c' in command:
                recovered.append(command)
                return SimpleNamespace(pid=1)
            return SimpleNamespace(poll=lambda: 1)
        with patch.object(sys, 'argv', self.external[1:]), patch.object(updater, 'wait_owner'), \
                patch.object(updater.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(RuntimeError, 'New application failed'):
                updater.main()
        self.assertEqual(len(recovered), 1)
        self.assertTrue(self.old.is_dir())
        self.assertEqual(read(target / 'python-migration.json')['state'], 'failed_before_retirement')
        self.assertEqual((self.data / 'memory/history').read_bytes(), b'owner history must stay')

    def test_independent_root_guard_precedes_external_lease_mutation(self):
        with patch.object(bridge, 'location', return_value=self.old / 'forbidden'), \
                patch('python_migrate.installation_lock') as lease:
            with self.assertRaisesRegex(ValueError, 'independent programme folders'):
                bridge.handoff(self.args, self.prepared, lambda *a, **k: None, lambda: False)
            lease.assert_not_called()
        self.assertFalse((self.old / 'forbidden').exists())

    def test_real_subprocess_main_admits_old_sender_before_any_copy(self):
        # Real helper PID/birth + actual sender admission consumer. Only OS
        # process-control boundaries are synthetic in the disposable child.
        code = r'''
import os, sys, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from localharness import python_source_updater as updater,legacy_clean_bridge as bridge,migration_lifecycle
from localharness.package_notice import write
base=Path(sys.argv[2]);old=base/'old';stage=old/'.helene/updates/.helene-update-old24';source=stage/'Helene'
original=bridge.required
def wait_owner(pid,stopped=lambda:False):
 deadline=time.monotonic()+15
 while not (base/'sender-admitted').exists():
  if time.monotonic()>deadline:raise TimeoutError('sender did not admit')
  time.sleep(.02)
 assert not bridge.journal(old,source).exists()
def spawn(command,**kwargs):
 import psutil
 target=Path(command[command.index('--staging')+1])
 write(target/command[command.index('--admission-name')+1],{'pid':os.getpid(),'birth':psutil.Process().create_time(),'target':str(old)})
 write(base/'external-command.json',{'argv':command})
 return SimpleNamespace(pid=os.getpid(),poll=lambda:None)
sys.argv=['python_source_updater.py','--old',str(old),'--new',str(source),'--staging',str(stage),
 '--pid','42','--source-mode','clean','--admission-name','subprocess-admitted.json']
with patch.object(bridge,'required',side_effect=lambda *a,**k:original(*a,posix=True)), \
 patch.object(updater,'wait_owner',side_effect=wait_owner),patch.object(bridge.subprocess,'Popen',side_effect=spawn), \
 patch.object(migration_lifecycle,'stop'),patch.object(migration_lifecycle,'quiescent'), \
 patch('localharness.owner_stop.stopped',return_value=False),patch('localharness.session_exit.requested',return_value=False), \
 patch('localharness.portable_profile.default_data',return_value=base/'permanent/data'):
 raise SystemExit(updater.main())
'''
        with (self.base / 'subprocess.log').open('wb') as output:
            child = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code,
                str(Path(updater.__file__).resolve().parents[1]), str(self.base)], stdout=output, stderr=subprocess.STDOUT)
            try:
                admitted = updates.await_admission(child, self.stage, self.old, timeout=15, receipt_name='subprocess-admitted.json')
                self.assertEqual(admitted['pid'], child.pid)
                self.assertIn('birth', admitted)
                self.assertNotIn('helper_sha256', admitted)
                self.assertFalse(bridge.journal(self.old, self.source).exists())
                (self.base / 'sender-admitted').write_bytes(b'old sender may exit now')
                child.wait(timeout=30)
            finally:
                if child.poll() is None:
                    child.terminate(); child.wait(timeout=5)
        self.assertEqual(child.returncode, 0, (self.base / 'subprocess.log').read_text(encoding='utf8'))
        command = read(self.base / 'external-command.json')['argv']
        self.assertEqual(command[command.index('--pid') + 1], str(child.pid))
        self.assertTrue(self.source.is_dir())
        self.assertFalse(Path(command[command.index('--new') + 1]).is_relative_to(self.old))


if __name__ == '__main__':
    unittest.main()
