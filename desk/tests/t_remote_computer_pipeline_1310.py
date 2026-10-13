"""Full channel auth -> outgoing publisher -> native HTTP bridge -> response bytes.

The bridge here is a controlled fixture. Actual Windows body acceptance is a
separate owned native stand; these tests never touch a user's desktop.
"""
import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
from aiohttp import web, ClientSession
sys.path[:0] = [str(Path(__file__).resolve().parent.parent), str(Path(__file__).resolve().parent.parent / 'localharness')]
import deskapp
from deskd import remote_computer
from localharness.remote_computer_client import Publisher, grant, pair


class Pipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.owner = 'fixture-window-owner-1310'
        self.controller = 'fixture-scoped-controller-1310'
        for patch in (mock.patch.object(deskapp, 'TOKEN', self.owner),
                      mock.patch.object(deskapp.readers, 'tree', return_value=self.root),
                      mock.patch.object(deskapp.readers, 'config_path', return_value=self.root/'server.json'),
                      mock.patch.object(remote_computer, '_CONTROLLER', self.controller)):
            patch.start(); self.addCleanup(patch.stop)
        (self.root/'server.json').write_text('{}')
        self.app = deskapp.build_app()
        self.runner = web.AppRunner(self.app); await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0); await site.start()
        self.base = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
        self.http = ClientSession(); self.addAsyncCleanup(self.http.close)
        self.addAsyncCleanup(self.runner.cleanup)
        async with self.http.post(self.base+remote_computer.PREFIX+'/pair',
                headers={'Authorization': 'Bearer '+self.owner}, json={'name': 'Fixture Windows', 'scopes':['computer.apps']}) as result:
            self.assertEqual(result.status, 200); self.credential = await result.json()
        self.device = self.credential['id']
        self.app['_remote_computer'].select(self.device)

    async def test_scoped_tokens_do_not_become_window_owner_or_phone_tokens(self):
        for token, expected in [(self.credential['token'], 403), (self.controller, 403)]:
            async with self.http.post(self.base+remote_computer.PREFIX+'/pair',
                    headers={'Authorization': 'Bearer '+token}, json={'name':'forged','scopes':[]}) as result:
                self.assertEqual(result.status, expected)
            async with self.http.get(self.base+'/api/agentcfg', headers={'Authorization':'Bearer '+token}) as result:
                self.assertIn(result.status, (401,403))
        async with self.http.get(self.base+remote_computer.PREFIX+'/controller/state',
                headers={'Authorization':'Bearer '+self.controller}) as result:
            self.assertEqual(result.status,200)
            self.assertNotIn(self.credential['token'], await result.text())

    async def test_pair_selection_network_failure_restores_exact_local_grant(self):
        before=json.dumps({'mode':'remote','base':self.base,'key':self.owner,'computer':{'custom':'keep'}}).encode()
        path=self.root/'pair-failure.json';path.write_bytes(before)
        class BrokenResponse:
            async def __aenter__(self):raise ConnectionError('fixture offline')
            async def __aexit__(self,*args):pass
        from localharness import remote_computer_client as client
        original=client.aiohttp.ClientSession.post
        def failure(session,url,**kwargs):
            if url.endswith(('/select','/revoke')):return BrokenResponse()
            return original(session,url,**kwargs)
        with mock.patch.object(client.aiohttp.ClientSession,'post',failure):
            with self.assertRaises(ConnectionError):await pair(path,['computer.read'])
        self.assertEqual(path.read_bytes(),before)

    async def test_window_event_tunnel_can_read_and_change_its_own_desktop_registry(self):
        socket=await self.http.ws_connect(self.base+'/tunnel',headers={'Authorization':'Bearer '+self.owner})
        try:
            await socket.receive_json(timeout=2)
            for ident,method,path,value in [('state','GET','/state',None),
                                          ('select','POST','/select',{'device':self.device})]:
                await socket.send_json({'id':ident,'method':method,'path':remote_computer.PREFIX+path,'body':value})
                reply=await socket.receive_json(timeout=2)
                self.assertEqual(reply['id'],ident);self.assertEqual(reply['status'],200)
                self.assertEqual(reply['body']['selected'],self.device)
            denied=await deskapp._tunnel_dispatch('POST',remote_computer.PREFIX+'/pair',
                {'name':'phone','scopes':[]},role='device',app=self.app)
            self.assertEqual(denied['status'],403)
        finally:await socket.close()

    async def test_streamed_native_bytes_and_local_revocation_end_to_end(self):
        expected = json.dumps({'ok':True,'data':'fixture-stream-'*30000}).encode()
        async def invoke(request):
            self.assertEqual(request.headers.get('Authorization'), 'Bearer local-native-controller')
            value = await request.json(); self.assertEqual(value['capability'], 'desktop.window.read')
            response = web.StreamResponse(headers={'Content-Type':'application/json'})
            await response.prepare(request)
            for offset in range(0,len(expected),4096):
                await response.write(expected[offset:offset+4096]); await asyncio.sleep(0)
            await response.write_eof(); return response
        native = web.Application(); native.router.add_post('/v1/controller/'+self.device+'/invoke',invoke)
        native_runner=web.AppRunner(native); await native_runner.setup()
        native_site=web.TCPSite(native_runner,'127.0.0.1',0);await native_site.start()
        self.addAsyncCleanup(native_runner.cleanup)
        native_port=native_site._server.sockets[0].getsockname()[1]
        cfg={'mode':'remote','base':self.base,'computer':{'remote':{'enabled':True,'id':self.device,
             'token':self.credential['token'],'base':self.base,'scopes':['computer.apps']}},'service':{'session0':False}}
        path=self.root/'desktop.json';path.write_text(json.dumps(cfg))
        publisher=Publisher(path,self.root,self.root/'desktop-data')
        fake=mock.Mock(port=native_port,controller_token='local-native-controller')
        def start():
            publisher.initial=grant(path);publisher.load_permissions();publisher.body=fake
        with mock.patch.object(publisher,'start_body',side_effect=start), mock.patch('localharness.owner_stop.stopped',return_value=False):
            task=asyncio.create_task(publisher.connection())
            try:
                for _ in range(100):
                    if self.app['_remote_computer'].identities.get(self.device):break
                    await asyncio.sleep(.01)
                self.assertTrue(self.app['_remote_computer'].identities.get(self.device))
                uri=self.base+remote_computer.PREFIX+'/bridge/v1/controller/'+self.device+'/invoke'
                async with self.http.post(uri,headers={'Authorization':'Bearer '+self.controller},
                    json={'capability':'desktop.window.read','request_id':'fixture-rid','execution':'interactive','args':{}}) as result:
                    self.assertEqual(result.status,200);self.assertEqual(await result.read(),expected)
                cfg['computer']['remote']['enabled']=False;path.write_text(json.dumps(cfg))
                await asyncio.wait_for(task,3)
                fake.stop.assert_called()
                self.assertFalse(self.app['_remote_computer'].connections)
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)

if __name__ == '__main__':
    unittest.main()
