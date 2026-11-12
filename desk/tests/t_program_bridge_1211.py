import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import deskapp
from deskd import program
from localharness import session_host, program_commands


class Scope(unittest.TestCase):
    def test_only_new_current_owner_scope_can_operate_program(self):
        key='fixture-device'; row={'hash':deskapp._key_hash(key),'pair':'tg:123','scope':'program'}
        request=make_mocked_request('POST','/api/program/invoke',headers={'Authorization':'Bearer '+key})
        with patch.object(deskapp,'TOKEN','fixture-owner'), patch.object(deskapp,'_devices',return_value=[row]), patch.object(deskapp,'_tg_owner_id',return_value='123'):
            self.assertEqual(deskapp._role(request),'owner')
            row.pop('scope');self.assertEqual(deskapp._role(request),'device')
            self.assertFalse(deskapp._scope_ok('device','/api/program/invoke'))
            row['scope']='program';row['pair']='tg:999';self.assertEqual(deskapp._role(request),'device')
        with patch.object(deskapp,'_devices',return_value=[]):
            self.assertEqual(deskapp._role(request),'')

    def test_bootstrap_redacts_host_keys_and_switch_does_not_replace_native_window(self):
        descriptor={'private':'never exported'}
        with patch.object(program,'session',return_value=(descriptor,'one')), patch.object(session_host,'_rpc',return_value={'agent_id':'two','agent':'Second','base':'http://127.0.0.1:8000','key':'private-channel-key'}) as rpc:
            result=program.invoke({'command':'switch_agent','arguments':{'id':'two'}})
            self.assertEqual(result,{'agent_id':'two','agent':'Second','base':'/program/two'})
            self.assertEqual(rpc.call_args.args[1],'program_channel')
            self.assertNotIn('private',json.dumps(result))

    def test_shared_appearance_commands_retain_conflict_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old=program_commands.invoke(root,root,'appearance_read',{})
            result=program_commands.invoke(root,root,'appearance_patch',{'seen':old['seen'],'patch':{'frame.theme':'dark'}})
            self.assertEqual(result['values']['frame.theme'],'dark')
            conflict=program_commands.invoke(root,root,'appearance_patch',{'seen':old['seen'],'patch':{'frame.theme':'light'}})
            self.assertEqual(conflict['code'],'conflict')
            self.assertFalse(conflict['ok'])


class Gateway(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen=[]
        async def upstream(request):
            self.seen.append((request.path,dict(request.query),request.headers.get('Authorization'),await request.read()))
            if request.path=='/api/redirect':raise web.HTTPFound('http://example.invalid/steal')
            if request.path=='/tunnel':
                ws=web.WebSocketResponse();await ws.prepare(request)
                async for message in ws:await ws.send_str(message.data)
                return ws
            return web.json_response({'ok':True})
        app=web.Application();app.router.add_route('*','/{tail:.*}',upstream)
        self.upstream=TestServer(app);await self.upstream.start_server()
        self.role=patch.object(deskapp,'_role',return_value='owner');self.role.start()
        self.channel=patch.object(program,'channel',return_value={'base':str(self.upstream.make_url('')).rstrip('/'),'key':'host-key'});self.channel.start()
        front=web.Application();front.router.add_route('*','/program/{agent}/{tail:.*}',program.gateway)
        self.client=TestClient(TestServer(front));await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close();await self.upstream.close();self.channel.stop();self.role.stop()
    async def test_http_and_websocket_use_selected_channel_and_private_upstream_auth(self):
        response=await self.client.post('/program/other/api/say?key=phone-key&x=1',json={'text':'hello'})
        self.assertEqual(response.status,200)
        self.assertEqual(self.seen[-1][1],{'x':'1'})
        self.assertEqual(self.seen[-1][2],'Bearer host-key')
        self.channel.target.channel.assert_called_with('other')
        async with self.client.ws_connect('/program/other/tunnel?key=phone-key') as ws:
            await ws.send_str('fixture');self.assertEqual((await ws.receive()).data,'fixture')
    async def test_restricted_paths_redirects_and_devices_are_rejected(self):
        for path in ('pair/telegram','api/program/invoke','program/recursive'):
            self.assertEqual((await self.client.get('/program/one/'+path)).status,404)
        self.assertEqual((await self.client.get('/program/one/api/redirect')).status,502)
        with patch.object(deskapp,'_role',return_value='device'):
            self.assertEqual((await self.client.get('/program/one/api/state')).status,403)


if __name__=='__main__':unittest.main()
