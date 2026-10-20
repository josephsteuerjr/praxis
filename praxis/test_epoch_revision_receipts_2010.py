"""Real incoming/revision producers keep life receipts and delayed epoch E coherent.

Run through praxis_test.py; model and Telegram transport are never called.
"""
from __future__ import annotations

import asyncio
import copy
import datetime
import os
import unittest
from collections import defaultdict, deque
from unittest import mock

import agent
import frame_epoch as fe
import frame_layout
import group_context as gc
import media
import memory_life as ml
import mtproto_runner as runner
from test_coverage_vs_current import Base, ROOM
from test_deep_group_context import _LiveEvent, _LiveMessage, _EditedMessage, _DeletedEvent
from test_group_wake_snapshot import _Client, _no_compact


class IncomingEpochReceipts(unittest.TestCase):
    def setUp(self):
        fixture = Base()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.enterContext(mock.patch.dict(os.environ, {
            'PRAXIS_FRAME_EPOCH': '1', 'PRAXIS_FRAME_EPOCH_ROOMS': '*',
            'PRAXIS_FRAME_HEAD_STABLE': '1', 'PRAXIS_FRAME_EPOCH_MAX_CHARS': '0',
            'PRAXIS_FRAME_TAPE_HANDS': 'off', 'PRAXIS_KEAT_CAPTURE': 'off',
            'PRAXIS_DIALOGUE_ROLES': '1'}))
        for module, name, value in (
            (fe, 'STORE', fixture.tmp / 'epochs'),
            (gc, 'GROUPS_DIR', fixture.tmp / 'groups'), (gc, '_KEY_CACHE', {}),
            (runner, '_buf', defaultdict(lambda: deque(maxlen=600))),
            (runner, '_buffer_message_ids', defaultdict(deque)),
            (runner, '_buf_dirty', set()), (runner, '_persisted_life_sources', set()),
            (runner, '_pending_media', defaultdict(lambda: deque(maxlen=16))),
            (runner, '_seen_ids', defaultdict(lambda: deque(maxlen=50))),
            (runner, '_recent_msgs', defaultdict(lambda: deque(maxlen=12))),
            (runner, '_recent_senders', defaultdict(lambda: deque(maxlen=40))),
            (runner, '_group_wakes', {}), (runner, '_meta', {}),
            (runner, '_topic_titles', {}), (runner, '_entity_cache', {})):
            self.enterContext(mock.patch.object(module, name, value))
        for module, name, value in (
            (runner.rooms, 'is_allowed', True), (runner.rooms, 'effective_mode', 'normal'),
            (runner, '_known_forum', False), (runner, '_group_archive_enabled', True),
            (runner, '_under_tests', False), (runner.social, 'category', 'known'),
            (runner, '_should_wake', False), (runner, '_media_tag', ''),
            (agent, 'build_state_block', ''), (agent, 'build_state_evidence_block', ''),
            (agent, '_recall_block', '')):
            self.enterContext(mock.patch.object(module, name, return_value=value))
        self.enterContext(mock.patch.object(runner, '_chat_descriptor', mock.AsyncMock(
            return_value={'title': 'room', 'size': 10})))
        self.enterContext(mock.patch.object(runner, '_capture_typed_media', mock.AsyncMock(
            return_value=(None, ''))))
        self.enterContext(mock.patch.object(runner, '_maybe_compact', side_effect=_no_compact))
        self.enterContext(mock.patch.object(runner, '_arm'))
        self.enterContext(mock.patch.object(runner.bufstore, 'meta_update'))
        self.enterContext(mock.patch.object(runner.telegram_contacts, 'observe'))
        self.enterContext(mock.patch.object(runner.telegram_followups.LEDGER,
                                            'observe_incoming', return_value=None))
        self.addCleanup(fe.reset)
        self.addCleanup(frame_layout.reset)

    def event(self, mid, text, *, edit=False):
        message = (_EditedMessage if edit else _LiveMessage)(mid, text)
        message.reply_to = None
        message.reply_to_msg_id = None
        message.date = datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=mid)
        if edit:
            message.edit_date = message.date + datetime.timedelta(minutes=1)
        event = _LiveEvent(message)
        event.chat_id = int(ROOM)
        return event

    def new(self, mid, text):
        asyncio.run(runner.on_new(self.event(mid, text)))

    def prepare(self):
        return fe.prepare_tape(ROOM, peer_id=ROOM, whole_room=True)

    def fold(self, recap):
        selected = ml.prepare_authored_fold(ROOM, limit=1)
        self.assertTrue(selected['sources'], selected)
        receipt = ml.apply_authored_fold(ROOM, selected['selection'], recap)
        self.assertTrue(receipt['ok'], receipt)
        return receipt

    def test_on_new_whitespace_receipt_joins_and_authored_fold_retires_archive(self):
        self.prepare()  # Exercise incremental live receipt ingestion as well as bootstrap.
        for mid, text in ((1, 'with trailing space '), (2, 'with trailing NBSP\u00a0'),
                          (3, '\u00a0with leading and trailing whitespace \u00a0')):
            self.new(mid, text)
        events = ml.iter_events(chat_id=ROOM, kinds={'conversation_message'})
        archive = gc.archive_occurrences(ROOM)['occurrences']
        self.assertEqual(len(events), 3)
        self.assertEqual([row['row']['text'] for row in archive],
                         ['with trailing space ', 'with trailing NBSP\u00a0',
                          '\u00a0with leading and trailing whitespace \u00a0'])
        self.assertEqual([gc.epoch_life_signature(row['row']) for row in archive],
                         [(row['source_id'], 'in', row['text']) for row in events])
        selected = self.prepare()
        self.assertEqual([row['line'] for row in selected['rows']],
                         [row['text'] for row in events])
        self.new(4, 'unfolded tail')
        for _ in events:
            self.fold('I remember the incoming messages.')
        after = self.prepare()
        self.assertEqual([row['line'] for row in after['rows']], ['Alice: unfolded tail'])
        self.assertEqual(after['residuals'], [])
        self.assertIn('I remember the incoming messages.', after['summary'])
        # The common producer helper must not strip meaningful outgoing chunk edges.
        self.assertEqual(ml.canonical_event_text('Praxis: exact \u00a0 ', 'out'),
                         'Praxis: exact \u00a0 ')

    def revised_delayed_pass(self, *, deleted):
        for mid in (1, 2, 3):
            self.new(mid, 'original %s' % mid)
        ml.record_message(ROOM, 'Praxis: earlier answer', actor='Praxis', direction='out',
                          source_id='100', ts=datetime.datetime(2026, 10, 20,
                          tzinfo=datetime.timezone.utc).timestamp() + 1.5)
        wake = runner._group_trigger_snapshot(ROOM, mid=3, message_ts=1,
            kind='mention', addressed=True, query='original 3', name='Alice',
            sender_id=10, is_owner=True, known=True, family=False)
        runner._group_wakes[ROOM] = wake
        old_epoch = copy.deepcopy(wake.epoch_snapshot)
        receipt = self.fold('RECAP B: I retain the first message.')
        self.assertNotEqual(self.prepare()['boundary'], old_epoch['boundary'])
        if deleted:
            asyncio.run(runner.on_deleted(_DeletedEvent([2], chat_id=int(ROOM))))
        else:
            asyncio.run(runner.on_edited(self.event(2, 'EDITED AFTER FOLD', edit=True)))
        refreshed = runner._group_wakes[ROOM]
        self.assertIsNot(refreshed, wake)
        self.assertNotEqual(refreshed.epoch_snapshot['boundary'], old_epoch['boundary'])
        self.assertIn('RECAP B:', refreshed.epoch_snapshot['summary'])
        self.assertEqual(refreshed.epoch_snapshot['rows'], self.prepare()['rows'])
        for field in ('message_id', 'message_ts', 'kind', 'addressed', 'sender_id',
                      'owner', 'known', 'family', 'query', 'speaker'):
            self.assertEqual(getattr(refreshed, field), getattr(wake, field), field)
        self.assertEqual(wake.epoch_snapshot, old_epoch, 'old detached wake mutated')
        # Later traffic must not leak into the newly frozen revision selection.
        self.new(4, 'LATER TRAFFIC NOT IN REFRESH')
        self.prepare()
        captured = {}

        def ingress(_chat, _convo, speaker, *, ctx, history, current_text, **_kwargs):
            captured['ctx'] = ctx
            agent._voice_impl(current_text, history, speaker, ctx=ctx, tools_override=[])
            return media.TurnEnvelope()

        async def run():
            with mock.patch.object(runner, '_ONE_MIND', asyncio.Lock()):
                await runner._run_pass(ROOM)
                await asyncio.sleep(0)

        with mock.patch.object(runner, 'client', _Client()), \
             mock.patch.object(runner, '_last_pass', defaultdict(float)), \
             mock.patch.object(runner, '_passing', set()), \
             mock.patch.object(runner, '_missed', {}), \
             mock.patch.object(runner, '_cooldown', return_value=0), \
             mock.patch.object(runner, '_gate_group_wake_room_mode', return_value=('normal', 'open')), \
             mock.patch.object(runner, '_group_context_frozen', side_effect=AssertionError('moving tape')), \
             mock.patch.object(agent, 'voice_turn_envelope', side_effect=ingress), \
             mock.patch.object(agent, '_terminal_tool_loop',
                               side_effect=lambda **kw: captured.update(kw) or 'ok'):
            asyncio.run(run())
        messages = captured['messages']
        self.assertIn('RECAP B:', messages[0]['content'])
        tape = str(messages[1:])
        self.assertNotIn('original 1', tape, 'folded message reintroduced without recap boundary')
        self.assertNotIn('LATER TRAFFIC NOT IN REFRESH', tape)
        self.assertIn('original 3', tape)
        self.assertIn('message removed' if deleted else 'EDITED AFTER FOLD', tape)
        self.assertTrue(captured['ctx'].owner)
        self.assertEqual(captured['ctx'].principal_id, 10)
        self.assertEqual(captured['ctx'].origin_message_id, 3)
        self.assertEqual(captured['ctx'].origin_text, 'original 3')
        self.assertIn(receipt['compact_id'], str(ml.epoch_snapshot(ROOM)))

    def test_delayed_wake_fold_then_edit_refreshes_matching_recap_and_tape(self):
        self.revised_delayed_pass(deleted=False)

    def test_delayed_wake_fold_then_delete_refreshes_matching_recap_and_tape(self):
        self.revised_delayed_pass(deleted=True)

    def test_failed_envelope_refresh_does_not_publish_half_revised_wake(self):
        self.new(1, 'original 1')
        wake = runner._group_trigger_snapshot(ROOM, mid=1, message_ts=1,
            kind='mention', addressed=True, query='original 1', name='Alice',
            sender_id=10, is_owner=True, known=True, family=False)
        runner._group_wakes[ROOM] = wake
        with mock.patch.object(agent, '_build_prompt_parts', side_effect=RuntimeError('E unavailable')):
            # A new boundary means prepare has no existing E to reuse.
            fe.flip(ROOM, 'test refresh E failure')
            self.assertFalse(runner._refresh_group_wake_context(ROOM, message_id=1, query='edited'))
        self.assertIs(runner._group_wakes[ROOM], wake)


if __name__ == '__main__':
    unittest.main()
