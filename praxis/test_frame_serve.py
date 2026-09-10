"""Offline canary boundary regressions; no provider calls or live data."""
import copy
from contextlib import nullcontext
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent
import frame_measure
import frame_serve as serve
from test_frame_shadow import ShadowCase


def ctx(**kw):
    return agent.ChannelContext(chat_id=101, owner=True, **kw)


class CanaryBoundary(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, PRAXIS_FRAME_V6='serve',
                         PRAXIS_FRAME_V6_STREAMS='dm-101', PRAXIS_OWNER_ID='101')
        env.start()
        self.addCleanup(env.stop)
        source = patch.object(serve.frame_shadow, '_read', return_value='CONSTITUTION')
        source.start()
        self.addCleanup(source.stop)
        self.k = 'CONSTITUTION\n\n---\nCONSTITUTION' + serve.frame_shadow._SEP_E
        self.system = [{'type': 'text', 'text': 'LIVE', 'cache_control': {'type': 'ephemeral'}}]
        self.messages = [{'role': 'user', 'content': 'recipient=owner evidence now'}]
        self.tools = [{'name': 'shell', 'input_schema': {'type': 'object'}}]

    def test_gate_exact_owner_dm_only(self):
        self.assertTrue(serve.enabled(ctx()))
        for bad in (ctx(is_dm=False), ctx(hide_identity_load=True),
                    ctx(room_id=202), agent.ChannelContext(chat_id=202, owner=True),
                    agent.ChannelContext(chat_id=101), agent.ChannelContext(owner=True)):
            self.assertFalse(serve.enabled(bad))
        for mode, streams, owner in [('', 'dm-101', '101'), ('measure', 'dm-101', '101'),
                                     ('serve', '*', '101'), ('serve', 'dm-101,chat-101', '101'),
                                     ('serve', 'dm-101', ''), ('serve', 'dm-101', '-101')]:
            with patch.dict(os.environ, PRAXIS_FRAME_V6=mode,
                            PRAXIS_FRAME_V6_STREAMS=streams, PRAXIS_OWNER_ID=owner):
                self.assertFalse(serve.enabled(ctx()))

    def test_iterations_keep_role_tape_tools_and_transport_and_record_actual(self):
        seen, receipts = [], []
        def assemble(**kw):
            seen.append(copy.deepcopy(kw))
            return {'system': self.k + f'K E{len(seen)} T'}
        store = SimpleNamespace(store_result=lambda *args, **kw: receipts.append((args, kw)))
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        with patch.object(frame_measure, 'assemble', side_effect=assemble), \
             patch.object(agent.run_context, 'current_run', return_value=SimpleNamespace(run_id='fake')), \
             patch.object(agent, '_runs', return_value=store), \
             patch.object(agent, '_run_status_gate'), patch.object(agent, '_run_event_strict'), \
             patch.object(agent.frame_trace, 'metadata_for', return_value={'geometry': 'live-only'}), \
             patch.object(agent.llm, 'chat', return_value=response) as chat, \
             serve.bind(system=self.system, ctx=ctx(), dynamic='DYNAMIC_EXACT'):
            agent._model_call(self.system, self.messages, self.tools)
            self.assertTrue(chat.call_args.kwargs['system'].startswith(self.k + 'K E1 TDYNAMIC_EXACT'))
            self.assertIs(chat.call_args.kwargs['messages'], self.messages)
            self.assertIs(chat.call_args.kwargs['tools'], self.tools)
            self.messages.extend([
                {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'p', 'name': 'shell', 'input': {}}]},
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'p', 'content': 'still working'}]}])
            agent._model_call(self.system, self.messages, None)
            self.assertTrue(chat.call_args.kwargs['system'].startswith(self.k + 'K E2 TDYNAMIC_EXACT'))
            self.assertIs(chat.call_args.kwargs['messages'], self.messages)
            self.assertNotIn('tools', chat.call_args.kwargs)
            with patch.dict(os.environ, PRAXIS_FRAME_V6='off'):
                agent._model_call(self.system, self.messages, self.tools)
                self.assertIs(chat.call_args.kwargs['system'], self.system)
        inputs = [(json.loads(a[1]), k) for a, k in receipts if k['name'] == 'model-input']
        self.assertTrue(inputs[0][0]['system'].startswith(self.k + 'K E1 TDYNAMIC_EXACT'))
        self.assertEqual(serve.resume_system(inputs[0][0]), self.system)
        self.assertEqual(inputs[0][1]['metadata']['frame_serve']['served_variant'], 'v6')
        self.assertNotIn('geometry', inputs[0][1]['metadata'])
        self.assertNotIn('frame_v6_live_system', inputs[-1][0])
        self.assertEqual(seen[1]['tools'], [])

    def test_rollback_artifact_uses_structural_secret_scrubbing(self):
        secret = 'synthetic-critical-secret'
        system = [{'type': 'text', 'text': secret}]
        receipts = []
        store = SimpleNamespace(store_result=lambda *a, **k: receipts.append((a, k)))
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        with patch.object(frame_measure, 'assemble', return_value={'system': self.k + secret}), \
             patch.object(agent, '_critical_secret_values_from_messages', return_value=(secret,)), \
             patch.object(agent.run_context, 'current_run', return_value=SimpleNamespace(run_id='fake')), \
             patch.object(agent, '_runs', return_value=store), \
             patch.object(agent, '_run_status_gate'), patch.object(agent, '_run_event_strict'), \
             patch.object(agent.frame_trace, 'metadata_for', return_value=None), \
             patch.object(agent.llm, 'chat', return_value=response) as chat, \
             serve.bind(system=system, ctx=ctx(), dynamic='dynamic'):
            agent._model_call(system, self.messages, self.tools)
        payload = next(a[1] for a, k in receipts if k['name'] == 'model-input')
        self.assertNotIn(secret, payload)
        artifact = json.loads(payload)['frame_v6_live_system']
        self.assertIsInstance(artifact, list)
        self.assertEqual(artifact[0]['text'], agent._CRITICAL_PARAM_REPLACEMENT)
        self.assertIn(secret, chat.call_args.kwargs['system'])
        self.assertEqual(system[0]['text'], secret)

    def test_failure_identity_nested_reset_and_default_no_source_reads(self):
        def select(system=None):
            return serve.select(system=self.system if system is None else system,
                                messages=self.messages, tools=self.tools)
        with patch.object(frame_measure, 'assemble', side_effect=RuntimeError('PRIVATE')) as build:
            with serve.bind(system=self.system, ctx=ctx(), dynamic='dynamic'):
                result, receipt = select()
                self.assertIs(result, self.system)
                self.assertEqual(receipt['status'], 'fallback')
                self.assertNotIn('PRIVATE', json.dumps(receipt))
                self.assertIsNone(select(copy.deepcopy(self.system))[1])
                with serve.bind(system=self.system, ctx=ctx(is_dm=False), dynamic='bad'):
                    self.assertIsNone(select()[1])
            self.assertIsNone(select()[1])
            self.assertEqual(build.call_count, 1)
            with patch.dict(os.environ, PRAXIS_FRAME_V6=''), \
                 serve.bind(system=self.system, ctx=ctx(), dynamic='dynamic'):
                self.assertIsNone(select()[1])
            self.assertEqual(build.call_count, 1)

    def test_durable_continuation_resumes_live_but_preserves_tool_pair(self):
        runtime = object.__new__(agent._AgentResumeRuntime)
        runtime.tool_trace = []
        tape = [{'role': 'user', 'content': 'question'}]
        use = {'type': 'tool_use', 'id': 'p', 'name': 'shell', 'input': {}}
        result = {'type': 'tool_result', 'tool_use_id': 'p', 'content': 'done'}
        request = SimpleNamespace(
            model_input={'system': 'STALE_CANARY', 'frame_v6_live_system': self.system,
                         'messages': tape, 'tools': self.tools},
            model_output={'stop_reason': 'tool_use', 'blocks': [use]},
            resolutions=[object()], checkpoint={'iteration': 2})
        with patch.object(runtime, 'bind', return_value=nullcontext()), \
             patch.object(runtime, '_resolution_blocks', return_value=[result]), \
             patch.object(runtime, '_land_addressee_free_work_control', return_value=None), \
             patch.object(runtime, '_prepare_authored_delivery', return_value='delivered'), \
             patch.object(agent, '_persist_tool_loop_checkpoint') as checkpoint, \
             patch.object(agent, '_terminal_tool_loop', return_value='reply') as loop:
            self.assertEqual(runtime.continue_tool_response(request), 'delivered')
        self.assertEqual(loop.call_args.kwargs['system'], self.system)
        self.assertEqual(checkpoint.call_args.kwargs['system'], self.system)
        self.assertEqual(loop.call_args.kwargs['messages'], tape + [
            {'role': 'assistant', 'content': [use]}, {'role': 'user', 'content': [result]}])
        self.assertEqual(loop.call_args.kwargs['start_iteration'], 3)
        self.assertEqual(loop.call_args.kwargs['tools'], self.tools)
        self.assertEqual(serve.resume_system({'system': self.system}), self.system)

    def test_revalidates_audience_during_loop(self):
        channel = ctx()
        with serve.bind(system=self.system, ctx=channel, dynamic='dynamic'), \
             patch.object(frame_measure, 'assemble') as build:
            object.__setattr__(channel, 'is_dm', False)
            self.assertIsNone(serve.select(system=self.system, messages=[], tools=[])[1])
            build.assert_not_called()


