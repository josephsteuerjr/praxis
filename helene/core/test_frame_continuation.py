"""Inherited FIRST scenarios at the edition's actual bind_source/dispatch API.

No server-only selection/kind kwargs, private profile or provider is used. The
old silent-turn reset and all-DM-decline oracles are explicitly replaced: newly
admitted raw input may extend FIRST without inventing an accepted assistant.
"""
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
import frame_continuation as continuation
import frame_layout
import llm


def row(mid, own, text):
    return dict(id='event:' + str(mid), source_id=str(mid), actor='agent' if own else 'person',
                direction='out' if own else 'in', line=text, ts=mid, meta={})


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {
            'PRAXIS_FRAME_CONTINUATION': 'on', 'PRAXIS_KEAT': 'off',
            'PRAXIS_KEAT_CAPTURE': 'off', 'PRAXIS_FRAME_SHADOW': 'off',
            'PRAXIS_FRAME_SERVE': 'off', 'PRAXIS_FRAME_MEASURE': 'off',
            'PRAXIS_FRAME_TAPE_HANDS': 'on'}))
        self.ctx = agent.ChannelContext(chat_id='room', room_id='root', is_dm=False,
                                        principal_id='101')
        self.selection = dict(chat='room', source='synthetic-installation',
            generation=dict(n=1, boundary='source-boundary', scope={'peer_id': 'root'},
                            flip_id=None, summary_policy={'max_chars': 7000, 'sha256': 'summary'}),
            admission={'owner': 'installation-1'}, cache_root=self.tmp.name,
            rows=[row(1, False, 'earlier input'), row(2, True, 'earlier reply'),
                  row(3, False, 'first question'), row(4, False, 'second line')])
        self.live = copy.deepcopy(self.selection)
        self.system = 'synthetic constitution and transport'
        self.tools = [{'name': 'probe', 'input_schema': {'type': 'object'}}]
        frame_layout.begin()

    def prepare(self, selection=None, evidence='fresh evidence', **changes):
        selected = copy.deepcopy(selection or self.selection)
        self.live = copy.deepcopy(selected)
        history, current = continuation.dialogue(selected['rows'])
        prefix = [dict(role='user', content='frozen epoch E')]
        messages = prefix + frame_layout.tape(history, hands=True) + [
            dict(role='user', content='<evidence>' + evidence + '</evidence>\n' + current)]
        kwargs = dict(ctx=self.ctx, history=history, user_msg=current,
            messages=messages, system=self.system, tools=self.tools,
            model_key='test-model-profile', prefix_messages=prefix, hands=True,
            render=frame_layout.tape,
            render_current=lambda text: dict(role='user', content='<evidence>' + evidence + '</evidence>\n' + text))
        kwargs.update(changes)
        with continuation.bind_source(selected, lambda: copy.deepcopy(self.live)):
            return continuation.prepare(**kwargs)

    def commit(self, plan, selected=None, **changes):
        if selected is not None:
            self.live = copy.deepcopy(selected)
        args = dict(system=self.system, messages=plan.messages, tools=self.tools)
        args.update(changes)
        with continuation.bind(plan):
            continuation.dispatch(**args)
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
        self.assertIn('first question\nsecond line', original[-1]['content'])
        self.assertNotIn('first question\n\nsecond line', str(second.messages))
        one, two = llm.messages_to_openai(original), llm.messages_to_openai(second.messages)
        self.assertEqual(two[:len(one)], one)

    def test_extra_messages_before_delivered_reply_are_appended(self):
        first = self.prepare(); self.commit(first)
        selected = self.next_selection()
        selected['rows'].insert(4, row(45, False, 'arrived while working'))
        second = self.prepare(selected)
        self.assertEqual(second.messages[:len(first.messages)], first.messages)
        self.assertIn('arrived while working', str(second.messages[first.reused:]))
        self.assertIn('actually delivered', str(second.messages))

    def test_silent_or_failed_turn_cannot_invent_delivered_reply(self):
        first = self.prepare(); self.commit(first)
        selected = copy.deepcopy(self.selection)
        selected['rows'].append(row(5, False, 'more pending input'))
        second = self.prepare(selected)
        self.assertGreater(second.reused, 0)
        self.assertEqual(second.messages[:len(first.messages)], first.messages)
        self.assertIn('more pending input', str(second.messages))
        self.assertNotIn('actually delivered', str(second.messages))
        self.assertFalse(any(m['role'] == 'assistant' for m in second.messages[second.reused:]))

    def test_source_edit_delete_reorder_and_same_text_new_identity_reset(self):
        self.commit(self.prepare())
        for variant in ('edit', 'delete', 'reorder', 'identity'):
            with self.subTest(variant=variant):
                selected = self.next_selection()
                if variant == 'edit': selected['rows'][0]['line'] = 'edited'
                if variant == 'delete': selected['rows'].pop(0)
                if variant == 'reorder': selected['rows'][0], selected['rows'][1] = selected['rows'][1], selected['rows'][0]
                if variant == 'identity': selected['rows'][3]['source_id'] = 'different original'
                plan = self.prepare(selected)
                self.assertEqual(plan.reused, 0)
                self.assertEqual(plan.reason, 'source_edited_deleted_or_reordered')

    def test_fold_flip_summary_scope_and_source_reset(self):
        self.commit(self.prepare())
        changes = {'n': 2, 'boundary': 'new fold', 'flip_id': 'manual-flip',
                   'summary_policy': {'max_chars': 8000, 'sha256': 'new summary'},
                   'scope': {'peer_id': 'root', 'topic_id': 4}}
        for key, value in changes.items():
            with self.subTest(key=key):
                selected = self.next_selection(); selected['generation'][key] = value
                self.assertEqual(self.prepare(selected).reason, 'source_generation_changed')
        selected = self.next_selection(); selected['source'] = 'another-installation'
        self.assertEqual(self.prepare(selected).reason, 'source_generation_changed')

    def test_system_tools_profile_hands_and_render_changes_reset(self):
        self.commit(self.prepare())
        for key, value in [('system', 'changed constitution'), ('tools', []),
                           ('model_key', 'other account/profile'), ('hands', False)]:
            with self.subTest(key=key):
                self.assertEqual(self.prepare(self.next_selection(), **{key: value}).reused, 0)
        with mock.patch.dict(os.environ, {'PRAXIS_FRAME_TAPE_HANDS': 'off'}):
            self.assertEqual(self.prepare(self.next_selection()).reused, 0)

    def test_epoch_document_change_cannot_reuse_old_frozen_payload(self):
        self.commit(self.prepare())
        prefix = [dict(role='user', content='changed epoch E')]
        self.assertEqual(self.prepare(self.next_selection(), prefix_messages=prefix).reused, 0)

    def test_room_topic_and_privacy_boundary_are_distinct(self):
        self.commit(self.prepare())
        self.assertIsNone(self.prepare(self.next_selection(), ctx=agent.replace(self.ctx, chat_id='other-room')))
        for change in ({'room_id': 'different-root'}, {'owner': True}, {'known': False}, {'principal_id': '202'}):
            with self.subTest(change=change):
                self.assertEqual(self.prepare(self.next_selection(), ctx=agent.replace(self.ctx, **change)).reused, 0)
        selected = self.next_selection(); selected['admission']['owner'] = 'new-owner'
        self.assertEqual(self.prepare(selected).reused, 0)

    def test_dm_supported_but_unbound_worker_keat_media_unproved_decline(self):
        self.assertIsNotNone(self.prepare(ctx=agent.replace(self.ctx, is_dm=True)))
        with continuation.bind_source(None, None):
            self.assertIsNone(continuation.prepare(ctx=self.ctx, history=[], user_msg='worker',
                messages=[], system='', tools=[], model_key='', prefix_messages=[],
                hands=False, render=frame_layout.tape))
        for case in (dict(capture_state=object()), dict(history=[]),
                     dict(user_msg='different input'), dict(user_msg=[{'type': 'image'}])):
            with self.subTest(case=tuple(case)):
                self.assertIsNone(self.prepare(**case))
        for key, value in [('PRAXIS_KEAT', 'serve'), ('PRAXIS_KEAT_CAPTURE', 'on')]:
            with mock.patch.dict(os.environ, {key: value}):
                self.assertIsNone(self.prepare())

    def test_default_on_explicit_off_does_not_read_or_write_cache(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('PRAXIS_FRAME_CONTINUATION',None)
            self.assertIsNotNone(self.prepare())
        with mock.patch.dict(os.environ, {'PRAXIS_FRAME_CONTINUATION': 'off'}), \
                mock.patch.object(continuation, '_load', side_effect=AssertionError('read')):
            self.assertIsNone(self.prepare())
        self.assertFalse(list(Path(self.tmp.name).glob('*.json')))

    def test_corrupt_cache_falls_back_and_store_failures_cannot_stop_model(self):
        first = self.prepare(); self.commit(first)
        first.path.write_text('{truncated', encoding='utf-8')
        self.assertEqual(self.prepare(self.next_selection()).reused, 0)
        first = self.prepare()
        with mock.patch.object(continuation.os, 'replace', side_effect=OSError('private contents')):
            self.commit(first)
        self.assertEqual(list(first.path.parent.glob('*.tmp')), [])

    def test_substituted_request_critical_material_and_mutation_not_persisted(self):
        for mode in ('substituted', 'mutated', 'critical', 'system'):
            with self.subTest(mode=mode):
                plan = self.prepare()
                if mode == 'substituted': self.commit(plan, messages=copy.deepcopy(plan.messages))
                if mode == 'mutated':
                    plan.messages[-1]['content'] += 'unissued'; self.commit(plan)
                if mode == 'critical': self.commit(plan, critical=True)
                if mode == 'system': self.commit(plan, system='substituted')
                self.assertFalse(plan.path.exists())

    def test_restart_reopens_projection_and_private_file_mode(self):
        first = self.prepare(); self.commit(first)
        self.assertEqual(json.loads(first.path.read_text(encoding='utf-8'))['messages'], first.messages)
        self.assertGreater(self.prepare(self.next_selection()).reused, 0)
        if os.name != 'nt':
            self.assertEqual(first.path.stat().st_mode & 0o777, 0o600)
        # Windows has no POSIX owner mode; exact cache/checksum remains exercised.
        self.assertIsNotNone(continuation._load(first.path))

    def test_old_wake_cannot_rollback_new_checkpoint_or_cross_new_fold(self):
        old = self.prepare(); self.commit(old)
        selected = self.next_selection()
        new = self.prepare(selected); self.commit(new, selected)
        saved = new.path.read_bytes()
        stale = self.prepare()
        self.commit(stale, selected)
        self.assertEqual(new.path.read_bytes(), saved)
        stale = self.prepare(selected)
        folded = copy.deepcopy(selected); folded['generation']['n'] += 1
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
        plan = self.prepare(); original = copy.deepcopy(plan.messages)
        responses = [types.SimpleNamespace(stop_reason='tool_use', text='', blocks=[
            dict(type='tool_use', id='probe-1', name='probe', input={})]),
            types.SimpleNamespace(stop_reason='end_turn', text='unaccepted draft', blocks=[])]
        with continuation.bind(plan), mock.patch.object(agent.llm, 'chat', side_effect=responses) as chat, \
                mock.patch.dict(agent.TOOL_IMPL, {'probe': lambda: 'private tool trace'}):
            agent._terminal_tool_loop(system=self.system, messages=plan.messages, tools=self.tools)
        self.assertEqual(chat.call_count, 2)
        self.assertIn('private tool trace', str(plan.messages))
        self.assertEqual(json.loads(plan.path.read_text(encoding='utf-8'))['messages'], original)
        following = self.prepare(self.next_selection())
        self.assertNotIn('private tool trace', str(following.messages))
        self.assertNotIn('unaccepted draft', str(following.messages))
        self.assertIn('actually delivered', str(following.messages))

    def test_failed_provider_does_not_seed_checkpoint(self):
        plan = self.prepare()
        with continuation.bind(plan), mock.patch.object(agent.llm, 'chat', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): agent._model_call(self.system, plan.messages, self.tools)
        self.assertFalse(plan.path.exists())

    def test_voice_builder_binds_projection_at_real_model_boundary(self):
        observed = []
        response = types.SimpleNamespace(stop_reason='end_turn', text='draft', blocks=[],
                                         framework=llm._config()['roles']['voice']['framework'], model='offline')
        def loop(**kwargs):
            observed.append(copy.deepcopy(kwargs['messages']))
            return agent._model_call(kwargs['system'], kwargs['messages'], kwargs['tools']).text
        with mock.patch.object(agent, '_build_prompt_parts', return_value=('persona', '', 'live evidence')), \
                mock.patch.object(agent, '_system', return_value=self.system), \
                mock.patch.object(agent, '_terminal_tool_loop', side_effect=loop), \
                mock.patch.object(agent, '_arm_private_floor', return_value=0), \
                mock.patch.object(agent.llm, 'chat', return_value=response), \
                mock.patch.object(agent.llm, '_resolve_model', return_value='offline'):
            for selected in (self.selection, self.next_selection()):
                history, current = continuation.dialogue(selected['rows'])
                with continuation.bind_source(selected, lambda: copy.deepcopy(selected)):
                    agent._voice_impl(current, history, 'speaker', ctx=self.ctx, tools_override=self.tools)
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[1][:len(observed[0])], observed[0])


if __name__ == '__main__': unittest.main()
