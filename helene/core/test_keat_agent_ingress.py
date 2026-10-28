"""Production seam regressions: no provider, no live filesystem."""
import copy
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import agent


class IngressBoundary(unittest.TestCase):
    def test_capture_precedes_render_and_identity_never_enters_role_tape(self):
        history = [{'role': 'assistant', 'content': 'old', '_keat_occurrence': {'key': 'opaque'}}]
        original = copy.deepcopy(history)
        state = object()
        order = []
        def capture(**kw):
            order.append('capture')
            self.assertEqual(kw['history'], original)
            self.assertEqual(kw['user_msg'], 'new')
            return state
        def build(*args, **kw):
            order.append('render')
            return 'P', 'D', ''
        @contextmanager
        def bind(actual, *, messages):
            self.assertIs(actual, state)
            self.assertNotIn('_keat_occurrence', messages[0])
            yield
        with patch.object(agent, '_KEAT_ORIGINAL_INGRESS', SimpleNamespace(get=lambda: True)), \
             patch.object(agent.keat_live, 'capture_turn', side_effect=capture), \
             patch.object(agent.keat_live, 'bind_turn', side_effect=bind), \
             patch.object(agent, '_build_prompt_parts', side_effect=build), \
             patch.object(agent, '_terminal_tool_loop', return_value='answer') as loop, \
             patch.object(agent.frame_shadow, 'enabled', return_value=False):
            answer = agent._voice_impl('new', history, None,
                                      ctx=agent.ChannelContext(chat_id=101, owner=True), no_tools=True)
        self.assertEqual(answer, 'answer')
        self.assertEqual(order, ['capture', 'render'])
        self.assertEqual(history, original)
        self.assertNotIn('_keat_occurrence', loop.call_args.kwargs['messages'][0])

    def test_model_input_binds_exact_call_after_frame_selection_and_fallback_is_exact(self):
        system = [{'type': 'text', 'text': 'LIVE'}]
        selected = [{'type': 'text', 'text': 'SELECTED'}]
        messages = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'x', 'name': 'shell', 'input': {}}]},
                    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'x', 'content': 'ok'}]}]
        tools = []
        results = []
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        store = SimpleNamespace(store_result=lambda *args, **kw: results.append((args, kw)))
        receipt = {'status': 'served', 'served': True, 'head': 'a' * 64}
        with patch.object(agent.run_context, 'current_run', return_value=SimpleNamespace(run_id='r')), \
             patch.object(agent, '_runs', return_value=store), \
             patch.object(agent, '_run_status_gate'), patch.object(agent, '_run_event_strict'), \
             patch.object(agent.frame_serve, 'select', return_value=(selected, None)), \
             patch.object(agent.keat_live, 'select', side_effect=[receipt, RuntimeError('private')]) as select, \
             patch.object(agent.llm, 'chat', return_value=response) as chat:
            for i in range(2):
                agent._model_call(system, messages, tools)
                self.assertIs(chat.call_args.kwargs['system'], selected)
                self.assertIs(chat.call_args.kwargs['messages'], messages)
                self.assertIs(chat.call_args.kwargs['tools'], tools)
                self.assertIs(select.call_args.kwargs['system'], selected)
        inputs = [(json.loads(args[1]), kw) for args, kw in results if kw.get('event_kind') == 'model_input']
        # A fabricated positive without a verified current selection is not
        # a served receipt (genuine served coverage lives at the provider gate).
        self.assertNotIn('keat', inputs[0][0])
        self.assertNotIn('keat', inputs[0][1].get('metadata') or {})
        self.assertEqual(inputs[0][1]['call_id'], select.call_args_list[0].kwargs['call_id'])
        self.assertNotIn('keat', inputs[1][0])
        self.assertEqual(inputs[1][0]['messages'], messages)

    def test_respond_preserves_capture_identity_without_recapture_and_resets_sink(self):
        state = object()
        def voice(*args, **kw):
            agent._KEAT_HISTORY_SINK.get()['state'] = state
            return 'answer'
        def entry(actual, *, role, content):
            self.assertIs(actual, state)
            return {'role': role, 'content': content, '_keat_occurrence': role + '-id'}
        history = []
        with patch.object(agent.llm, 'configured', return_value=True), \
             patch.object(agent, '_voice', side_effect=voice), \
             patch.object(agent.keat_live, 'history_entry', side_effect=entry):
            self.assertEqual(agent.respond('new', history=history), 'answer')
        self.assertEqual([row['_keat_occurrence'] for row in history], ['user-id', 'assistant-id'])
        self.assertIsNone(agent._KEAT_HISTORY_SINK.get())

    def test_append_capture_precedes_extension_and_failure_preserves_exact_roles(self):
        for fail in (False, True):
            messages = [{'role': 'user', 'content': 'original'}]
            additions = [{'role': 'assistant', 'content': [{'type': 'text', 'text': 'answer'}]},
                         {'role': 'user', 'content': [{'type': 'text', 'text': 'continue'}]}]
            def capture(tape, new):
                self.assertIs(tape, messages)
                self.assertEqual(len(tape), 1)
                self.assertIs(new, additions)
                if fail:
                    raise ValueError('private')
            with patch.object(agent.keat_live, 'capture_appended', side_effect=capture, create=True):
                agent._append_captured_messages(messages, additions)
            self.assertIs(messages[-2], additions[0])
            self.assertIs(messages[-1], additions[1])

    def test_critical_tool_original_is_not_captured(self):
        messages = []
        additions = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'x',
                     'name': 'telegram_account', 'input': {'action': 'call', 'params': {'secret': 'value'}}}]}]
        with patch.object(agent, '_critical_telegram_tool_input', return_value=True), \
             patch.object(agent.keat_live, 'capture_appended', create=True) as capture:
            agent._append_captured_messages(messages, additions)
        capture.assert_not_called()
        self.assertIs(messages[0], additions[0])

    def test_direct_voice_without_explicit_ingress_does_not_issue(self):
        with patch.object(agent.keat_live, 'capture_turn') as capture, \
             patch.object(agent, '_build_prompt_parts', return_value=('P', 'D', '')), \
             patch.object(agent, '_terminal_tool_loop', return_value='answer'), \
             patch.object(agent.frame_shadow, 'enabled', return_value=False):
            agent._voice_impl('flattened legacy conversation', [], None,
                              ctx=agent.ChannelContext(chat_id=101, owner=True), no_tools=True)
        capture.assert_not_called()

    def test_real_capture_to_model_input_checkpoint(self):
        import os
        import tempfile
        from pathlib import Path
        from keat_capture import NarrowingReader
        with tempfile.TemporaryDirectory() as root:
            policy_path = Path(root) / 'policy.json'
            policy_path.write_text(json.dumps({
                'schema': 'keat.capture-policy.v1', 'namespace': 'production-seam-test',
                'root': root, 'enrollments': [{'stream': '101', 'mode': 'owner', 'capture': {
                    'issuer': 'test-admin', 'policy_revision': 'p1', 'audience': ['owner'],
                    'transfer': 'none', 'presence_hidden': False}}],
            }))
            env = {'PRAXIS_KEAT_CAPTURE': 'on', 'PRAXIS_KEAT_CAPTURE_POLICY': str(policy_path),
                   'PRAXIS_KEAT': 'serve', 'PRAXIS_KEAT_MODES': 'owner', 'PRAXIS_KEAT_STREAMS': '101'}
            results = []
            store = SimpleNamespace(store_result=lambda *args, **kw: results.append((args, kw)))
            response = SimpleNamespace(blocks=[], text='answer', stop_reason='end_turn')
            with patch.dict(os.environ, env), \
                 patch.object(agent.run_context, 'current_run', return_value=SimpleNamespace(run_id='r')), \
                 patch.object(agent, '_runs', return_value=store), \
                 patch.object(agent, '_run_status_gate'), patch.object(agent, '_run_event_strict'), \
                 patch.object(agent.frame_serve, 'select', side_effect=lambda **kw: (kw['system'], None)), \
                 patch.object(agent.llm, 'chat', return_value=response) as chat:
                ctx = agent.ChannelContext(chat_id=101, owner=True)
                state = agent.keat_live.capture_turn(ctx, 'original', [])
                messages = [{'role': 'user', 'content': '> original with current evidence'}]
                with agent.keat_live.bind_turn(state, messages):
                    agent._model_call('system', messages, [])
                    grant = state.ledger.snapshot()['receipts'][0]['capture']['grant']
                    NarrowingReader(state.ledger.directory, state.ledger.namespace).narrow(grant)
                    agent._model_call('system', messages, [])
                self.assertIs(chat.call_args.kwargs['messages'], messages)
            inputs = [json.loads(args[1]) for args, kw in results if kw.get('event_kind') == 'model_input']
            self.assertIn('keat', inputs[0])
            self.assertEqual(inputs[1]['keat']['status'], 'fallback')
            self.assertEqual(inputs[0]['messages'], inputs[1]['messages'])