class FreshServing(ShadowCase):
    def test_missing_constitution_falls_back_to_live(self):
        system = object()
        with patch.dict(os.environ, PRAXIS_FRAME_V6='serve',
                        PRAXIS_FRAME_V6_STREAMS='dm-101', PRAXIS_OWNER_ID='101'), \
             serve.bind(system=system, ctx=ctx(), dynamic='dynamic'):
            for name in ('SOUL.md', 'VOICE.md'):
                path = self.base / 'soul' / name
                previous = path.read_text(encoding='utf-8')
                path.write_text('', encoding='utf-8')
                selected, receipt = serve.select(system=system, messages=[], tools=[])
                self.assertIs(selected, system)
                self.assertEqual(receipt['status'], 'fallback')
                path.unlink()
                selected, receipt = serve.select(system=system, messages=[], tools=[])
                self.assertIs(selected, system)
                self.assertEqual(receipt['status'], 'fallback')
                path.write_text(previous, encoding='utf-8')

    def test_actual_fresh_e_with_complete_dynamic_no_epoch_io(self):
        index = self.base / 'memory' / 'INDEX.md'
        system = object()
        dynamic = '\ntransport status; current recipient; extra_system; recap\n'
        with patch.dict(os.environ, PRAXIS_FRAME_V6='serve',
                        PRAXIS_FRAME_V6_STREAMS='dm-101', PRAXIS_OWNER_ID='101'), \
             serve.bind(system=system, ctx=ctx(), dynamic=dynamic):
            index.write_text('FRESH_FIRST', encoding='utf-8')
            first, _ = serve.select(system=system, messages=[], tools=[])
            index.write_text('FRESH_SECOND', encoding='utf-8')
            second, receipt = serve.select(system=system, messages=[], tools=[])
        self.assertEqual(receipt['status'], 'served')
        self.assertIn('FRESH_FIRST', first)
        self.assertIn('FRESH_SECOND', second)
        self.assertNotIn('FRESH_FIRST', second)
        self.assertIn(dynamic, second)
        self.assertFalse(list(self.base.rglob('epoch.json')))
        self.assertFalse(list(self.base.rglob('metrics.jsonl')))
