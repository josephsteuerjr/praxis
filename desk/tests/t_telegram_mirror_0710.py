"""The local archive mirrors real Telegram IDs, backfill, edits and own posts."""
from __future__ import annotations
import json
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t_telegram_contract_0710 as fixtures


class Mirror(unittest.TestCase):
    setUp = fixtures.Contract.setUp
    transport = fixtures.Contract.transport
    message = fixtures.Contract.message
    def test_outgoing_reply_keeps_original_excerpt_from_canonical_archive(self):
        obj = self.transport()
        obj._ingest({'message': self.message('Original words', message_id=7)})
        obj.rooms.record('-100777', 'Agent reply', outgoing=True, source_id='8', reply={'message_id':7})
        archive = self.root / 'memory/groups/-100777.jsonl'
        rows = [json.loads(line) for line in archive.read_text('utf-8').splitlines()]
        reply = next(row for row in rows if row.get('source_message_id') == '8')
        self.assertEqual(reply['reply_to_text'], 'Original words')
        self.assertEqual(reply['reply_to_sender_name'], rows[0]['sender_name'])

    def test_repeated_update_and_edit_keep_one_archived_message(self):
        obj = self.transport()
        obj._ingest({'message': self.message('first')})
        obj._ingest({'message': self.message('first')})
        obj._ingest({'edited_message': self.message('corrected')})
        archive = self.root / 'memory/groups/-100777.jsonl'
        rows = [json.loads(line) for line in archive.read_text('utf-8').splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source_message_id'], '7')
        self.assertEqual(rows[0]['text'], 'corrected')
        self.assertIsNone(obj.pop_pending())

    def test_backfill_inserts_older_post_in_order_without_waking(self):
        obj = self.transport()
        obj._ingest({'message': self.message('later', message_id=9, date=1700000100)})
        obj._ingest({'message': self.message('Лира, older', message_id=8), '_history': True})
        self.assertEqual(obj.rooms.lines('-100777'), ['Owner: Лира, older', 'Owner: later'])
        self.assertIsNone(obj.pop_pending())

    def test_own_remote_post_and_local_send_echo_are_archived_once_without_waking(self):
        obj = self.transport()
        obj.rooms.record('-100777', 'from agent', outgoing=True, source_id='7', ts=1700000000)
        own = self.message('from agent', **{'from': {'id': 99, 'first_name': 'Лирея'}})
        obj._ingest({'message': own, '_outgoing': True})
        obj._ingest({'message': {**own, 'message_id': 8, 'text': 'from another device'}, '_outgoing': True})
        self.assertEqual(obj.rooms.lines('-100777'), ['Лира: from agent', 'Лира: from another device'])
        self.assertIsNone(obj.pop_pending())

    def test_deleted_post_disappears_from_model_and_window_dialogue(self):
        from deskd import readers
        obj = self.transport()
        obj._ingest({'message': self.message('remove me')})
        obj._ingest({'_peer': '-100777', '_deleted': [7]})
        self.assertEqual(obj.rooms.lines('-100777'), [])
        with mock.patch.object(readers, 'tree', return_value=self.root), \
             mock.patch.object(readers, 'chats', return_value=[
                 {'peer_id': '-100777', 'archive': 'memory/groups/-100777.jsonl'}]):
            self.assertEqual(readers.chat_tail('-100777'), [])


class History(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AccountAdapter.asyncSetUp
    event = fixtures.AccountAdapter.event
    async def test_gap_fetch_uses_persisted_telegram_ids_and_is_passive(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'memory/groups/-100777__topic__19.jsonl'
            archive.parent.mkdir(parents=True)
            archive.write_text(json.dumps({'source_message_id': '7'}) + '\n')
            event = self.event()
            event.message.id = 8
            event.message.get_sender = event.get_sender
            dialog = types.SimpleNamespace(id=-100777, is_user=False, entity=types.SimpleNamespace(title='Room'))
            async def dialogs():
                yield dialog
            options = []
            async def messages(entity, **kwargs):
                options.append(kwargs)
                yield event.message
            self.client.client.iter_dialogs = dialogs
            self.client.client.iter_messages = messages
            ingest = mock.Mock()
            await self.client.sync_history(root, ingest, 100, lambda: False)
            self.assertEqual(options, [{'min_id': 7, 'reverse': True}])
            self.assertTrue(ingest.call_args.args[0]['_history'])
            self.assertEqual(ingest.call_args.args[0]['message']['message_id'], 8)
            event.message.get_reply_message.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
