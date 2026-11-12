"""Bot API retry outcomes: receipt replay, ambiguity, explicit refusal, 429."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'localharness')]
from telegram_receipts import once,UnknownDelivery,BotApiDeliveryRefused
from botapi import BotApiError
from localharness import package_notice


class Receipts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.tree=Path(self.temp.name)
        self.identity={'peer':'42','sha256':'content','bot':'identity'}

    def send(self,operation,identity=None):
        return once(self.tree,'same-key',identity or self.identity,operation)

    def test_ack_replays_and_changed_content_is_rejected(self):
        operation=mock.Mock(return_value={'message_id':44,'date':100,'text':'not stored'})
        self.assertEqual(self.send(operation),{'message_id':44,'date':100})
        self.assertEqual(self.send(operation)['message_id'],44)
        self.assertEqual(operation.call_count,1)
        with self.assertRaises(UnknownDelivery): self.send(operation,dict(self.identity,sha256='other'))
        raw=next(self.tree.rglob('*.json')).read_text()
        self.assertNotIn('not stored',raw)

    def test_timeout_and_process_death_cannot_blindly_resend(self):
        for failure in (TimeoutError('uncertain'),KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                operation=mock.Mock(side_effect=failure)
                key=type(failure).__name__
                with self.assertRaises((UnknownDelivery,KeyboardInterrupt)):
                    once(self.tree,key,self.identity,operation)
                with self.assertRaises(UnknownDelivery): once(self.tree,key,self.identity,operation)
                self.assertEqual(operation.call_count,1)

    def test_crash_writing_ack_leaves_unknown_not_false_success(self):
        real=package_notice.write
        def write(path,row):
            if row['state']=='accepted': raise OSError('disk failure after HTTP')
            real(path,row)
        operation=mock.Mock(return_value={'message_id':44})
        with mock.patch.object(package_notice,'write',side_effect=write):
            with self.assertRaises(OSError): self.send(operation)
        with self.assertRaises(UnknownDelivery): self.send(operation)
        self.assertEqual(operation.call_count,1)

    def test_reentrant_claim_never_sends_twice(self):
        second=mock.Mock()
        def operation():
            with self.assertRaises(UnknownDelivery): self.send(second)
            return {'message_id':44}
        self.send(operation);second.assert_not_called()

    def test_explicit_flood_wait_is_retryable(self):
        operation=mock.Mock(side_effect=[BotApiError('sendMessage',429,'wait',retry_after=1),{'message_id':44}])
        with self.assertRaises(BotApiError): self.send(operation)
        self.assertEqual(self.send(operation)['message_id'],44)
        self.assertEqual(operation.call_count,2)

    def test_permanent_refusal_and_corrupt_receipt_fail_closed(self):
        operation=mock.Mock(side_effect=BotApiError('sendMessage',403,'blocked'))
        with self.assertRaises(BotApiError): self.send(operation)
        with self.assertRaises(BotApiDeliveryRefused): self.send(operation)
        self.assertEqual(operation.call_count,1)
        next(self.tree.rglob('*.json')).write_text('{torn',encoding='utf-8')
        with self.assertRaises(UnknownDelivery): self.send(operation)
        self.assertEqual(operation.call_count,1)


if __name__=='__main__': unittest.main()
