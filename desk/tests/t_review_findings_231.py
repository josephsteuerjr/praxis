"""Regression cases from the actual independent Jarvis update review."""
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'helene/core'))
import forge
import forge_swarm
import llm
from test_forge_remote_admission_2710 import RemoteAdmission


class Findings(unittest.TestCase):
    setUp = RemoteAdmission.setUp
    patch = RemoteAdmission.patch
    unit = RemoteAdmission.unit
    host = RemoteAdmission.host

    def test_whitespace_start_cannot_bypass_pause(self):
        remote = self.host()
        forge.control(self.tid, 'pause')
        self.assertIn('refused', forge.process(self.tid, ' Start ', command='echo test'))
        self.assertIn('refused', forge.verify(self.tid, ' Start ', commands='echo test'))
        remote.call.assert_not_called()
        remote.process.assert_not_called()

    def test_detached_remote_has_parent_blocks_writer_and_receives_group_stop(self):
        remote = self.host()
        parent = self.unit()
        stopped = False
        def call(action, params, **kwargs):
            nonlocal stopped
            if action == 'op.start': return {'ok': True, 'id': 'op-test123'}
            self.assertEqual(params['operation_id'], 'op-test123')
            if action == 'op.stop': stopped = True; return {'ok': True}
            return {'ok': True, 'status': 'stopped' if stopped else 'running'}
        remote.call.side_effect = call
        remote.process.return_value = 'legacy unowned remote start'
        forge.process(self.tid, 'start', command='echo test', _parent_request_path=str(parent/'request.json'))
        rows = forge._units(self.tid, 'processes')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['parent_unit_id'], parent.name)
        forge._atomic_json(parent/'result.json', {'status': 'done'})
        self.patch('_pid_alive', lambda *args: False)
        self.assertIn('shared-root writer', forge._agent_capacity(self.tid, {'id':'agent-new', 'role':'worker'}))
        self.assertEqual(forge.control(self.tid, 'stop')['status'], 'settled')
        self.assertTrue(stopped)
        self.assertEqual(forge._units(self.tid, 'processes')[0]['status'], 'stopped')

    def test_explicit_host_refusal_is_terminal_but_transport_is_unknown(self):
        remote = self.host()
        remote.call.return_value = {'type':'result', 'ok':False, 'code':'operation_cap', 'error':'capacity full'}
        result = json.loads(forge.run(self.tid, 'echo test'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(forge.control(self.tid, 'stop')['status'], 'settled')

    def test_uncertain_local_timeout_keeps_one_owned_identity_for_subtree_stop(self):
        parent = self.unit(status='queued')
        proc = mock.Mock(pid=9281, returncode=None)
        proc.poll.return_value = None
        proc.wait.side_effect = forge.subprocess.TimeoutExpired('echo test', 1)
        self.patch('_remote_client', lambda _: None)
        self.patch('_stop_foreground', lambda _: (False, 'group census unavailable'))
        self.patch('_RUNNERS', {})
        with mock.patch.object(forge.subprocess, 'Popen', return_value=proc):
            result = forge.run(self.tid, 'echo test', timeout=1, _parent_request_path=str(parent/'request.json'))
        rows = forge._units(self.tid, 'processes')
        self.assertEqual(len(rows), 1, 'one admitted command must retain one owned identity')
        self.assertEqual(rows[0]['parent_unit_id'], parent.name)
        self.assertIn(rows[0]['id'], result)
        self.patch('_kill_tree', lambda *args, **kw: 'остановлен')
        self.patch('_pid_alive', lambda *args: False)
        self.patch('_stop_foreground', lambda _: (True, 'process group exited'))
        proc.poll.return_value = 0
        proc.wait.side_effect = None
        self.assertEqual(forge.control(self.tid, 'stop', agent_id=parent.name)['status'], 'settled')
        self.assertEqual(len(forge._units(self.tid, 'processes')), 1)
        self.assertNotEqual(forge._units(self.tid, 'processes')[0]['status'], 'unknown')

    def test_legacy_blocked_dependency_recovers_only_never_started_nodes(self):
        plan = forge_swarm.create(self.directory, [
            {'id':'a', 'brief':'a'}, {'id':'b', 'brief':'b', 'deps':['a']},
            {'id':'stopped', 'brief':'s', 'deps':['a']}, {'id':'executed', 'brief':'x', 'deps':['a']}])
        plan['version'] = 1
        plan['nodes'][0].update(agent_id='agent-a')
        plan['nodes'][1].update(status='blocked', finished='old')
        plan['nodes'][2].update(status='stopped', finished='owner stop')
        plan['nodes'][3].update(status='blocked', agent_id='agent-x')
        forge_swarm.save(self.directory, plan)
        def state(aid):
            if aid == 'agent-x': return {'status':'stopped'}
            return {'status':'done', 'result_sha256':'abc',
                    'current_disposition':{'disposition':'integrated','result_sha256':'abc'}}
        recovered = forge_swarm.refresh(self.directory, state)
        self.assertEqual([n['id'] for n in forge_swarm.ready_nodes(recovered)], ['b'])
        self.assertEqual(recovered['nodes'][2]['status'], 'stopped')
        self.assertEqual(recovered['nodes'][3]['status'], 'stopped')
        self.assertEqual(forge_swarm.load(self.directory)['version'], 2)

    def test_windows_transient_mutex_permission_error_retries_without_breaking_owner(self):
        # Patch only Forge's os facade; pathlib must keep the real platform.
        facade = types.SimpleNamespace(**vars(forge.os)); facade.name = 'nt'
        original = forge.os.open
        attempts = []
        def opening(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1: raise PermissionError('delete pending')
            return original(*args, **kwargs)
        facade.open = opening
        self.patch('os', facade)
        with mock.patch.object(forge, '_break_stale_lock') as break_lock:
            with forge._mutation_lock(self.tid, timeout=.2): pass
            break_lock.assert_not_called()
        self.assertEqual(len(attempts), 2)
        self.assertFalse((self.directory/'.mutation.lock').exists())
        facade.open = mock.Mock(side_effect=PermissionError('permanent access denied'))
        with self.assertRaises(TimeoutError):
            with forge._mutation_lock(self.tid, timeout=.1): self.fail('admitted despite denial')


class LegacySDK(unittest.TestCase):
    def test_automatic_responses_requires_sdk_capability_explicit_mode_reports_it(self):
        old = types.SimpleNamespace(base_url='https://api.openai.com/v1')
        modern = types.SimpleNamespace(base_url=old.base_url, responses=types.SimpleNamespace(create=lambda **kw: None))
        config = {'frameworks': {'openai': {}}}
        with mock.patch.object(llm, '_client_for', return_value=old) as client, \
             mock.patch.object(llm, '_config', return_value=config), \
             mock.patch.object(llm, '_protocol', return_value='openai'), \
             mock.patch.object(llm, '_call_openai', return_value='chat') as chat, \
             mock.patch.object(llm, '_call_responses', return_value='responses') as responses:
            self.assertEqual(llm._call('openai', 'test', _resolved=True), 'chat')
            responses.assert_not_called()
            client.return_value = modern
            self.assertEqual(llm._call('openai', 'test', _resolved=True), 'responses')
            config['frameworks']['openai']['api_mode'] = 'responses'
            client.return_value = old
            with self.assertRaisesRegex(RuntimeError, 'SDK.*Responses'):
                llm._call('openai', 'test', _resolved=True)
            self.assertEqual(chat.call_count, 1)


if __name__ == '__main__': unittest.main()
