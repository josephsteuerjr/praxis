"""Real channel/menu seams; all helper processes and external network mocked."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'localharness'))
import deskapp
import phone_access as access
from aiohttp.test_utils import TestClient, TestServer
from deskd import readers


class MenuClient:
    def __init__(self):
        self.button = {'type': 'commands'}
        self.writes = []
        self.ambiguous = False
    def call(self, method, **params):
        if method == 'getChatMenuButton': return self.button
        if method == 'getChat': return {'id': 123, 'type': 'private'}
        if method == 'setChatMenuButton':
            self.button = params['menu_button']
            self.writes.append(self.button)
            if self.ambiguous:
                self.ambiguous = False
                raise TimeoutError('ambiguous after durable remote acceptance')
            return True
        raise AssertionError(method)


class Menu(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = MenuClient()
        self.menu = access.MenuOwner(Path(self.temp.name), self.client, 123, 'test-token')
    async def test_rotation_verified_and_disable_restores_predecessor(self):
        await self.menu.publish('https://first.trycloudflare.com/', 'Мира')
        await self.menu.publish('https://second.trycloudflare.com/', 'Мира')
        self.assertEqual(self.client.button['web_app']['url'], 'https://second.trycloudflare.com/tg/')
        await self.menu.restore()
        self.assertEqual(self.client.button, {'type':'commands'})
    async def test_ambiguous_write_reconciles_without_duplicate(self):
        self.client.ambiguous = True
        with self.assertRaises(TimeoutError): await self.menu.publish('https://first.trycloudflare.com/', 'Мира')
        await self.menu.publish('https://first.trycloudflare.com/', 'Мира')
        self.assertEqual(len(self.client.writes), 1)
    async def test_foreign_menu_is_preserved(self):
        await self.menu.publish('https://first.trycloudflare.com/', 'Мира')
        other = {'type':'web_app', 'text':'Other', 'web_app':{'url':'https://other.example/'}}
        self.client.button = other
        with self.assertRaises(ValueError): await self.menu.publish('https://second.trycloudflare.com/', 'Мира')
        await self.menu.restore()
        self.assertEqual(self.client.button, other)


class Migration(unittest.TestCase):
    def test_explicit_migration_preserves_unrelated_data_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'helene.json'
            original = {'phone':{'enabled':True,'external':'https://old.example'},'model':{'key':'private'},'tree':'data'}
            path.write_text(json.dumps(original))
            self.assertTrue(access.migrate_config(path, author='deploy:test')['changed'])
            migrated = json.loads(path.read_text())
            self.assertEqual(migrated['model'], original['model'])
            self.assertNotIn('external', migrated['phone'])
            self.assertEqual(migrated['phone']['mode'], 'automatic')
            receipt = json.loads((Path(temp)/'data/memory/.state/phone-migration.json').read_text())
            self.assertEqual(receipt['before'], original['phone'])
            self.assertFalse(access.migrate_config(path, author='deploy:test')['changed'])
    def test_legacy_parse_never_enables_tunnel(self):
        self.assertFalse(access.enabled({'phone':{'enabled':True}}))
        self.assertFalse(access.enabled({'phone':{'enabled':False,'mode':'automatic'}}))
        self.assertTrue(access.enabled({'phone':{'enabled':True,'mode':'automatic'}}))


class Channel(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = Path(self.temp.name)/'helene.json'
        self.cfg.write_text(json.dumps({'phone':{'enabled':False,'mode':'automatic'}}))
        self.saved = (deskapp.TOKEN, deskapp._PHONE_ACCESS)
        deskapp.TOKEN = 'owner-test'
        self.patch = patch.dict(os.environ, {'HELENE_CONFIG':str(self.cfg),'PRAXIS_DESK_TREE':self.temp.name})
        self.patch.start()
        self.app = deskapp.build_app(port=8193)
        self.runtime = deskapp._PHONE_ACCESS
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close()
        self.patch.stop()
        deskapp.TOKEN, deskapp._PHONE_ACCESS = self.saved
    async def test_owner_only_status_and_public_nonce(self):
        self.assertEqual((await self.client.get('/api/phone')).status,403)
        response = await self.client.get('/api/phone', headers={'Authorization':'Bearer owner-test'})
        self.assertEqual(response.status,200)
        self.assertEqual(self.runtime.port,8193)
        probe = await self.client.get('/api/phone/probe')
        self.assertEqual((await probe.json())['instance'],self.runtime.instance)
    async def test_public_ready_checks_the_advertised_instance_and_http_status(self):
        self.runtime.url=str(self.client.make_url('/'))
        self.assertTrue(await self.runtime.public_ready())
        self.runtime.url=str(self.client.make_url('/missing/'))
        self.assertFalse(await self.runtime.public_ready())
    async def test_living_tunnel_with_dead_public_origin_is_stopped_for_recreation(self):
        runtime=self.runtime
        config={'phone':{'enabled':True,'mode':'automatic'}}
        runtime.load_config=lambda:config
        from unittest.mock import AsyncMock
        tunnel=type('Tunnel',(),{})()
        tunnel.start=AsyncMock();tunnel.wait_url=AsyncMock(return_value=str(self.client.make_url('/')))
        tunnel.stop=AsyncMock();tunnel.returncode=None
        tick=[0]
        def advance():tick[0]+=40;return tick[0]
        with patch.object(access,'ensure_cloudflared',new=AsyncMock(return_value=Path('/unused'))), \
             patch.object(access,'QuickTunnel',return_value=tunnel), patch.object(access.os,'getpgrp',side_effect=os.getpid,create=True), \
             patch.object(runtime,'clock',side_effect=advance), patch.object(runtime,'public_ready',new=AsyncMock(return_value=False)) as probe, \
             patch.object(runtime,'delay',new=AsyncMock()):
            with self.assertRaisesRegex(access.CloudflaredError,'Public origin'):await runtime.cycle(config)
        self.assertEqual(probe.await_count,3)
        tunnel.stop.assert_awaited_once();self.assertEqual(runtime.url,'')
    async def test_panic_requires_a_key_and_does_not_offer_remote_resume(self):
        self.assertEqual((await self.client.post('/api/panic',json={})).status,403)
        pair=deskapp._new_pair()
        redemption=await self.client.get('/pair/redeem',params={'token':pair['token']})
        key=(await redemption.json())['key']
        with patch('localharness.emergency.request',return_value={'ok':True,'latched':True,'note':'stop'}) as stop:
            response=await self.client.post('/api/panic',json={},headers={'Authorization':'Bearer '+key})
            self.assertTrue((await response.json())['latched'])
            self.assertEqual(stop.call_args.kwargs['via'],'phone')
        self.assertEqual((await self.client.post('/api/resume',json={},headers={'Authorization':'Bearer '+key})).status,403)
    async def test_telegram_binary_is_served_authenticated_by_the_real_artifact_route(self):
        folder=Path(self.temp.name)/'memory/telegram-media';folder.mkdir(parents=True)
        (folder/'sticker.json').write_bytes(b'{"v":"5.5","assets":[]}')
        (folder/'movie.webm').write_bytes(b'video-fixture')
        for name,expected in [('sticker.json',b'{"v":"5.5","assets":[]}'),('movie.webm',b'video-fixture')]:
            url='/api/artifact?path=memory/telegram-media/'+name+'&preview=1'
            self.assertEqual((await self.client.get(url)).status,403)
            response=await self.client.get(url,headers={'Authorization':'Bearer owner-test'})
            self.assertEqual(response.status,200)
            self.assertEqual(await response.read(),expected)
        response=await self.client.get('/api/artifact?path=memory/telegram-media/../../helene.json',headers={'Authorization':'Bearer owner-test'})
        self.assertEqual(response.status,404)
    async def test_exact_managed_host_and_no_wildcard(self):
        self.runtime.url = 'https://chosen.trycloudflare.com/'
        response = await self.client.get('/api/phone/probe',headers={'Host':'chosen.trycloudflare.com'})
        self.assertEqual(response.status,200)
        response = await self.client.get('/api/phone/probe',headers={'Host':'other.trycloudflare.com'})
        self.assertEqual(response.status,403)
        self.runtime.url = ''
        response = await self.client.get('/api/phone/probe',headers={'Host':'chosen.trycloudflare.com'})
        self.assertEqual(response.status,403)
    async def test_pair_unknown_after_pruning_is_not_foreign(self):
        pair = deskapp._new_pair()
        deskapp._PAIRS[pair['token']]['expires'] = 0
        deskapp._new_pair()
        response = await self.client.get('/pair/redeem',params={'token':pair['token']})
        self.assertEqual((await response.json())['code'],'pair_unknown')
    async def test_unsigned_telegram_cannot_enter(self):
        response = await self.client.post('/pair/telegram',json={'initData':'user=bad'})
        self.assertIn(response.status,(403,503))
    async def test_miniapp_pre_auth_page_has_a_route(self):
        response = await self.client.get('/tg/')
        self.assertNotEqual(response.status,403)
        self.assertNotEqual(response.status,404)
    async def test_phone_websocket_is_authenticated_and_events_arrive(self):
        ws = await self.client.ws_connect('/tunnel',headers={'Authorization':'Bearer owner-test'})
        self.assertIn('hello',await ws.receive_json())
        for queue in list(deskapp._SSE_CLIENTS):
            await queue.put({'t':'health'})
        self.assertEqual((await ws.receive_json())['event']['t'],'health')
        await ws.close()


if __name__ == '__main__': unittest.main(verbosity=2)
