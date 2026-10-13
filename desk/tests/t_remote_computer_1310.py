"""Actual HTTP/WebSocket relay acceptance, with no user desktop or external host."""
import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from aiohttp import web, ClientSession
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deskd.remote_computer import Relay, PREFIX, body_route


class RemoteRelay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.relay = Relay(Path(self.tmp.name))
        self.pair = self.relay.pair('Fixture Windows', ['computer.read'])
        self.device = self.pair['id']
        self.relay.select(self.device)
        app = web.Application()
        app.router.add_get(PREFIX + '/connect/{device}', self.relay.connect)
        app.on_shutdown.append(self.relay.close)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{port}{PREFIX}/connect/{self.device}'
        self.http = ClientSession()
        self.addAsyncCleanup(self.runner.cleanup)
        self.addAsyncCleanup(self.http.close)

    async def connect(self):
        ws = await self.http.ws_connect(self.url, headers={'Authorization': 'Bearer ' + self.pair['token']})
        await ws.send_json({'kind': 'hello', 'identity': {'platform': 'windows', 'name': 'Fixture Windows', 'scopes':['computer.read']}})
        ready = await ws.receive_json(timeout=2)
        self.assertEqual(ready['device'], self.device)
        self.addAsyncCleanup(ws.close)
        return ws

    async def test_native_bridge_rpc_response_is_bound_to_selected_device(self):
        ws = await self.connect()
        path = f'/v1/controller/{self.device}/invoke'
        call = asyncio.create_task(self.relay.rpc('POST', path, b'{"capability":"body.status"}', 'application/json', timeout=2))
        request = await ws.receive_json(timeout=2)
        self.assertEqual(base64.b64decode(request['body']), b'{"capability":"body.status"}')
        self.assertEqual(request['path'], path)
        await ws.send_json({'kind': 'response', 'id': request['id'], 'status': 200,
                            'body': base64.b64encode(b'{"ok":true}').decode(), 'content_type': 'application/json'})
        self.assertEqual(await call, (200, b'{"ok":true}', 'application/json', {}))

    async def test_revocation_cancels_pending_and_the_key_cannot_reconnect(self):
        ws = await self.connect()
        call = asyncio.create_task(self.relay.rpc('POST', f'/v1/controller/{self.device}/invoke', b'{"capability":"body.status"}', 'application/json', timeout=2))
        await ws.receive_json(timeout=2)
        await self.relay.revoke(self.device)
        with self.assertRaises(ConnectionError):
            await call
        self.assertFalse(self.relay.authenticate(self.device, self.pair['token']))
        self.assertEqual(self.relay.snapshot()['selected'], '')

    async def test_wrong_key_and_another_device_key_are_rejected(self):
        other = self.relay.pair('Other Windows', [])
        for key in ('wrong-key', other['token']):
            with self.assertRaises(Exception) as caught:
                await self.http.ws_connect(self.url, headers={'Authorization': 'Bearer ' + key})
            self.assertEqual(caught.exception.status, 401)
        public = self.relay.snapshot()
        self.assertNotIn('key_hash', json.dumps(public))
        self.assertNotIn(self.pair['token'], json.dumps(public))

    async def test_disconnect_fails_without_replaying_mutations(self):
        ws = await self.connect()
        call = asyncio.create_task(self.relay.rpc('POST', f'/v1/controller/{self.device}/invoke', b'{"capability":"body.status"}', 'application/json', timeout=2))
        await ws.receive_json(timeout=2)
        await ws.close()
        with self.assertRaises(ConnectionError):
            await call
        await self.connect()
        self.assertEqual(self.relay.pending, {})

    async def test_wrong_host_traversal_controller_and_method_never_leave_server(self):
        await self.connect()
        for method, path in [('GET', 'http://127.0.0.1:9999/private'),
                             ('POST', '/v1/controller/' + 'a'*32 + '/invoke'),
                             ('DELETE', f'/v1/controller/{self.device}/invoke'),
                             ('POST', f'/v1/controller/{self.device}/../invoke'),
                             ('GET', '/v1/artifacts/' + 'a'*64 + '/content?url=http://evil')]:
            with self.assertRaises(ValueError):
                await self.relay.rpc(method, path, b'', 'application/json', timeout=1)
        self.assertFalse(self.relay.pending)

    async def test_unconfirmed_identity_and_offline_device_cannot_execute(self):
        with self.assertRaises(ConnectionError):
            await self.relay.rpc('POST', f'/v1/controller/{self.device}/invoke', b'{"capability":"body.status"}', 'application/json', timeout=1)
        ws = await self.http.ws_connect(self.url, headers={'Authorization': 'Bearer ' + self.pair['token']})
        self.addAsyncCleanup(ws.close)
        with self.assertRaises(ConnectionError):
            await self.relay.rpc('POST', f'/v1/controller/{self.device}/invoke', b'{"capability":"body.status"}', 'application/json', timeout=1)

    async def test_late_old_connection_does_not_remove_replacement(self):
        first = object(); second = object()
        self.relay.connections[self.device] = second
        self.relay.drop(self.device, first)
        self.assertIs(self.relay.connections[self.device], second)
        self.relay.connections.clear()

    async def test_server_pair_and_actual_desktop_scopes_both_bound_admission(self):
        await self.connect()
        path=f'/v1/controller/{self.device}/invoke'
        for cap in ('desktop.window.read','process.start','fs.read','desktop.unknown_admin'):
            with self.assertRaises(PermissionError):
                await self.relay.rpc('POST',path,json.dumps({'capability':cap}).encode(),'application/json',timeout=1)
        self.assertFalse(self.relay.pending)

if __name__ == '__main__':
    unittest.main()
