"""Paired HTTP/WSS and actual grant/dispatch boundaries; never native desktop input."""
import asyncio
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock,patch

from aiohttp import web,ClientSession
import agent
import body
import deskapp
import frame_language
import windows_mcp_tools
from deskd import remote_computer
from localharness import windows_mcp_access as access,windows_mcp_client as client
from localharness import windows_mcp_remote as protocol,windows_mcp_runtime as runtime
from localharness import python_desktop_commands as commands,owner_stop
from localharness.remote_computer_client import Publisher,grant

RAW='Windows body praxis.body.v1 Алекс 中文 {literal} $& C:/данные/file.txt'


class RemoteMCPContract(unittest.TestCase):
    def test_exact21_native_names_strict_envelope_and_sampling_never_expand(self):
        self.assertEqual(protocol.NAMES,{name.removeprefix('windows_mcp_') for name in windows_mcp_tools.SCHEMAS})
        original={'name':'Scrape','arguments':{'url':RAW,'use_sampling':True}}
        saved=copy.deepcopy(original)
        name,args=protocol.invocation(original)
        self.assertEqual(name,'Scrape');self.assertFalse(args['use_sampling']);self.assertEqual(args['url'],RAW)
        self.assertEqual(original,saved)
        for value in ({'name':'windows_mcp_Click','arguments':{}},{'name':'Unknown','arguments':{}},
                      {'name':'Click','arguments':[]},{'name':'Click','arguments':{},'url':'http://evil'},
                      {'name':'Click','arguments':{'x':float('nan')}}):
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                protocol.invocation(value)

    def test_release_and_saved_screenshots_need_their_actual_effect_scopes(self):
        self.assertEqual(protocol.required({'name':'ControlStatus','arguments':{}}),{'computer.read'})
        self.assertEqual(protocol.required({'name':'ControlStatus','arguments':{'release':True}}),{'computer.apps'})
        self.assertEqual(protocol.required({'name':'Screenshot','arguments':{'save_path':RAW}}),{'computer.read','computer.files'})
        self.assertTrue(access.rights('Screenshot',{'save_path':RAW})[1])

    def test_actual_app_handshake_admits_only_complete_pinned21_tool_catalogue(self):
        rows=[{'name':name.removeprefix('windows_mcp_'),'inputSchema':schema} for name,schema in windows_mcp_tools.SCHEMAS.items()]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for actual,success in ((rows,True),(rows[:-1],False),(rows+[rows[0]],False)):
                stop=threading.Event();child=types.SimpleNamespace(handle=object(),poll=lambda:None,stop=Mock())
                owner=types.SimpleNamespace(root=root,lock=threading.RLock(),stop=stop,exiting=False,background=Mock(return_value=child))
                manager=runtime.Manager(owner)
                with patch('localharness.windows_mcp_component.directory',return_value=root),patch('localharness.windows_mcp_component.interpreter',return_value='never-executed'),patch('localharness.program_state.directory',return_value=root),patch.object(runtime,'rpc',side_effect=[{}, {'tools':actual}]),patch.object(runtime.time,'monotonic',side_effect=[0,61]):
                    manager._start(stop)
                self.assertEqual(manager.state,'running' if success else 'error')
                if success:self.assertEqual(set(manager.tools),protocol.NAMES)
                else:
                    self.assertEqual(manager.failure['code'],'not_ready')
                    self.assertEqual(manager.failure['cause']['code'],'invalid_catalog')
                    child.stop.assert_called_once()

    def test_actual_runner_body_singleton_supplies_live_permissions_and_remote_transport(self):
        self.assertIn('body_module=body',Path(__file__).parents[1].joinpath('localharness/runner.py').read_text(encoding='utf-8'))
        calls=[]
        class Live:
            def __init__(self,*args):self.enabled=True;self.scopes=['computer.read']
            def refresh(self):calls.append('refresh')
        module=types.SimpleNamespace(_LiveScopes=Live)
        subject=types.SimpleNamespace(_is_sovereign_actor=lambda:True)
        before=windows_mcp_tools._call,windows_mcp_tools._allowed
        try:
            with patch.object(client,'call',return_value={'content':[{'type':'text','text':RAW}]}) as forwarded:
                access.install(subject,Path('literal-tree'),{},None,Path('literal-program'),body_module=module)
                self.assertEqual(windows_mcp_tools._allowed('Clipboard',{'mode':'get'}),'')
                self.assertTrue(windows_mcp_tools._allowed('ControlStatus',{'release':True}))
                self.assertEqual(windows_mcp_tools._call('Clipboard',{'mode':'get'})['content'][0]['text'],RAW)
                self.assertIs(forwarded.call_args.kwargs['body_module'],module)
                self.assertEqual(calls,['refresh','refresh'])
        finally:windows_mcp_tools.configure(*before)

    def test_remote_client_binding_offline_and_transport_errors_never_fallback_or_replay(self):
        device='a'*32
        class Target:
            def metadata(self,**kwargs):return True
        module=types.SimpleNamespace(_BODY=Target(),RemoteBody=Target,STATE={'connected':True},
            _settings=lambda:('url','private-controller',device),_raw_request=Mock())
        with tempfile.TemporaryDirectory() as folder,patch.object(client,'invoke') as local:
            root=Path(folder);root.joinpath('helene.json').write_text('{"lang":"en"}')
            for reply in ({'host':'windows','device_id':'b'*32,'result':{'content':[]}},
                          {'ok':False,'code':'desktop_disconnected'}):
                module._raw_request.reset_mock();module._raw_request.return_value=reply
                result=client.call(root,root,'Click',{'x':1},body_module=module)
                self.assertTrue(result['in_doubt']);module._raw_request.assert_called_once()
                self.assertEqual(module._raw_request.call_args.args[1],f'/v1/controller/{device}/windows-mcp/call')
            module.STATE['connected']=False;module._raw_request.reset_mock()
            result=client.call(root,root,'Click',{'x':1},body_module=module)
            self.assertEqual(result['helene_error']['code'],'remote_unavailable')
            module._raw_request.assert_not_called();local.assert_not_called()

    def test_remote_owned_failure_uses_frame_language_independently_of_interface(self):
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with tempfile.TemporaryDirectory() as folder:
                    root=Path(folder);root.joinpath('helene.json').write_text(json.dumps({'lang':ui}),encoding='utf-8')
                    from localharness import windows_mcp_words as words
                    value=words.failure(root,'remote_refused')
                    with frame_language.use(language):
                        self.assertEqual(windows_mcp_tools._format_message(value['helene_error']),frame_language.text('mcp.remote_refused'))


class RemoteMCPPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.config=self.root/'desktop.json'
        self.owner_key='fixture-owner';self.controller='fixture-controller'
        for change in (patch.object(deskapp,'TOKEN',self.owner_key),
                       patch.object(deskapp.readers,'tree',return_value=self.root),
                       patch.object(deskapp.readers,'config_path',return_value=self.root/'server.json'),
                       patch.object(remote_computer,'_CONTROLLER',self.controller),
                       patch.object(body,'_REMOTE_CONTROLLER',self.controller),
                       patch.object(owner_stop,'stopped',return_value=False)):
            change.start();self.addCleanup(change.stop)
        self.root.joinpath('server.json').write_text('{}')
        self.app=deskapp.build_app();self.relay=self.app['_remote_computer']
        self.credential=self.relay.pair('Windows fixture',['computer.read']);self.device=self.credential['id']
        self.relay.select(self.device)
        self.runner=web.AppRunner(self.app);await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.port=site._server.sockets[0].getsockname()[1];self.base='http://127.0.0.1:'+str(self.port)
        self.addAsyncCleanup(self.runner.cleanup)
        self.http=ClientSession();self.addAsyncCleanup(self.http.close)
        self.cfg={'mode':'remote','base':self.base,'computer':{'remote':{
            'enabled':True,'id':self.device,'token':self.credential['token'],'base':self.base,'scopes':['computer.read']}}}
        self.write()
        self.publisher=Publisher(self.config,self.root,self.root/'desktop-data')
        self.publisher.initial=grant(self.config);self.publisher.load_permissions()
        self.owner=types.SimpleNamespace(root=self.root,current=lambda:types.SimpleNamespace(config=self.config),
            stop=threading.Event(),exiting=False)
        self.manager=types.SimpleNamespace(call=Mock(return_value={'content':[{'type':'text','text':RAW}]}))
        self.enterContext(patch.object(runtime,'manager',return_value=self.manager))
        self.ipc=[]
        def invoke(program,command,arguments,**kwargs):
            self.ipc.append((program,command,copy.deepcopy(arguments),kwargs))
            self.assertEqual(kwargs['tree'],self.publisher.data)
            return commands.invoke(self.owner,command,arguments)
        self.enterContext(patch.object(client,'invoke',side_effect=invoke))
        self.enterContext(patch.dict(body._TOKENS,{'url':self.base+remote_computer.PREFIX+'/bridge',
            'controller':self.controller,'device':self.device},clear=True))
        self.enterContext(patch.dict(body.STATE,{},clear=True))
        self.enterContext(patch.object(body,'_BODY',body.RemoteBody(self.root,{'port':self.port})))

    def write(self):self.config.write_text(json.dumps(self.cfg),encoding='utf-8')

    async def connect(self,scopes=None):
        socket=await self.http.ws_connect(self.base+remote_computer.PREFIX+'/connect/'+self.device,
            headers={'Authorization':'Bearer '+self.credential['token']})
        self.addAsyncCleanup(socket.close)
        await socket.send_json({'kind':'hello','identity':{'platform':'windows','scopes':scopes or ['computer.read']}})
        self.assertEqual((await socket.receive_json(timeout=2))['kind'],'ready')
        return socket

    async def answer(self,socket,*,disconnect=False):
        request=await socket.receive_json(timeout=3)
        self.assertEqual(request['path'],f'/v1/controller/{self.device}/windows-mcp/call')
        self.assertNotIn(self.credential['token'],json.dumps(request))
        if disconnect:
            await socket.close();return request
        response=await self.publisher.execute(request,self.http)
        await socket.send_json(response)
        return request

    async def call(self,name='Clipboard',arguments=None):
        return await asyncio.to_thread(client.call,self.root,self.root,name,
            {'mode':'get'} if arguments is None else arguments,body_module=body)

    async def test_actual_controller_http_wss_publisher_and_program_grant_dispatch_keep_literal_result(self):
        socket=await self.connect();answer=asyncio.create_task(self.answer(socket))
        result=await self.call();request=await answer
        self.assertEqual(result,{'content':[{'type':'text','text':RAW}]})
        self.manager.call.assert_called_once_with('Clipboard',{'mode':'get'})
        self.assertEqual(len(self.ipc),1);self.assertEqual(self.ipc[0][1],'windows_mcp_remote_call')
        self.assertEqual(json.loads(base64.b64decode(request['body'])),{'name':'Clipboard','arguments':{'mode':'get'}})

    async def test_local_grant_revoked_after_hello_refuses_before_ipc_without_false_uncertainty(self):
        socket=await self.connect();self.cfg['computer']['remote']['scopes']=[];self.write()
        answer=asyncio.create_task(self.answer(socket));result=await self.call();await answer
        self.assertEqual(result['helene_error']['code'],'remote_refused')
        self.assertNotIn('in_doubt',result);self.assertFalse(self.ipc);self.manager.call.assert_not_called()

    async def test_server_and_live_desktop_grants_each_block_expanded_effects(self):
        await self.connect()
        for payload in ({'name':'ControlStatus','arguments':{'release':True}},
                        {'name':'PowerShell','arguments':{'command':RAW}},
                        {'name':'Screenshot','arguments':{'save_path':RAW}}):
            with self.assertRaises(PermissionError):
                await self.relay.rpc('POST',f'/v1/controller/{self.device}/windows-mcp/call',
                    json.dumps(payload).encode(),'application/json',timeout=1)
        self.assertFalse(self.relay.pending);self.assertFalse(self.ipc)

    async def test_disconnect_settles_pending_as_unknown_and_never_replays(self):
        socket=await self.connect();answer=asyncio.create_task(self.answer(socket,disconnect=True))
        result=await self.call();await answer
        self.assertTrue(result['in_doubt']);self.assertFalse(self.relay.pending)
        await self.connect();self.assertFalse(self.ipc);self.manager.call.assert_not_called()

    async def test_app_rechecks_exact_profile_pair_scope_and_owner_stop(self):
        await self.connect()
        base={'device_id':self.device,'base':self.base,'name':'Clipboard','arguments':{'mode':'get'}}
        for patch_args in ({'device_id':'b'*32},{'base':'https://other.invalid'},
                           {'name':'PowerShell','arguments':{'command':RAW}}):
            with self.assertRaises(PermissionError):commands.invoke(self.owner,'windows_mcp_remote_call',dict(base,**patch_args))
        self.owner.stop.set()
        with self.assertRaises(PermissionError):commands.invoke(self.owner,'windows_mcp_remote_call',base)
        self.owner.stop.clear();self.cfg['computer']['remote']['enabled']=False;self.write()
        with self.assertRaises(PermissionError):commands.invoke(self.owner,'windows_mcp_remote_call',base)
        self.manager.call.assert_not_called()

    async def test_status_is_fixed_target_metadata_without_body_or_desktop_effect(self):
        socket=await self.connect()
        async def answer():
            request=await socket.receive_json(timeout=3)
            self.assertEqual(request['path'],f'/v1/controller/{self.device}/windows-mcp/status')
            self.assertEqual(request['method'],'GET')
            response=await self.publisher.execute(request,self.http);await socket.send_json(response)
        task=asyncio.create_task(answer())
        with patch.object(runtime,'component_status',return_value={'id':'windows-mcp','installed':True,'runtime':{'running':True,'tools':21}}):
            result=await asyncio.to_thread(client.remote_request,self.root,body,'status');await task
        self.assertTrue(result['runtime']['running']);self.assertEqual(result['runtime']['tools'],21)
        self.manager.call.assert_not_called()

    async def test_owner_ui_status_uses_same_target_and_exports_only_public_language_neutral_metadata(self):
        socket=await self.connect()
        async def answer():
            request=await socket.receive_json(timeout=3)
            response=await self.publisher.execute(request,self.http);await socket.send_json(response)
        task=asyncio.create_task(answer())
        component={'id':'windows-mcp','installed':True,'path':RAW,'private':'never-export',
                   'runtime':{'running':True,'state':'running','tools':21,'error':RAW}}
        with patch.object(runtime,'component_status',return_value=component):
            async with self.http.get(self.base+remote_computer.PREFIX+'/windows-mcp',headers={'Authorization':'Bearer '+self.owner_key}) as response:
                self.assertEqual(response.status,200);value=await response.json()
            await task
        self.assertEqual(value,{'device_id':self.device,'state':'confirmed','component':{
            'id':'windows-mcp','installed':True,'runtime':{'running':True,'state':'running','tools':21}}})
        for token in (self.controller,self.credential['token']):
            async with self.http.get(self.base+remote_computer.PREFIX+'/windows-mcp',headers={'Authorization':'Bearer '+token}) as response:
                self.assertEqual(response.status,403)
        self.manager.call.assert_not_called()

    async def test_unpaired_offline_and_switched_target_status_never_claim_component_absent(self):
        self.assertEqual((await self.relay.mcp_status())['state'],'offline')
        self.relay.select('');self.assertEqual((await self.relay.mcp_status())['state'],'unpaired')
        self.relay.select(self.device);socket=await self.connect()
        status=asyncio.create_task(self.relay.mcp_status());request=await socket.receive_json(timeout=3)
        other=self.relay.pair('Other Windows',[]);self.relay.select(other['id'])
        raw=json.dumps({'host':'windows','device_id':self.device,'result':{'id':'windows-mcp','installed':True,
            'runtime':{'running':True,'state':'running','tools':21}}}).encode()
        await socket.send_json({'kind':'response','id':request['id'],'status':200,'content_type':'application/json',
            'body':base64.b64encode(raw).decode()})
        result=await status;self.assertEqual(result['state'],'unknown');self.assertIsNone(result['component'])


if __name__=='__main__':unittest.main()
