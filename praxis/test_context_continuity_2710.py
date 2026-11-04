from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import agent
import context_policy
import run_context
import run_manager
import turns
from test_turns import TurnsBase

POLICY = dict(schema=context_policy.SCHEMA, continuation_mode='canonical',
              tool_inline_chars=16000, host_text_chars=16000,
              recent_work_chars=1200, durable_host_full=True)


class PolicyTests(unittest.TestCase):
    def test_capability_inventory_distinguishes_page_from_destructive_clip(self):
        import rails
        with mock.patch.object(context_policy, 'read', return_value=POLICY), \
                mock.patch.object(agent, 'HOST_TEXT_CAP', 16000):
            text = rails._host_text_clip_value()
            self.assertIn('без ранней обрезки', text)
            self.assertIn('ResultRef', text)
            self.assertIn('inline 16000', text)
            self.assertIn('вне durable run', text)
        with mock.patch.object(context_policy, 'read', return_value=POLICY), \
                mock.patch.object(rails, '_live', return_value=None):
            self.assertIn('16000 симв.', rails._host_text_clip_value())

    def test_migration_is_explicit_atomic_and_reversible(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
                context_policy, 'PATH', Path(tmp) / 'policy.json'):
            path = context_policy.PATH
            self.assertEqual(context_policy.read(), context_policy.LEGACY)
            for bad in ('{partial', '[]', '{}', json.dumps(dict(POLICY, tool_inline_chars=True))):
                path.write_text(bad)
                self.assertEqual(context_policy.read(), context_policy.LEGACY)
            path.write_text(json.dumps(POLICY))
            self.assertEqual(context_policy.inline_chars(), 16000)
            path.write_text(json.dumps(dict(context_policy.LEGACY, schema=context_policy.SCHEMA)))
            self.assertEqual(context_policy.read(), context_policy.LEGACY)


class RecentWorkTests(TurnsBase):
    def test_exact_topic_scope_and_latest_turn_not_owner_global_feed(self):
        self._mk(chat_id='room:1', note='checked exact file; pending review; result-1', run_id='run-local')
        self._mk(chat_id='room:2', note='other topic secret')
        self._mk(chat_id='room:1', scope='group', note='other scope')
        self._mk(chat_id=None, kind='task_window', note='private work')
        note = turns.recent_work_note('room:1', 'owner', 1200)
        self.assertEqual(note['run_id'], 'run-local')
        self.assertEqual(note['authored_note'], 'checked exact file; pending review; result-1')
        self.assertIsNone(turns.recent_work_note(None, 'owner', 1200))
        self.assertIsNone(turns.recent_work_note('room:1', 'owner', 0))
        self._mk(chat_id='room:1', note='')
        self.assertIsNone(turns.recent_work_note('room:1', 'owner', 1200))

    def test_restart_and_tail_projection_keep_full_source_and_actual_delivery(self):
        text = 'checked artifact; pending deployment. ' * 80
        self._mk(note=text, run_id='run-note', delivery='failed', out='not accepted')
        turns._reset()
        with mock.patch.object(context_policy, 'read', return_value=POLICY), \
                mock.patch.object(agent.notes, 'read', return_value=''):
            ctx = agent.ChannelContext.from_legacy('777', is_dm=True, owner=True, known=True, scope='owner')
            payload = [json.loads(line) for line in agent._presence_evidence(ctx).splitlines()]
        note = next(r['content'] for r in payload if r['label'] == 'recent_work_note')
        self.assertEqual(note['delivery'], 'failed')
        self.assertTrue(note['truncated'])
        self.assertEqual(note['authored_note'], text[:1200])
        self.assertEqual(turns.recent(n=1)[0]['note'], text)
        self.assertNotIn('not accepted', str(note))


class WorkingResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.manager = run_manager.RunManager(self.tmp.name)
        self.ctx = run_context.RunContext.create(kind='chat_turn', goal='read evidence',
            principal_id='test', scope='owner', origin_chat_id='777')
        self.manager.create(self.ctx, 'test snapshot')
        self.manager.transition(self.ctx.run_id, 'running', expected='pending')
        for patch in (mock.patch.object(context_policy, 'read', return_value=POLICY),
                      mock.patch.object(agent, '_runs', return_value=self.manager)):
            patch.start(); self.addCleanup(patch.stop)

    def test_real_normal_and_resumed_tool_boundaries_share_budget_and_full_receipt(self):
        payload = 'START\n' + 'x' * 6000 + '\nMIDDLE-EVIDENCE\n' + 'y' * 4000 + '\nEND'
        tools = [dict(name='probe', input_schema={'type': 'object'})]
        responses = [types.SimpleNamespace(stop_reason='tool_use', text='', blocks=[
            dict(type='tool_use', id='normal-call', name='probe', input={})]),
            types.SimpleNamespace(stop_reason='end_turn', text='done', blocks=[])]
        messages = [dict(role='user', content='read')]
        with run_context.bind_run(self.ctx), \
                mock.patch.object(agent, '_model_call', side_effect=responses), \
                mock.patch.object(agent, '_work_loop_continue', return_value=False), \
                mock.patch.dict(agent.TOOL_IMPL, probe=lambda: payload):
            agent._terminal_tool_loop(system='test', messages=messages, tools=tools)
        normal = next(b['content'] for m in messages if isinstance(m['content'], list)
                      for b in m['content'] if b.get('type') == 'tool_result')
        self.assertIn(payload, normal)
        runtime = object.__new__(agent._AgentResumeRuntime)
        runtime.manager = self.manager; runtime.tool_trace = []
        runtime.bind = lambda: run_context.bind_run(self.ctx)
        with mock.patch.dict(agent.TOOL_IMPL, probe=lambda: payload):
            resumed = runtime._execute_tool(call_id='resume-call', name='probe', call_input={})
        self.assertIn(payload, resumed['content'])
        ref = resumed['result_ref']
        self.assertEqual(self.manager.read_result(self.ctx.run_id, ref['result_id'])['text'], payload)

    def test_large_host_result_middle_is_durable_and_pageable(self):
        payload = 'a' * 18000 + 'EXACT-MIDDLE' + 'z' * 18000
        with run_context.bind_run(self.ctx):
            text = agent._clip_host_text(payload)
        self.assertEqual(text, payload)
        ref = self.manager.store_result(self.ctx.run_id, text, name='host',
                                        inline_chars=context_policy.inline_chars())
        self.assertTrue(ref['inline']['truncated'])
        self.assertNotIn('EXACT-MIDDLE', agent._result_for_model(ref))
        page = self.manager.read_result(self.ctx.run_id, ref['result_id'], byte_offset=17990, byte_limit=100)
        self.assertIn('EXACT-MIDDLE', page['text'])
        with mock.patch.object(agent, 'HOST_TEXT_CAP', 16000):
            self.assertIn('вырезано', agent._clip_host_text(payload))

    def test_compact_pages_expand_without_losing_unicode_or_advancing_past_hidden_text(self):
        from test_memory_compact_paging_2310 import META, COMPACT, ROOM, unpack
        source = ('Я🐺漢字e\u0301' * 9000) + '\n  END\t '
        offset, pinned, pieces = 0, '', []
        with mock.patch.object(agent.memory_life, 'read_compact', return_value={
                'ok': True, 'meta': dict(META), 'text': source}):
            for index in range(20):
                response = agent.tool_memory_compact('read', place=ROOM, compact_id=COMPACT,
                    start_char=offset, expected_sha256=pinned)
                self.assertLessEqual(len(response), 16000)
                ref = self.manager.store_result(self.ctx.run_id, response, name='compact-page',
                                                inline_chars=context_policy.inline_chars())
                self.assertFalse(ref['inline']['truncated'])
                page, body = unpack(response)
                self.assertEqual(page['start_char'], offset)
                self.assertEqual(page['end_char'], offset + len(body))
                if index == 0:
                    self.assertGreater(len(body), 12000)
                pieces.append(body); pinned = page['sha256']
                if page['eof']:
                    break
                offset = page['next_start_char']
        self.assertEqual(''.join(pieces), source)


if __name__ == '__main__':
    unittest.main()
