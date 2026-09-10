"""Offline monotonic phase receipts: no provider, private input or runtime writes."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent
import pre_model_timing as timing


class TimingTests(unittest.TestCase):
    def test_monotonic_partition_and_once_only_preparation(self):
        with patch.object(timing.time, 'monotonic', side_effect=[10, 10.125, 11, 12]):
            prep = timing.Timer()
            prep.mark('old_context')
            call = timing.Timer()
            call.mark('canary')
        with timing.bind(prep):
            first = timing.payload(call)
            second = timing.payload(call)
        self.assertEqual(first['stages_ms'], {'old_context': 125, 'canary': 1000})
        self.assertEqual(first['measured_total_ms'], 1125)
        self.assertTrue(first['preparation_observed'])
        self.assertFalse(second['preparation_observed'])
        self.assertNotIn('old_context', second['stages_ms'])
        self.assertFalse(timing.payload(call)['preparation_observed'])

    def test_closed_stage_names(self):
        with self.assertRaises(ValueError):
            timing.Timer().mark('secret prompt text')

    def test_nested_binding_restores_outer(self):
        outer, inner = timing.Timer(), timing.Timer()
        outer.stages = {'old_context': 2}
        inner.stages = {'shadow': 3}
        with timing.bind(outer):
            with timing.bind(inner):
                self.assertEqual(timing.payload(timing.Timer())['stages_ms'], {'shadow': 3})
            self.assertEqual(timing.payload(timing.Timer())['stages_ms'], {'old_context': 2})

    def test_receipt_separate_from_artifact_and_failure_nonblocking(self):
        messages = [{'role': 'user', 'content': 'PRIVATE_SENTINEL'}]
        system, tools = 'SYSTEM_SENTINEL', []
        artifacts, events = [], []
        def event(kind, **fields):
            events.append((kind, fields))
            if kind == 'model_preparation_timing':
                raise OSError('telemetry sink unavailable')
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        with patch.object(agent.run_context, 'current_run', return_value=SimpleNamespace(run_id='fake')), \
             patch.object(agent, '_runs', return_value=SimpleNamespace(store_result=lambda *a, **k: artifacts.append((a, k)))), \
             patch.object(agent, '_run_status_gate'), \
             patch.object(agent, '_run_event_strict', side_effect=event), \
             patch.object(agent.frame_trace, 'metadata_for', return_value={'fixed': 1}), \
             patch.object(agent.frame_measure, 'measure', return_value=None), \
             patch.object(agent.frame_serve, 'select', return_value=(system, None)), \
             patch.object(agent.llm, 'chat', return_value=response) as chat:
            agent._model_call(system, messages, tools)
        self.assertIs(chat.call_args.kwargs['system'], system)
        self.assertIs(chat.call_args.kwargs['messages'], messages)
        self.assertIs(chat.call_args.kwargs['tools'], tools)
        receipts = [fields for kind, fields in events if kind == 'model_preparation_timing']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(set(receipts[0]['stages_ms']),
                         {'call_setup', 'measure', 'canary', 'model_input_artifact'})
        self.assertNotIn('SENTINEL', json.dumps(receipts))
        inp = next(k for a, k in artifacts if k['name'] == 'model-input')
        self.assertEqual(inp['metadata'], {'fixed': 1})
        self.assertLess([k for k, _ in events].index('model_started'),
                        [k for k, _ in events].index('model_preparation_timing'))

class TimingFaultTests(unittest.TestCase):
    def test_voice_model_fault_matrix(self):
        # Exercise both voice preparation and call-local meters through the real
        # _voice_impl -> _model_call boundary, without providers or artifacts.
        import contextlib
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        class BrokenBinding:
            def __init__(self, boundary):
                self.boundary = boundary
            def __enter__(self):
                timing._PREPARATION.set({'shadow': 999})
                if self.boundary == 'entry':
                    raise RuntimeError('meter entry')
            def __exit__(self, *args):
                timing._PREPARATION.set({'shadow': 999})
                raise RuntimeError('meter exit')
        for fault in ('construction', 'mark', 'bind_factory', 'entry', 'exit', 'payload', 'sink'):
            for model_fails in (False, True):
                with self.subTest(fault=fault, model_fails=model_fails), contextlib.ExitStack() as stack:
                    if fault == 'construction':
                        stack.enter_context(patch.object(timing, 'Timer', side_effect=RuntimeError('meter')))
                    elif fault == 'mark':
                        stack.enter_context(patch.object(timing.Timer, 'mark', side_effect=RuntimeError('meter')))
                    elif fault == 'bind_factory':
                        stack.enter_context(patch.object(timing, 'bind', side_effect=RuntimeError('meter')))
                    elif fault in ('entry', 'exit'):
                        stack.enter_context(patch.object(timing, 'bind', return_value=BrokenBinding(fault)))
                    elif fault == 'payload':
                        stack.enter_context(patch.object(timing, 'payload', side_effect=RuntimeError('meter')))
                    def event(kind, **fields):
                        if fault == 'sink' and kind == 'model_preparation_timing':
                            raise RuntimeError('meter')
                    stack.enter_context(patch.object(agent, '_run_event_strict', side_effect=event))
                    stack.enter_context(patch.object(agent.run_context, 'current_run', return_value=SimpleNamespace(run_id='fake')))
                    stack.enter_context(patch.object(agent, '_runs', return_value=SimpleNamespace(store_result=lambda *a, **k: None)))
                    stack.enter_context(patch.object(agent, '_run_status_gate'))
                    stack.enter_context(patch.object(agent, '_build_prompt_parts', return_value=('persona', 'dynamic', '')))
                    stack.enter_context(patch.object(agent.frame_shadow, 'enabled', return_value=False))
                    stack.enter_context(patch.object(agent.frame_trace, 'metadata_for', return_value=None))
                    stack.enter_context(patch.object(agent.frame_measure, 'measure', return_value=None))
                    stack.enter_context(patch.object(agent.frame_serve, 'select', side_effect=lambda **k: (k['system'], None)))
                    expected = {}
                    def loop(**kw):
                        expected.update(kw)
                        return agent._model_call(kw['system'], kw['messages'], kw['tools'])
                    stack.enter_context(patch.object(agent, '_terminal_tool_loop', side_effect=loop))
                    error = LookupError('original model error')
                    chat = stack.enter_context(patch.object(agent.llm, 'chat', side_effect=error if model_fails else None, return_value=response))
                    outer = {'old_context': 42}
                    token = timing._PREPARATION.set(outer)
                    try:
                        if model_fails:
                            with self.assertRaises(LookupError) as caught:
                                agent._voice_impl('hello', [], None, no_tools=True)
                            self.assertIs(caught.exception, error)
                        else:
                            self.assertIs(agent._voice_impl('hello', [], None, no_tools=True), response)
                        chat.assert_called_once()
                        for key in ('system', 'messages', 'tools'):
                            self.assertIs(chat.call_args.kwargs[key], expected[key])
                        self.assertIs(timing._PREPARATION.get(), outer)
                    finally:
                        timing._PREPARATION.reset(token)

    def test_safe_nested_binding_and_body_failure(self):
        outer, inner = timing.Timer(), timing.Timer()
        outer.stages, inner.stages = {'old_context': 2}, {'shadow': 3}
        error = ValueError('voice failure')
        with timing.safe_bind(outer):
            with self.assertRaises(ValueError) as caught:
                with timing.safe_bind(inner):
                    self.assertEqual(timing.payload(timing.Timer())['stages_ms'], {'shadow': 3})
                    raise error
            self.assertIs(caught.exception, error)
            self.assertEqual(timing.payload(timing.Timer())['stages_ms'], {'old_context': 2})
        self.assertIsNone(timing._PREPARATION.get())
