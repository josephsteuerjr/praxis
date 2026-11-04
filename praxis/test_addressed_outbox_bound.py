"""The read bound is immutable transport-intent evidence, not live turn state."""
import tempfile
import unittest
from pathlib import Path

from telegram_outbox import TelegramOutbox, TelegramOutboxConflict, TelegramOutboxValidationError


class AddressedOutboxBound(unittest.TestCase):
    def test_bound_survives_acceptance_restart_and_idempotent_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = TelegramOutbox(Path(tmp) / 'outbox')
            args = dict(peer_id=-700, topic_id=8, text='answer', run_id='r',
                        call_id='c', purpose='tool:reply', addressed_observed_message_id=20)
            box.prepare_text('key', **args)
            accepted = box.mark_accepted('key', message_id=100)
            restarted = TelegramOutbox(Path(tmp) / 'outbox')
            self.assertEqual(restarted.prepare_text('key', **args), accepted)
            self.assertEqual(restarted.get('key')['addressed_observed_message_id'], 20)
            with self.assertRaises(TelegramOutboxConflict):
                restarted.prepare_text('key', **dict(args, addressed_observed_message_id=22))
            # Same speech deduplication must not upgrade the old read proof.
            twin = restarted.prepare_text('another-key', **dict(args, addressed_observed_message_id=22))
            self.assertEqual(twin['addressed_observed_message_id'], 20)

    def test_legacy_intent_has_no_invented_bound_and_invalid_bound_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = TelegramOutbox(Path(tmp) / 'outbox')
            args = dict(peer_id=-700, text='answer', run_id='r', call_id='c', purpose='tool:reply')
            self.assertNotIn('addressed_observed_message_id', box.prepare_text('old', **args))
            for value in (True, 0, -1, '20'):
                with self.assertRaises(TelegramOutboxValidationError):
                    box.prepare_text('new', **args, addressed_observed_message_id=value)

    def test_exact_membership_is_frozen_validated_and_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = TelegramOutbox(Path(tmp) / 'outbox')
            ids = [20, 25]
            args = dict(peer_id=-700, text='answer', run_id='r', call_id='c',
                        purpose='tool:reply', addressed_observed_message_id=25,
                        addressed_observed_message_ids=ids)
            prepared = box.prepare_text('key', **args)
            ids.append(22)
            prepared['addressed_observed_message_ids'].append(23)
            box.mark_accepted('key', message_id=100)
            restarted = TelegramOutbox(Path(tmp) / 'outbox')
            self.assertEqual(restarted.get('key')['addressed_observed_message_ids'], [20, 25])
            with self.assertRaises(TelegramOutboxConflict):
                restarted.prepare_text('key', **args)
            twin = restarted.prepare_text('twin', **args)
            self.assertEqual(twin['addressed_observed_message_ids'], [20, 25])
            for value in (True, 20, '20', (20,), [True], [0], [-1], ['20']):
                with self.assertRaises(TelegramOutboxValidationError):
                    box.prepare_text('bad', **dict(args, addressed_observed_message_ids=value))
