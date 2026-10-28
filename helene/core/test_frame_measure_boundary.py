"""Offline acceptance for the opt-in measurement seam; no model/network calls."""
import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent
import frame_measure as meter
import frame_shadow
from test_frame_shadow import ShadowCase, _ctx


class CandidateSemantics(ShadowCase):
    def assemble(self, ctx=None, sections=(), messages=None):
        return meter.assemble(ctx=ctx or _ctx(), live_sections=sections,
                              messages=messages or [], tools=[])

    def test_e_is_fresh_each_call_without_epoch_writes(self):
        index = self.base / 'memory' / 'INDEX.md'
        index.write_text('fresh E first', encoding='utf-8')
        first = self.assemble()['system']
        index.write_text('fresh E second', encoding='utf-8')
        second = self.assemble()['system']
        self.assertIn('fresh E first', first)
        self.assertIn('fresh E second', second)
        self.assertNotIn('fresh E first', second)
        self.assertFalse(list(self.base.rglob('epoch.json')))
        self.assertFalse(list(self.base.rglob('metrics.jsonl')))

    def test_addressee_evidence_and_unfinished_actions_stay_in_original_roles(self):
        messages = [
            {'role': 'user', 'content': '<CURRENT_SITUATION>reply_to=321 recipient=Guest</CURRENT_SITUATION> evidence'},
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'pending', 'name': 'shell', 'input': {'cmd': 'pending work'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'pending', 'content': 'unfinished: still running'}]},
        ]
        sections = [{'included': True, 'name': 'frame.extra_system',
                     'text': 'task remains unfinished; no delivery receipt'}]
        candidate = self.assemble(sections=sections, messages=messages)
        self.assertEqual(candidate['messages'], messages)
        self.assertIn('task remains unfinished; no delivery receipt', candidate['system'])
        self.assertNotIn('pending work', candidate['system'])
        self.assertNotIn('работа: —', candidate['system'])

    def test_recap_only_history_survives_without_role_tape(self):
        fact = 'Historical decision: the archive key is amber-742.'
        sections = [{'included': True, 'name': 'evidence.tier',
                     'label': 'Ранее в этом диалоге (сводка)', 'text': fact}]
        before = copy.deepcopy(sections)
        candidate = self.assemble(sections=sections, messages=[])
        self.assertEqual(candidate['messages'], [])
        self.assertIn(fact, candidate['system'])
        self.assertEqual(candidate['system'].count(fact), 1)
        self.assertEqual(sections, before)

    def test_other_audience_does_not_receive_owner_index(self):
        (self.base / 'memory' / 'INDEX.md').write_text('OWNER_PRIVATE_INDEX', encoding='utf-8')
        self.assertIn('OWNER_PRIVATE_INDEX', self.assemble()['system'])
        for ctx in (_ctx(owner_audience=False), _ctx(dm=False)):
            self.assertNotIn('OWNER_PRIVATE_INDEX', self.assemble(ctx=ctx)['system'])


