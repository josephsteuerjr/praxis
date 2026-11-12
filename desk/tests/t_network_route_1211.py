import asyncio
import json
import os
import sys
import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'localharness')]
from deskd.network_route import Route,SelectedProxy
from deskd.telegram_http import Client,BotApiError
from deskd import connection_checks
from phone_tunnel.proxy_bridge import CloudflareProxy
import botapi

CONFIG={'telegram':{'bot_token':'fixture','owner_id':'42'},
        'network':{'proxy':{'mode':'proxy','protocol':'http','host':'127.0.0.1','port':8888}}}


class Routes(unittest.TestCase):
    def test_status_stages_are_independent_and_retry_reaches_receiver(self):
        import tempfile,time
        from phone_access import PhoneAccess
        with tempfile.TemporaryDirectory() as directory:
            tree=Path(directory);config=json.loads(json.dumps(CONFIG))
            access=PhoneAccess(tree/'memory/.state/phone-access',lambda:config,1234,owner_token=True)
            access.record('ready','HTTPS ready',telegram='ready')
            state=access.state()['checks']
            self.assertEqual(state['https']['state'],'ready')
            self.assertEqual(state['menu']['state'],'ready')
            self.assertEqual(state['bot']['state'],'unknown')
            self.assertEqual(state['authorization']['state'],'unknown')
            bot=botapi.BotTransport(types.SimpleNamespace(),tree,types.SimpleNamespace(record_message=mock.Mock()),config)
            bot._last_poll_ok=time.time();bot._record_connection('ready')
            self.assertEqual(access.state()['checks']['bot']['state'],'ready')
            access.authorized()
            self.assertEqual(access.state()['checks']['authorization']['state'],'ready')
            access.retry();started=time.monotonic();bot._wait_connection_retry(30)
            self.assertLess(time.monotonic()-started,1)
            config['telegram']['bot_token']='changed'
            self.assertEqual(access.state()['checks']['bot']['state'],'unknown')
            self.assertEqual(access.state()['checks']['authorization']['state'],'unknown')

    def test_explicit_proxy_ignores_unrelated_system_bypass(self):
        route=Route(CONFIG)
        with mock.patch.dict(os.environ,{'NO_PROXY':'*','no_proxy':'*'}):
            self.assertEqual(route.proxy_for('https://api.telegram.org'),'http://127.0.0.1:8888')
            request=urllib.request.Request('https://api.telegram.org/getMe')
            handler=SelectedProxy(route.proxies())
            handler.proxy_open(request,route.url,'https')
            self.assertEqual(request.host,'127.0.0.1:8888')
            self.assertEqual(request._tunnel_host,'api.telegram.org')
        self.assertEqual(route.proxy_for('http://127.0.0.1:1234'),'')

    def test_system_and_direct_remain_distinct(self):
        with mock.patch('urllib.request.getproxies',return_value={'https':'http://proxy.invalid:8888'}), \
                mock.patch('urllib.request.proxy_bypass',return_value=False):
            self.assertEqual(Route({}).proxy_for('https://example.org'),'http://proxy.invalid:8888')
            self.assertEqual(Route({'network':{'proxy':{'mode':'direct'}}}).proxy_for('https://example.org'),'')

    def test_checks_and_runtime_share_the_actual_client_and_saved_route(self):
        calls=[]
        class Response:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return json.dumps({'ok':True,'result':self.data}).encode()
        def open_request(request,timeout):
            calls.append((request.full_url,timeout))
            return Response({'getMe':{'id':99,'is_bot':True,'username':'fixture'},
                'getChat':{'id':42,'type':'private'},'sendMessage':{'message_id':123},'getUpdates':[]}[request.full_url.rsplit('/',1)[1]])
        with mock.patch.object(Route,'open',side_effect=open_request):
            self.assertTrue(connection_checks.check_telegram(CONFIG)['ok'])
            client=botapi.BotClient.from_config(CONFIG)
            client.call('getUpdates',timeout=25,_http_timeout=35)
            self.assertEqual(client.call('sendMessage',chat_id=42,text='fixture')['message_id'],123)
        self.assertIsInstance(client,Client)
        self.assertEqual(client.route.url,'http://127.0.0.1:8888')
        self.assertEqual([url.rsplit('/',1)[1] for url,_ in calls],['getMe','getChat','getUpdates','sendMessage'])

    def test_non_http_payload_and_http_200_refusal_preserve_real_failure(self):
        client=Client.from_config(CONFIG)
        for payload,code in (([],0),({'ok':False,'error_code':429,'parameters':{'retry_after':12}},429)):
            response=mock.MagicMock();response.__enter__.return_value.read.return_value=json.dumps(payload).encode()
            with mock.patch.object(client,'_open',return_value=response),self.assertRaises(BotApiError) as result:
                client.call('getMe')
            self.assertEqual(result.exception.code,code)
            if code==429:self.assertEqual(result.exception.retry_after,12)


class ProxyBridge(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests=[];self.reject=False;self.handlers=set()
        async def proxy(reader,writer):
            task=asyncio.current_task();self.handlers.add(task)
            try:
                header=await reader.readuntil(b'\r\n\r\n');self.requests.append(header)
                writer.write(b'HTTP/1.1 403 Forbidden\r\n\r\n' if self.reject else b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
                if not self.reject:
                    while block:=await reader.read(4096):writer.write(block);await writer.drain()
            finally:
                writer.close();await writer.wait_closed();self.handlers.discard(task)
        self.proxy=await asyncio.start_server(proxy,'127.0.0.1',0)
        port=self.proxy.sockets[0].getsockname()[1]
        config={'network':{'proxy':{**CONFIG['network']['proxy'],'port':port}}}
        self.bridge=CloudflareProxy(Route(config));await self.bridge.start()

    async def asyncTearDown(self):
        await self.bridge.stop()
        self.proxy.close();await self.proxy.wait_closed()
        tasks=list(self.handlers)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    async def test_fixed_edge_connect_forwards_opaque_bytes_and_closes(self):
        address=self.bridge.servers[0].sockets[0].getsockname()
        self.assertEqual(address[0],'127.0.0.1')
        reader,writer=await asyncio.open_connection(*address)
        writer.write(b'opaque TLS fixture');await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(18),2),b'opaque TLS fixture')
        self.assertTrue(self.requests[0].startswith(b'CONNECT region1.v2.argotunnel.com:7844 HTTP/1.1'))
        await self.bridge.stop()
        self.assertEqual(await asyncio.wait_for(reader.read(),2),b'')
        self.assertEqual(self.bridge.tasks,set())
        writer.close();await writer.wait_closed()

    async def test_proxy_refusal_is_visible_and_not_bypassed(self):
        self.reject=True
        reader,writer=await asyncio.open_connection(*self.bridge.servers[1].sockets[0].getsockname())
        self.assertEqual(await asyncio.wait_for(reader.read(),2),b'')
        self.assertIn('7844',self.bridge.error)
        self.assertEqual(len(self.requests),1)
        writer.close();await writer.wait_closed()

    async def test_bootstrap_has_no_open_proxy_endpoint(self):
        import aiohttp
        base=self.bridge.arguments[1]
        async with aiohttp.ClientSession() as client:
            async with client.post(base.rsplit('/',1)[0]+'/tunnel') as response:
                self.assertEqual(response.status,404)
            async with client.post(base+'/tunnel',data=b'unexpected') as response:
                self.assertEqual(response.status,400)
        self.assertEqual(self.requests,[])


if __name__=='__main__':unittest.main()
