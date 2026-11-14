"""Real Windows descendants, durable ownership and fail-closed recovery."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import forge
import forge_recovery
import forge_runtime
import forge_windows_custody as custody
from command_process import CommandProcess, WindowsJob


@unittest.skipUnless(os.name == 'nt', 'native Windows Job ownership')
class WindowsCustody(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'workspace'; self.root.mkdir()
        for name, value in [('BASE', self.base), ('STATE_DIR', self.base/'memory/.forge'),
                            ('TASKS_DIR', self.base/'memory/.forge/tasks'), ('REPO', self.base)]:
            patch = mock.patch.object(forge, name, value); patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(forge_recovery, 'windows_custody', return_value=custody)
        patch.start(); self.addCleanup(patch.stop)
        self.tid = 'code-native-custody'
        forge._save_task({'id': self.tid, 'root': str(self.root), 'scope': 'self',
                         'status': 'active', 'goal': 'native custody fixture'})
        self.script = self.base/'worker.py'
        self.script.write_text("""import json, os, subprocess, sys, time
from pathlib import Path
d = Path(sys.argv[sys.argv.index('--request')+1]).parent
child = '''import subprocess, sys, time
from pathlib import Path
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
Path(sys.argv[1]).write_text(str(p.pid))
time.sleep(120)
'''
p = subprocess.Popen([sys.executable, '-c', child, str(d/'grandchild.pid')])
(d/'child.pid').write_text(str(p.pid))
""", encoding='utf8')

    def unit(self, name='agent-old', kind='agents', parent=''):
        d = forge._unit_dir(self.tid, kind, name)
        forge._atomic_json(d/'request.json', {'id': name, 'task_id': self.tid, 'root': str(self.root),
            'scope': 'self', 'created': forge._now(), 'status': 'starting', 'role': 'worker',
            'parent_unit_id': parent})
        return d

    def launch(self, d):
        pid = forge._spawn_runner(self.script, d/'request.json', d/'supervisor.log')
        proc = forge._RUNNERS[pid]
        self.addCleanup(lambda: forge._RUNNERS.pop(pid, None))
        self.addCleanup(proc.stop_tree)
        request = forge._read_json(d/'request.json')
        request.update(supervisor_pid=pid, supervisor_started_at=forge._proc_started_at(pid), status='running')
        forge._atomic_json(d/'request.json', request)
        deadline = time.monotonic()+10
        while not (d/'grandchild.pid').exists() or proc._proc.poll() is None:
            if time.monotonic() > deadline:
                self.fail('fixture descendants did not start: '+forge._tail(d/'supervisor.log'))
            time.sleep(.02)
        return proc

    def history(self, d):
        return {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()
                and p.name not in {custody.FILE, 'liveness.json'}}

    def assert_tree_gone(self, d):
        import psutil
        for name in ('child.pid', 'grandchild.pid'):
            self.assertFalse(psutil.pid_exists(int((d/name).read_text())))

    def test_missing_leader_still_holds_descendants_until_exact_job_stop(self):
        d = self.unit(); proc = self.launch(d)
        forge._atomic_json(d/'stop.json', {'state': 'unresolved', 'note': 'old Stop'})
        journal = forge_runtime.Journal(d)
        journal.start(forge._read_json(d/'request.json'), {'next_boundary': 'summary_in_flight'})
        journal.payload('retained model output'); journal.close()
        generation = forge_runtime.read_progress(d)['generation']
        (self.root/'work.txt').write_text('keep working copy')
        before = self.history(d)
        self.assertIsNone(proc.poll())
        self.assertFalse(forge._writer_ended(d))
        self.assertIn(d.name, forge._shared_writer_reason(self.tid, ''))
        self.assertFalse(forge_runtime.continuation_info(d)['safe'])
        # Forget the in-memory Popen: durable custody must suffice.
        forge._RUNNERS.pop(proc.pid)
        with mock.patch.object(forge, '_kill_tree', side_effect=AssertionError('no PID fallback')):
            result = forge.control(self.tid, 'stop', d.name, generation)
            self.assertEqual(result['status'], 'settled', result)
            self.assertEqual(forge.control(self.tid, 'stop', d.name, generation)['status'], 'settled')
        self.assert_tree_gone(d)
        self.assertEqual(self.history(d), before)
        self.assertEqual((self.root/'work.txt').read_text(), 'keep working copy')
        self.assertFalse((d/'result.json').exists())
        self.assertTrue(forge._writer_ended(d))
        self.assertEqual(forge._shared_writer_reason(self.tid, ''), '')
        self.assertEqual(forge._unit_state(d)['status'], 'lost')
        self.assertTrue(forge._unit_state(d)['custody_recovered'])
        self.assertFalse(forge_runtime.continuation_info(d)['safe'])
        self.assertEqual(forge_runtime.continuation_info(d)['next_boundary'], 'summary_in_flight')
        self.assertEqual(forge._agent_capacity(self.tid, {'id':'agent-new', 'role':'worker'}), '')

    def test_stop_parent_rechecks_newly_recovered_command_and_check(self):
        parent = self.unit(); self.launch(parent)
        child = self.unit('proc-child', 'processes', parent.name); self.launch(child)
        check = self.unit('verify-child', 'verifications', parent.name); self.launch(check)
        sibling = self.unit('agent-sibling'); sibling_proc = self.launch(sibling)
        result = forge.control(self.tid, 'stop', parent.name)
        self.assertEqual(result['status'], 'settled', result)
        for d in (parent, child, check):
            self.assert_tree_gone(d)
            self.assertTrue(forge._unit_state(d)['custody_recovered'])
            self.assertNotIn(forge._read_json(d/'request.json')['supervisor_pid'], forge._RUNNERS)
        self.assertIsNone(sibling_proc.poll())
        self.assertFalse((sibling/custody.FILE).exists())

    def test_historical_failure_survives_recovery(self):
        d = self.unit('verify-failed', 'verifications'); self.launch(d)
        forge._atomic_json(d/'result.json', {'status':'failed', 'checks':[{'exit':139}]})
        before = self.history(d)
        self.assertIn('extinct', forge.verify(self.tid, 'stop', verification_id=d.name))
        self.assertEqual(self.history(d), before)
        self.assertEqual(forge._unit_state(d)['status'], 'failed')

    def test_wrong_generation_never_signals(self):
        d = self.unit(); proc = self.launch(d)
        result = forge.control(self.tid, 'stop', d.name, 'wrong-generation')
        self.assertEqual(result['status'], 'refused')
        self.assertIsNone(proc.poll())
        self.assertFalse((d/custody.FILE).exists())

    def test_access_denial_is_unknown_even_with_terminal_result(self):
        d = self.unit('proc-denied', 'processes'); proc = self.launch(d)
        forge._atomic_json(d/'result.json', {'status':'done'})
        with mock.patch.object(custody, 'stop_job', side_effect=PermissionError('fixture access denied')):
            self.assertIn('unconfirmed', forge.process(self.tid, 'stop', process_id=d.name))
        self.assertEqual(forge._unit_state(d)['status'], 'unknown')
        self.assertIsNone(proc.poll())
        self.assertFalse((d/custody.FILE).exists())

    def test_foreign_host_rebound_request_and_pid_cannot_release(self):
        d = self.unit(); proc = self.launch(d)
        original = (d/'request.json').read_bytes()
        with mock.patch.object(custody, 'host_identity', return_value='different-machine'):
            with self.assertRaises(ValueError): custody.reconcile(forge, d)
        for changes in ({'root':str(self.base/'foreign')}, {'supervisor_pid':os.getpid()}, {'scope':'host'}):
            request = json.loads(original); request.update(changes)
            forge._atomic_json(d/'request.json', request)
            with self.assertRaises(ValueError): custody.reconcile(forge, d)
            self.assertFalse((d/custody.FILE).exists())
        (d/'request.json').write_bytes(original)
        self.assertIsNone(proc.poll())

    def test_changed_progress_invalidates_receipt(self):
        d = self.unit(); self.launch(d); custody.reconcile(forge, d)
        self.assertIsNotNone(forge_recovery.read_recovery(d))
        forge._atomic_json(d/'progress.json', {'generation':'another-generation'})
        self.assertIsNone(forge_recovery.read_recovery(d))

    def test_absent_unique_job_after_close_proves_extinction(self):
        d = self.unit(); proc = self.launch(d)
        self.assertTrue(proc.stop_tree()[0])
        proof = custody.reconcile(forge, d)
        self.assertEqual(proof['evidence'], 'job_absent')
        self.assert_tree_gone(d)

    def test_binding_failure_never_resumes_executable(self):
        d = self.unit()
        with mock.patch.object(custody, 'bind', side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):
                forge._spawn_runner(self.script, d/'request.json', d/'supervisor.log')
        self.assertFalse((d/'child.pid').exists())

    def test_coordinator_crash_closes_job_and_recovery_survives_restart(self):
        d = self.unit()
        owner_script = self.base/'owner.py'
        owner_script.write_text('''import pathlib, sys, time
sys.path.insert(0, sys.argv[1])
import forge
base = pathlib.Path(sys.argv[2]); d = pathlib.Path(sys.argv[3])
forge.BASE=base; forge.REPO=base
forge.STATE_DIR=base/'memory/.forge'; forge.TASKS_DIR=forge.STATE_DIR/'tasks'
pid=forge._spawn_runner(base/'worker.py', d/'request.json', d/'supervisor.log')
request=forge._read_json(d/'request.json')
request.update(supervisor_pid=pid, supervisor_started_at=forge._proc_started_at(pid), status='running')
forge._atomic_json(d/'request.json',request)
(d/'owner-ready').write_text('ready')
time.sleep(120)
''', encoding='utf8')
        with (self.base/'owner.log').open('wb') as log:
            owner = subprocess.Popen([sys.executable, str(owner_script), str(Path(forge.__file__).parent),
                                      str(self.base), str(d)], stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            self.addCleanup(lambda: owner.kill() if owner.poll() is None else None)
            deadline=time.monotonic()+15
            while not (d/'owner-ready').exists() or not (d/'grandchild.pid').exists():
                if time.monotonic()>deadline or owner.poll() is not None:
                    self.fail('owner fixture failed: '+forge._tail(self.base/'owner.log'))
                time.sleep(.02)
            owner.kill(); owner.wait(5)
        result=forge.control(self.tid,'stop',d.name)
        self.assertEqual(result['status'],'settled',result)
        self.assert_tree_gone(d)
        self.assertTrue(forge._unit_state(d)['custody_recovered'])

    def test_foreground_command_has_durable_binding_and_preserves_completion(self):
        reply=forge.run_command(self.tid,[sys.executable,'-c','print("fixture completed")'])
        self.assertIn('fixture completed',reply)
        rows=forge._units(self.tid,'processes')
        self.assertEqual(len(rows),1)
        d=forge._unit_dir(self.tid,'processes',rows[0]['id'])
        self.assertIsNotNone(custody._binding(d))
        before=self.history(d)
        forge.process(self.tid,'stop',process_id=d.name)
        self.assertEqual(self.history(d),before)
        self.assertEqual(forge._unit_state(d)['status'],'done')

    def test_collision_does_not_adopt_existing_job(self):
        job = WindowsJob(custody.new_job_name()); self.addCleanup(job.close)
        with self.assertRaises(FileExistsError): WindowsJob(job.name)
        self.assertFalse(job.live())

    def test_legacy_missing_binding_is_not_recovered(self):
        d = self.unit()
        request = forge._read_json(d/'request.json')
        request.update(supervisor_pid=2147483000, supervisor_started_at='old', status='running')
        forge._atomic_json(d/'request.json', request)
        with mock.patch.object(forge, '_kill_tree', return_value='уже завершён'):
            result = forge.control(self.tid, 'stop', d.name)
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse((d/custody.FILE).exists())


@unittest.skipUnless(os.name == 'nt', 'Windows reboot witness')
class LegacyRecovery(unittest.TestCase):
    setUp = WindowsCustody.setUp
    unit = WindowsCustody.unit

    def legacy(self):
        d = self.unit()
        forge._atomic_json(d/'stop.json', {'state':'unresolved', 'note':'tree exit unproven'})
        request = forge._read_json(d/'request.json')
        request.update(supervisor_pid=2147483000, supervisor_started_at='old-birth', status='running')
        forge._atomic_json(d/'request.json', request)
        journal = forge_runtime.Journal(d)
        journal.start(request, {'next_boundary':'summary_in_flight'})
        journal.payload('preserve partial summary'); journal.close()
        return d

    def boundary(self, boot):
        return {'host_id':custody.host_identity(), 'boot_id':f'fixture-boot-{boot}', 'boot_time':10000+boot}

    def prepare(self, d):
        return forge.control(self.tid, 'prepare_recovery', d.name,
            forge_runtime.read_progress(d)['generation'], host_binding='verified-local-execution-host',
            reason='original execution host independently confirmed for fixture')

    def test_actual_boot_source_is_stable_and_bound_to_this_host(self):
        custody.boot_boundary.cache_clear()
        value = custody.boot_boundary()
        self.assertEqual(value['host_id'], custody.host_identity())
        self.assertGreater(value['boot_time'], 0)
        self.assertLess(value['boot_time'], time.time()+5)
        self.assertEqual(value, custody.boot_boundary())

    def test_missing_selected_participant_is_refused_without_a_boot_probe(self):
        with mock.patch.object(custody,'boot_boundary',side_effect=AssertionError('no boot probe needed')):
            result=forge.control(self.tid,'recover','agent-missing')
        self.assertEqual(result['status'],'refused')
        self.assertIn('missing',result['reason'])

    def test_two_phase_recovery_retains_all_history_and_unsafe_boundary(self):
        d=self.legacy()
        original={p.relative_to(d):p.read_bytes() for p in d.rglob('*') if p.is_file()}
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(1)):
            self.assertEqual(self.prepare(d)['status'],'prepared')
            witness=(d/custody.WITNESS).read_bytes()
            self.assertEqual(forge.control(self.tid,'recover',d.name)['status'],'refused')
            self.assertEqual(forge.control(self.tid,'stop',d.name)['status'],'unknown')
            self.assertEqual(self.prepare(d)['status'],'prepared')
            self.assertEqual((d/custody.WITNESS).read_bytes(),witness)
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(2)):
            self.assertEqual(forge.control(self.tid,'recover',d.name)['status'],'settled')
            self.assertEqual(forge.control(self.tid,'recover',d.name)['status'],'settled')
            self.assertEqual(forge.control(self.tid,'stop',d.name)['status'],'settled')
            self.assertTrue(forge._writer_ended(d))
            self.assertTrue(forge._unit_state(d)['custody_recovered'])
            self.assertEqual(forge._shared_writer_reason(self.tid,''),'')
            self.assertFalse(forge_runtime.continuation_info(d)['safe'])
            self.assertEqual(forge._unit_state(d)['status'],'lost')
            self.assertIsNotNone(forge_recovery.read_recovery(d))
        for p,raw in original.items(): self.assertEqual((d/p).read_bytes(),raw,str(p))
        self.assertFalse((d/'result.json').exists())

    def test_no_witness_no_confirmation_or_wrong_host_cannot_release(self):
        d=self.legacy()
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(1)):
            self.assertEqual(forge.control(self.tid,'recover',d.name)['status'],'refused')
            self.assertEqual(forge.control(self.tid,'prepare_recovery',d.name)['status'],'refused')
            self.assertFalse((d/custody.WITNESS).exists())
            self.assertEqual(self.prepare(d)['status'],'prepared')
        with mock.patch.object(custody,'boot_boundary',return_value={**self.boundary(2),'host_id':'foreign'}):
            self.assertEqual(forge.control(self.tid,'recover',d.name)['status'],'refused')
        self.assertFalse((d/custody.FILE).exists())

    def test_changed_history_invalidates_witness(self):
        d=self.legacy()
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(1)):
            self.assertEqual(self.prepare(d)['status'],'prepared')
        forge._atomic_json(d/'result.json',{'status':'done','late':'new history'})
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(2)):
            self.assertEqual(forge.control(self.tid,'recover',d.name)['status'],'refused')
        self.assertFalse((d/custody.FILE).exists())

    def test_repeated_stop_preserves_prepared_parent_and_command_witnesses(self):
        d=self.legacy()
        child=self.unit('proc-old','processes',d.name)
        request=forge._read_json(child/'request.json')
        request.update(supervisor_pid=2147483001,supervisor_started_at='old-child',status='running')
        forge._atomic_json(child/'request.json',request)
        forge._atomic_json(child/'stop.json',{'state':'unresolved','note':'command tree unknown'})
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(1)):
            self.assertEqual(self.prepare(d)['status'],'prepared')
            originals={p:p.read_bytes() for folder in (d,child) for p in folder.rglob('*') if p.is_file()}
            self.assertEqual(forge.control(self.tid,'stop',d.name)['status'],'unknown')
            for path,raw in originals.items():self.assertEqual(path.read_bytes(),raw,str(path))
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(2)):
            self.assertEqual(forge.control(self.tid,'recover',d.name)['status'],'settled')
            self.assertTrue(forge._unit_state(child)['custody_recovered'])

    def test_live_or_remote_work_cannot_prepare_local_witness(self):
        d=self.legacy()
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(1)), \
             mock.patch.object(forge,'_pid_alive',return_value=True):
            self.assertEqual(self.prepare(d)['status'],'refused')
        request=forge._read_json(d/'request.json');request['scope']='host'
        forge._atomic_json(d/'request.json',request)
        with mock.patch.object(custody,'boot_boundary',return_value=self.boundary(1)):
            self.assertEqual(self.prepare(d)['status'],'refused')
        self.assertFalse((d/custody.WITNESS).exists())


if __name__ == '__main__':
    unittest.main()
