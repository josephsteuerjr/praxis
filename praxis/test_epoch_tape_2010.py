"""Source-complete epoch selections and runner -> agent model-boundary regressions.

No model, network or process-spawning tests. Run with praxis_test.py.
"""
from __future__ import annotations
import asyncio
import copy
import json
import os
import tempfile
import unittest
from collections import defaultdict, deque
from pathlib import Path
from unittest import mock

import agent
import frame_epoch as fe
import frame_layout
import group_context as gc
import memory_life as ml
import memory_provenance as mp
import media
import mtproto_runner as runner
import telegram_routes

ROOM = '-100999999999999999'


class EpochSources(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.stack = self.enterContext(mock.patch.dict(os.environ, {
            'PRAXIS_FRAME_EPOCH': '1', 'PRAXIS_FRAME_EPOCH_ROOMS': '*',
            'PRAXIS_FRAME_HEAD_STABLE': '1', 'PRAXIS_FRAME_EPOCH_MAX_CHARS': '0',
            'PRAXIS_FRAME_TAPE_HANDS': 'off'}))
        for module, key, value in ((fe, 'STORE', self.base / 'epochs'),
                                   (gc, 'GROUPS_DIR', self.base / 'groups'),
                                   (gc, '_KEY_CACHE', {})):
            self.enterContext(mock.patch.object(module, key, value))
        self.snapshot = dict(boundary='boundary-a', hot=[], events=[],
                             covered_event_ids=[], summary='selected summary')
        self.enterContext(mock.patch.object(ml, 'epoch_snapshot',
            side_effect=lambda _, **_kwargs: copy.deepcopy(self.snapshot)))
        self.addCleanup(fe.reset)
        self.addCleanup(frame_layout.reset)

    def add(self, mid, text, *, outgoing=False, edited_at=None, reply=None, media='', origin='live', topic=None):
        gc.observe_message(peer_id=ROOM, topic_id=topic, message_id=mid,
            sender_id=1 if outgoing else 2, sender_name='Praxis' if outgoing else 'Alice',
            reply_to_message_id=reply, timestamp='2026-10-20T00:00:00Z',
            text=text, media=media, outgoing=outgoing, edited_at=edited_at, origin=origin)

    def life(self, eid, sid, text, *, own=False, unprovable=False):
        event = dict(id=eid, source_id=sid, source='telegram', chat_id=ROOM,
                     kind='conversation_message', text=text,
                     actor='Praxis' if own else 'Alice', direction='out' if own else 'in',
                     meta={'is_dm': False})
        row = dict(event, line=text, chat=ROOM, unprovable=unprovable)
        self.snapshot['events'].append(event)
        self.snapshot['hot'].append(row)
        return event

    def prepare(self):
        return fe.prepare_tape(ROOM, peer_id=ROOM, whole_room=True)

    @staticmethod
    def text(selected):
        return '\n'.join(row['line'] for row in selected['rows'])

    def test_exact_coverage_ghost_residual_and_absent_hot(self):
        self.add(1, 'covered')
        self.add(2, 'archive-only', origin='backfill')
        event = self.life('e1', '1', 'Alice: covered')
        self.snapshot['hot'].clear()
        self.snapshot['covered_event_ids'] = [event['id']]
        self.life('ghost', '3', 'Praxis: exact trailing \u00a0', own=True, unprovable=True)
        selected = self.prepare()
        self.assertNotIn('Alice: covered', self.text(selected))
        self.assertNotIn('archive-only', self.text(selected))
        self.assertEqual(selected['prebaseline_archive'], 'retained_archival_evidence_not_claimed_covered')
        self.assertIn('archive-only', gc.archive_path(ROOM).read_text(encoding='utf-8'))
        self.assertIn('exact trailing \u00a0', [r['role_line'] for r in selected['rows']])
        self.snapshot['covered_event_ids'].clear()
        self.snapshot['boundary'] = 'new-coverage-boundary'
        self.assertNotIn('archive-only', self.text(self.prepare()))

    def test_scope_expansion_recovers_failed_life_receipts_without_narrow_leak(self):
        def narrow():
            return fe.prepare_tape(ROOM, peer_id=ROOM, topic_id=10)

        self.assertEqual(narrow()['rows'], [])
        self.add(200, 'other topic original', topic=20)
        self.add(200, 'other topic edited', topic=20,
                 edited_at='2026-10-20T00:01:00Z')
        self.add(201, 'other topic backfill', topic=20, origin='backfill')
        selected = narrow()
        self.assertEqual(selected['rows'], [])
        self.assertGreater(selected['cursor']['offset'], 0)
        # No life rows exist: the real archive is the only durable receipt.
        self.assertEqual(self.snapshot['hot'], [])
        fe.reset()
        gc._KEY_CACHE.clear()
        self.assertEqual(narrow()['rows'], [])
        expanded = self.prepare()
        self.assertEqual(len(expanded['rows']), 2)
        self.assertIn('other topic original', self.text(expanded))
        self.assertIn('other topic edited', self.text(expanded))
        self.assertNotIn('other topic backfill', self.text(expanded))
        self.assertEqual(self.prepare()['rows'], expanded['rows'])
        # A shrink must hide previously selected receipts, not erase them.
        self.assertEqual(narrow()['rows'], [])
        fe.reset()
        self.assertEqual(self.prepare()['rows'], expanded['rows'])
        self.snapshot['boundary'] = 'boundary-after-restart'
        self.assertEqual(self.prepare()['rows'], expanded['rows'])

    def test_scope_membership_expansion_joins_late_life_exactly_once(self):
        def select(members):
            return fe.prepare_tape(ROOM, peer_id=ROOM, topic_id=10, members=members)

        select([10])
        self.add(200, 'late canonical write', topic=20)
        self.assertEqual(select([10])['rows'], [])
        expanded = select([10, 20])
        self.assertEqual(len(expanded['rows']), 1)
        # A delayed life append must join the archived occurrence rather than
        # append a second copy in the now-expanded frozen epoch.
        self.life('late', '200', 'Alice: late canonical write')
        self.assertEqual(select([10, 20])['rows'], expanded['rows'])
        self.snapshot['hot'].clear()
        self.snapshot['covered_event_ids'] = ['late']
        self.snapshot['boundary'] = 'covered-late'
        self.assertEqual(select([10, 20])['rows'], [])
        self.assertEqual(fe.load(ROOM)['tape']['residuals'], [])

    def test_wrong_bytes_and_revision_never_join_covered_mid(self):
        self.prepare()  # raw occurrences below belong to the selected epoch tail
        self.add(1, 'new raw bytes')
        self.life('e1', '1', 'Alice: old bytes')
        self.snapshot['hot'].clear()
        self.snapshot['covered_event_ids'] = ['e1']
        self.assertIn('new raw bytes', self.text(self.prepare()))
        self.add(1, 'edit bytes', edited_at='2026-10-20T00:01:00Z')
        self.assertIn('edit bytes', self.text(self.prepare()))

    def test_bootstrap_is_current_canonical_not_whole_archive(self):
        self.add(1, 'old archive-only evidence', reply=99, origin='backfill')
        self.life('e1', '2', 'Alice: current canonical absent from archive')
        selected = self.prepare()
        self.assertEqual([r['line'] for r in selected['rows']],
                         ['Alice: current canonical absent from archive'])
        self.assertGreater(selected['bootstrap_cursor']['offset'], 0)

    def test_low_id_revisions_append_restart_and_envelope_do_not_erase_tape(self):
        self.add(500, 'first')
        self.life('first', '500', 'Alice: first')
        first = self.prepare()
        with fe.bind(ROOM, first['anchor'], tape=first):
            fe.serve(ROOM, first['anchor'], [('Эта комната', 'json', 'E-a')])
        self.add(1, 'late edit', edited_at='2026-10-20T00:01:00Z')
        gc.observe_deletion(peer_id=ROOM, message_id=1, timestamp='2026-10-20T00:02:00Z', origin='live')
        second = self.prepare()
        self.assertEqual(second['n'], first['n'])
        self.assertEqual(second['rows'][:len(first['rows'])], first['rows'])
        self.assertIn('late edit', self.text(second))
        self.assertIn('message deleted in Telegram', self.text(second))
        self.assertEqual(self.prepare()['rows'], second['rows'])
        self.assertEqual(fe.load(ROOM)['blocks'][0][-1], 'E-a')

    def test_delayed_wake_uses_its_frozen_E_after_boundary_and_same_reason_flips(self):
        self.add(1, 'one')
        self.life('one', '1', 'Alice: one')
        first = self.prepare()
        with fe.bind(ROOM, first['anchor'], tape=first):
            fe.serve(ROOM, first['anchor'], [('Эта комната', 'json', 'E-a')])
            first = copy.deepcopy(fe.bound_tape(ROOM))
        self.snapshot['boundary'], self.snapshot['summary'] = 'boundary-b', 'summary-b'
        second = self.prepare()
        with fe.bind(ROOM, second['anchor'], tape=second):
            fe.serve(ROOM, second['anchor'], [('Эта комната', 'json', 'E-b')])
        saved = copy.deepcopy(fe.load(ROOM))
        with fe.bind(ROOM, first['anchor'], tape=first):
            blocks, receipt = fe.serve(ROOM, first['anchor'], [('Эта комната', 'json', 'newest')])
        self.assertEqual(blocks[0][-1], 'E-a')
        self.assertEqual(receipt['boundary'], 'boundary-a')
        self.assertEqual(fe.load(ROOM), saved, 'delayed wake rolled room storage backward')
        for _ in range(2):
            before = self.prepare()['n']
            fe.flip(ROOM, 'same reason')
            selected = self.prepare()
            self.assertEqual(selected['n'], before + 1)
            with fe.bind(ROOM, selected['anchor'], tape=selected):
                fe.serve(ROOM, selected['anchor'], [])
            self.assertEqual(self.prepare()['n'], selected['n'])

    def test_oversized_retains_full_material_and_never_slides(self):
        text = ' ' + 'x' * (gc.EPOCH_TEXT_CHARS + 101) + '\x00 '
        self.add(1, text, outgoing=True)
        self.life('long', '1', 'Praxis: ' + text, own=True)
        with mock.patch.object(fe, 'max_chars', return_value=100):
            selected = self.prepare()
        self.assertFalse(selected['capacity']['within'])
        self.assertEqual(selected['rows'][0]['role_line'], text)
        self.assertEqual(fe.load(ROOM)['tape']['rows'][0]['role_line'], text)
        with self.assertRaisesRegex(fe.EpochUnavailable, 'no sliding fallback'):
            fe.require_servable(selected)
        with mock.patch.object(fe, 'max_chars', return_value=10), \
             mock.patch.object(runner, '_route_from_state', return_value=runner.telegram_topics.route_from_conversation_id(ROOM)), \
             mock.patch.object(gc, 'context_rows', side_effect=AssertionError('sliding fallback')):
            with self.assertRaises(fe.EpochUnavailable):
                runner._group_context_frozen(ROOM, {'context_hot': 1})

    def test_reader_has_no_50k_window_torn_tail_is_explicit(self):
        self.add(1, 'earliest')
        path = gc.archive_path(ROOM)
        topic = dict(schema=gc.SCHEMA_TOPIC, kind='topic', peer_id=ROOM,
                     topic_id=2, title='t', timestamp='2026-10-20T00:00:00Z', message_id=2)
        with path.open('ab') as stream:
            stream.write((json.dumps(topic) + '\n').encode() * (gc.MAX_READ_RECORDS + 1))
            stream.write(b'{"torn":')
        batch = gc.archive_occurrences(ROOM)
        self.assertEqual(batch['occurrences'][0]['row']['text'], 'earliest')
        self.assertGreater(batch['incomplete_tail_bytes'], 0)
        with path.open('ab') as stream:
            stream.write(b'true}\n')
        after = gc.archive_occurrences(ROOM, cursor=batch['cursor'])
        self.assertEqual(len(after['occurrences']), 1)
        self.assertIn('gap', after['occurrences'][0])
        path.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'truncated'):
            gc.archive_occurrences(ROOM, cursor=batch['cursor'])

    def test_cursor_precedes_life_snapshot_and_racing_occurrence_is_not_lost(self):
        def race(_chat, **_kwargs):
            # Archive append after capture, then life write before the snapshot.
            self.add(1, 'between cursor and snapshot')
            self.life('race', '1', 'Alice: between cursor and snapshot')
            return copy.deepcopy(self.snapshot)
        with mock.patch.object(ml, 'epoch_snapshot', side_effect=race):
            selected = self.prepare()
        self.assertEqual([r['line'] for r in selected['rows']],
                         ['Alice: between cursor and snapshot'])

    def test_life_before_archive_and_archive_before_life_are_not_lost_or_duplicated(self):
        self.life('first', '1', 'Alice: life before archive')
        first = self.prepare()
        self.add(1, 'life before archive')
        second = self.prepare()
        self.assertEqual(second['rows'], first['rows'])
        self.add(2, 'archive before life')
        third = self.prepare()
        self.life('second', '2', 'Alice: archive before life')
        self.assertEqual(self.prepare()['rows'], third['rows'])

    def test_future_archive_orphan_survives_fold_and_late_proved_coverage_retires_it(self):
        self.prepare()
        self.add(1, 'failed life write')
        selected = self.prepare()
        self.assertIn('failed life write', self.text(selected))
        self.snapshot['boundary'] = 'next-fold'
        self.assertIn('failed life write', self.text(self.prepare()))
        self.life('recovered', '1', 'Alice: failed life write')
        self.snapshot['hot'].clear()
        self.snapshot['covered_event_ids'] = ['recovered']
        self.snapshot['boundary'] = 'recovered-fold'
        self.assertNotIn('failed life write', self.text(self.prepare()))

    def test_current_life_absent_hot_stays_selected(self):
        event = self.life('missing-hot', '1', 'Alice: canonical missing state')
        # Source snapshot itself must do this, rather than treating hot absence
        # as a coverage receipt; exercised with a real graph below as well.
        self.assertEqual(self.prepare()['rows'][0]['source']['id'], event['id'])

    def test_future_backfill_not_live_even_when_earlier_message_id(self):
        self.prepare()
        self.add(1, 'old historical backfill', origin='backfill')
        selected = self.prepare()
        self.assertNotIn('old historical backfill', self.text(selected))
        self.add(1, 'old historical backfill', origin='live')
        self.assertIn('old historical backfill', self.text(self.prepare()),
                      'backfill dedupe swallowed a later actual live admission')

    def test_live_before_bootstrap_failed_life_is_selected_but_untagged_is_archival(self):
        self.add(1, 'live edit life failed', edited_at='2026-10-20T00:01:00Z')
        self.add(2, 'unknown historical archive', origin='')
        selected = self.prepare()
        self.assertIn('live edit life failed', self.text(selected))
        self.assertNotIn('unknown historical archive', self.text(selected))

    def test_exact_producer_life_envelope_prevents_reply_duplicate(self):
        self.prepare()
        line = 'Alice (в ответ Praxis: «exact old preview»): payload'
        self.life('reply', '1', line)
        gc.observe_message(peer_id=ROOM, topic_id=None, message_id=1, sender_id=2,
                           sender_name='Alice', reply_to_message_id=99,
                           timestamp='2026-10-20T00:00:00Z', text='payload', origin='live',
                           life_source=dict(source_id='1', direction='in', line=line))
        self.assertEqual([r['line'] for r in self.prepare()['rows']], [line])

    def test_real_edit_handler_archive_commit_then_failed_life_before_bootstrap(self):
        from test_deep_group_context import _LiveEvent, _EditedMessage
        event = _LiveEvent(_EditedMessage(71, 'handler failed life edit'))
        with mock.patch.object(runner.rooms, 'is_allowed', return_value=True), \
             mock.patch.object(runner.rooms, 'effective_mode', return_value='normal'), \
             mock.patch.object(runner, '_known_forum', return_value=True), \
             mock.patch.object(runner, '_catalog_for_routing', mock.AsyncMock(return_value=frozenset({77}))), \
             mock.patch.object(runner, '_group_archive_enabled', return_value=True), \
             mock.patch.object(runner, '_topic_titles', {}), \
             mock.patch.object(runner, '_buf', defaultdict(lambda: deque(maxlen=600))), \
             mock.patch.object(runner, '_buffer_message_ids', defaultdict(lambda: deque(maxlen=600))), \
             mock.patch.object(runner, '_persisted_life_sources', set()), \
             mock.patch.object(runner, '_under_tests', return_value=False), \
             mock.patch.object(runner, '_record_life_message_offloop', mock.AsyncMock(return_value=False)), \
             mock.patch.object(runner.bufstore, 'meta_update'), \
             mock.patch.object(runner, '_group_wakes', {}):
            asyncio.run(runner.on_edited(event))
        batch = gc.archive_occurrences('-1001')
        self.assertEqual(batch['occurrences'][0]['row']['origin'], 'live')
        selected = fe.prepare_tape('-1001__topic__77', peer_id='-1001', topic_id=77)
        self.assertIn('handler failed life edit', self.text(selected))


