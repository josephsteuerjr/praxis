from __future__ import annotations

import copy
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import agent
import dialogue_projection as projection
import frame_continuation as continuation
import frame_epoch
import frame_layout
import llm


def row(mid, own, text):
    return dict(self=own, line=('Praxis: ' if own else '') + text,
                role_line=text, logical_send=None,
                source=dict(id='event:' + str(mid), source_id=str(mid), chat='room'))


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(frame_epoch, 'STORE', Path(self.tmp.name) / 'epoch')
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {
            'PRAXIS_FRAME_CONTINUATION': 'on', 'PRAXIS_FRAME_EPOCH': '1',
            'PRAXIS_FRAME_EPOCH_ROOMS': '*', 'PRAXIS_FRAME_TAPE_HANDS': 'on',
            'PRAXIS_KEAT': 'off', 'PRAXIS_KEAT_CAPTURE': 'off', 'PRAXIS_FRAME_V6': 'off',
        })
        patcher.start(); self.addCleanup(patcher.stop)
        self.ctx = agent.ChannelContext(chat_id='room', room_id='root', is_dm=False,
                                        principal_id='101')
        self.selection = dict(chat='room', n=1, boundary='source-boundary',
                              scope=dict(peer_id='root', topic_id=None), flip_id=None,
                              summary_policy=dict(max_chars=7000, sha256='summary'),
                              complete=True, capacity=dict(within=True),
                              rows=[row(1, False, 'earlier input'), row(2, True, 'earlier reply'),
                                    row(3, False, 'first question'), row(4, False, 'second line')])
        self.system = 'fresh constitution and transport'
        self.tools = [{'name': 'probe', 'input_schema': {'type': 'object'}}]

    def prepare(self, selection=None, evidence='fresh evidence', **changes):
        selected = selection or self.selection
        history, current = projection.epoch_dialogue(selected['rows'])
        messages = ([dict(role='user', content='frozen epoch E')]
                    + frame_layout.tape(history)
                    + [dict(role='user', content='<evidence>' + evidence + '</evidence>\n' + current)])
        kwargs = dict(ctx=self.ctx, selection=selected, history=history, user_msg=current,
                      messages=messages, system=self.system, tools=self.tools,
                      kind='chat_turn', model_key='test-model-profile', prefix_messages=messages[:1])
        kwargs.update(changes)
        return continuation.prepare(**kwargs)

    def commit(self, plan, selected=None, **changes):
        args = dict(system=self.system, messages=plan.messages, tools=self.tools)
        args.update(changes)
        with mock.patch.object(frame_epoch, 'load', return_value=dict(tape=selected or self.selection)), \
                continuation.bind(plan):
            continuation.observe(**args)

    def next_selection(self):
        selected = copy.deepcopy(self.selection)
        selected['rows'] += [row(5, True, 'actually delivered'), row(6, False, 'next question')]
        return selected

    def test_next_turn_extends_exact_sent_input_not_reconstructed_history(self):
        first = self.prepare(evidence='old changing evidence')
        original = copy.deepcopy(first.messages)
        self.commit(first)
        second = self.prepare(self.next_selection(), evidence='new changing evidence')
        self.assertGreater(second.reused, 0)
        self.assertEqual(second.messages[:len(original)], original)
        self.assertIn('new changing evidence', second.messages[-1]['content'])
        # The ingress renderer changes two pending lines from LF to LF+LF in
        # historical roles. Source coordinates still prove the original input.
        self.assertIn('first question\nsecond line', original[-1]['content'])
        self.assertNotIn('first question\n\nsecond line', str(second.messages))
        wire1, wire2 = llm.messages_to_openai(original), llm.messages_to_openai(second.messages)
        self.assertEqual(wire2[:len(wire1)], wire1)

    def test_extra_messages_before_delivered_reply_are_appended(self):
        first = self.prepare(); self.commit(first)
        selected = self.next_selection()
        selected['rows'].insert(4, row(45, False, 'arrived while she was working'))
        second = self.prepare(selected)
        self.assertEqual(second.messages[:len(first.messages)], first.messages)
        self.assertIn('arrived while she was working', str(second.messages[first.reused:]))
        self.assertIn('actually delivered', str(second.messages))

    def test_silent_or_failed_turn_cannot_invent_delivered_reply(self):
        first = self.prepare(); self.commit(first)
        selected = copy.deepcopy(self.selection)
        selected['rows'].append(row(5, False, 'more pending input'))
        second = self.prepare(selected)
        self.assertEqual(second.reused, 0)
        self.assertIn('more pending input', str(second.messages))

    def test_source_edit_delete_reorder_and_same_text_new_identity_reset(self):
        self.commit(self.prepare())
        for variant in ('edit', 'delete', 'reorder', 'identity'):
            with self.subTest(variant=variant):
                selected = self.next_selection()
                if variant == 'edit': selected['rows'][0]['role_line'] = 'edited'
                if variant == 'delete': selected['rows'].pop(0)
                if variant == 'reorder': selected['rows'][0], selected['rows'][1] = selected['rows'][1], selected['rows'][0]
                if variant == 'identity': selected['rows'][3]['source']['id'] = 'different original'
                plan = self.prepare(selected)
                self.assertIsNotNone(plan)
                self.assertEqual(plan.reused, 0)

    def test_fold_flip_summary_and_scope_reset(self):
        self.commit(self.prepare())
        changes = {'n': 2, 'boundary': 'new fold', 'flip_id': 'manual-flip',
                   'summary_policy': {'max_chars': 8000, 'sha256': 'new summary'},
                   'scope': {'peer_id': 'root', 'topic_id': 4}}
        for key, value in changes.items():
            with self.subTest(key=key):
                selected = self.next_selection(); selected[key] = value
                self.assertEqual(self.prepare(selected).reused, 0)

    def test_system_tools_profile_and_render_changes_reset(self):
        self.commit(self.prepare())
        for key, value in [('system', 'changed constitution'), ('tools', []),
                           ('model_key', 'other account/profile')]:
            with self.subTest(key=key):
                self.assertEqual(self.prepare(self.next_selection(), **{key: value}).reused, 0)
        with mock.patch.dict(os.environ, {'PRAXIS_FRAME_TAPE_HANDS': 'off'}):
            self.assertEqual(self.prepare(self.next_selection()).reused, 0)

    def test_epoch_document_change_cannot_reuse_old_frozen_payload(self):
        self.commit(self.prepare())
        messages = self.prepare(self.next_selection()).messages
        changed_prefix = [dict(role='user', content='changed epoch E')]
        changed_messages = changed_prefix + messages[1:]
        plan = self.prepare(self.next_selection(), prefix_messages=changed_prefix,
                            messages=changed_messages)
        self.assertEqual(plan.reused, 0)

    def test_room_topic_and_privacy_boundary_are_distinct(self):
        self.commit(self.prepare())
        ctx = agent.replace(self.ctx, chat_id='other-room')
        self.assertIsNone(self.prepare(self.next_selection(), ctx=ctx))
        for change in ({'room_id': 'different-root'}, {'owner': True}, {'known': False}):
            ctx = agent.replace(self.ctx, **change)
            self.assertEqual(self.prepare(self.next_selection(), ctx=ctx).reused, 0)

    def test_workers_dm_keat_media_and_unproved_projection_retain_legacy(self):
        cases = [dict(kind='task_window'), dict(kind='coding_window'), dict(kind='wake'),
                 dict(ctx=agent.replace(self.ctx, is_dm=True)), dict(capture_state=object()),
                 dict(history=[]), dict(user_msg='different input'), dict(user_msg=[{'type': 'image'}])]
        for case in cases:
            with self.subTest(case=tuple(case)):
                self.assertIsNone(self.prepare(**case))
        for key, value in [('PRAXIS_KEAT', 'serve'), ('PRAXIS_KEAT_CAPTURE', 'on')]:
            with mock.patch.dict(os.environ, {key: value}):
                self.assertIsNone(self.prepare())
        with mock.patch.object(continuation.frame_serve, 'enabled', return_value=True):
            self.assertIsNone(self.prepare())

    def test_default_off_does_not_read_or_write_cache(self):
        with mock.patch.dict(os.environ, {'PRAXIS_FRAME_CONTINUATION': 'off'}), \
                mock.patch.object(continuation, '_load', side_effect=AssertionError('read')):
            self.assertIsNone(self.prepare())
        self.assertFalse(continuation._path('room').exists())

    def test_corrupt_cache_falls_back_and_store_failures_cannot_stop_model(self):
        first = self.prepare(); self.commit(first)
        first.path.write_text('{truncated', encoding='utf-8')
        self.assertEqual(self.prepare(self.next_selection()).reused, 0)
        first = self.prepare()
        with mock.patch('frame_continuation.os.replace', side_effect=OSError('private contents')):
            self.commit(first)
        self.assertEqual(list(first.path.parent.glob('*.tmp')), [])

    def test_substituted_request_critical_material_and_mutation_not_persisted(self):
        for mode in ('substituted', 'mutated', 'critical', 'system'):
            with self.subTest(mode=mode):
                plan = self.prepare()
                if mode == 'substituted': self.commit(plan, messages=copy.deepcopy(plan.messages))
                if mode == 'mutated':
                    plan.messages[-1]['content'] += 'unissued'
                    self.commit(plan)
                if mode == 'critical': self.commit(plan, critical=True)
                if mode == 'system': self.commit(plan, system='substituted')
                self.assertFalse(plan.path.exists())

    def test_restart_reopens_projection_and_private_file_mode(self):
        first = self.prepare(); self.commit(first)
        saved = json.loads(first.path.read_text())
        self.assertEqual(saved['messages'], first.messages)
        self.assertGreater(self.prepare(self.next_selection()).reused, 0)
        self.assertEqual(first.path.stat().st_mode & 0o777, 0o600)

    def test_old_wake_cannot_rollback_new_checkpoint_or_cross_new_fold(self):
        old = self.prepare(); self.commit(old)
        next_selected = self.next_selection()
        new = self.prepare(next_selected); self.commit(new, next_selected)
        saved = new.path.read_bytes()
        old = self.prepare(); self.commit(old, next_selected)
        self.assertEqual(new.path.read_bytes(), saved)
        stale = self.prepare(next_selected)
        folded = copy.deepcopy(next_selected); folded['n'] += 1
        self.commit(stale, folded)
        self.assertEqual(new.path.read_bytes(), saved)

    def test_nested_background_binding_masks_outer_chat(self):
        plan = self.prepare()
        with continuation.bind(plan):
            self.assertIsNotNone(continuation.receipt())
            with continuation.bind(None):
                self.assertIsNone(continuation.receipt())
                continuation.observe(system=self.system, messages=plan.messages, tools=self.tools)
            self.assertFalse(plan.observed)
        self.assertIsNone(continuation.receipt())

    def test_real_model_boundary_keeps_first_input_outside_private_tool_trace(self):
        plan = self.prepare()
        first_input = copy.deepcopy(plan.messages)
        responses = [
            types.SimpleNamespace(stop_reason='tool_use', text='', blocks=[
                dict(type='tool_use', id='probe-1', name='probe', input={})]),
            types.SimpleNamespace(stop_reason='end_turn', text='unaccepted draft', blocks=[]),
        ]
        with continuation.bind(plan), \
                mock.patch.object(frame_epoch, 'load', return_value=dict(tape=self.selection)), \
                mock.patch.object(agent.llm, 'chat', side_effect=responses) as chat, \
                mock.patch.dict(agent.TOOL_IMPL, {'probe': lambda: 'private tool trace'}):
            agent._terminal_tool_loop(system=self.system, messages=plan.messages, tools=self.tools)
        self.assertEqual(chat.call_count, 2)
        self.assertIn('private tool trace', str(plan.messages))
        saved = json.loads(plan.path.read_text())
        self.assertEqual(saved['messages'], first_input)
        next_plan = self.prepare(self.next_selection())
        self.assertNotIn('private tool trace', str(next_plan.messages))
        self.assertNotIn('unaccepted draft', str(next_plan.messages))
        self.assertIn('actually delivered', str(next_plan.messages))

    def test_failed_provider_does_not_seed_checkpoint(self):
        plan = self.prepare()
        with continuation.bind(plan), mock.patch.object(agent.llm, 'chat', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):
                agent._model_call(self.system, plan.messages, self.tools)
        self.assertFalse(plan.path.exists())

    def test_voice_builder_binds_projection_at_real_model_boundary(self):
        selected = self.selection
        history, current = projection.epoch_dialogue(selected['rows'])
        observed = []
        response = types.SimpleNamespace(stop_reason='end_turn', text='draft', blocks=[])
        def loop(**kwargs):
            observed.append(copy.deepcopy(kwargs['messages']))
            return agent._model_call(kwargs['system'], kwargs['messages'], kwargs['tools']).text
        with mock.patch.object(agent, '_build_prompt_parts', return_value=('persona', '', 'live evidence')), \
                mock.patch.object(agent, '_system', return_value=self.system), \
                mock.patch.object(agent, '_current_run_kind', return_value='chat_turn'), \
                mock.patch.object(agent, 'offered_tools_for', return_value=self.tools), \
                mock.patch.object(agent, '_terminal_tool_loop', side_effect=loop), \
                mock.patch.object(agent.llm, 'chat', return_value=response), \
                mock.patch.object(frame_epoch, 'taken', return_value=('frozen epoch E', {})), \
                mock.patch.object(frame_epoch, 'load', side_effect=lambda _: dict(tape=selected)):
            with frame_epoch.bind('room', 1, tape=selected):
                agent._voice_impl(current, history, 'speaker', ctx=self.ctx)
            selected = self.next_selection()
            history, current = projection.epoch_dialogue(selected['rows'])
            with frame_epoch.bind('room', 1, tape=selected):
                agent._voice_impl(current, history, 'speaker', ctx=self.ctx)
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[1][:len(observed[0])], observed[0])


if __name__ == '__main__':
    unittest.main()
