"""Received bot messages remain context; addressing only controls waking."""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
import botapi
import mtproto


class PassiveBotIntake(unittest.TestCase):
    def make_transport(self, cls, root):
        life = types.SimpleNamespace(record_message=mock.Mock())
        transport = cls.__new__(cls)
        botapi.BotTransport.__init__(transport, types.SimpleNamespace(), root, life, {
            'agent': {'name': 'Probe'},
            'telegram': {'bot_token': 'test-only', 'owner_id': '11',
                         'allow_from': 'listed', 'allowed_ids': ['22']},
        })
        transport.me = {'id': 99, 'is_bot': True}
        transport.username = 'probe'
        transport.on_incoming = mock.Mock()
        return transport, life

    def message(self, text='ordinary group context', *, sender=22, is_bot=True, topic=None):
        message = {'message_id': 42, 'date': 1700000000, 'text': text,
                   'chat': {'id': -100777, 'type': 'supergroup', 'title': 'Room'},
                   'from': {'id': sender, 'is_bot': is_bot, 'first_name': 'Peer'}}
        if topic is not None:
            message.update(is_topic_message=True, message_thread_id=topic)
        return message

    def assert_archived(self, transport, life, conversation):
        archive = transport.tree / 'memory' / 'groups' / (conversation + '.jsonl')
        rows = [json.loads(line) for line in archive.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sender_name'], 'Peer')
        self.assertFalse(rows[0]['outgoing'])
        life.record_message.assert_called_once()
        self.assertEqual(life.record_message.call_args.args[0], conversation)
        self.assertEqual(life.record_message.call_args.kwargs['source_id'], '42')
        return rows[0]

    def cases(self):
        return (botapi.BotTransport, mtproto.MtprotoTransport)

    def test_passive_human_and_bot_text_is_archived_without_wake_or_notice(self):
        for cls in self.cases():
            for is_bot in (False, True):
                with self.subTest(transport=cls.__name__, is_bot=is_bot), tempfile.TemporaryDirectory() as tmp:
                    transport, life = self.make_transport(cls, Path(tmp))
                    transport._ingest({'message': self.message(is_bot=is_bot)})
                    row = self.assert_archived(transport, life, '-100777')
                    self.assertEqual(row['text'], 'ordinary group context')
                    self.assertEqual(transport.rooms.lines('-100777'), ['Peer: ordinary group context'])
                    self.assertEqual(list(transport._pending), [])
                    transport.on_incoming.assert_not_called()

    def test_passive_bot_document_preserves_caption_and_topic_without_wake(self):
        for cls in self.cases():
            with self.subTest(transport=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                transport, life = self.make_transport(cls, Path(tmp))
                message = self.message(text='', topic=19)
                message.update(caption='Shared memo', document={'file_id': 'test-file', 'file_name': 'memo.txt'})
                transport._ingest({'message': message})
                row = self.assert_archived(transport, life, '-100777__topic__19')
                self.assertIn('Shared memo', row['text'])
                self.assertIn('memo.txt', row['text'])
                self.assertEqual(list(transport._pending), [])
                transport.on_incoming.assert_not_called()

    def test_allowed_addressed_bot_message_is_archived_and_can_wake(self):
        for cls in self.cases():
            with self.subTest(transport=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                transport, life = self.make_transport(cls, Path(tmp))
                transport._ingest({'message': self.message('@probe please look')})
                self.assert_archived(transport, life, '-100777')
                self.assertEqual(list(transport._pending), ['-100777'])
                transport.on_incoming.assert_called_once()
                self.assertEqual(transport.on_incoming.call_args.kwargs['sender_id'], '22')

    def test_unadmitted_addressed_bot_is_archived_without_wake(self):
        for cls in self.cases():
            with self.subTest(transport=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                transport, life = self.make_transport(cls, Path(tmp))
                transport._ingest({'message': self.message('@probe please look', sender=33)})
                self.assert_archived(transport, life, '-100777')
                self.assertEqual(list(transport._pending), [])
                transport.on_incoming.assert_not_called()

    def test_verified_self_echo_is_not_recorded_twice_or_woken(self):
        for cls in self.cases():
            with self.subTest(transport=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                transport, life = self.make_transport(cls, Path(tmp))
                transport._ingest({'message': self.message('@probe echo', sender=99)})
                self.assertFalse((Path(tmp) / 'memory' / 'groups').exists())
                life.record_message.assert_not_called()
                self.assertEqual(list(transport._pending), [])
                transport.on_incoming.assert_not_called()


if __name__ == '__main__':
    unittest.main()
