"""A zero-message receipt completes a plan; it never proves speech."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import run_context
import run_manager
import turns


class EmptyDeliveryReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = run_manager.RunManager(self.root)
        self.patches = [
            mock.patch.object(agent, '_RUN_MANAGER', self.manager),
            mock.patch.object(agent, 'BASE', self.root),
            mock.patch.object(turns, 'STATE_DIR', self.root / 'memory/.state'),
            mock.patch.object(turns, 'PATH', self.root / 'memory/.state/turns.jsonl'),
        ]
        for patch in self.patches:
            patch.start()
        turns._reset()
        ctx = run_context.RunContext.create(
            kind='chat_turn', goal='decide whether to reply', principal_id='praxis:self',
            scope='group', origin_chat_id='101', delivery_chat_id='101')
        self.ctx = self.manager.create(ctx, 'Synthetic conversation')
        self.manager.transition(self.ctx.run_id, 'running')

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        turns._reset()
        self.temp.cleanup()

    def record(self, *, held='unspoken', text=''):
        row = turns.begin(kind='chat', chat_id='101', scope='group', who='Synthetic')
        row.update(run_id=self.ctx.run_id, held=held, note='Private draft', out=text)
        turns.record(row)

    def recap(self):
        return (self.manager.path(self.ctx.run_id) / 'RECAP.md').read_text(encoding='utf-8')

    def test_unspoken_draft_never_becomes_sent_or_accepted(self):
        self.record()
        with mock.patch.object(agent, 'reply_hand_message_ids', return_value=[]):
            self.assertTrue(agent.run_delivery_completed(
                self.ctx.run_id, silent=True,
                silent_reason='turn ended without a reply hand (not a declared silence)'))
            self.assertTrue(agent.run_delivery_finalize_recovered(self.ctx.run_id))
        row = turns.recent(1, scope='group', chat_id='101')[-1]
        self.assertEqual(row['delivery'], 'authored')
        self.assertEqual(row['held'], 'unspoken')
        self.assertEqual(row['note'], 'Private draft')
        self.assertFalse(row['out'])
        self.assertIn('- State: `skipped`', self.recap())
        self.assertNotIn('- State: `sent`', self.recap())
        self.assertTrue(agent._delivery_evidence(self.ctx.run_id)['ready'])
        self.assertEqual(self.manager.manifest(self.ctx.run_id)['status'], 'done')

    def test_declared_silence_remains_her_own_decision(self):
        self.record(held='voice')
        with mock.patch.object(agent, 'reply_hand_message_ids', return_value=[]):
            self.assertTrue(agent.run_delivery_completed(self.ctx.run_id, silent=True))
        row = turns.recent(1, scope='group', chat_id='101')[-1]
        self.assertEqual(row['held'], 'voice')
        self.assertNotEqual(row['delivery'], 'accepted')
        self.assertIn('- State: `skipped`', self.recap())
        self.assertIn('silent decision', self.recap())

    def test_delivered_reply_hand_survives_the_empty_boundary_receipt(self):
        self.record(held='', text='Explicitly spoken')
        with mock.patch.object(agent, 'reply_hand_message_ids', return_value=['700']):
            self.assertTrue(agent.run_delivery_completed(self.ctx.run_id, silent=True))
        row = turns.recent(1, scope='group', chat_id='101')[-1]
        self.assertEqual(row['delivery'], 'accepted')
        self.assertEqual(row['out'], 'Explicitly spoken')
        self.assertIn('- State: `sent`', self.recap())
        self.assertIn('Messages accepted through the reply hand: 1', self.recap())

    def test_normal_text_receipt_stays_sent(self):
        self.record(held='', text='Actual message')
        self.assertTrue(agent.run_delivery_completed(
            self.ctx.run_id, text='Actual message', message_ids=['701']))
        row = turns.recent(1, scope='group', chat_id='101')[-1]
        self.assertEqual(row['delivery'], 'accepted')
        self.assertEqual(row['out'], 'Actual message')
        self.assertIn('- State: `sent`', self.recap())


if __name__ == '__main__':
    unittest.main()
