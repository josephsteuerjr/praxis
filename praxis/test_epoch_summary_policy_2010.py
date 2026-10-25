"""ROOM recap policy survives coherent epoch selection and the model boundary.

Run through praxis_test.py; no model, Telegram transport or live base is used.
"""
from __future__ import annotations

import copy
import hashlib
import os
import unittest
from unittest import mock

import agent
import frame_epoch as fe
import frame_layout
import group_context as gc
import memory_life as ml
import mtproto_runner as runner
from test_coverage_vs_current import Base, ROOM


class EpochSummaryPolicy(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ, {
            'PRAXIS_FRAME_EPOCH': '1', 'PRAXIS_FRAME_EPOCH_ROOMS': '*',
            'PRAXIS_FRAME_HEAD_STABLE': '1', 'PRAXIS_FRAME_EPOCH_MAX_CHARS': '400000',
            'PRAXIS_FRAME_TAPE_HANDS': 'off', 'PRAXIS_KEAT_CAPTURE': 'off',
            'PRAXIS_DIALOGUE_ROLES': '1'}))
        for module, key, value in ((fe, 'STORE', self.tmp / 'epochs'),
                                   (gc, 'GROUPS_DIR', self.tmp / 'groups'),
                                   (gc, '_KEY_CACHE', {})):
            self.enterContext(mock.patch.object(module, key, value))
        self.enterContext(mock.patch.object(agent.rooms, 'room_policy', return_value={
            'context_summary_chars': 4200}))
        self.enterContext(mock.patch.object(agent, 'dossier_contract_enabled', return_value=True))
        self.enterContext(mock.patch.object(agent, 'SUMMARY_FRAME_CHARS', 2200))
        for name in ('build_state_block', 'build_state_evidence_block', '_recall_block'):
            self.enterContext(mock.patch.object(agent, name, return_value=''))
        self.addCleanup(fe.reset)
        self.addCleanup(frame_layout.reset)

    def frontier(self):
        # All four recaps are valid independent frontier nodes, not accidental
        # duplicates. Old large leaves must stay on disk, not flood ROOM's frame.
        rows = [self._msg(i, 'event %s' % i) for i in range(1, 6)]
        specs = ((rows[:2], 'WIDEST ' + 'w' * 450),
                 ([rows[2]], 'OLD_A ' + 'a' * 400000),
                 ([rows[3]], 'OLD_B ' + 'b' * 400000),
                 ([rows[4]], 'RECENT ' + 'r' * 500))
        compacts = [self._compact(source, summary=text) for source, text in specs]
        self._msg(6, 'exact uncovered tail')
        return compacts

    def freeze(self):
        route = runner.telegram_topics.route_from_conversation_id(ROOM)
        with mock.patch.object(runner, '_route_from_state', return_value=route), \
             mock.patch.object(runner, '_room_policy_for_state', return_value={'context_hot': 1}):
            return runner._group_trigger_snapshot(ROOM, mid=6, message_ts=6,
                kind='mention', addressed=True, query='exact uncovered tail', name='Alice',
                sender_id=2, is_owner=False, known=True, family=False)

    def test_oversized_frontier_uses_read_summary_policy_and_preserves_raw_sources(self):
        compacts = self.frontier()
        paths = [ml._compact_dir(ROOM) / (m['id'] + '.md') for m in compacts]
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        expected = agent.read_summary(ROOM)
        self.assertEqual(agent._summary_context_budget(ROOM), 2200)
        self.assertIn('WIDEST ', expected)
        self.assertIn('RECENT ', expected)
        self.assertNotIn('OLD_A ', expected)
        self.assertNotIn('OLD_B ', expected)
        self.assertIn('2 компактов из 4', expected)
        self.assertIn('есть на диске и достаются recall', expected)
        with mock.patch.object(ml, '_both_graphs', wraps=ml._both_graphs) as graph, \
             mock.patch.object(ml, '_canonical_compact_graph',
                               side_effect=AssertionError('split graph reread')):
            selected = ml.epoch_snapshot(ROOM, summary_max_chars=2200)
        self.assertEqual(graph.call_count, 1)
        self.assertEqual(selected['summary'], expected)
        self.assertEqual([row['line'] for row in selected['hot']],
                         ['Николай: exact uncovered tail'])
        wake = self.freeze()
        self.assertEqual(wake.epoch_snapshot['summary'], expected)
        self.assertTrue(wake.epoch_snapshot['capacity']['within'])
        self.assertLess(wake.epoch_snapshot['capacity']['chars'], 400000)
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in paths], hashes)
        # A larger explicit budget can still select the retained old recaps.
        full = ml.context_summary(ROOM, max_chars=900000)
        self.assertIn('OLD_A ', full)
        self.assertIn('OLD_B ', full)

    def test_one_oversized_widest_keeps_existing_explicit_clipping_notice(self):
        first = self._msg(1, 'first')
        self._compact([first], summary='WIDEST ' + 'x' * 12000)
        expected = ml.context_summary(ROOM, max_chars=1000)
        self.assertIn('КОМПАКТ ОБРЕЗАН', expected)
        selected = ml.epoch_snapshot(ROOM, summary_max_chars=1000)
        self.assertEqual(selected['summary'], expected)
        self.assertEqual(len(selected['covered_event_ids']), 1)
        self.assertEqual(selected['hot'], [])

    def test_budget_or_selected_text_change_invalidates_E_not_coverage_boundary(self):
        self.frontier()
        first = self.freeze().epoch_snapshot
        with mock.patch.object(agent, 'SUMMARY_FRAME_CHARS', 1200):
            second = self.freeze().epoch_snapshot
        self.assertEqual(first['boundary'], second['boundary'])
        self.assertEqual(first['rows'], second['rows'])
        self.assertEqual(second['n'], first['n'] + 1)
        self.assertEqual(second['summary_policy']['max_chars'], 1200)
        self.assertNotEqual(first['summary'], second['summary'])
        self.assertNotEqual(first['envelope'], second['envelope'])
        # Even if coverage remains identical, a changed presentable projection
        # cannot reuse an E from an earlier selected summary.
        original = ml.render_context_summary
        with mock.patch.object(agent, 'SUMMARY_FRAME_CHARS', 1200), \
             mock.patch.object(ml, 'render_context_summary',
                               side_effect=lambda *a, **kw: original(*a, **kw) + '\nNEW NOTICE'):
            third = self.freeze().epoch_snapshot
        self.assertEqual(third['boundary'], first['boundary'])
        self.assertEqual(third['n'], second['n'] + 1)
        self.assertIn('NEW NOTICE', third['summary'])

    def test_budget_change_rotates_generation_even_if_selected_text_is_identical(self):
        first = self._msg(1, 'first')
        self._compact([first], summary='small recap')
        self._msg(6, 'exact uncovered tail')
        before = self.freeze().epoch_snapshot
        with mock.patch.object(agent, 'SUMMARY_FRAME_CHARS', 2100):
            after = self.freeze().epoch_snapshot
        self.assertEqual(before['summary'], after['summary'])
        self.assertEqual(before['boundary'], after['boundary'])
        self.assertEqual(after['n'], before['n'] + 1)

    def test_delayed_frozen_summary_reaches_actual_model_boundary_without_reread(self):
        self.frontier()
        wake = self.freeze()
        frozen = copy.deepcopy(wake.epoch_snapshot)
        with mock.patch.object(agent, 'SUMMARY_FRAME_CHARS', 1200):
            self.freeze()
        saved = copy.deepcopy(fe.load(ROOM))
        history, current = runner._group_dialogue(wake.turns_snapshot, exact=True)
        # This fixture has no earlier own turn: runner uses its frozen flat
        # context in that case, rather than dispatching an empty user message.
        if not current:
            current = wake.context_snapshot
        captured = {}
        with fe.bind(ROOM, frozen['anchor'], tape=frozen), \
             mock.patch.object(agent, 'read_summary', side_effect=AssertionError('later summary')), \
             mock.patch.object(ml, 'context_summary', side_effect=AssertionError('later graph')), \
             mock.patch.object(agent, 'SUMMARY_FRAME_CHARS', 100), \
             mock.patch.object(agent, '_terminal_tool_loop',
                               side_effect=lambda **kw: captured.update(kw) or 'ok'):
            result = agent._voice_impl(current, history, 'Alice', tools_override=[],
                ctx=agent.ChannelContext(chat_id=ROOM, room_id=ROOM, is_dm=False,
                                         principal_id=2, owner=False))
        self.assertEqual(result, 'ok')
        # The document renderer quotes each line; this is presentation, not a
        # second summary cut. Compare the whole quoted projection, not a marker.
        self.assertIn(frame_layout.quote(frozen['summary']), captured['messages'][0]['content'])
        self.assertIn('есть на диске и достаются recall', captured['messages'][0]['content'])
        self.assertNotIn('OLD_A ', captured['messages'][0]['content'])
        self.assertIn('exact uncovered tail', str(captured['messages'][1:]))
        self.assertEqual(fe.load(ROOM), saved, 'delayed wake rewound newer room generation')
        self.assertEqual(wake.epoch_snapshot, frozen)

    def test_selected_forum_general_keeps_none_and_artifact_scope(self):
        from test_places import Base as PlaceFixture, FORUM
        from test_places import tr
        self.enterContext(mock.patch.object(tr, 'DIR', self.tmp / 'routes'))
        from types import SimpleNamespace
        fixture = SimpleNamespace(_msg=lambda *a, **kw: PlaceFixture._msg(self, *a, **kw))
        PlaceFixture._forum_room(fixture)
        tr.observe(FORUM, kind='topic_opener_seen', since_message_id=1,
                   until_message_id=9999)
        tr.observe_branches(FORUM, gc.branch_containers(FORUM))
        scope = tr.read_scope(FORUM, None)
        self.assertEqual(scope.members, {None, 100})
        for mid, topic, text in ((100, None, 'General root'),
                                 (102, 100, 'General artifact'),
                                 (6, 5, 'separate real topic')):
            gc.observe_message(peer_id=FORUM, topic_id=topic, message_id=mid,
                sender_id=1, sender_name='Alice', reply_to_message_id=None,
                timestamp=None, edited_at=None, text=text, topic_title='',
                media='', outgoing=False, origin='live')
        route = runner.telegram_topics.route_from_conversation_id(FORUM)
        with mock.patch.object(runner, '_route_from_state', return_value=route):
            selected = {}
            text, turns = runner._group_context_frozen(FORUM, {'context_hot': 1},
                                                       epoch_sink=selected)
        self.assertEqual(selected['scope']['members'], [None, 100])
        self.assertIn('General root', text)
        self.assertIn('General artifact', text)
        self.assertNotIn('separate real topic', text)
        self.assertTrue(turns)

    def test_effective_cap_respects_room_policy_and_optional_dossier_ceiling(self):
        self.assertEqual(agent._summary_context_budget(ROOM), 2200)
        with mock.patch.object(agent, 'dossier_contract_enabled', return_value=False):
            self.assertEqual(agent._summary_context_budget(ROOM), 4200)
        with mock.patch.object(agent, 'SUMMARY_FRAME_CHARS', 0):
            self.assertEqual(agent._summary_context_budget(ROOM), 4200)


if __name__ == '__main__':
    unittest.main()
