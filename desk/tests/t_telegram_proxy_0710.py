"""Gateway routing, authorization, Telegram-only destinations and TCP framing."""
from __future__ import annotations
import asyncio
import importlib.util
import io
import json
import logging
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
import botapi
import telegram_proxy


class Configuration(unittest.TestCase):
    def test_only_valid_https_proxy_with_key_can_be_enabled(self):
        self.assertEqual(telegram_proxy.settings({}), ('', ''))
        for url in ('http://server', 'https://u:p@server', 'https://server?q=token', 'https://server/#key'):
            with self.assertRaises(ValueError):
                telegram_proxy.settings({'telegram': {'proxy': {'enabled': True, 'url': url, 'key': 'fixture'}}})
        self.assertEqual(telegram_proxy.settings({'telegram': {'proxy':
            {'enabled': True, 'url': 'https://gateway.invalid/helene/telegram/', 'key': 'fixture'}}}),
            ('https://gateway.invalid/helene/telegram', 'fixture'))

    def test_bot_call_keeps_token_out_of_gateway_url_and_preserves_body(self):
        client = botapi.BotClient('123:fixture', proxy_url='https://gateway.invalid/helene/telegram', proxy_key='access')
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true,"result":{"id":123}}'
        with mock.patch('urllib.request.urlopen', return_value=response) as open_url:
            self.assertEqual(client.call('getMe'), {'id': 123})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, 'https://gateway.invalid/helene/telegram/bot/getMe')
        self.assertEqual(request.get_header('X-telegram-bot-token'), '123:fixture')
        self.assertEqual(request.get_header('Authorization'), 'Bearer access')
        self.assertEqual(request.data, b'')

    def test_proxy_failure_cannot_silently_send_directly(self):
        client = botapi.BotClient('123:fixture', proxy_url='https://gateway.invalid/helene/telegram', proxy_key='access')
        with mock.patch('urllib.request.urlopen', side_effect=OSError('offline')) as open_url:
            with self.assertRaises(OSError):
                client.call('getMe')
        open_url.assert_called_once()
        self.assertIn('gateway.invalid', open_url.call_args.args[0].full_url)


class Gateway(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import aiohttp
        from aiohttp.test_utils import TestClient, TestServer
        spec = importlib.util.spec_from_file_location('tg_gateway_test', DESK / 'server/telegram_gateway.py')
        self.gateway = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.gateway)
        self.http = TestClient(TestServer(self.gateway.application('fixture-access')))
        await self.http.start_server()

    async def asyncTearDown(self):
        await self.http.close()

    async def test_health_requires_access_key(self):
        response = await self.http.get('/helene/telegram/health')
        self.assertEqual(response.status, 401)
        response = await self.http.get('/helene/telegram/health', headers={'Authorization': 'Bearer fixture-access'})
        self.assertEqual(await response.json(), {'ok': True})

    async def test_target_cannot_be_localhost_or_arbitrary_service(self):
        for host, port in (('127.0.0.1', 22), ('149.154.167.51', 22), ('169.254.169.254', 80),
                           ('example.com', 443), ('8.8.8.8', 443)):
            self.assertFalse(self.gateway.telegram_target(host, port))
        self.assertTrue(self.gateway.telegram_target('149.154.167.51', 443))
        self.assertTrue(self.gateway.telegram_target('2001:b28:f23d::1', 443))
        ws = await self.http.ws_connect('/helene/telegram/connect', headers={'Authorization': 'Bearer fixture-access'})
        with mock.patch.object(self.gateway.asyncio, 'open_connection', new_callable=mock.AsyncMock) as connect:
            await ws.send_json({'host': '127.0.0.1', 'port': 22})
            self.assertEqual(await ws.receive_json(), {'ok': False})
            connect.assert_not_called()
        await ws.close()

    async def test_telethon_gateway_stream_preserves_frames_and_releases_session(self):
        import aiohttp
        from collections import defaultdict
        from telethon.network.connection.tcpfull import FullPacketCodec
        async def echo(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(echo, '127.0.0.1', 0)
        local_port = server.sockets[0].getsockname()[1]
        original_open = asyncio.open_connection
        original_ws = aiohttp.ClientSession.ws_connect
        def open_connection(host, port, *args, **kwargs):
            if host == '149.154.167.51':
                host, port = '127.0.0.1', local_port
            return original_open(host, port, *args, **kwargs)
        def ws_connect(session, url, **kwargs):
            self.assertEqual(url, 'wss://gateway.invalid/helene/telegram/connect')
            return original_ws(session, self.http.make_url('/helene/telegram/connect'), **kwargs)
        conn = telegram_proxy.connection_type('https://gateway.invalid/helene/telegram', 'fixture-access')(
            '149.154.167.51', 443, 2, loggers=defaultdict(lambda: logging.getLogger('proxy-test')))
        try:
            with mock.patch.object(asyncio, 'open_connection', open_connection), \
                 mock.patch.object(aiohttp.ClientSession, 'ws_connect', ws_connect):
                await conn.connect(timeout=5)
                for payload in (b'first packet', b'second packet' * 100):
                    await conn.send(payload)
                    self.assertEqual(await asyncio.wait_for(conn.recv(), 5), payload)
                session = conn._writer.session
                await conn.disconnect()
                self.assertTrue(session.closed)
        finally:
            await conn.disconnect()
            server.close()
            await server.wait_closed()


class AccountLogin(unittest.IsolatedAsyncioTestCase):
    async def test_login_uses_selected_config_even_with_external_data_directory(self):
        import mtproto_login
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / 'selected/helene.json'
            cfg.parent.mkdir()
            cfg.write_text(json.dumps({'telegram': {'proxy': {'enabled': True,
                           'url': 'https://selected.invalid/helene/telegram', 'key': 'selected-fixture'}}}))
            args = types.SimpleNamespace(session=str(root/'external/telegram/account'), config=str(cfg),
                     api_id='123', api_hash='fixture', step='status', phone='', code='', password='')
            fake = types.SimpleNamespace(connect=mock.AsyncMock(), disconnect=mock.AsyncMock(),
                                         is_user_authorized=mock.AsyncMock(return_value=False))
            marker = type('SelectedGateway', (), {})
            with mock.patch('telethon.TelegramClient', return_value=fake) as constructor, \
                 mock.patch.object(telegram_proxy, 'connection_type', return_value=marker) as factory, \
                 mock.patch.object(mtproto_login, '_out'):
                await mtproto_login.main(args)
            factory.assert_called_once_with('https://selected.invalid/helene/telegram', 'selected-fixture')
            self.assertIs(constructor.call_args.kwargs['connection'], marker)


if __name__ == '__main__':
    unittest.main()
