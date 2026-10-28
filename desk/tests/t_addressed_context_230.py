"""Exact late-input scheduling evidence through real local/Telegram consumers."""
from __future__ import annotations
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'helene/core'), str(ROOT/'praxis'), str(ROOT/'desk'),
                str(ROOT/'desk/localharness'), str(ROOT/'desk/tests')]
import agent
import addressed_replies as replies
import transport
import run_context
import runner
import telegram_inputs
from t_telegram_pending_inputs import TelegramInputs


class LateTelegram(TelegramInputs):
    def test_fifo_late_media_stays_pending_and_other_actor_does_not_change_authority(self):
        self.feed(1, 'initial')
        self.bound(telegram_inputs.select(runner, '123'))
        with replies.bind(self.root, '123'):
            messages = []
            self.drain(runner._drain_owner_inputs, messages)
            self.feed(2, voice=True)
            self.feed(3, 'late text by another admitted actor', sender=22)
            self.feed(4, 'second late text', sender=11)
            # One late text per model boundary, not a fresh ambient snapshot.
            self.drain(runner._drain_owner_inputs, messages)
            self.assertIn('late text by another', messages[-1]['content'])
            self.assertNotIn('second late text', json.dumps(messages))
            self.assertIsNotNone(self.bot.inputs.get('123:2'))
            self.assertIsNotNone(self.bot.inputs.get('123:4'))
            self.assertEqual(self.current.principal_id, '11')
            self.assertEqual(self.current.scope, 'owner')
            replies.dispatch(self.current.run_id, messages)
            self.assertFalse(replies.covered(self.root, self.current.run_id, '1'))
            replies.presented(self.current.run_id, messages)
            self.drain(runner._drain_owner_inputs, messages)
            self.assertIn('second late text', messages[-1]['content'])
            replies.dispatch(self.current.run_id, messages)
            replies.presented(self.current.run_id, messages)
            self.bot.agent = agent
            self.bot._before_send = lambda chat: None
            self.bot.client.call.return_value = {'message_id': 500, 'date': 1}
            with run_context.bind_run(self.current):
                self.bot.deliver_text('123', 'narration, not reply')
                self.assertFalse(replies.covered(self.root, self.current.run_id, '1'))
                self.bot.deliver_text('123', 'accepted reply', reply_evidence=True)
            for mid in ('1', '3', '4'):
                self.assertTrue(replies.covered(self.root, self.current.run_id, mid), mid)
            self.assertFalse(replies.covered(self.root, self.current.run_id, '2'))
            self.assertEqual(self.calls, [])  # late voice was not recognized as text

    def test_failed_and_partial_split_send_do_not_cover_incoming(self):
        self.feed(10, 'observed words')
        self.bound(telegram_inputs.select(runner, '123'))
        with replies.bind(self.root, '123'):
            messages = []; self.drain(runner._drain_owner_inputs, messages)
            replies.presented(self.current.run_id, messages)
            self.bot.agent = agent
            self.bot._before_send = lambda chat: None
            self.bot._retry_send = lambda *a, **k: (_ for _ in ()).throw(ConnectionError('synthetic'))
            with run_context.bind_run(self.current), self.assertRaises(ConnectionError):
                self.bot.deliver_text('123', 'failed reply', reply_evidence=True)
            self.assertFalse(replies.covered(self.root, self.current.run_id, '10'))
            calls = []
            def split(*a, **k):
                calls.append(1)
                if len(calls) > 1: raise ConnectionError('second chunk failed')
                return {'message_id': 501, 'date': 1}, 0
            self.bot._retry_send = split
            with run_context.bind_run(self.current), self.assertRaises(ConnectionError):
                self.bot.deliver_text('123', 'x'*10000, reply_evidence=True)
            self.assertFalse(replies.covered(self.root, self.current.run_id, '10'))


class LocalReceipts(unittest.TestCase):
    def test_real_local_reply_has_stable_row_id_and_exact_membership_not_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            desks = transport.Desks(root, 'Owner', 'Hélène')
            current = run_context.RunContext.create(kind='chat_turn', goal='synthetic',
                principal_id='unknown', scope='owner', origin_chat_id='window', delivery_chat_id='window')
            one = {'role': 'user', 'content': 'same text'}
            two = {'role': 'user', 'content': 'same text'}
            with patch.dict(agent._TELETHON, {}, clear=True), patch.dict(agent.TOOL_IMPL, agent.TOOL_IMPL), \
                 replies.bind(root, 'window'), run_context.bind_run(current):
                transport.install(agent, desks)
                replies.stage([one], ['note:001', 'note:003'])
                replies.presented(current.run_id, [one])
                agent._TELETHON['send_message']('window', 'unrelated narration')
                self.assertFalse(replies.covered(root, current.run_id, 'note:001'))
                receipt = agent._TELETHON['reply']('window', 'actual reply')
                self.assertIn('helene-', receipt)
                self.assertTrue(replies.covered(root, current.run_id, 'note:001'))
                self.assertTrue(replies.covered(root, current.run_id, 'note:003'))
                self.assertFalse(replies.covered(root, current.run_id, 'note:002'))
                outgoing = [row for row in desks.default.rows() if row.get('outgoing')]
                self.assertTrue(all(row.get('message_id') and row.get('delivery_key') for row in outgoing))
                # Same text with another occurrence is not a textual duplicate.
                replies.stage([two], ['note:004'])
                replies.presented(current.run_id, [one, two])
                self.assertFalse(replies.covered(root, current.run_id, 'note:004'))

    def test_model_intent_is_not_read_and_exact_resume_can_publish_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = [{'role': 'user', 'content': 'delivered source'}]
            with replies.bind(root, 'window'):
                replies.stage(request, ['row-A'])
                replies.dispatch('run-A', request)
            self.assertFalse(replies.covered(root, 'run-A', 'row-A'))
            # A resumed exact request has no old process ContextVar. Its saved
            # source association is restored only by matching the actual bytes.
            with patch.object(replies, 'BASE', root):
                replies.presented('run-A', [{'role': 'user', 'content': 'different'}])
                self.assertFalse(replies.accepted(root, 'run-A', 'window', 'reply1', delivery_key='key1'))
                replies.presented('run-A', request)
            self.assertTrue(replies.accepted(root, 'run-A', 'window', 'reply2', delivery_key='key2'))
            self.assertTrue(replies.covered(root, 'run-A', 'row-A'))
            self.assertFalse(replies.covered(root, 'other-run', 'row-A'))


if __name__ == '__main__': unittest.main()
