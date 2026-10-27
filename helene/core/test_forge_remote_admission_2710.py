"""Real Forge control consumers with deterministic remote transport, no processes/network."""
import json
import threading
import unittest
from unittest import mock
import forge
import test_forge_coordination_2610 as fixtures

class RemoteAdmission(unittest.TestCase):
    setUp = fixtures.Coordination.setUp
    patch = fixtures.Coordination.patch
    unit = fixtures.Coordination.unit

    def host(self):
        self.task['scope'] = 'host'; forge._save_task(self.task)
        remote = mock.Mock(PROTOCOL='fake-test-only')
        self.patch('_remote_client', lambda _: remote)
        return remote

    def test_pause_and_terminal_task_admit_no_remote_command(self):
        remote = self.host()
        forge.control(self.tid, 'pause')
        self.assertIn('refused', forge.run(self.tid, 'echo test'))
        remote.run.assert_not_called(); remote.call.assert_not_called()
        forge.control(self.tid, 'stop')
        self.assertIn('terminal', forge.run(self.tid, 'echo test'))
        remote.call.assert_not_called()

    def test_ancestor_stop_admits_no_remote_command(self):
        remote = self.host(); d = self.unit()
        forge._atomic_json(d/'stop.json', {'requested': True})
        self.assertIn('stop-fenced', forge.run(self.tid, 'echo test', _parent_request_path=str(d/'request.json')))
        remote.call.assert_not_called()

    def test_group_stop_reaches_owned_remote_id_while_run_is_waiting(self):
        remote = self.host(); started=threading.Event(); cancelled=threading.Event()
        def call(action, params):
            if action == 'op.start': started.set(); return {'ok':True,'id':'remote-123'}
            self.assertEqual(params['operation_id'], 'remote-123')
            if action == 'op.stop': cancelled.set(); return {'ok':True}
            return {'ok':True,'status':'stopped' if cancelled.is_set() else 'running'}
        remote.call.side_effect=call
        result=[]; t=threading.Thread(target=lambda:result.append(forge.run(self.tid,'echo test',timeout=10)))
        t.start(); self.assertTrue(started.wait(2))
        response=forge.control(self.tid,'stop'); t.join(3)
        self.assertFalse(t.is_alive()); self.assertTrue(cancelled.is_set())
        self.assertEqual(response['status'],'settled')
        rows=forge._units(self.tid,'processes'); self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['remote_id'],'remote-123'); self.assertEqual(rows[0]['status'],'stopped')

    def test_lost_admission_reply_stays_unknown_and_blocks_settled(self):
        remote=self.host(); remote.call.side_effect=TimeoutError('reply lost')
        value=json.loads(forge.run(self.tid,'echo test'))
        self.assertEqual(value['status'],'unknown')
        result=forge.control(self.tid,'stop')
        self.assertEqual(result['status'],'unknown'); self.assertEqual(len(result['unknown_units']),1)

    def assert_unresolved_poll(self, remote, payload):
        # Exercise the public synchronous consumer with its deadline disabled.
        # Before normalization, either alias would poll forever; fail on the
        # first retry rather than leaving a test thread running indefinitely.
        self.patch('_HAND_CEILING_SEC', 0)
        with mock.patch('forge_remote_run.time.sleep',
                        side_effect=AssertionError('uncertain poll must return, not retry')):
            value = json.loads(forge.run(self.tid, 'echo test', timeout=0))
        self.assertEqual(value['status'], 'unknown')
        self.assertEqual(value['remote_id'], 'remote-123')
        self.assertEqual(value['remote_result'], payload)
        rows = forge._units(self.tid, 'processes')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'unknown')
        directory = forge._unit_dir(self.tid, 'processes', value['id'])
        self.assertEqual(forge._read_json(directory / 'state.json')['status'], 'unknown')
        self.assertEqual(forge._read_json(directory / 'request.json')['remote_id'], 'remote-123')
        self.assertFalse((directory / 'result.json').exists())
        # A later stop still cannot settle uncertainty into success/failure.
        stopped = forge.control(self.tid, 'stop')
        self.assertEqual(stopped['status'], 'unknown')
        self.assertEqual(len(stopped['unknown_units']), 1)
        self.assertFalse((directory / 'result.json').exists())

    def test_host_lost_poll_returns_unknown_without_relaunch(self):
        remote = self.host()
        # serverd/hostproc.py:135-151, brokerops.py:926-953 (op.poll).
        payload = {'ok': True, 'id': 'remote-123', 'status': 'lost',
                   'supervisor_pid': 123, 'child_pid': 124, 'boot_id': 'old-boot',
                   'body': '', 'log': '/operations/remote-123/command.log',
                   'body_cut_chars': 0, 'body_tail_cap': 30000}
        def call(action, params):
            if action == 'op.start':
                self.assertEqual(params['timeout'], 0)
                return {'ok': True, 'id': 'remote-123'}
            self.assertEqual(params['operation_id'], 'remote-123')
            self.assertIn(action, ('op.poll', 'op.stop'))
            return payload if action == 'op.poll' else {'ok': True}
        remote.call.side_effect = call
        self.assert_unresolved_poll(remote, payload)
        self.assertEqual(sum(c.args[0] == 'op.start' for c in remote.call.call_args_list), 1)
        remote.process_start.assert_not_called()

    def test_body_in_doubt_poll_returns_unknown_without_relaunch(self):
        remote = self.host()
        self.task['scope'] = 'windows'; forge._save_task(self.task)
        # body/crates/praxis-body/src/process.rs:588-631 (process.status).
        payload = {'ok': True, 'operation_id': 'remote-123', 'status': 'in_doubt',
                   'identity': {}, 'supervisor_pid': 123, 'child_pid': None,
                   'result': None, 'stdout_tail': '', 'stderr_tail': '',
                   'supervisor_error': None, 'job_note': None,
                   'operation_dir': r'C:\operations\remote-123'}
        remote.process_start.return_value = {'ok': True, 'operation_id': 'remote-123'}
        remote.process_status.return_value = payload
        remote.call.return_value = {'ok': True}
        self.assert_unresolved_poll(remote, payload)
        remote.process_start.assert_called_once_with(
            command='echo test', shell='power_shell', cwd=self.task['root'], timeout=0)
        remote.process_status.assert_called_with('remote-123', 30000)
        self.assertTrue(all(c.args[0] == 'process.cancel' for c in remote.call.call_args_list))

if __name__ == '__main__': unittest.main()