class EpochConsumer(EpochSources):
    def patch_prompt(self):
        for name, value in (('build_state_block', ''), ('build_state_evidence_block', ''),
                            ('read_summary', 'WRONG LATER SUMMARY'), ('_recall_block', '')):
            self.enterContext(mock.patch.object(agent, name, return_value=value))

    def test_actual_runner_snapshot_to_voice_model_boundary_over_501_roles(self):
        self.patch_prompt()
        # Exact archive producer records, then real source selection/render and
        # real agent assembly. Only the network/tool-loop boundary is replaced.
        records = []
        for index in range(561):
            own = index % 2 == 1
            text = (' own %s \u00a0' if own else 'human %s') % index
            row = dict(schema=gc.SCHEMA_MESSAGE, kind='message', peer_id=ROOM, topic_id=None,
                       topic_title='', message_id=index + 1, sender_id=1 if own else 2,
                       sender_name='Praxis' if own else 'Alice', reply_to_message_id=None,
                       timestamp='2026-10-20T00:00:00Z', edited_at=None, revision_order=0,
                       text=text, media='', outgoing=own, origin='live')
            records.append(row)
            self.life('e' + str(index), str(index + 1),
                      ('Praxis: ' if own else 'Alice: ') + text, own=own)
        path = gc.archive_path(ROOM)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        route = runner.telegram_topics.route_from_conversation_id(ROOM)
        with mock.patch.object(runner, '_route_from_state', return_value=route), \
             mock.patch.object(runner, '_room_policy_for_state', return_value={'context_hot': 1}), \
             mock.patch.object(ml, 'TAPE_CHARS', 20), \
             mock.patch.object(ml, 'GROUP_TAPE_CHARS', 20):
            wake = runner._group_trigger_snapshot(ROOM, mid=561, message_ts=1,
                kind='reply', addressed=True, query='human 560', name='Alice',
                sender_id=2, is_owner=False, known=True, family=False)
        frozen = copy.deepcopy(wake.epoch_snapshot)
        self.snapshot['boundary'] = 'later-boundary'
        self.snapshot['summary'] = 'WRONG NEW SUMMARY'
        self.add(562, 'LATER TRAFFIC')
        self.prepare()
        captured = {}
        history, current = runner._group_dialogue(wake.turns_snapshot, exact=True)
        self.assertEqual(len(history), 560)
        with mock.patch.object(agent, '_terminal_tool_loop',
                               side_effect=lambda **kw: captured.update(kw) or 'ok'), \
             mock.patch.object(agent, 'HISTORY_TURNS', 2), \
             fe.bind(ROOM, frozen['anchor'], tape=frozen):
            result = agent._voice_impl(current, history, 'Alice', tools_override=[],
                ctx=agent.ChannelContext(chat_id=ROOM, room_id=ROOM, is_dm=False,
                                         principal_id=2, owner=False))
        self.assertEqual(result, 'ok')
        messages = captured['messages']
        self.assertEqual(len(messages), 562)
        self.assertIn('selected summary', messages[0]['content'])
        self.assertNotIn('WRONG', messages[0]['content'])
        self.assertIn('human 0', messages[1]['content'])
        self.assertEqual(messages[2]['content'], ' own 1 \u00a0')
        self.assertNotIn('LATER TRAFFIC', str(messages))
        self.assertEqual(wake.epoch_snapshot, frozen)
        # Exercise the actual delayed-pass dispatch and offloaded agent ingress,
        # not just the renderer API. Keep delivery/model side effects replaced.
        from test_group_wake_snapshot import _Client, _no_compact

        def ingress(_chat, convo, speaker, *, ctx, history, current_text, **kwargs):
            agent._voice_impl(current_text, history, speaker, ctx=ctx, tools_override=[])
            return media.TurnEnvelope()

        async def delayed_pass():
            with mock.patch.object(runner, '_ONE_MIND', asyncio.Lock()):
                await runner._run_pass(ROOM)
                await asyncio.sleep(0)

        with mock.patch.object(runner, 'client', _Client()), \
             mock.patch.object(runner, '_meta', {ROOM: dict(is_dm=False, peer_id=ROOM,
                                                          entity=int(ROOM), title='room')}), \
             mock.patch.object(runner, '_group_wakes', {ROOM: wake}), \
             mock.patch.object(runner, '_last_pass', defaultdict(float)), \
             mock.patch.object(runner, '_passing', set()), \
             mock.patch.object(runner, '_pending_media', defaultdict(lambda: deque(maxlen=16))), \
             mock.patch.object(runner, '_recent_msgs', defaultdict(lambda: deque(maxlen=12))), \
             mock.patch.object(runner, '_missed', {}), \
             mock.patch.object(runner, '_cooldown', return_value=0.0), \
             mock.patch.object(runner, '_gate_group_wake_room_mode', return_value=('normal', 'open')), \
             mock.patch.object(runner, '_maybe_compact', side_effect=_no_compact), \
             mock.patch.object(runner, '_group_context_frozen', side_effect=AssertionError('moving snapshot')), \
             mock.patch.object(agent, 'voice_turn_envelope', side_effect=ingress), \
             mock.patch.object(agent, '_terminal_tool_loop',
                               side_effect=lambda **kw: captured.update(kw) or 'ok'), \
             mock.patch.object(agent, 'HISTORY_TURNS', 2):
            asyncio.run(delayed_pass())
        self.assertEqual(len(captured['messages']), 562)
        self.assertIn('selected summary', captured['messages'][0]['content'])
        self.assertNotIn('LATER TRAFFIC', str(captured['messages']))

    def test_media_ingress_preserves_selected_current_whitespace(self):
        self.add(1, 'seed')
        selected = self.prepare()
        ctx = agent.ChannelContext(chat_id=ROOM, room_id=ROOM, is_dm=False)
        raw = '  exact current \u00a0'
        with fe.bind(ROOM, selected['anchor'], tape=selected):
            _receipt, model = agent._media_prompt(raw, (), ctx, model_text=raw)
        self.assertEqual(model, raw)


