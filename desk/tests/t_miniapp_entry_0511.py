"""Mini App registration and real BotClient HTTP capture; no Telegram traffic."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
import botapi
import deskapp
import phone_access
import phone_entry


class Entry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.tree = Path(self.temp.name)
        self.config = {'phone': {'enabled': True, 'mode': 'automatic'},
                       'telegram': {'bot_token': 'fixture:token', 'owner_id': '123'}, 'agent': {'name': 'Мира'}}
        self.bot = SimpleNamespace(tree=self.tree, _cfg=self.config, owner_id='123', username='FixtureBot', client=Mock())
        self.runtime = phone_access.PhoneAccess(self.tree / 'memory/.state/phone-access', lambda:self.config, 8094, owner_token=True)
        self.runtime.menu = SimpleNamespace(identity=phone_entry.identity('fixture:token', '123'))
        self.runtime.url = 'https://first.trycloudflare.com/'
        self.register()

    def register(self): self.runtime.record('ready', 'fixture', telegram='ready')
    def message(self, text='/app', **changes):
        msg = {'chat': {'id':123,'type':'private'}, 'from':{'id':123}, 'message_id':1, 'date':time.time(), 'text':text}
        msg.update(changes); return msg
    def url(self): return self.bot.client.call.call_args.kwargs['reply_markup']['inline_keyboard'][0][0]['web_app']['url']

    def test_rotation_resolves_new_button_and_scopes_each_agent(self):
        self.assertTrue(phone_entry.handle(self.bot, {}, self.message()))
        self.assertEqual(self.url(), 'https://first.trycloudflare.com/tg/')
        self.runtime.url = 'https://second.trycloudflare.com/'; self.register()
        phone_entry.handle(self.bot, {}, self.message('/start helene',message_id=2))
        self.assertEqual(self.url(), 'https://second.trycloudflare.com/tg/')
        self.assertNotIn('token', self.url())
        self.assertNotIn('123', self.url())
        self.config['telegram']['bot_token'] = 'other-bot'
        self.assertEqual(phone_entry.current(self.tree,self.config), '')
        self.config['telegram']['bot_token'] = 'fixture:token'; self.config['telegram']['owner_id']='124'
        self.assertEqual(phone_entry.current(self.tree,self.config), '')
        self.assertEqual(phone_entry.current(self.tree/'other-agent',self.config), '')

    def test_stale_disabled_offline_and_future_registration_never_issue_button(self):
        for at in [time.time()-91,time.time()+5]:
            path=self.runtime.root/'status.json'; row=json.loads(path.read_text(encoding='utf-8'));row['at']=at
            phone_access.atomic_json(path,row)
            self.assertEqual(phone_entry.current(self.tree,self.config), '')
        self.register();self.runtime.record('retry','offline')
        phone_entry.handle(self.bot, {}, self.message())
        self.assertNotIn('reply_markup',self.bot.client.call.call_args.kwargs)
        self.register();self.config['phone']['enabled']=False
        self.assertEqual(phone_entry.current(self.tree,self.config), '')

    def test_only_fresh_owner_dm_and_correct_bot_can_request_entry(self):
        variants=[({'_catchup':True},{}),({'_history':True},{}),({'edited_message':{}},{}),
                  ({},{'from':{'id':124}}),({},{'chat':{'id':-1,'type':'group'}}),
                  ({},{'date':time.time()-121}),({},{'date':time.time()+10}),({},{'text':'/app@OtherBot'})]
        for update,changes in variants:
            with self.subTest(update=update,changes=changes):
                phone_entry.handle(self.bot,update,self.message(**changes))
        self.bot.client.call.assert_not_called()

    def test_duplicate_and_ambiguous_send_are_not_replayed(self):
        self.bot.client.call.side_effect=TimeoutError('fixture accepted remotely')
        self.assertTrue(phone_entry.handle(self.bot,{},self.message()))
        self.assertTrue(phone_entry.handle(self.bot,{},self.message()))
        self.assertEqual(self.bot.client.call.call_count,1)
        phone_entry.handle(self.bot,{},self.message(message_id=2))
        self.assertEqual(self.bot.client.call.call_count,2)

    def test_real_ingest_routes_entry_without_wake_or_model_capture(self):
        bot=botapi.BotTransport(SimpleNamespace(),self.tree,None,self.config)
        bot.username='FixtureBot';bot.client=self.bot.client
        bot.rooms=Mock();bot.contacts=Mock();bot.inputs=Mock()
        with patch.object(bot,'_capture_allowed',return_value=False):
            bot._ingest({'message':self.message()})
        self.assertIsNone(bot.pop_pending())
        self.assertEqual(bot.client.call.call_args.args[0],'sendMessage')
        bot.inputs.capture.assert_not_called()

    def test_reject_credential_bearing_and_non_https_origins(self):
        for url in ['http://example.org/','https://user:secret@example.org/','https://example.org/?key=private',
                    'https://example.org/#key','https://example.org/path','https://example.org:8443/']:
            with self.subTest(url=url),self.assertRaises(ValueError):phone_entry.origin(url)


class BotHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.button={'type':'commands'};self.calls=[];self.fail_after_apply=False
        async def capture(request):
            method=request.match_info['method'];params=dict(await request.post());self.calls.append((method,params))
            if method=='getChatMenuButton':result=self.button
            elif method=='getChat':result={'id':123,'type':'private'}
            elif method=='getMe':result={'id':987,'is_bot':True,'username':'FixtureBot'}
            elif method=='setChatMenuButton':
                self.button=json.loads(params['menu_button']);result=True
                if self.fail_after_apply:
                    self.fail_after_apply=False
                    return web.json_response({'ok':False,'error_code':500,'description':'fixture after apply'},status=500)
            elif method=='sendMessage':result={'message_id':8}
            else:raise AssertionError(method)
            return web.json_response({'ok':True,'result':result})
        app=web.Application();app.router.add_post('/{method}',capture)
        self.server=TestServer(app);await self.server.start_server()
        self.client=botapi.BotClient('fixture:token');self.client._base=str(self.server.make_url('/'))
        self.menu=phone_access.MenuOwner(Path(self.temp.name),self.client,123,'fixture:token')
    async def asyncTearDown(self):await self.server.close()

    async def test_rotation_and_ambiguous_remote_apply_reconcile_over_http(self):
        self.fail_after_apply=True
        with self.assertRaises(botapi.BotApiError):await self.menu.publish('https://first.trycloudflare.com/','Мира')
        await self.menu.publish('https://first.trycloudflare.com/','Мира')
        writes=lambda:[p for method,p in self.calls if method=='setChatMenuButton']
        self.assertEqual(len(writes()),1)
        await self.menu.publish('https://second.trycloudflare.com/','Мира')
        self.assertEqual(self.button['web_app']['url'],'https://second.trycloudflare.com/tg/')
        self.assertTrue(all(p['chat_id']=='123' for _,p in self.calls))
        await self.menu.restore();self.assertEqual(self.button,{'type':'commands'})

    async def test_second_installation_cannot_hijack_same_bot_menu(self):
        await self.menu.publish('https://first.trycloudflare.com/','Мира')
        other=phone_access.MenuOwner(Path(self.temp.name)/'other',self.client,123,'fixture:token')
        with self.assertRaisesRegex(ValueError,'другой установки'):
            await other.publish('https://second.trycloudflare.com/','Другой агент')
        self.assertEqual(self.button['web_app']['url'],'https://first.trycloudflare.com/tg/')

    async def test_fresh_button_serializes_through_real_botclient_http(self):
        tree=Path(self.temp.name)
        config={'phone':{'enabled':True,'mode':'automatic'},'telegram':{'bot_token':'fixture:token','owner_id':123}}
        runtime=phone_access.PhoneAccess(tree/'memory/.state/phone-access',lambda:config,8094,owner_token=True)
        runtime.menu=self.menu;runtime.url='https://fresh.trycloudflare.com/'
        runtime.record('ready','fixture',telegram='ready')
        transport=SimpleNamespace(tree=tree,_cfg=config,owner_id='123',username='FixtureBot',client=self.client)
        message={'chat':{'id':123,'type':'private'},'from':{'id':123},'text':'/app','date':time.time(),'message_id':3}
        await asyncio.to_thread(phone_entry.handle,transport,{},message)
        method,params=self.calls[-1];self.assertEqual(method,'sendMessage')
        self.assertEqual(json.loads(params['reply_markup'])['inline_keyboard'][0][0]['web_app']['url'],
                         'https://fresh.trycloudflare.com/tg/')

    async def test_same_agent_can_migrate_with_its_ownership_ledger(self):
        await self.menu.publish('https://local.trycloudflare.com/','Мира')
        moved_root=Path(self.temp.name)/'moved';moved_root.mkdir()
        # Transfer freezes the old publisher and carries its private ledger.
        (moved_root/'telegram-menu.json').write_bytes(self.menu.path.read_bytes())
        moved=phone_access.MenuOwner(moved_root,self.client,123,'fixture:token')
        await moved.publish('https://vps.trycloudflare.com/','Мира')
        self.assertEqual(self.button['web_app']['url'],'https://vps.trycloudflare.com/tg/')
        await moved.restore();self.assertEqual(self.button,{'type':'commands'})

    async def test_stable_cycle_registers_bot_then_withdraws_entry_on_disable(self):
        config={'phone':{'enabled':True,'mode':'stable','external':'https://stable.example/'},
                'telegram':{'bot_token':'fixture:token','owner_id':123}}
        runtime=phone_access.PhoneAccess(Path(self.temp.name),lambda:config,8094,owner_token=True)
        runtime.menu=self.menu;observed=[]
        async def disable(_):
            observed.append(runtime.state());config['phone']['enabled']=False
        runtime.delay=disable
        with patch.object(phone_access,'probe_origin',new=AsyncMock(return_value=(True,''))), \
             patch.object(phone_access,'ensure_cloudflared',new=AsyncMock()) as download, \
             patch.object(phone_access.os,'getpgrp',side_effect=os.getpid,create=True):
            import copy
            await runtime.cycle(copy.deepcopy(config))
        download.assert_not_awaited()
        self.assertEqual(observed[0]['bot_url'],'https://t.me/FixtureBot?start=helene')
        self.assertEqual(observed[0]['telegram'],'ready')
        self.assertEqual(runtime.url,'');self.assertEqual(self.button,{'type':'commands'})
        self.assertNotEqual(json.loads((runtime.root/'status.json').read_text(encoding='utf-8'))['state'],'ready')


class RetiredInstall(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_never_exports_existing_device_or_pair(self):
        app=web.Application();app.router.add_get('/m/manifest.webmanifest',deskapp.mobile_manifest)
        client=TestClient(TestServer(app));await client.start_server()
        try:
            response=await client.get('/m/manifest.webmanifest?pair=secret',cookies={deskapp.COOKIE:'dk-private'})
            self.assertEqual(response.status,410)
            text=await response.text();self.assertNotIn('dk-private',text);self.assertNotIn('secret',text)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
        finally:await client.close()


class SignedOwnerHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.tree=Path(self.temp.name);config=self.tree/'helene.json'
        config.write_text(json.dumps({'telegram':{'bot_token':'fixture:token','owner_id':123},
                                      'phone':{'enabled':False,'mode':'automatic'}}),encoding='utf-8')
        self.saved=deskapp._PHONE_ACCESS
        self.patches=[patch.object(deskapp,'TOKEN','fixture-owner-key'),
                      patch.object(deskapp.readers,'tree',return_value=self.tree),
                      patch.object(deskapp.readers,'config_path',return_value=config),
                      patch.dict(os.environ,{'HELENE_TG_BOT_TOKENS':'','HELENE_TG_OWNER_ID':'',
                                            'HELENE_DESK_STATE':str(self.tree/'channel')})]
        for item in self.patches:item.start()
        self.client=TestClient(TestServer(deskapp.build_app(port=8193)))
        await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close()
        for item in reversed(self.patches):item.stop()
        deskapp._PHONE_ACCESS=self.saved
    async def test_current_signature_owner_and_device_scope_on_real_http(self):
        from desk.tests.t_phone import _init_data
        response=await self.client.post('/pair/telegram',json={'initData':_init_data({'id':123},token='fixture:token')},
                                        headers={'X-Forwarded-Proto':'https'})
        self.assertEqual(response.status,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertIn('Secure',response.headers['Set-Cookie'])
        key=(await response.json())['key'];headers={'Authorization':'Bearer '+key}
        self.assertEqual((await self.client.get('/api/md',headers=headers)).status,403)
        self.assertTrue(deskapp._device_ok(key))
        for data in [_init_data({'id':124},token='fixture:token'),_init_data({'id':123},token='other-bot'),
                     _init_data({'id':123},token='fixture:token',auth_date=int(time.time()-8*86400))]:
            denied=await self.client.post('/pair/telegram',json={'initData':data},cookies={deskapp.COOKIE:key},headers=headers)
            self.assertEqual(denied.status,403)
        response=await self.client.post('/pair/telegram',json={'initData':_init_data({'id':123},token='fixture:token')})
        self.assertEqual(response.status,200);new=(await response.json())['key']
        self.assertTrue(deskapp._device_ok(new));self.assertFalse(deskapp._device_ok(key))


if __name__=='__main__':unittest.main()
