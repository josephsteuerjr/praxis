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


class Presentation(unittest.TestCase):
    """The phone shows the whole programme but never acts on the computer's desktop."""

    def host(self, root, config):
        import contextlib, threading
        invoked = []

        class Owner:
            selected = 'base'
            def __init__(self): self.root = root
            def _default(self): return 'base'
            @contextlib.contextmanager
            def selection(self, identity): yield
            def current(self): return SimpleNamespace(config=config, enabled=True, conflict=False)
            def invoke(self, command, arguments):
                invoked.append((command, arguments))
                if command == 'config_load':
                    return {'config': json.loads(config.read_text(encoding='utf-8')), 'path': str(config)}
                return {'ok': True}
        host = object.__new__(session_host.Host)
        host.root = root
        host.owner = Owner()
        host.stop = threading.Event()
        host.migration_request = None
        host.lock = threading.RLock()
        host.observers = {}
        host.sequence = 0
        host.events = []
        return host, invoked

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'helene.json'
        self.config.write_text(json.dumps({'mode': 'remote', 'base': 'https://agent.example', 'key': 'window-key',
                                           'model': {'key': 'owner-model-key'}}), encoding='utf-8')
        patcher = patch.object(session_host.agents, 'find', return_value=SimpleNamespace(id='base'))
        patcher.start(); self.addCleanup(patcher.stop)

    def call(self, host, command, arguments):
        return host.dispatch('invoke', {'agent': 'base', 'command': command, 'arguments': arguments, 'presentation': 'miniapp'})

    def test_computer_desktop_actions_are_refused_with_a_reason_and_never_reach_the_controller(self):
        host, invoked = self.host(self.root, self.config)
        for command, arguments in (('reveal_path', {'path': 'C:/x'}), ('install_service', {}),
                                   ('open_privacy_pane', {'kind': 'screen'}), ('open_path', {'path': 'C:/Users/me/x.log'}),
                                   ('tray_exit', {})):
            with self.assertRaisesRegex(ValueError, '^На компьютере: '):
                self.call(host, command, arguments)
        self.assertEqual(invoked, [])
        self.call(host, 'open_path', {'path': 'https://example.org/'})
        self.assertEqual(invoked[-1][0], 'open_path')
        # The native window keeps every desktop action.
        host.dispatch('invoke', {'agent': 'base', 'command': 'reveal_path', 'arguments': {'path': 'C:/x'}})
        self.assertEqual(invoked[-1][0], 'reveal_path')

    def test_channel_key_stays_on_the_computer_and_survives_a_phone_save(self):
        host, invoked = self.host(self.root, self.config)
        loaded = self.call(host, 'config_load', {})
        self.assertNotIn('window-key', json.dumps(loaded))
        self.assertEqual(loaded['config']['model']['key'], 'owner-model-key')  # the owner's own setting
        self.call(host, 'config_save', {'config': json.dumps(loaded['config']), 'mtimeNs': '1'})
        self.assertEqual(json.loads(invoked[-1][1]['config'])['key'], 'window-key')
        typed = {**loaded['config'], 'key': 'new-window-key'}
        self.call(host, 'config_save', {'config': json.dumps(typed), 'mtimeNs': '1'})
        self.assertEqual(json.loads(invoked[-1][1]['config'])['key'], 'new-window-key')
        native = host.dispatch('invoke', {'agent': 'base', 'command': 'config_load', 'arguments': {}})
        self.assertEqual(native['config']['key'], 'window-key')

    def test_full_restart_is_one_addressed_window_or_an_honest_refusal(self):
        host, invoked = self.host(self.root, self.config)
        with self.assertRaisesRegex(ValueError, 'Окно программы на компьютере закрыто'):
            self.call(host, 'restart_self', {'full': True})
        host.observers = {'b' * 32: {}, 'a' * 32: {}}
        self.assertEqual(self.call(host, 'restart_self', {'full': True}), {'ok': True, 'restarting': True})
        self.assertEqual(host.events[-1]['name'], 'program-restart')
        self.assertEqual(host.events[-1]['data'], {'observer': 'a' * 32})
        self.assertEqual(host.events[-1]['agent'], '')
        self.assertEqual(invoked, [])

    def test_the_addressed_window_restarts_the_whole_programme(self):
        import python_app
        app = object.__new__(python_app.Application)
        calls = []
        app.host = SimpleNamespace(observer='a' * 32)
        app.invoke = lambda command, args: calls.append((command, args))
        python_app.Application.event(app, 'program-restart', {'observer': 'b' * 32})
        self.assertEqual(calls, [])
        python_app.Application.event(app, 'program-restart', {'observer': 'a' * 32})
        self.assertEqual(calls, [('restart_self', {'full': True})])


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