class OutgoingEpochReceipts(unittest.TestCase):
    """Actual _run_pass -> archive/life -> epoch, with only model/transport replaced."""

    def setUp(self):
        from test_coverage_vs_current import Base
        fixture = Base()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.base = fixture.tmp
        self.enterContext(mock.patch.dict(os.environ, {
            'PRAXIS_FRAME_EPOCH': '1', 'PRAXIS_FRAME_EPOCH_ROOMS': '*',
            'PRAXIS_FRAME_EPOCH_MAX_CHARS': '0', 'PRAXIS_DIALOGUE_ROLES': '1',
            'PRAXIS_KEAT_CAPTURE': 'off'}))
        for module, key, value in ((fe, 'STORE', self.base / 'epochs'),
                                   (gc, 'GROUPS_DIR', self.base / 'groups'),
                                   (gc, '_KEY_CACHE', {}),
                                   (runner, '_buf', defaultdict(lambda: deque(maxlen=600))),
                                   (runner, '_buffer_message_ids', defaultdict(deque)),
                                   (runner, '_buf_dirty', set()),
                                   (runner, '_persisted_life_sources', set())):
            self.enterContext(mock.patch.object(module, key, value))
        self.enterContext(mock.patch.object(runner.bufstore, 'meta_update'))
        self.enterContext(mock.patch.object(runner, '_under_tests', return_value=False))
        self.capture = self.enterContext(mock.patch('keat_live.capture_ingress', return_value=None))
        self.addCleanup(fe.reset)

    def prepare(self):
        return fe.prepare_tape(ROOM, peer_id=ROOM, whole_room=True)

    def send(self, *, native=False, fail_at=None, race=False):
        from types import SimpleNamespace
        from test_group_wake_snapshot import _wake, _no_compact
        reply = 'first ' * 800 + ' middle\n' * 600 + ' last\u00a0 '
        chunks = runner._split_telegram_text(reply)
        self.assertGreaterEqual(len(chunks), 3)
        calls, accepted, during_send = [], [], []

        async def transport(_entity, chunk, **_kwargs):
            if race and accepted:
                # Previous chunk archive has committed, but aggregate persistence
                # is still pending. The next chunk may fail at this exact point.
                during_send.append(self.prepare())
            index = len(calls)
            calls.append(chunk)
            if index == fail_at:
                raise RuntimeError('known transport failure')
            accepted.append(chunk)
            return SimpleNamespace(id=1001 + index), 123

        wake = _wake(mid=50, ts=1, context='Alice: answer')

        async def run():
            with mock.patch.object(runner, '_ONE_MIND', asyncio.Lock()):
                await runner._run_pass(ROOM)
                await asyncio.sleep(0)

        with mock.patch.object(runner, '_meta', {ROOM: dict(is_dm=False, peer_id=ROOM,
                    entity=int(ROOM), title='room', room_mode='normal')}), \
             mock.patch.object(runner, '_group_wakes', {ROOM: wake}), \
             mock.patch.object(runner, '_last_pass', defaultdict(float)), \
             mock.patch.object(runner, '_passing', set()), \
             mock.patch.object(runner, '_pending_media', defaultdict(lambda: deque(maxlen=16))), \
             mock.patch.object(runner, '_recent_msgs', defaultdict(lambda: deque(maxlen=12))), \
             mock.patch.object(runner, '_missed', {}), \
             mock.patch.object(runner, '_cooldown', return_value=0), \
             mock.patch.object(runner, '_gate_group_wake_room_mode', return_value=('normal', 'open')), \
             mock.patch.object(runner, '_group_context_frozen', return_value=('', ())), \
             mock.patch.object(runner, '_group_archive_enabled', return_value=True), \
             mock.patch.object(runner, '_group_native_persistence_enabled', return_value=native), \
             mock.patch.object(runner, '_send_message_idempotent', side_effect=transport), \
             mock.patch.object(runner, '_maybe_compact', side_effect=_no_compact) as compact, \
             mock.patch.object(agent, 'voice_turn_envelope',
                               return_value=media.TurnEnvelope(text=reply)) as voice:
            asyncio.run(run())
        self.assertEqual(voice.call_count, 1)
        self.assertEqual(compact.call_count, 1, 'chunk count multiplied reminder scheduling')
        self.assertEqual(list(runner._buf[ROOM]), ['Praxis: ' + ''.join(accepted)] if accepted else [])
        self.assertEqual(self.capture.call_count, len(accepted) if native else 0)
        return accepted, during_send

    def exercise(self, *, native=False, partial=False, race=False):
        self.prepare()  # archive below is a new append, not legacy prebaseline
        chunks, racing = self.send(native=native, fail_at=2 if partial else None, race=race)
        events = ml.iter_events(chat_id=ROOM, kinds={'conversation_message'})
        self.assertEqual([row['text'] for row in events], ['Praxis: ' + part for part in chunks])
        self.assertEqual([str(row['source_id']) for row in events],
                         [str(1001 + i) for i in range(len(chunks))])
        metadata = [row['meta']['logical_send'] for row in events]
        self.assertEqual(metadata, [dict(id=metadata[0]['id'], index=i, count=len(chunks))
                                    for i in range(len(chunks))])
        archive = gc.archive_occurrences(ROOM)['occurrences']
        self.assertEqual(len(archive), len(chunks))
        self.assertEqual([gc.epoch_life_signature(row['row']) for row in archive],
                         [(str(row['source_id']), 'out', row['text']) for row in events])
        selected = self.prepare()
        self.assertEqual([row['role_line'] for row in selected['rows']], chunks)
        self.assertEqual(self.prepare()['rows'], selected['rows'])
        if racing:
            self.assertTrue(racing[0]['residuals'])
            self.assertEqual([row['line'] for row in selected['rows'][:len(racing[0]['rows'])]],
                             [row['line'] for row in racing[0]['rows']])
        # A later user turn gives the real role renderer a current message.
        ml.record_message(ROOM, 'Alice: next', actor='Alice', direction='in', source_id='2000',
                          dedupe_key=f'telegram:{ROOM}:2000:in')
        with mock.patch.object(runner, '_route_from_state',
                               return_value=runner.telegram_topics.route_from_conversation_id(ROOM)):
            text, turns = runner._group_context_frozen(ROOM, {'context_hot': 1})
        history, current = runner._group_dialogue(turns, exact=True)
        self.assertEqual(current, 'Alice: next')
        self.assertEqual([row['role'] for row in history], ['assistant'])
        self.assertEqual(history[0]['content'], ''.join(chunks))
        self.assertEqual(text, 'Praxis: ' + ''.join(chunks) + '\nAlice: next')
        # Exercise the public authored-selection writer, not a fake coverage set.
        remaining = {row['id'] for row in events}
        # The real bounded authored selector can require several folds for a
        # long reply. Every pass must make progress and select exact sent IDs.
        for _ in events:
            sources = ml.prepare_authored_fold(ROOM, limit=100)
            self.assertTrue(sources['ok'])
            chosen = {row['id'] for row in sources['sources']}
            self.assertTrue(chosen and chosen <= remaining, sources)
            receipt = ml.apply_authored_fold(ROOM, sources['selection'], 'I sent the accepted prefix.')
            self.assertTrue(receipt['ok'], receipt)
            remaining -= chosen
            if not remaining:
                break
        self.assertFalse(remaining)
        after = self.prepare()
        self.assertEqual([row['role_line'] for row in after['rows']], ['Alice: next'])
        self.assertFalse(after['residuals'], 'exact covered receipts grew forever')
        self.assertIn('I sent the accepted prefix.', after['summary'])

    def test_epoch_native_disabled_full_send(self):
        self.exercise()

    def test_capture_enabled_without_native_readiness_does_not_capture(self):
        with mock.patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'on'}):
            self.exercise()

    def test_epoch_native_enabled_full_send(self):
        with mock.patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'on'}):
            self.exercise(native=True)

    def test_epoch_native_disabled_partial_prefix(self):
        self.exercise(partial=True)

    def test_epoch_native_enabled_partial_prefix(self):
        with mock.patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'on'}):
            self.exercise(native=True, partial=True)

    def test_prepare_between_archive_and_life_full_send(self):
        self.exercise(race=True)

    def test_prepare_between_archive_and_life_partial_prefix(self):
        self.exercise(race=True, partial=True)

    def test_no_receipt_persists_nothing(self):
        chunks, _ = self.send(fail_at=0)
        self.assertEqual(chunks, [])
        self.assertEqual(ml.iter_events(chat_id=ROOM, kinds={'conversation_message'}), [])
        self.assertEqual(self.prepare()['rows'], [])

    def test_epoch_disabled_retains_legacy_aggregate(self):
        with mock.patch.dict(os.environ, {'PRAXIS_FRAME_EPOCH': '0'}):
            chunks, _ = self.send()
        events = ml.iter_events(chat_id=ROOM, kinds={'conversation_message'})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['text'], 'Praxis: ' + ''.join(chunks))
        self.assertEqual(events[0]['source_id'], ','.join(str(1001+i) for i in range(len(chunks))))
        self.assertNotIn('logical_send', events[0]['meta'])

    def test_existing_aggregate_is_not_rewritten_when_epoch_turns_on(self):
        with mock.patch.dict(os.environ, {'PRAXIS_FRAME_EPOCH': '0'}):
            chunks, _ = self.send()
        original = ml.iter_events(chat_id=ROOM, kinds={'conversation_message'})
        selected = self.prepare()
        self.assertEqual([row['role_line'] for row in selected['rows']], [''.join(chunks)] + chunks)
        self.assertEqual(ml.iter_events(chat_id=ROOM, kinds={'conversation_message'}), original)

    def test_failed_life_chunk_remains_exact_archive_residual_across_boundary(self):
        self.prepare()
        original = ml.record_message

        def fail_second(chat, line, **kwargs):
            if str(kwargs.get('source_id')) == '1002':
                raise OSError('life append failed')
            return original(chat, line, **kwargs)

        with mock.patch.object(ml, 'record_message', side_effect=fail_second):
            chunks, _ = self.send()
        selected = self.prepare()
        self.assertEqual([row['role_line'] for row in selected['rows']], chunks)
        # No guessed numeric equality: even a real event with same ID but wrong
        # bytes cannot retire the archive body.
        ml.record_message(ROOM, 'Praxis: WRONG', actor='Praxis', direction='out', source_id='1002')
        fe.flip(ROOM, 'test boundary')
        selected = self.prepare()
        self.assertIn(chunks[1], [row['role_line'] for row in selected['rows']])

    def test_epoch_enabled_but_unselected_room_keeps_legacy_aggregate(self):
        with mock.patch.dict(os.environ, {'PRAXIS_FRAME_EPOCH_ROOMS': '-100123'}):
            chunks, _ = self.send()
        events = ml.iter_events(chat_id=ROOM, kinds={'conversation_message'})
        self.assertEqual([row['text'] for row in events], ['Praxis: ' + ''.join(chunks)])
        self.assertNotIn('logical_send', events[0]['meta'])

    def test_incomplete_or_reordered_batch_does_not_invent_logical_send(self):
        self.send()
        selected = self.prepare()
        rows = selected['rows']
        current = dict(self=False, line='Alice: next', role_line='Alice: next')
        with mock.patch.object(fe, 'prepare_tape', return_value=dict(selected, rows=rows[::-1] + [current])):
            _text, turns = runner._group_context_frozen(ROOM, {'context_hot': 1})
        history, _current = runner._group_dialogue(turns, exact=True)
        self.assertEqual(history[0]['content'], '\n\n'.join(row['role_line'] for row in rows[::-1]))


