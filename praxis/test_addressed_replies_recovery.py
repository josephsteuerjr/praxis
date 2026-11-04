"""Disposable receipt index faults never suppress wakes or accepted delivery."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import addressed_replies as receipts


class AddressedIndexRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.policy = self.root / 'memory/policies/addressed-replies.json'
        self.policy.parent.mkdir(parents=True)
        self.policy.write_text('{"enabled": true}')
        self.patch = patch.object(receipts, 'BASE', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        receipts._CACHE.clear()
        self.chat = '-700__topic__8'

    def record(self, reply=100, observed=90):
        return receipts.record(self.chat, reply, delivery_key='accepted:1',
                               observed_message_id=observed)

    def test_observed_frontier_not_outgoing_id_and_legacy_not_evidence(self):
        self.assertTrue(self.record())
        self.assertTrue(receipts.covered(self.chat, 90))
        self.assertFalse(receipts.covered(self.chat, 91))
        self.assertFalse(receipts.covered('-700__topic__9', 90))
        self.assertFalse(self.record())
        self.assertFalse(receipts.record(self.chat, 200, delivery_key='legacy'))
        path = receipts._path(self.chat)
        path.write_text(json.dumps(dict(schema='praxis.addressed-reply.v1',
            chat_id=self.chat, reply_message_id=500, delivery_key='old')) + '\n')
        self.assertEqual(receipts.watermark(self.chat), 0)
        self.assertTrue(self.record(600, 95))
        self.assertEqual(receipts.watermark(self.chat), 95)

    def test_torn_tail_disables_coverage_and_repair_is_backed_up_idempotent(self):
        self.assertTrue(self.record())
        path = receipts._path(self.chat)
        with path.open('ab') as stream:
            stream.write(b'{"partial":\xff')
        damaged = path.read_bytes()
        self.assertFalse(receipts.covered(self.chat, 90))
        report = receipts.diagnostics(self.chat)
        self.assertFalse(report['ok'])
        self.assertEqual(report['issues'][0]['line'], 2)
        self.assertNotIn('partial', str(report))
        self.assertFalse(self.record(200, 150))
        self.assertEqual(path.read_bytes(), damaged)
        result = receipts.recover(self.chat)
        self.assertEqual(Path(result['backup']).read_bytes(), damaged)
        self.assertEqual(result['removed'], 1)
        self.assertTrue(receipts.covered(self.chat, 90))
        self.assertFalse(receipts.recover(self.chat)['changed'])
        self.assertTrue(self.record(200, 150))
        receipts._CACHE.clear()
        self.assertEqual(receipts.watermark(self.chat), 150)

    def test_bad_interior_rows_and_foreign_topic_never_supply_coverage(self):
        self.record()
        path = receipts._path(self.chat)
        valid = path.read_bytes()
        wrong = json.loads(valid)
        wrong['chat_id'] = '-999'
        wrong['observed_message_id'] = 99
        path.write_bytes(b'[]\n' + valid + json.dumps(wrong).encode() + b'\nnull\n')
        self.assertEqual(receipts.watermark(self.chat), 0)
        self.assertEqual(receipts.recover(self.chat)['removed'], 3)
        self.assertEqual(receipts.watermark(self.chat), 90)

    def test_complete_row_without_newline_repaired_before_append(self):
        self.record()
        path = receipts._path(self.chat)
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        self.assertFalse(self.record(200, 150))
        self.assertTrue(receipts.recover(self.chat)['changed'])
        self.assertTrue(self.record(200, 150))
        self.assertTrue(receipts.diagnostics(self.chat)['ok'])

    def test_bad_policy_or_read_error_is_fail_open_for_wake(self):
        self.record()
        for value in ('{', '[]', 'null'):
            self.policy.write_text(value)
            self.assertFalse(receipts.covered(self.chat, 90))
            self.assertFalse(self.record(200, 150))
            self.assertFalse(receipts.diagnostics(self.chat)['ok'])
        self.policy.write_text('{"enabled": true}')
        receipts._CACHE.clear()
        with patch.object(Path, 'read_bytes', side_effect=OSError('private detail')):
            self.assertEqual(receipts.watermark(self.chat), 0)

    def test_accepted_outbox_write_failure_does_not_escape(self):
        entry = dict(state='accepted', kind='text', purpose='tool:reply',
                     peer_id='-700', topic_id=8, key='accepted:1',
                     receipt={'message_id': 100}, addressed_observed_message_id=90)
        original_write = os.write
        def fail_receipt(fd, data):
            if b'praxis.addressed-reply.v3' in bytes(data):
                raise OSError('disk full')
            return original_write(fd, data)
        with patch.object(receipts.os, 'write', side_effect=fail_receipt):
            self.assertFalse(receipts.record_outbox(entry))
        self.assertEqual(entry['state'], 'accepted')
        self.assertTrue(receipts.record_outbox(entry))
        self.assertFalse(receipts.record_outbox(entry))

    def test_failed_backup_never_replaces_original(self):
        self.record()
        path = receipts._path(self.chat)
        path.write_bytes(path.read_bytes() + b'{')
        damaged = path.read_bytes()
        with patch.object(receipts.os, 'fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                receipts.recover(self.chat)
        self.assertEqual(path.read_bytes(), damaged)

    def test_partial_write_then_failure_stays_recoverable(self):
        self.record()
        original_write = os.write
        failed = [False]
        def partial(fd, data):
            if b'praxis.addressed-reply.v3' in bytes(data):
                failed[0] = True
                return original_write(fd, bytes(data)[:8])
            if failed[0]:
                raise OSError('disk full')
            return original_write(fd, data)
        with patch.object(receipts.os, 'write', side_effect=partial):
            self.assertFalse(self.record(200, 150))
        self.assertEqual(receipts.watermark(self.chat), 0)
        self.assertEqual(receipts.recover(self.chat)['removed'], 1)
        self.assertTrue(self.record(200, 150))

    def test_exact_union_accepts_late_lower_id_after_restart(self):
        self.assertTrue(receipts.record(self.chat, 200, delivery_key='first',
                                       observed_message_ids=[90, 150]))
        receipts._CACHE.clear()
        self.assertFalse(receipts.covered(self.chat, 89))
        self.assertFalse(receipts.covered(self.chat, 100))
        self.assertTrue(receipts.record(self.chat, 201, delivery_key='late',
                                       observed_message_ids=[89, 90]))
        receipts._CACHE.clear()
        for mid in (89, 90, 150):
            self.assertTrue(receipts.covered(self.chat, mid))
        self.assertFalse(receipts.record(self.chat, 202, delivery_key='duplicate',
                                        observed_message_ids=[89, 150]))
        self.assertEqual(receipts.watermark(self.chat), 150)
        self.assertTrue(receipts.diagnostics(self.chat)['ok'])

    def test_v2_frontier_is_not_membership_even_after_recovery(self):
        path = receipts._path(self.chat)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(schema='praxis.addressed-reply.v2',
            chat_id=self.chat, reply_message_id=200, observed_message_id=150,
            delivery_key='legacy')) + '\n{')
        receipts.recover(self.chat)
        self.assertEqual(receipts.diagnostics(self.chat)['legacy_rows'], 1)
        for mid in (90, 150):
            self.assertFalse(receipts.covered(self.chat, mid))
        self.assertTrue(self.record(201, 90))
        self.assertTrue(receipts.covered(self.chat, 90))
        self.assertFalse(receipts.covered(self.chat, 89))

    def test_invalid_v3_membership_is_rejected_and_removed_by_recovery(self):
        self.record()
        path = receipts._path(self.chat)
        row = json.loads(path.read_bytes())
        for ids in (None, 90, ['90'], [True], [0], [], [100]):
            row['observed_message_ids'] = ids
            with path.open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        self.assertFalse(receipts.covered(self.chat, 90))
        self.assertEqual(receipts.recover(self.chat)['removed'], 7)
        self.assertTrue(receipts.covered(self.chat, 90))

    def test_outbox_exact_list_overrides_legacy_singleton(self):
        entry = dict(state='accepted', kind='text', purpose='tool:reply',
                     peer_id='-700', topic_id=8, key='accepted:1',
                     receipt={'message_id': 200}, addressed_observed_message_id=150,
                     addressed_observed_message_ids=[90, 99])
        self.assertTrue(receipts.record_outbox(entry))
        self.assertTrue(receipts.covered(self.chat, 99))
        self.assertFalse(receipts.covered(self.chat, 150))
        self.assertFalse(receipts.record_outbox(dict(entry, addressed_observed_message_ids=[])))