class BoundaryObservation(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'PRAXIS_FRAME_V6': 'measure',
                                          'PRAXIS_FRAME_V6_STREAMS': 'dm-101'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.system = [{'type': 'text', 'text': 'PRIVATE_SYSTEM', 'cache_control': {'type': 'ephemeral'}}]
        self.messages = [{'role': 'user', 'content': [{'type': 'image', 'source': {'data': 'PRIVATE_IMAGE'}}]}]
        self.tools = [{'name': 'PRIVATE_TOOL'}]

    def test_switches_are_exact_and_default_off_serve_unsupported(self):
        for mode, streams in [('', 'dm-101'), ('serve', 'dm-101'), ('measure', ''), ('measure', '*'), ('measure', 'dm-10')]:
            with patch.dict(os.environ, {'PRAXIS_FRAME_V6': mode, 'PRAXIS_FRAME_V6_STREAMS': streams}):
                self.assertFalse(meter.enabled(_ctx()))
        self.assertTrue(meter.enabled(_ctx()))

    def test_identity_scope_nested_reset_and_same_call_numeric_only(self):
        def assemble(**kw):
            return dict(system='PRIVATE_CANDIDATE', messages=kw['messages'], tools=kw['tools'])
        with patch.object(meter, 'assemble', side_effect=assemble), meter.bind(system=self.system, ctx=_ctx(), live_sections=[]):
            one = meter.measure(system=self.system, messages=self.messages, tools=self.tools)
            self.assertEqual(one['pair_status'], 'same_call')
            self.assertNotIn('PRIVATE', json.dumps(one))
            self.assertIsNone(meter.measure(system=copy.deepcopy(self.system), messages=[], tools=[]))
            with meter.bind(system='nested', ctx=_ctx(202), live_sections=[]):
                self.assertIsNone(meter.measure(system=self.system, messages=[], tools=[]))
            self.assertIsNotNone(meter.measure(system=self.system, messages=[], tools=[]))
        self.assertIsNone(meter.measure(system=self.system, messages=[], tools=[]))

    def test_actual_model_boundary_failure_does_not_mutate_or_suppress_input(self):
        before = copy.deepcopy((self.system, self.messages, self.tools))
        def broken(**kw):
            kw['messages'].clear()
            kw['tools'].clear()
            raise RuntimeError('PRIVATE_EXCEPTION')
        receipts = []
        store = SimpleNamespace(store_result=lambda *a, **kw: receipts.append(kw))
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        with patch.object(meter, 'assemble', side_effect=broken), \
             patch.object(agent.run_context, 'current_run', return_value=SimpleNamespace(run_id='fake')), \
             patch.object(agent, '_runs', return_value=store), \
             patch.object(agent, '_run_status_gate'), patch.object(agent, '_run_event_strict'), \
             patch.object(agent.frame_trace, 'metadata_for', return_value=None), \
             patch.object(agent.llm, 'chat', return_value=response) as chat, \
             meter.bind(system=self.system, ctx=_ctx(), live_sections=[]):
            self.assertIs(agent._model_call(self.system, self.messages, self.tools), response)
        self.assertEqual((self.system, self.messages, self.tools), before)
        for name, value in [('system', self.system), ('messages', self.messages), ('tools', self.tools)]:
            self.assertIs(chat.call_args.kwargs[name], value)
        metadata = receipts[0]['metadata']
        self.assertEqual(metadata['variant'], 'measure')
        self.assertEqual(metadata['frame_measure']['status'], 'failed')
        self.assertNotIn('PRIVATE', json.dumps(metadata))

    def test_meter_itself_throwing_is_noninterfering(self):
        with patch.object(agent.run_context, 'current_run', return_value=None), \
             patch.object(meter, 'measure', side_effect=RuntimeError('PRIVATE')), \
             patch.object(agent.llm, 'chat', return_value='ok') as chat:
            self.assertEqual(agent._model_call(self.system, self.messages, self.tools), 'ok')
        self.assertIs(chat.call_args.kwargs['system'], self.system)

    def test_failed_context_setup_and_disabled_mode_do_not_read_sources(self):
        with meter.bind(system=self.system, ctx=_ctx(), live_sections=lambda: 1/0):
            self.assertIsNone(meter.measure(system=self.system, messages=[], tools=[]))
        with patch.dict(os.environ, {'PRAXIS_FRAME_V6': ''}), \
             meter.bind(system=self.system, ctx=_ctx(), live_sections=lambda: self.fail('disabled source read')):
            self.assertIsNone(meter.measure(system=self.system, messages=[], tools=[]))

    def test_success_boundary_each_iteration_uses_current_tape_and_fresh_pair(self):
        seen = []
        pairs = []
        original_compare = meter.compare
        def compare(**kw):
            pairs.append((kw['actual'].call, kw['candidate'].call))
            return original_compare(**kw)
        def assemble(**kw):
            seen.append(copy.deepcopy(kw))
            return dict(system='candidate', messages=kw['messages'], tools=kw['tools'])
        with patch.object(meter, 'assemble', side_effect=assemble), \
             patch.object(meter, 'compare', side_effect=compare), \
             patch.object(agent.run_context, 'current_run', return_value=None), \
             patch.object(agent.llm, 'chat', return_value='ok') as chat, \
             meter.bind(system=self.system, ctx=_ctx(), live_sections=[]):
            agent._model_call(self.system, self.messages, self.tools)
            self.messages.append({'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': 'pending', 'name': 'shell', 'input': {}}]})
            self.messages.append({'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'pending', 'content': 'still running'}]})
            agent._model_call(self.system, self.messages, None)
        self.assertEqual(len(seen[0]['messages']), 1)
        self.assertEqual(seen[1]['messages'], self.messages)
        self.assertEqual(seen[1]['tools'], [])
        self.assertNotIn('tools', chat.call_args.kwargs)
        self.assertIs(chat.call_args.kwargs['messages'], self.messages)
        self.assertTrue(all(a is b for a, b in pairs))
        self.assertIsNot(pairs[0][0], pairs[1][0])
