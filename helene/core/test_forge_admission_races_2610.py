"""Forge admission/race regressions; in-process only, ALL subprocess doors raise.

Run with praxis_test.py. Fixtures clean themselves; no live profile or model.
The history test measures the actual global tick_queued consumer, not source_update.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import statistics
import threading
import time
import types
import unittest
from unittest import mock

import forge
import forge_swarm
import test_forge_coordination_2610 as fixtures


class AdmissionRaces(unittest.TestCase):
    setUp = fixtures.Coordination.setUp
    patch = fixtures.Coordination.patch
    unit = fixtures.Coordination.unit

    def nodes(self):
        return {node['id']: node for node in forge_swarm.load(self.directory)['nodes']}

    def accept(self, directory, disposition='integrated'):
        digest = forge._result_identity(directory / 'result.json')
        receipt = json.loads(forge.agent(self.tid, 'disposition', agent_id=directory.name,
                                        result_sha256=digest, disposition=disposition))
        self.assertEqual(receipt['disposition'], disposition)
        return digest

    def queued_dependent(self):
        forge.swarm(self.tid, 'plan', plan=json.dumps([
            {'id': 'A', 'role': 'scout', 'brief': 'prerequisite'},
            {'id': 'B', 'role': 'worker', 'brief': 'dependent', 'deps': ['A']}]))
        forge.swarm(self.tid, 'start')
        prerequisite = self.directory / 'agents' / self.nodes()['A']['agent_id']
        forge._atomic_json(prerequisite / 'result.json', {'status': 'done', 'result': 'revision A1'})
        forge.release_worker_root(prerequisite / 'request.json')
        digest = self.accept(prerequisite)
        blocker = self.unit('agent-eeeeeeee')
        forge.swarm(self.tid, 'tick')
        dependent = self.directory / 'agents' / self.nodes()['B']['agent_id']
        self.assertEqual(forge._read_json(dependent / 'request.json')['status'], 'queued')
        self.assertEqual(self.nodes()['B']['dependency_results'], {'A': digest})
        self.assertEqual(self.spawn.call_count, 1)
        forge._atomic_json(blocker / 'result.json', {'status': 'done'})
        forge.release_worker_root(blocker / 'request.json')
        return prerequisite, dependent, digest

    def test_never_started_resume_is_idempotent_before_and_after_completion(self):
        source = self.unit(status='queued')
        forge.agent(self.tid, 'stop', agent_id=source.name)
        old_result = (source / 'result.json').read_bytes()
        self.assertTrue(json.loads(old_result)['never_started'])
        first = forge.control(self.tid, 'resume', source.name)
        self.assertEqual(first['status'], 'queued')
        successor = self.directory / 'agents' / first['agent_id']
        req = forge._read_json(successor / 'request.json')
        self.assertEqual(req['resume_of'], source.name)
        self.assertNotIn('continuation_source', req)
        self.assertEqual(forge.control(self.tid, 'resume', source.name)['agent_id'], successor.name)
        self.assertEqual(len(list((self.directory / 'agents').iterdir())), 2)
        self.assertEqual(forge.tick_queued(self.tid)[0]['agent_id'], successor.name)
        forge._atomic_json(successor / 'result.json', {'status': 'done', 'result': 'one execution'})
        after = forge.control(self.tid, 'resume', source.name)
        self.assertEqual(after['agent_id'], successor.name)
        self.assertEqual(after['status'], 'done')
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual((source / 'result.json').read_bytes(), old_result)

    def test_task_mutex_release_retries_windows_reader_sharing_and_keeps_successor(self):
        path = self.directory / '.mutation.lock'
        unlink = Path.unlink
        calls = []
        def overlap(candidate, *args, **kwargs):
            if candidate == path:
                calls.append(candidate)
                if len(calls) == 1:
                    raise PermissionError('contender briefly reading owner token')
            return unlink(candidate, *args, **kwargs)
        windows = types.SimpleNamespace(**{name: getattr(forge.os, name) for name in dir(forge.os)
                                          if not name.startswith('__')})
        windows.name = 'nt'
        with mock.patch.object(forge, 'os', windows), mock.patch.object(Path, 'unlink', overlap):
            with forge._mutation_lock(self.tid):
                self.assertTrue(path.exists())
        self.assertFalse(path.exists())
        self.assertEqual(len(calls), 2)
        def replaced_during_retry(candidate, *args, **kwargs):
            if candidate == path:
                candidate.write_text('another-owner-token', encoding='utf-8')
                raise PermissionError('different holder now owns the file')
            return unlink(candidate, *args, **kwargs)
        with mock.patch.object(forge, 'os', windows), mock.patch.object(Path, 'unlink', replaced_during_retry):
            with forge._mutation_lock(self.tid):
                pass
        self.assertEqual(path.read_text(encoding='utf-8'), 'another-owner-token')
        unlink(path)  # solely the synthetic test token

    def test_checkpoint_resume_retains_safe_identity_and_dedupes(self):
        source = self.unit(result={'status': 'stopped', 'result': 'partial'})
        old_result = (source / 'result.json').read_bytes()
        forge._atomic_json(source / 'progress.json', {'generation': 'generation1'})
        info = {'safe': True, 'generation': 'generation1', 'checkpoint_sha256': 'c' * 64}
        with mock.patch.object(forge, '_writer_ended', return_value=True), \
             mock.patch('forge_runtime.continuation_info', return_value=info):
            self.assertEqual(forge.control(self.tid, 'resume', source.name, 'wrong')['status'], 'refused')
            first = forge.control(self.tid, 'resume', source.name, 'generation1')
            self.assertEqual(forge.control(self.tid, 'resume', source.name)['agent_id'], first['agent_id'])
        successor = self.directory / 'agents' / first['agent_id']
        req = forge._read_json(successor / 'request.json')
        self.assertEqual(req['resume_of'], source.name)
        self.assertEqual(req['continuation_source']['checkpoint_sha256'], 'c' * 64)
        self.assertTrue(forge._read_json(successor / 'resume.json')['old_writer_ended'])
        self.assertEqual((source / 'result.json').read_bytes(), old_result)
        forge._atomic_json(successor / 'result.json', {'status': 'done', 'result': 'checkpoint continuation complete'})
        with mock.patch.object(forge, '_writer_ended', return_value=True):
            self.assertEqual(forge.control(self.tid, 'resume', source.name)['agent_id'], successor.name)
        self.assertEqual(len(list((self.directory / 'agents').iterdir())), 2)

    def test_resume_lineage_is_not_intentional_rework(self):
        source = self.unit(status='queued')
        forge.agent(self.tid, 'stop', agent_id=source.name)
        rework = self.unit('agent-bbbbbbbb', role='scout', status='queued')
        req = forge._read_json(rework / 'request.json')
        req['previous_unit_id'] = source.name  # rework provenance, NOT a continuation
        forge._atomic_json(rework / 'request.json', req)
        first = forge.control(self.tid, 'resume', source.name)
        self.assertNotEqual(first['agent_id'], rework.name)
        self.assertEqual(forge.control(self.tid, 'resume', source.name)['agent_id'], first['agent_id'])

    def test_queued_dependency_revocation_blocks_then_exact_restore_admits(self):
        prerequisite, dependent, digest = self.queued_dependent()
        self.accept(prerequisite, 'deferred')
        with mock.patch.object(forge_swarm, 'refresh', wraps=forge_swarm.refresh) as refresh, \
             mock.patch.object(forge, '_agent_snapshot', wraps=forge._agent_snapshot) as snapshots:
            self.assertEqual(forge.tick_queued(self.tid), [])
        refresh.assert_called_once()
        self.assertEqual(len([c for c in snapshots.call_args_list if c.args[1] == prerequisite.name]), 1)
        self.assertIn('dependency result revision changed', forge._read_json(dependent / 'request.json')['queued_reason'])
        self.assertEqual(self.spawn.call_count, 1)
        self.accept(prerequisite)
        self.assertEqual(forge.tick_queued(self.tid)[0]['agent_id'], dependent.name)
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.nodes()['B']['dependency_results'], {'A': digest})

    def test_queued_dependency_rework_does_not_rebind_old_attempt(self):
        prerequisite, dependent, digest = self.queued_dependent()
        forge.swarm(self.tid, 'rework', node_id='A', message='correct candidate', result_sha256=digest)
        self.assertEqual(forge.tick_queued(self.tid), [])
        self.assertEqual(self.spawn.call_count, 1)
        forge.swarm(self.tid, 'tick')
        replacement = self.directory / 'agents' / self.nodes()['A']['agent_id']
        self.assertNotEqual(replacement.name, prerequisite.name)
        forge._atomic_json(replacement / 'result.json', {'status': 'done', 'result': 'revision A2'})
        new_digest = self.accept(replacement)
        self.assertNotEqual(new_digest, digest)
        self.assertEqual(forge.tick_queued(self.tid), [])
        self.assertEqual(forge._read_json(dependent / 'request.json')['status'], 'queued')
        self.assertEqual(self.nodes()['B']['dependency_results'], {'A': digest})
        self.assertEqual(self.spawn.call_count, 2)  # only A2, never stale B

    def test_dependency_negative_control_would_execute_the_stale_assignment(self):
        prerequisite, dependent, _ = self.queued_dependent()
        self.accept(prerequisite, 'deferred')
        # Remove ONLY the admission check to reproduce the proven prior behavior.
        with mock.patch.object(forge, '_queued_dependency_reason', return_value=''):
            self.assertEqual(forge.tick_queued(self.tid)[0]['agent_id'], dependent.name)
        self.assertEqual(self.spawn.call_count, 2)

    def status_rework_race(self, bypass_status_lock=False):
        forge.swarm(self.tid, 'plan', plan='[{"id":"A","brief":"read","role":"scout"}]')
        forge.swarm(self.tid, 'start')
        original = self.directory / 'agents' / self.nodes()['A']['agent_id']
        forge._atomic_json(original / 'result.json', {'status': 'done', 'result': 'fix needed'})
        digest = forge._result_identity(original / 'result.json')
        loaded, release, rework_entered, corrected = (threading.Event() for _ in range(4))
        errors, answers = [], {}
        real_load, real_lock = forge_swarm.load, forge._mutation_lock
        def load(path):
            value = real_load(path)
            if threading.current_thread().name == 'status-reader':
                loaded.set()
                if not release.wait(5):
                    raise AssertionError('barrier not released')
            return value
        def lock(*args, **kwargs):
            if bypass_status_lock and threading.current_thread().name == 'status-reader':
                return contextlib.nullcontext()
            return real_lock(*args, **kwargs)
        def status():
            try:
                answers['status'] = forge.inspect(self.tid, 'status')
            except BaseException as exc:
                errors.append(exc)
        def rework():
            rework_entered.set()
            try:
                answers['rework'] = forge.swarm(self.tid, 'rework', node_id='A',
                    message='corrected brief', result_sha256=digest)
            except BaseException as exc:
                errors.append(exc)
            finally:
                corrected.set()
        with mock.patch.object(forge_swarm, 'load', load), mock.patch.object(forge, '_mutation_lock', lock), \
             mock.patch.object(forge, '_git_root', return_value=None):
            reader = threading.Thread(target=status, name='status-reader')
            writer = threading.Thread(target=rework, name='rework-writer')
            reader.start()
            self.assertTrue(loaded.wait(5))
            writer.start()
            self.assertTrue(rework_entered.wait(5))
            completed_while_reader_loaded = corrected.wait(.15 if not bypass_status_lock else 5)
            release.set()
            reader.join(5); writer.join(5)
            self.assertFalse(reader.is_alive()); self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertIn('swarm', answers['status'])
        return completed_while_reader_loaded, self.nodes()['A'], original, digest

    def test_status_and_rework_share_one_transaction_mutex(self):
        interleaved, node, original, digest = self.status_rework_race()
        self.assertFalse(interleaved, 'rework must wait for the status graph transaction')
        self.assertEqual(node['status'], 'pending')
        self.assertEqual(node['brief'], 'corrected brief')
        self.assertEqual(node['previous_unit_id'], original.name)
        self.assertEqual(node['attempts'][0]['result_sha256'], digest)

    def test_unlocked_status_negative_control_erases_the_new_attempt(self):
        interleaved, node, original, digest = self.status_rework_race(bypass_status_lock=True)
        self.assertTrue(interleaved)
        self.assertEqual(node['agent_id'], original.name)
        self.assertEqual(node['attempts'], [])
        self.assertNotEqual(node['brief'], 'corrected brief')

    def test_reserved_spawn_revalidates_after_unlock_without_rebinding(self):
        forge.swarm(self.tid, 'plan', plan=json.dumps([
            {'id': 'A', 'role': 'scout', 'brief': 'prerequisite'},
            {'id': 'B', 'brief': 'consume', 'deps': ['A']}]))
        forge.swarm(self.tid, 'start')
        prerequisite = self.directory / 'agents' / self.nodes()['A']['agent_id']
        forge._atomic_json(prerequisite / 'result.json', {'status': 'done'})
        self.accept(prerequisite)
        real_agent = forge.agent
        def revoke(tid, action, **kwargs):
            if action == 'spawn' and kwargs.get('node_id') == 'B':
                self.accept(prerequisite, 'deferred')
            return real_agent(tid, action, **kwargs)
        with mock.patch.object(forge, 'agent', side_effect=revoke):
            forge.swarm(self.tid, 'tick')
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.nodes()['B']['status'], 'pending')
        self.accept(prerequisite)
        forge.swarm(self.tid, 'tick')
        self.assertEqual(self.spawn.call_count, 2)

    def test_windows_repeat_group_stop_retains_settled_unit_proof(self):
        unit = self.unit(result={'status': 'done', 'result': 'retained output'})
        original = (unit / 'result.json').read_bytes()
        job = self.directory / 'processes' / 'proc-aaaaaaaa'
        forge._atomic_json(job / 'request.json', {'id': job.name, 'task_id': self.tid,
            'status': 'running', 'supervisor_pid': 782, 'supervisor_started_at': 'job-born'})
        alive, calls = {'worker': True}, []
        windows = types.SimpleNamespace(**{name: getattr(forge.os, name) for name in dir(forge.os)
                                          if not name.startswith('__')})
        windows.name = 'nt'
        def kill(*args, **kwargs):
            alive['worker'] = False
            return 'остановлен'
        def stop_job(tid, action, process_id):
            calls.append(process_id)
            if len(calls) == 1:
                forge._atomic_json(job / 'stop.json', {'state': 'unresolved'})
                return 'unknown'
            forge._atomic_json(job / 'result.json', {'status': 'done'})
            forge._atomic_json(job / 'stop.json', {'state': 'settled'})
            return 'settled'
        with mock.patch.object(forge, 'os', windows), \
             mock.patch.object(forge, '_pid_alive', side_effect=lambda pid, *args: alive['worker'] if pid == 781 else False), \
             mock.patch.object(forge, '_kill_tree', side_effect=kill) as stopped, \
             mock.patch.object(forge, 'process', side_effect=stop_job):
            self.assertEqual(forge.control(self.tid, 'stop')['status'], 'unknown')
            settled = (unit / 'stop.json').read_bytes()
            self.assertEqual(json.loads(settled)['state'], 'settled')
            self.assertEqual(forge.control(self.tid, 'stop')['status'], 'settled')
            stopped.assert_called_once()
        self.assertEqual((unit / 'stop.json').read_bytes(), settled)
        self.assertEqual((unit / 'result.json').read_bytes(), original)
        self.assertEqual(len(calls), 2)
        self.assertEqual(forge.control(self.tid, 'resume')['status'], 'refused')

    def test_changed_result_cannot_inherit_an_earlier_settled_stop_receipt(self):
        unit = self.unit(result={'status': 'done', 'result': 'current result'})
        forge._atomic_json(unit / 'stop.json', {'state': 'settled', 'result_sha256': '0' * 64})
        windows = types.SimpleNamespace(**{name: getattr(forge.os, name) for name in dir(forge.os)
                                          if not name.startswith('__')})
        windows.name = 'nt'
        with mock.patch.object(forge, 'os', windows), mock.patch.object(forge, '_pid_alive', return_value=False):
            forge.agent(self.tid, 'stop', agent_id=unit.name)
        self.assertEqual(forge._read_json(unit / 'stop.json')['state'], 'unresolved')

    def test_terminal_group_stop_retries_unknown_jobs_but_never_reopens_admission(self):
        directory = self.directory / 'processes' / 'proc-aaaaaaaa'
        forge._atomic_json(directory / 'request.json', {'id': directory.name, 'task_id': self.tid,
            'status': 'running', 'supervisor_pid': 781, 'supervisor_started_at': 'born'})
        calls = []
        def stop(tid, action, process_id):
            self.assertEqual((tid, action, process_id), (self.tid, 'stop', directory.name))
            calls.append(action)
            if len(calls) == 1:
                forge._atomic_json(directory / 'stop.json', {'state': 'unresolved', 'note': 'PID not yet published'})
                return 'unknown, retained'
            forge._atomic_json(directory / 'result.json', {'status': 'done', 'result': 'finished naturally'})
            forge._atomic_json(directory / 'stop.json', {'state': 'settled', 'note': 'tree exited'})
            return 'settled, result retained'
        with mock.patch.object(forge, 'process', side_effect=stop):
            first = forge.control(self.tid, 'stop')
            self.assertEqual(first['status'], 'unknown')
            self.assertEqual(forge.get(self.tid)['status'], 'stopped')
            second = forge.control(self.tid, 'stop')
        self.assertEqual(second['status'], 'settled')
        self.assertEqual(len(calls), 2)
        self.assertEqual(forge.get(self.tid)['status'], 'stopped')
        self.assertEqual(forge.control(self.tid, 'resume')['status'], 'refused')
        self.assertEqual(forge.control(self.tid, 'pause')['status'], 'refused')
        self.assertIn('terminal', forge.agent(self.tid, 'spawn', brief='must not run'))
        self.spawn.assert_not_called()
        self.assertEqual(forge._read_json(directory / 'result.json')['status'], 'done')

    def test_blocked_queue_reasons_do_not_rewrite_and_pause_resume_is_noticed(self):
        self.unit()
        forge.agent(self.tid, 'spawn', brief='waiting', agent_id='agent-bbbbbbbb')
        forge.tick_queued()
        with mock.patch.object(forge, '_atomic_json', wraps=forge._atomic_json) as writes:
            forge.tick_queued()
            self.assertEqual(writes.call_count, 0)
        # Canonical producer changes remain visible while the queue identity is current.
        forge._atomic_json(self.directory / 'control.json', {'action': 'pause'})
        forge.tick_queued()
        req = self.directory / 'agents/agent-bbbbbbbb/request.json'
        self.assertIn('pause-fenced', forge._read_json(req)['queued_reason'])
        forge.control(self.tid, 'resume')
        forge._atomic_json(self.directory / 'agents/agent-aaaaaaaa/result.json', {'status': 'done'})
        forge.release_worker_root(self.directory / 'agents/agent-aaaaaaaa/request.json')
        self.assertEqual(forge.tick_queued()[0]['agent_id'], 'agent-bbbbbbbb')
        self.assertEqual(forge.tick_queued(), [])
        self.assertEqual(self.spawn.call_count, 1)

    def test_discovery_notices_new_task_existing_task_and_partial_publication(self):
        forge.tick_queued()  # bootstrap no agents
        partial = self.directory / 'agents/agent-aaaaaaaa'
        partial.mkdir(parents=True)
        forge.tick_queued()  # sees directory before the atomic request publication
        self.unit(status='queued')
        self.assertEqual(forge.tick_queued()[0]['agent_id'], partial.name)
        forge._atomic_json(partial / 'result.json', {'status': 'done'})
        forge.release_worker_root(partial / 'request.json')
        # New public spawn into a previously scanned task must be noticed too.
        blocker = self.unit('agent-cccccccc')
        forge.agent(self.tid, 'spawn', brief='later', agent_id='agent-bbbbbbbb')
        forge.tick_queued()
        forge._atomic_json(blocker / 'result.json', {'status': 'done'})
        forge.release_worker_root(blocker / 'request.json')
        self.assertEqual(forge.tick_queued()[0]['agent_id'], 'agent-bbbbbbbb')
        other = 'code-87654321'
        forge._atomic_json(forge._task_dir(other) / 'task.json', {**self.task, 'id': other})
        forge._atomic_json(forge._task_dir(other) / 'agents/agent-dddddddd/request.json', {
            'id': 'agent-dddddddd', 'task_id': other, 'root': str(self.root), 'role': 'worker',
            'status': 'queued', 'created': 'now'})
        self.assertEqual(forge.tick_queued()[0]['task_id'], other)
        self.assertEqual(self.spawn.call_count, 3)

    def test_restart_rediscovers_an_existing_queue_without_replaying_started_units(self):
        self.unit(status='queued')
        forge._QUEUE_OBSERVER.clear()  # a new process has no discovery hints
        self.assertEqual(forge.tick_queued()[0]['agent_id'], 'agent-aaaaaaaa')
        forge._QUEUE_OBSERVER.clear()
        self.assertEqual(forge.tick_queued(), [])
        self.assertEqual(self.spawn.call_count, 1)

    def test_global_observer_notices_resume_after_a_queued_unit_stop(self):
        self.unit('agent-cccccccc')
        forge.agent(self.tid, 'spawn', brief='queued assignment', agent_id='agent-aaaaaaaa')
        self.assertEqual(forge.tick_queued(), [])
        forge.agent(self.tid, 'stop', agent_id='agent-aaaaaaaa')
        self.assertEqual(forge.tick_queued(), [])  # old stopped identity is discharged
        resumed = forge.control(self.tid, 'resume', 'agent-aaaaaaaa')
        forge._atomic_json(self.directory / 'agents/agent-cccccccc/result.json', {'status': 'done'})
        forge.release_worker_root(self.directory / 'agents/agent-cccccccc/request.json')
        self.assertEqual(forge.tick_queued()[0]['agent_id'], resumed['agent_id'])
        self.assertEqual(forge.tick_queued(), [])
        self.assertEqual(self.spawn.call_count, 1)

    def test_thousand_retained_tasks_full_tick_cold_bounded_and_warm_no_json_or_writes(self):
        tasks = self.root / 'retained-tasks'
        self.patch('TASKS_DIR', tasks)
        for index in range(1000):
            tid = f'code-{index:08x}'
            directory = tasks / tid
            forge._atomic_json(directory / 'task.json', {**self.task, 'id': tid, 'status': 'done'})
            unit = directory / 'agents' / f'agent-{index:08x}'
            forge._atomic_json(unit / 'request.json', {'id': unit.name, 'task_id': tid, 'status': 'running'})
            forge._atomic_json(unit / 'result.json', {'status': 'done'})
        real_read = forge._read_json
        baseline_times = []
        # Exact old global discovery/drain path for settled requests; no process seam reached.
        for _ in range(2):
            started = time.perf_counter()
            for path in tasks.glob('*/task.json'):
                with forge._mutation_lock(path.parent.name, timeout=.1):
                    for directory in sorted((path.parent / 'agents').glob('agent-*')):
                        self.assertNotEqual(real_read(directory / 'request.json')['status'], 'queued')
            baseline_times.append(time.perf_counter() - started)
        forge._QUEUE_OBSERVER.clear()
        cold, cold_reads = [], []
        with mock.patch.object(forge, '_read_json', wraps=real_read) as reads, \
             mock.patch.object(forge, '_atomic_json', wraps=forge._atomic_json) as writes, \
             mock.patch.object(forge, '_mutation_lock', wraps=forge._mutation_lock) as locks:
            for _ in range(63):
                reads.reset_mock()
                started = time.perf_counter()
                self.assertEqual(forge.tick_queued(), [])
                cold.append(time.perf_counter() - started)
                cold_reads.append(reads.call_count)
            self.assertLessEqual(max(cold_reads), 16)
            self.assertEqual(sum(cold_reads), 1000)
            self.assertEqual(writes.call_count, 0)
            self.assertEqual(locks.call_count, 0)
            reads.reset_mock()
            warm = []
            for _ in range(30):
                started = time.perf_counter()
                self.assertEqual(forge.tick_queued(), [])
                warm.append(time.perf_counter() - started)
            self.assertEqual(reads.call_count, 0)
            self.assertEqual(writes.call_count, 0)
            self.assertEqual(locks.call_count, 0)
        self.spawn.assert_not_called()
        print('FORGE_HISTORY_1000 ' + json.dumps({
            'baseline_full_tick_seconds': baseline_times,
            'cold_ticks': len(cold), 'cold_max_seconds': max(cold),
            'cold_max_json_reads': max(cold_reads), 'cold_total_json_reads': sum(cold_reads),
            'warm_ticks': len(warm), 'warm_mean_seconds': statistics.mean(warm),
            'warm_max_seconds': max(warm), 'warm_json_reads': 0, 'warm_json_writes': 0,
            'warm_mutation_locks': 0, 'retained_tasks': 1000,
            'remaining_metadata_poll': 'one agents-directory stat per known task each tick'}))


if __name__ == '__main__':
    unittest.main()