class RealLifeSnapshot(unittest.TestCase):
    def test_real_graph_coverage_ghost_and_state_only_residual(self):
        from test_coverage_vs_current import Base
        fixture = Base()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        first = fixture._msg(1, 'covered', chat=ROOM)
        fixture._compact([first], chat=ROOM, summary='authored summary')
        ghost = ml.record_message(ROOM, 'Praxis: exact trailing \u00a0', actor='Praxis',
            direction='out', source_id='2', ts=3, dedupe_key='telegram:ghost:out')
        state = ml._load_state(ROOM)
        state['hot'].append(dict(id='state-only', source_id='3', direction='in',
                                 line='Alice: only in state', actor='Alice'))
        ml._save_state(state)
        selected = ml.epoch_snapshot(ROOM)
        self.assertIn(first['id'], selected['covered_event_ids'])
        self.assertNotIn(first['id'], [r['id'] for r in selected['hot']])
        self.assertIn(ghost['id'], [r['id'] for r in selected['hot']])
        self.assertIn('state-only', [r['id'] for r in selected['hot']])
        self.assertIn('authored summary', selected['summary'])
        boundary = selected['boundary']
        fixture._msg(4, 'new', chat=ROOM)
        self.assertEqual(ml.epoch_snapshot(ROOM)['boundary'], boundary)
        state = ml._load_state(ROOM)
        state['hot'] = []
        ml._save_state(state)
        self.assertIn(ghost['id'],
                      [r['id'] for r in ml.epoch_snapshot(ROOM)['hot']])


if __name__ == '__main__':
    unittest.main()
