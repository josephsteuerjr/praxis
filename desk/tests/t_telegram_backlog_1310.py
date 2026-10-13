"""Real ingress recovery: archived missed mentions, fresh wake, owner panic."""
import time
import unittest
import sys
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parent))
from t_telegram_contract_0710 import Contract as Fixtures
import botapi


class BacklogRecovery(unittest.TestCase):
    setUp = Fixtures.setUp
    transport = Fixtures.transport
    message = Fixtures.message
    def test_missed_mentions_and_dm_retained_without_model_wakes(self):
        obj = self.transport()
        boundary = time.time() - 30
        for mid, chat, text in [(31, '-100777', 'Лира, старый вопрос'),
                                (32, '-100777', 'Лира, ещё старый вопрос'),
                                (33, '7', 'Старая личка')]:
            message = self.message(text, chat={'id': int(chat), 'type': 'private' if int(chat)>0 else 'supergroup'}, message_id=mid)
            message['date'] = int(boundary - 3600)
            update = {'update_id': mid, 'message': message}
            obj._ingest(botapi._catchup_update(update, boundary))
            self.assertNotIn('_catchup', update)
        self.assertIsNone(obj.pop_pending())

        self.assertIn('Owner: Лира, старый вопрос', obj.rooms.lines('-100777'))
        self.assertIn('Owner: Лира, ещё старый вопрос', obj.rooms.lines('-100777'))
        self.assertTrue(obj.rooms.lines('7'))
        fresh = self.message('Лира, сейчас', message_id=34, date=int(time.time()))
        obj._ingest(botapi._catchup_update({'message': fresh}, boundary))
        self.assertEqual(obj.pop_pending(), '-100777')
        self.assertIsNone(obj.pop_pending())

    def test_connection_boundary_stays_fixed_while_backlog_is_slow(self):
        boundary = 1000
        message = self.message('Лира, новое', message_id=35)
        message['date'] = 1001
        with mock.patch.object(botapi.time, 'time', return_value=10000):
            self.assertNotIn('_catchup', botapi._catchup_update({'message': message}, boundary))

    def test_delayed_owner_panic_still_controls_but_historical_panic_does_not(self):
        obj = self.transport()
        obj.owner_control = mock.Mock()
        message = self.message('/panic', message_id=36)
        message['date'] = 1
        update = botapi._catchup_update({'message': message}, time.time() - 30)
        obj._ingest(update)
        obj.owner_control.assert_called_once()
        obj._ingest({**update, '_history': True})
        obj.owner_control.assert_called_once()
        self.assertIsNone(obj.pop_pending())

    def test_reconnect_poll_archives_old_before_advancing_offset(self):
        obj = self.transport()
        old = self.message('Лира, пропущено', message_id=37)
        old['date'] = int(time.time() - 3600)
        fresh = self.message('Лира, свежее', message_id=38, date=int(time.time()))
        received = []
        def call(method, **kwargs):
            if method == 'getMe':
                return {'id': 99, 'username': 'lira_bot'}
            if method == 'getUpdates':
                obj._stop.set()
                return [{'update_id': 70, 'message': old}, {'update_id': 71, 'message': fresh}]
            raise AssertionError(method)
        original = obj._ingest
        def ingest(update):
            received.append(update.get('_catchup', False))
            original(update)
        obj.client.call = call
        obj._ingest = ingest
        obj._poll_forever()
        self.assertEqual(received, [True, False])
        self.assertEqual(obj._load_offset(), 72)
        self.assertIn('Owner: Лира, пропущено', obj.rooms.lines('-100777'))
        self.assertEqual(obj.pop_pending(), '-100777')
        self.assertIsNone(obj.pop_pending())

if __name__ == '__main__':
    unittest.main()
