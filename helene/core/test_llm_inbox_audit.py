"""Offline receipts for the 09.09 inbox per-call attribution repair."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import llm


class InboxCallTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trace = Path(self.tmp.name) / 'calls.jsonl'
        p = patch.object(llm, '_CALL_TRACE', self.trace)
        p.start()
        self.addCleanup(p.stop)

    def record(self, context):
        with patch('run_context.current_run', return_value=context):
            llm._call_trace('voice', 'test-model', ok=True, cached=2, prompt=3,
                            out_tokens=4, latency_ms=5)
        return json.loads(self.trace.read_text().splitlines()[-1])

    def test_attribution_contains_only_bounded_ids(self):
        row = self.record(SimpleNamespace(run_id='r' * 100, origin_chat_id=-123,
                         delivery_chat_id=456, principal_id='p' * 60,
                         forge_task_id='t' * 60, name='PRIVATE NAME',
                         message='PRIVATE MESSAGE'))
        self.assertEqual(row['chat'], '-123')
        self.assertEqual(row['run'], 'r' * 64)
        self.assertEqual(row['who'], 'p' * 32)
        self.assertEqual(row['task'], 't' * 32)
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_delivery_chat_and_missing_context(self):
        self.assertEqual(self.record(SimpleNamespace(delivery_chat_id=456))['chat'], '456')
        for context in (None, SimpleNamespace()):
            row = self.record(context)
            self.assertFalse({'chat', 'who', 'task'} & row.keys())

    def test_successful_fallback_has_actual_model_and_normalized_usage(self):
        cfg = {'roles': {'voice': {'framework': 'openai', 'model': 'primary',
                                  'fallback_framework': 'openai', 'fallback_model': 'backup'}}}
        response = llm.LLMResponse(text='ok', model='actual-backup',
                                   usage={'in': 11, 'out': 7, 'cache_read': 19})
        with patch.object(llm, '_config', return_value=cfg), \
             patch.object(llm, '_resolve_model', return_value='primary'), \
             patch.object(llm, '_resolve_fallback_model', return_value='backup'), \
             patch.object(llm, '_client_for', return_value=object()), \
             patch.object(llm, '_call_retrying_empty', side_effect=RuntimeError('offline failure')), \
             patch.object(llm, '_fallbackable', return_value=True), \
             patch.object(llm, '_call', return_value=response) as call, \
             patch.object(llm, '_usage_add'), patch.object(llm, '_brain_note'), \
             patch.object(llm, '_journal'), patch.object(llm, '_note_truncation'), \
             patch.object(llm, '_call_gap', return_value=-1), \
             patch.dict(llm._STATE, {'voice': {'on_fallback': False, 'last_error': ''}}), \
             patch('run_context.current_run', return_value=None):
            self.assertIs(llm.chat('voice', messages=[]), response)
        self.assertEqual(call.call_args.args, ('openai', 'backup'))
        rows = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]['ok'])
        self.assertEqual(rows[0]['model'], 'primary')
        self.assertTrue(rows[1]['ok'])
        self.assertTrue(rows[1]['fallback'])
        self.assertNotIn('err', rows[1])
        self.assertEqual(rows[1]['model'], 'actual-backup')
        self.assertEqual((rows[1]['in'], rows[1]['out'], rows[1]['cached']), (11, 7, 19))
        self.assertEqual(rows[1]['tools'], rows[0]['tools'])

    def test_trace_write_failure_is_nonfatal(self):
        with patch.object(llm, '_CALL_TRACE', Path(self.tmp.name)):
            llm._call_trace(
                'voice', 'test-model', ok=True, cached=0, prompt=0,
                out_tokens=0, latency_ms=0)


if __name__ == '__main__':
    unittest.main()
