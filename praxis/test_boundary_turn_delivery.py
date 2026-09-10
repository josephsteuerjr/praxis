"""Boundary receipts, not staged intent, populate the single lived-turn row."""
import contextlib
from pathlib import Path
from unittest import mock

import agent
import run_context
import run_manager
import turns
from test_turns import TurnsBase
from test_reply_hand import _TurnHarness, _says, _calls_reply, _calls_tool


class BoundaryReceiptProjection(TurnsBase):
    def setUp(self):
        super().setUp()
        self.manager = run_manager.RunManager(self.tmp)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(agent, '_RUN_MANAGER', self.manager))
        self.notes = self.stack.enter_context(mock.patch.object(agent.notes, 'append'))
        self.promises = self.stack.enter_context(mock.patch.object(agent.promises, 'note_outbound'))
        ctx = run_context.RunContext.create(kind='chat', goal='boundary fixture',
            principal_id='owner', scope='owner', origin_chat_id='777', delivery_chat_id='777')
        ctx = self.manager.create(ctx, 'fixture')
        self.manager.transition(ctx.run_id, 'running')
        self.rid = ctx.run_id
        row = turns.begin(kind='chat', chat_id='777', scope='owner')
        row.update(run_id=self.rid, held='unspoken', note='staged private draft')
        turns.record(row)
        agent.run_delivery_started(self.rid, chat_id='777', text_chars=7, media_count=0)

    def row(self):
        return turns.recent(1, scope='owner', chat_id='777')[-1]

    def test_intent_alone_is_not_speech(self):
        self.assertFalse(agent.run_delivery_finalize_recovered(self.rid))
        self.assertEqual(self.row()['out'], '')
        self.assertEqual(self.row()['held'], 'unspoken')
        self.notes.assert_not_called()
        self.promises.assert_not_called()

    def test_receipt_projects_exact_text_once_live_and_after_reload(self):
        agent.run_delivery_text_accepted(self.rid, text='visible', message_ids=['71'])
        self.assertEqual(self.row()['out'], 'visible')
        self.assertEqual(self.row()['held'], '')
        self.assertEqual(self.row()['delivery'], 'accepted')
        self.assertNotIn('без реплики', turns.format_line(self.row()))
        turns._reset()
        agent.run_delivery_text_accepted(self.rid, text='visible', message_ids=['71'])
        agent.run_delivery_finalize_recovered(self.rid)
        agent.run_delivery_finalize_recovered(self.rid)
        self.assertEqual(self.row()['out'], 'visible')
        self.assertEqual(len(turns.PATH.read_text().splitlines()), 1)
        self.notes.assert_called_once()
        self.promises.assert_called_once()
        self.assertEqual(self.promises.call_args.args[1], 'visible')
        self.assertEqual(turns.recent(10, scope='public', chat_id='999'), [])

    def test_failed_receipt_write_never_projects(self):
        with mock.patch.object(self.manager, 'store_result', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                agent.run_delivery_text_accepted(self.rid, text='visible', message_ids=['71'])
        self.assertEqual(self.row()['out'], '')
        self.notes.assert_not_called()

    def test_failed_projection_write_is_retryable_from_receipt(self):
        with mock.patch.object(Path, 'replace', side_effect=OSError('fixture')):
            agent.run_delivery_text_accepted(self.rid, text='visible', message_ids=['71'])
        self.assertEqual(self.row()['delivery'], 'authored')
        self.notes.assert_not_called()
        agent.run_delivery_finalize_recovered(self.rid)
        self.assertEqual(self.row()['out'], 'visible')
        self.notes.assert_called_once()

    def test_recovery_projects_receipt_without_live_callback(self):
        import json
        self.manager.store_result(self.rid, json.dumps({'text': 'visible', 'message_ids': ['71']}),
            call_id=f'delivery:{self.rid}', name='telegram-text',
            media_type='application/json; charset=utf-8', idempotent=True)
        agent.run_delivery_finalize_recovered(self.rid)
        self.assertEqual(self.row()['out'], 'visible')
        self.assertEqual(self.row()['held'], '')
        self.notes.assert_called_once()


class BoundaryOptIn(_TurnHarness):
    def test_opt_in_leaves_unspoken_word_pending_without_serving_draft(self):
        with mock.patch.object(agent, 'BOUNDARY_DELIVERS_UNSPOKEN', True):
            envelope, _ = self.turn(_says('boundary candidate'))
        row = self.recorded.rows[-1]
        self.assertEqual(envelope.text, '')
        self.assertEqual(row.get('out', ''), '')
        self.assertEqual(row['held'], 'unspoken')
        self.assertEqual(agent._runs().manifest(envelope.run_id)['status'], 'running')
        self.assertFalse(agent._delivery_evidence(envelope.run_id)['silent'])

    def test_opt_in_does_not_deliver_after_reply_hand(self):
        with mock.patch.object(agent, 'BOUNDARY_DELIVERS_UNSPOKEN', True):
            envelope, _ = self.turn(_calls_reply('boundary-hand', 'already sent'),
                                    _says('private afterthought'))
        self.assertEqual(envelope.text, '')
        self.assertEqual(self.recorded.rows[-1]['out'], 'already sent')
        self.assertEqual(len(self.bridge.calls), 1)

    def test_opt_in_never_overrides_declared_silence(self):
        with mock.patch.object(agent, 'BOUNDARY_DELIVERS_UNSPOKEN', True):
            envelope, _ = self.turn(_calls_tool('boundary-silent', 'stay_silent',
                                               {'reason': 'fixture silence'}),
                                    _says('private afterthought'))
        self.assertEqual(envelope.text, '')
        self.assertEqual(self.recorded.rows[-1]['held'], 'voice')
        self.assertTrue(agent._delivery_evidence(envelope.run_id)['silent'])
        self.assertEqual(self.bridge.calls, [])
