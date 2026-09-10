"""Hermetic task -> detached request -> worker -> receipt attribution checks."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import forge
import forge_worker
import llm
import run_context
from test_llm import Base, RateLimitError


def origin():
    return run_context.RunContext.create(kind='chat', goal='test', principal_id='123',
                                         scope='group', origin_chat_id='-456',
                                         delivery_chat_id='-789')


class WorkerAttributionTests(unittest.TestCase):
    def test_task_spawn_worker_and_nested_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = 'code-attribution'
            ctx = origin()
            saved = []
            with run_context.bind_run(ctx), \
                 mock.patch.object(forge, '_resolve_target', return_value=(root, 'external')), \
                 mock.patch.object(forge, '_git_root', return_value=None), \
                 mock.patch.object(forge, '_id', return_value=task_id), \
                 mock.patch.object(forge, '_save_task', side_effect=saved.append), \
                 mock.patch.object(forge, '_event'), mock.patch.object(forge, '_arm_upstreams'), \
                 mock.patch.object(forge, '_orientation_text', return_value='orientation'), \
                 mock.patch.object(forge, '_task_dir', return_value=root):
                forge.start('test', target=str(root), isolation='direct')
            task = saved[0]
            self.assertEqual(task['run_context']['principal_id'], '123')
            unit = root / 'agent'; unit.mkdir()
            # Detached spawn after the original context has gone away.
            with mock.patch.object(forge, '_task_root', return_value=(task, root, '')), \
                 mock.patch.object(forge, '_unit_dir', return_value=unit), \
                 mock.patch.object(forge, '_spawn_runner', return_value=1), \
                 mock.patch.object(forge, '_event'), mock.patch.object(forge, '_id', return_value='agent-test'):
                forge.agent(task_id, 'spawn', brief='offline')
            request = unit / 'request.json'
            captured = []
            def run_body(*args, **kwargs):
                captured.append(forge._task_run_context('code-nested'))
                llm._call_trace('voice', 'actual', ok=True, cached=1, prompt=2,
                                out_tokens=3, latency_ms=4)
                return SimpleNamespace(model="actual", stop_reason="end_turn", text="done")
            trace = root / 'calls.jsonl'
            with mock.patch.object(forge_worker, '_chat_with_transport_retry', side_effect=run_body), \
                 mock.patch.object(forge_worker, '_system', return_value='offline'), \
                 mock.patch.object(forge, 'inspect', return_value=''), \
                 mock.patch.object(forge, '_event'), mock.patch.object(forge, 'emit_unit_event'), \
                 mock.patch.object(llm, '_CALL_TRACE', trace):
                self.assertEqual(forge_worker.run(request), 0)
            row = json.loads(trace.read_text())
            self.assertEqual((row['run'], row['chat'], row['who'], row['task']),
                             (ctx.run_id, '-456', '123', task_id))
            self.assertEqual(captured[0]['forge_task_id'], 'code-nested')
            self.assertEqual(captured[0]['principal_id'], '123')
            self.assertIsNone(run_context.current_run())

    def test_worker_context_restored_after_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / 'request.json'
            request.write_text(json.dumps({'task_id': 'code-child', 'id': 'agent-test',
                                           'run_context': origin().to_dict()}))
            outer = origin()
            def body(*args, **kwargs):
                self.assertEqual(run_context.current_run().forge_task_id, 'code-child')
                raise RuntimeError('offline worker failure')
            with run_context.bind_run(outer), \
                 mock.patch.object(forge_worker, '_system', side_effect=body), \
                 mock.patch.object(forge, '_event'), mock.patch.object(forge, 'emit_unit_event'):
                self.assertEqual(forge_worker.run(request), 2)
                self.assertIs(run_context.current_run(), outer)
            self.assertIn('offline worker failure', json.loads(
                (request.parent / 'result.json').read_text())['error'])

    def test_malformed_context_completes_as_error_without_calls_or_tools(self):
        for captured in ({'run_id': 'run-partial'}, 'wrong-type', ['wrong'], 1, False):
            with self.subTest(captured=captured), tempfile.TemporaryDirectory() as tmp:
                request = Path(tmp) / 'request.json'
                request.write_text(json.dumps({'task_id': 'code-child', 'id': 'agent-test',
                                               'run_context': captured}))
                outer = origin()
                def completed(*args, **kwargs):
                    self.assertIsNone(run_context.current_run())
                with run_context.bind_run(outer), \
                     mock.patch.object(forge_worker, '_chat_with_transport_retry') as chat, \
                     mock.patch.object(forge_worker, '_dispatch') as dispatch, \
                     mock.patch.object(forge, '_event') as event, \
                     mock.patch.object(forge, 'emit_unit_event', side_effect=completed) as completion:
                    self.assertEqual(forge_worker.run(request), 2)
                    self.assertIs(run_context.current_run(), outer)
                data = json.loads((request.parent / 'result.json').read_text())
                self.assertEqual(data['status'], 'error')
                self.assertIn('kind must not be empty' if isinstance(captured, dict)
                              else 'run_context must be an object or null', data['error'])
                chat.assert_not_called()
                dispatch.assert_not_called()
                self.assertEqual(event.call_args.args[1], 'agent_error')
                self.assertEqual(completion.call_args.args[2], data)

    def test_legacy_task_does_not_inherit_ambient_principal_for_calls_or_tools(self):
        self.assertEqual(forge._task_run_context('code-old'), {})
        for context_field in ({}, {'run_context': None}, {'run_context': {}}):
            with self.subTest(context_field=context_field), tempfile.TemporaryDirectory() as tmp:
                request = Path(tmp) / 'request.json'
                request.write_text(json.dumps({'task_id': 'code-old', 'id': 'agent-old',
                                               'max_iters': 2, **context_field}))
                observed = []
                def chat(*args, **kwargs):
                    observed.append(('chat', run_context.current_run()))
                    if len(observed) == 1:
                        return SimpleNamespace(model='actual', stop_reason='tool_use', blocks=[
                            {'type': 'tool_use', 'id': 't1', 'name': 'inspect', 'input': {}}])
                    return SimpleNamespace(model='actual', stop_reason='end_turn', text='done')
                def dispatch(*args):
                    observed.append(('tool', run_context.current_run()))
                    self.assertEqual(forge._task_run_context('nested'), {})
                    return 'ok'
                outer = origin()
                with run_context.bind_run(outer), \
                     mock.patch.object(forge_worker, '_system', return_value='offline'), \
                     mock.patch.object(forge_worker, '_chat_with_transport_retry', side_effect=chat), \
                     mock.patch.object(forge_worker, '_dispatch', side_effect=dispatch), \
                     mock.patch.object(forge, 'inspect', return_value=''), \
                     mock.patch.object(forge, '_event'), mock.patch.object(forge, 'emit_unit_event'):
                    self.assertEqual(forge_worker.run(request), 0)
                    self.assertIs(run_context.current_run(), outer)
                self.assertEqual(observed, [('chat', None), ('tool', None), ('chat', None)])
            self.assertIsNone(run_context.current_run())


class FallbackFailureTests(Base):
    def test_failed_fallback_has_own_error_receipt_and_tools(self):
        self._write_cfg(voice={'framework': 'openai', 'model': 'primary',
                               'fallback_framework': 'openai', 'fallback_model': 'backup'})
        trace = self.tmp / 'calls.jsonl'
        with mock.patch.object(llm, '_resolve_model', side_effect=lambda fw, m: m), \
             mock.patch.object(llm, '_resolve_fallback_model', return_value='backup'), \
             mock.patch.object(llm, '_client_for', return_value=object()), \
             mock.patch.object(llm, '_call_retrying_empty', side_effect=RateLimitError('primary failed')), \
             mock.patch.object(llm, '_call', side_effect=RuntimeError('backup failed')), \
             mock.patch.object(llm, '_brain_note'), mock.patch.object(llm, '_CALL_TRACE', trace):
            with self.assertRaisesRegex(RuntimeError, 'backup failed'):
                llm.chat('voice', messages=[], tools=[{'name': 'inspect', 'input_schema': {}}])
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual([r['model'] for r in rows], ['primary', 'backup'])
        self.assertTrue(all(not r['ok'] for r in rows))
        self.assertTrue(rows[1]['fallback'])
        self.assertIn('backup failed', rows[1]['err'])
        self.assertEqual(rows[0]['tools'], rows[1]['tools'])
