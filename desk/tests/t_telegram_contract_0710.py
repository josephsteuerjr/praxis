"""Telegram intake, addressing and offline recovery, without external traffic."""
from __future__ import annotations

import asyncio
import json
import queue
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
import botapi
import mtproto
import runner


class Contract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.life = types.SimpleNamespace(record_message=mock.Mock())

    def transport(self, cls=botapi.BotTransport):
        obj = cls.__new__(cls)
        botapi.BotTransport.__init__(obj, types.SimpleNamespace(), self.root, self.life, {
            'agent': {'name': 'Лира'},
            'telegram': {'bot_token': 'fixture', 'owner_id': '11',
                         'allow_from': 'listed', 'allowed_ids': ['22']},
        })
        obj.me = {'id': 99, 'username': 'lyra_fixture', 'first_name': 'Лирея'}
        obj.username = 'lyra_fixture'
        obj.client = mock.Mock()
        return obj

    def message(self, text='', **fields):
        return {'message_id': 7, 'date': 1700000000, 'text': text,
                'from': {'id': 11, 'first_name': 'Owner'},
                'chat': {'id': -100777, 'type': 'supergroup', 'title': 'Room'}, **fields}

    def test_current_names_handle_and_reply_with_word_boundaries(self):
        for cls in (botapi.BotTransport, mtproto.MtprotoTransport):
            obj = self.transport(cls)
            for text in ('Лира, прочти', 'лирея!', '@LYRA_FIXTURE привет', '/go@lyra_fixture'):
                self.assertTrue(obj._addressed(self.message(text)), text)
            for text in ('лираграф', 'суперЛира', '@lyra_fixture_other', '@Лира',
                         'x@lyra_fixture', '@other_fixture привет', 'Праксис', 'Джарвис'):
                self.assertFalse(obj._addressed(self.message(text)), text)
            self.assertTrue(obj._addressed(self.message(caption='Лира, фото', photo=[{}])))
            self.assertTrue(obj._addressed(self.message(reply_to_message={'from': {'id': 99}})))
            self.assertFalse(obj._addressed(self.message(reply_to_message={'from': {'id': 100}})))
            obj.me = {}
            self.assertFalse(obj._addressed(self.message(reply_to_message={})))

    def test_renaming_replaces_old_names_handle_and_id(self):
        obj = self.transport()
        obj.set_agent_name('Веста')
        obj.client.call.return_value = {'id': 100, 'username': 'vesta_fixture', 'first_name': 'Вестея'}
        obj.refresh_identity()
        for text in ('Лира', 'Лирея', '@lyra_fixture'):
            self.assertFalse(obj._addressed(self.message(text)), text)
        for text in ('Веста', 'Вестея', '@vesta_fixture'):
            self.assertTrue(obj._addressed(self.message(text)), text)
        self.assertFalse(obj._addressed(self.message(reply_to_message={'from': {'id': 99}})))
        self.assertTrue(obj._addressed(self.message(reply_to_message={'from': {'id': 100}})))

    def test_passive_words_are_context_for_following_addressed_topic_turn(self):
        obj = self.transport()
        topic = {'is_topic_message': True, 'message_thread_id': 19}
        obj._ingest({'message': self.message('previous context', **topic)})
        self.assertIsNone(obj.pop_pending())
        obj._ingest({'message': self.message('Лира, что было выше?', message_id=8, **topic)})
        conversation = obj.pop_pending()
        self.assertEqual(conversation, '-100777__topic__19')
        self.assertIn('Owner: previous context', obj.rooms.lines(conversation))
        self.assertEqual(obj.wake_sender(conversation), ('Owner', '11'))

    def test_passive_unadmitted_sender_does_not_replace_turn_authority(self):
        obj = self.transport()
        obj._ingest({'message': self.message('Лира, прочти')})
        obj._ingest({'message': self.message('Лира, чужая просьба', message_id=8,
                                             **{'from': {'id': 33, 'first_name': 'Guest'}})})
        self.assertEqual(obj.wake_sender(obj.pop_pending()), ('Owner', '11'))
        self.assertIn('Guest: Лира, чужая просьба', obj.rooms.lines('-100777'))
        self.assertIsNone(obj.pop_pending())

    def test_start_is_nonblocking_and_idempotent(self):
        obj = self.transport()
        with mock.patch.object(botapi.threading, 'Thread') as make:
            make.return_value.is_alive.return_value = True
            obj.start()
            obj.start()
        obj.client.call.assert_not_called()
        make.assert_called_once()
        make.return_value.start.assert_called_once()
        obj.stop()
        self.assertFalse(obj.connected())

    def test_offline_first_start_and_later_break_keep_offset_and_one_receiver(self):
        obj = self.transport()
        obj.me = {}
        obj._save_offset(41)
        attempts = {'getMe': 0, 'getUpdates': 0}
        offsets = []

        def call(method, **params):
            attempts[method] += 1
            if method == 'getMe':
                if attempts[method] <= 2:
                    raise OSError('offline')
                return {'id': 99, 'username': 'lyra_fixture', 'first_name': 'Лирея'}
            offsets.append(params.get('offset'))
            if attempts[method] == 1:
                return [{'update_id': 55, 'message': self.message('passive before reconnect')}]
            if attempts[method] == 2:
                raise ConnectionError('VPN off')
            obj._stop.set()
            return []

        obj.client.call.side_effect = call
        with mock.patch.object(obj._stop, 'wait', return_value=False):
            obj._poll_forever()
        self.assertEqual(offsets, [41, 56, 56])
        self.assertEqual(obj._load_offset(), 56)
        self.assertEqual(attempts['getMe'], 4)
        self.assertIn('Owner: passive before reconnect', obj.rooms.lines('-100777'))
        self.assertIsNone(obj.pop_pending())

    def test_record_failure_is_retried_without_acknowledging_or_dropping_input(self):
        obj = self.transport()
        obj.client.call.return_value = obj.me
        obj._save_offset(41)
        calls = []

        def call(method, **params):
            if method == 'getMe':
                return obj.me
            calls.append(params.get('offset'))
            if len(calls) > 6:
                obj._stop.set()
                return []
            return [{'update_id': 55, 'message': self.message('save me')}]

        obj.client.call.side_effect = call
        with mock.patch.object(obj, '_ingest', side_effect=OSError('disk unavailable')), \
             mock.patch.object(obj._stop, 'wait', return_value=False), self.assertLogs('frame.botapi'):
            obj._poll_forever()
        self.assertEqual(calls, [41] * 7)
        self.assertEqual(obj._load_offset(), 41)

    def test_account_start_retries_in_shared_receiving_thread(self):
        obj = self.transport(mtproto.MtprotoTransport)
        obj.client.start.side_effect = [ConnectionError('offline'), None]

        def call(method, **params):
            if method == 'getMe':
                return obj.me
            obj._stop.set()
            return []

        obj.client.call.side_effect = call
        with mock.patch.object(obj._stop, 'wait', return_value=False):
            obj._poll_forever()
        self.assertEqual(obj.client.start.call_count, 2)
        self.assertEqual(obj._load_offset(), 0)

    def test_health_and_route_are_independent_of_model_readiness(self):
        for cls, route in ((botapi.BotTransport, 'Bot API'), (mtproto.MtprotoTransport, 'Telethon')):
            obj = self.transport(cls)
            with mock.patch.object(runner, '_bot', obj), mock.patch.object(runner, '_tree', None):
                line = runner._orient('window')
            self.assertIn(route, line)
            self.assertIn('приём ещё не подтверждён', line)
            self.assertIn('@lyra_fixture', line)


