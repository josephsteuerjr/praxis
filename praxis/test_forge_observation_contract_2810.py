"""Forge metadata/read/admission contracts; actual APIs, isolated task records.

Run with helene/core first on sys.path. No models or external remote calls.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import forge
import forge_worker


class ObservationContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.tasks = self.base / 'tasks'
        self.task_id = 'code-contract'
        self.folder = self.tasks / self.task_id
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.task = {'id': self.task_id, 'status': 'active', 'root': str(self.root),
                     'goal': 'durable contract', 'isolation': 'git-worktree'}
        for name, value in [('TASKS_DIR', self.tasks), ('BASE', self.base)]:
            patch = mock.patch.object(forge, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.put(self.folder / 'task.json', self.task)

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def unit(self, name='agent-parent', parent='', status='running'):
        folder = self.folder / 'agents' / name
        self.put(folder / 'request.json', {'id': name, 'task_id': self.task_id,
            'parent_unit_id': parent, 'role': 'worker', 'status': 'running'})
        self.put(folder / 'result.json', {'status': status, 'iterations': 3})
        return folder

    def test_missing_worktree_status_and_history_preserve_tree_without_refresh(self):
        self.unit(status='done')
        self.root.rmdir()
        self.task['status'] = 'done'
        self.put(self.folder / 'task.json', self.task)
        (self.folder / 'events.jsonl').write_text(
            json.dumps({'kind': 'finished', 'summary': 'exact durable event'}) + '\n', encoding='utf-8')
        before = {p.relative_to(self.folder): p.read_bytes()
                  for p in self.folder.rglob('*') if p.is_file()}
        with mock.patch.object(forge, '_git_root', side_effect=AssertionError('git probe')), \
             mock.patch.object(forge, '_remote_client', side_effect=AssertionError('remote probe')), \
             mock.patch.object(forge.forge_swarm, 'refresh', side_effect=AssertionError('graph write')):
            status = forge.inspect(self.task_id, 'status')
            history = json.loads(forge.inspect(self.task_id, 'history', end=1))
        for part in ('[done]', 'agent-parent', 'coordination:', 'durable contract', 'недоступно'):
            self.assertIn(part, status)
        self.assertNotIn('abandon', status)
        self.assertEqual(history[0]['summary'], 'exact durable event')
        self.assertEqual(before, {p.relative_to(self.folder): p.read_bytes()
                                  for p in self.folder.rglob('*') if p.is_file()})

    def test_actual_remote_backends_refuse_exact_cursor_without_remote_effect(self):
        for scope, backend in [('host', forge.serverd_client), ('windows', forge.body_client)]:
            self.put(self.folder / 'task.json', {**self.task, 'scope': scope})
            with mock.patch.object(backend, 'call', side_effect=AssertionError('unguarded RPC')), \
                 mock.patch.object(forge, '_remote_observations'):
                for cursor in ({'start_char': 1}, {'expected_sha256': 'a' * 64}):
                    with self.subTest(scope=scope, cursor=cursor):
                        out = forge.inspect(self.task_id, 'read', path='long.txt', **cursor)
                        self.assertIn('does not support paged reads', out)
                        self.assertIn('No read was performed', out)

    def test_host_legacy_reader_preserves_actual_broker_arguments(self):
        self.put(self.folder / 'task.json', {**self.task, 'scope': 'host'})
        with mock.patch.object(forge.serverd_client, 'call', return_value={'ok': True, 'text': 'two\nthree'}) as rpc, \
             mock.patch.object(forge, '_remote_observations'):
            self.assertEqual(forge.inspect(self.task_id, 'read', path='x', start=2, end=3), 'two\nthree')
        rpc.assert_called_once_with('workspace.inspect', {'root': str(self.root), 'action': 'read',
            'path': 'x', 'query': '', 'glob': '**/*', 'start': 2, 'end': 3, 'base': ''})

    def test_live_partial_result_advances_through_real_worker_cas(self):
        folder = self.unit()
        with mock.patch.object(forge, '_event'):
            self.assertEqual(forge._admission_reason(self.task_id, str(folder / 'request.json')), '')
            self.assertTrue(forge_worker._publish_result(folder / 'request.json', self.task_id,
                                                       {'status': 'done', 'result': 'settled'}))
        result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(result, {'status': 'done', 'result': 'settled', 'iterations': 3})
        self.assertFalse(forge_worker._publish_result(folder / 'request.json', self.task_id,
                                                     {'status': 'error'}))

    def test_live_partial_ancestors_allow_descendant_but_unknown_and_terminal_do_not(self):
        parent = self.unit()
        child = self.unit('agent-child', parent=parent.name)
        for status in ('starting', 'running', 'finishing'):
            self.put(parent / 'result.json', {'status': status})
            self.assertEqual(forge._admission_reason(self.task_id, str(child / 'request.json')), '')
        for data in ({'status': 'done'}, {'status': 'stopped'}, {'status': 'unknown'}, {'status': []}, {}, []):
            self.put(parent / 'result.json', data)
            self.assertTrue(forge._admission_reason(self.task_id, str(child / 'request.json')))
        (parent / 'result.json').write_text('not-json', encoding='utf-8')
        self.assertTrue(forge._admission_reason(self.task_id, str(child / 'request.json')))

    def test_live_partial_never_bypasses_stop_pause_or_yield(self):
        parent = self.unit()
        child = self.unit('agent-child', parent=parent.name)
        request = str(child / 'request.json')
        self.put(parent / 'stop.json', {'state': 'unresolved'})
        self.assertIn('stop-fenced', forge._admission_reason(self.task_id, request))
        (parent / 'stop.json').unlink()
        for action in ('stop', 'pause'):
            self.put(self.folder / 'control.json', {'action': action})
            self.assertIn('fenced', forge._admission_reason(self.task_id, request))
        (self.folder / 'control.json').unlink()
        self.put(child / 'handoff.json', {'state': 'waiting', 'child_unit_id': 'agent-grandchild'})
        self.assertIn('yielded', forge._admission_reason(self.task_id, request))


class WorkerResultContract(unittest.TestCase):
    setUp = ObservationContract.setUp
    put = ObservationContract.put
    unit = ObservationContract.unit

    def worker(self):
        folder = self.unit()
        (folder / 'result.json').unlink()
        request_path = folder / 'request.json'
        request = json.loads(request_path.read_text(encoding='utf-8'))
        self.put(request_path, {**request, 'brief': 'observe', 'max_iters': 2})
        return request_path

    def test_native_llm_length_contract_is_stalled_with_exact_partial_and_no_replay(self):
        import forge_runtime
        import llm
        for text in ('  partial\n', ''):
            with self.subTest(text=text):
                path = self.worker()
                # Real provider normalizer and empty-answer guard, not a guessed stop enum.
                answer = llm._guard_answer(llm._openai_stream_result([text], {
                    0: {'name': 'inspect', 'id': 'partial-call', 'args': '{"action":"status"}'}
                }, 'length', 1, 1, 0, 'offline-model'))
                answer.text = text  # raw provider payload can contain trailing whitespace
                with mock.patch.object(forge_worker, '_system', return_value='offline'), \
                     mock.patch.object(llm, 'chat', return_value=answer) as chat, \
                     mock.patch.object(forge_worker, '_dispatch') as dispatch, \
                     mock.patch.object(forge, 'inspect', return_value=''), \
                     mock.patch.object(forge, '_event'), mock.patch.object(forge, 'emit_unit_event'):
                    self.assertEqual(forge_worker.run(path), 0)
                    self.assertEqual(forge_worker.run(path), 0)  # terminal redelivery
                chat.assert_called_once()
                dispatch.assert_not_called()
                result = json.loads((path.parent / 'result.json').read_text(encoding='utf-8'))
                self.assertEqual((result['status'], result['complete'], result['stop_reason']),
                                 ('stalled', False, 'max_tokens'))
                self.assertIn('max_tokens', result['stop'])
                event = next(e for e in forge_runtime.read_events(path.parent)['events']
                             if e['kind'] == 'model_result')
                raw = json.loads(forge_runtime.read_payload_full(path.parent, event['output_ref']))
                self.assertEqual(raw['text'], text)
                self.assertEqual(raw['blocks'], answer.blocks)
                self.assertEqual(raw['stop_reason'], 'max_tokens')
                # Keep the first independent case and create the next fixture fresh.
                if text:
                    import shutil
                    shutil.rmtree(path.parent)

    def test_early_control_failure_clears_ambient_context_and_restores_it(self):
        import run_context
        path = self.worker()
        self.put(self.folder / 'control.json', {'action': 'stop'})
        outer = run_context.RunContext.create(kind='chat', goal='outer', principal_id='owner',
                                             scope='private', origin_chat_id='123', delivery_chat_id='123')
        seen = []
        with run_context.bind_run(outer), \
             mock.patch.object(llm := forge_worker.llm, 'chat') as chat, \
             mock.patch.object(forge, '_event'), \
             mock.patch.object(forge, 'emit_unit_event', side_effect=lambda *a, **k: seen.append(run_context.current_run())):
            self.assertEqual(forge_worker.run(path), 2)
            self.assertIs(run_context.current_run(), outer)
        chat.assert_not_called()
        self.assertEqual(seen, [None])
        self.assertEqual(json.loads((path.parent / 'result.json').read_text(encoding='utf-8'))['status'], 'stopped')

    def test_malformed_captured_context_cannot_inherit_ambient_error_audience(self):
        import run_context
        path = self.worker()
        request = json.loads(path.read_text(encoding='utf-8'))
        self.put(path, {**request, 'run_context': {'run_id': 'partial'}})
        outer = run_context.RunContext.create(kind='chat', goal='outer', principal_id='owner',
                                             scope='private', origin_chat_id='123', delivery_chat_id='123')
        seen = []
        with run_context.bind_run(outer), \
             mock.patch.object(forge_worker.llm, 'chat') as chat, \
             mock.patch.object(forge, '_event'), \
             mock.patch.object(forge, 'emit_unit_event', side_effect=lambda *a, **k: seen.append(run_context.current_run())):
            self.assertEqual(forge_worker.run(path), 2)
            self.assertIs(run_context.current_run(), outer)
        chat.assert_not_called()
        self.assertEqual(seen, [None])
        self.assertEqual(json.loads((path.parent / 'result.json').read_text(encoding='utf-8'))['status'], 'error')


if __name__ == '__main__':
    unittest.main()