class AccountAdapter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = mtproto.MtprotoClient.__new__(mtproto.MtprotoClient)
        self.client._seq = 0
        self.client._queue = queue.Queue()
        self.client._batch = []
        self.client._handler_installed = False
        self.client.client = mock.Mock()
        tltypes = types.ModuleType('telethon.tl.types')
        tltypes.Channel = type('Channel', (), {})
        tltypes.MessageActionTopicCreate = type('MessageActionTopicCreate', (), {})
        events = types.SimpleNamespace(NewMessage=mock.Mock(), MessageEdited=mock.Mock(), MessageDeleted=mock.Mock())
        self.modules = mock.patch.dict(sys.modules, {'telethon.tl.types': tltypes,
                                                     'telethon': types.SimpleNamespace(events=events)})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def event(self, own_reply=True, media=False):
        m = types.SimpleNamespace(id=7, date=None, message='caption' if media else 'hello',
                                  media=object() if media else None, action=None, sender_id=11,
                                  photo=object() if media else None,
                                  reply_to=types.SimpleNamespace(forum_topic=True,
                                        reply_to_top_id=19, reply_to_msg_id=6),
                                  get_reply_message=mock.AsyncMock(return_value=
                                        types.SimpleNamespace(sender_id=99 if own_reply else 100)))
        return types.SimpleNamespace(message=m, is_private=False, chat_id=-100777,
                    get_chat=mock.AsyncMock(return_value=types.SimpleNamespace(title='Room')),
                    get_sender=mock.AsyncMock(return_value=types.SimpleNamespace(id=11, first_name='Owner')))

    async def test_account_reply_carries_actual_author_in_its_topic(self):
        for own in (True, False):
            update = await self.client._to_update(self.event(own))
            m = update['message']
            self.assertEqual(m['message_thread_id'], 19)
            self.assertEqual(m['reply_to_message']['from']['id'], 99 if own else 100)
            obj = botapi.BotTransport.__new__(botapi.BotTransport)
            obj.me = {'id': 99}
            obj.username = ''
            self.assertEqual(obj._addressed(m), own)

    async def test_caption_and_captionless_media_are_preserved(self):
        event = self.event(media=True)
        update = await self.client._to_update(event)
        self.assertIn('caption', botapi._placeholder(update['message']))
        event.message.message = ''
        update = await self.client._to_update(event)
        self.assertEqual(botapi._placeholder(update['message']), '[фото]')

    async def test_failed_entity_and_reply_lookup_preserves_words_and_sender_id(self):
        event = self.event()
        event.get_chat.side_effect = ConnectionError('offline')
        event.get_sender.side_effect = ConnectionError('offline')
        event.message.get_reply_message.side_effect = ConnectionError('offline')
        update = await self.client._to_update(event)
        self.assertEqual(update['message']['text'], 'hello')
        self.assertEqual(update['message']['from']['id'], 11)
        self.assertNotIn('from', update['message']['reply_to_message'])

    async def test_retrying_account_connection_installs_one_handler(self):
        api = self.client.client
        api.is_connected.return_value = False
        api.disconnect = mock.AsyncMock()
        api.connect = mock.AsyncMock(side_effect=[OSError('offline'), None])
        api.is_user_authorized = mock.AsyncMock(return_value=True)
        api.get_me = mock.AsyncMock(return_value=types.SimpleNamespace(id=99))
        api.catch_up = mock.AsyncMock()
        with self.assertRaises(OSError):
            await self.client._connect()
        await self.client._connect()
        self.assertEqual(api.add_event_handler.call_count, 3)
        self.assertEqual([call.args[0] for call in api.add_event_handler.call_args_list],
                         [self.client._on_message, self.client._on_edit, self.client._on_delete])
        api.catch_up.assert_awaited_once()

    async def test_empty_queue_is_not_connection_health(self):
        self.client.client.is_connected.return_value = False
        with self.assertRaises(ConnectionError):
            self.client.call('getUpdates', timeout=1)

    async def test_adapter_replays_batch_until_offset_acknowledges_it(self):
        self.client._queue.put({'update_id': 1, 'message': {'text': 'one'}})
        first = self.client._updates(.001)
        self.assertEqual(self.client._updates(.001), first)
        self.client._queue.put({'update_id': 2, 'message': {'text': 'two'}})
        self.assertEqual(self.client._updates(.001, 2)[0]['message']['text'], 'two')


if __name__ == '__main__':
    # Windows asyncio creates a loopback socketpair for its wakeup pipe.
    original_connect = __import__('socket').socket.connect
    def connect(sock, address):
        if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
            return original_connect(sock, address)
        raise AssertionError('External network forbidden')
    with mock.patch('socket.create_connection', side_effect=AssertionError('Network forbidden')), \
         mock.patch('socket.socket.connect', connect):
        unittest.main()
