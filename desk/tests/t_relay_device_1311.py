"""Device sign-in across the desktop, owner channel and root relay boundary."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness import python_relay, relay_login
from deskd import phone_settings, relay_control

LOGIN_ID = '3a1368dc-7451-4476-9495-5783313c10ca'


def raw_state():
    return {'port': 5011, 'api_key': 'PRIVATE-API', 'auth_directory': 'PRIVATE-DIR',
            'accounts': [{'slot': 'primary', 'configured': True, 'active': True},
                         {'slot': 'secondary', 'configured': False, 'active': False}],
            'login': {'pending': True, 'slot': 'secondary', 'method': 'device_code', 'id': LOGIN_ID},
            'device_code': {'verification_url': relay_login.DEVICE_URL, 'user_code': 'ABCD-EFGH',
                            'expires_at': int(time.time()) + 400, 'device_auth_id': 'PRIVATE-DEVICE'},
            'tokens': {'refresh_token': 'PRIVATE-REFRESH'}}


class DeviceLogin(unittest.TestCase):
    def test_projection_returns_only_public_prompt_and_slot_state(self):
        value = relay_login.public_state(raw_state())
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertEqual(value['login']['id'], LOGIN_ID)
        self.assertEqual(value['login']['device_code']['user_code'], 'ABCD-EFGH')
        self.assertEqual(value['slots'][0], {'slot':'primary','stored':True,'active':True})
        self.assertEqual(relay_login.account_view(value)['slots'][1]['pending'], True)

    def test_cancel_completion_expiry_and_bad_origin_do_not_expose_code(self):
        for mutation in ('ended', 'expired', 'bad-origin', 'injection', 'far-future'):
            row = raw_state()
            if mutation == 'ended': row['login']['pending'] = False
            if mutation == 'expired': row['device_code']['expires_at'] = time.time() - 1
            if mutation == 'bad-origin': row['device_code']['verification_url'] = 'https://auth.openai.com.evil.invalid/codex/device'
            if mutation == 'injection': row['device_code']['user_code'] = '<img onerror=alert(1)>'
            if mutation == 'far-future': row['device_code']['expires_at'] = time.time() + 36000
            value = relay_login.public_state(row)
            self.assertIsNone(value['login']['device_code'], mutation)
            self.assertIsNone(value['login']['url'], mutation)

    def test_browser_link_validation(self):
        for url in ('https://evil.invalid/', 'https://auth.openai.com@evil.invalid/',
                    'http://auth.openai.com/', 'https://auth.openai.com:443/', 'javascript:alert(1)'):
            self.assertIsNone(relay_login.login_url(url))
        self.assertEqual(relay_login.login_url('https://auth.openai.com/oauth/authorize?state=public'),
                         'https://auth.openai.com/oauth/authorize?state=public')

    def test_device_default_browser_explicit_and_id_scoped_cancel(self):
        with patch.object(python_relay, 'control', return_value=raw_state()) as invoke:
            agent = object()
            relay_login.action(agent, {'action':'login', 'slot':'secondary'})
            self.assertEqual(invoke.call_args.args, (agent, 'login_device'))
            self.assertEqual(invoke.call_args.kwargs['slot'], 'secondary')
            relay_login.action(agent, {'action':'login', 'method':'browser'})
            self.assertEqual(invoke.call_args.args[1], 'login')
            relay_login.action(agent, {'action':'cancel_login', 'login_id':LOGIN_ID})
            self.assertEqual(invoke.call_args.args[1], 'cancel_login_id')
            self.assertEqual(invoke.call_args.kwargs['slot'], LOGIN_ID)
            for body in ({'action':'cancel_login'}, {'action':'login','method':'shell'},
                         {'action':'login','slot':'../secret'}, {'action':'configure'},
                         {'action':'login','method':'browser'}):
                invoke.reset_mock()
                with self.assertRaises(ValueError): relay_login.action(agent, body, browser=False)
                invoke.assert_not_called()

    def test_desktop_uses_same_projection_and_slot_scoped_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp); config = tree/'helene.json'; config.write_text('{}')
            agent = SimpleNamespace(tree=tree, config=config)
            owner = SimpleNamespace(current=lambda: agent)
            with patch.object(python_relay,'control',return_value=raw_state()):
                view = python_relay.invoke(owner, 'relay_account', {})
                self.assertNotIn('PRIVATE', json.dumps(view))
                self.assertEqual(view['login']['id'], LOGIN_ID)
                self.assertIsNone(python_relay.invoke(owner,'relay_login_url',{'slot':'primary'}))
                self.assertEqual(python_relay.invoke(owner,'relay_login_url',{'slot':'secondary'}), relay_login.DEVICE_URL)

    def test_server_channel_never_reads_root_control_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = root/'helene.json'
            config.write_text(json.dumps({'relay':{'enabled':True}}))
            with patch.dict(os.environ, {'HELENE_SUPERVISOR':'serverboot'}), \
                 patch.object(phone_settings.readers,'config_path',return_value=config), \
                 patch.object(phone_settings.readers,'tree',return_value=root), \
                 patch.object(relay_control,'request',return_value={'available':True}) as broker, \
                 patch.object(python_relay,'control',side_effect=AssertionError('private descriptor')):
                self.assertEqual(phone_settings.subscription(), {'available':True})
                self.assertEqual(broker.call_args.args, (root, {'action':'status'}))
                body = {'action':'login','slot':'secondary','method':'device_code'}
                phone_settings.subscription(body)
                self.assertEqual(broker.call_args.args, (root, body))


class SupervisorKeepsPendingLogin(unittest.TestCase):
    """Real control() against a local relay endpoint, real supervisor marker read.

    2.7.1 bumped login-generation as soon as a login started; the desktop
    supervisor then restarted the relay and the pending sign-in died with it.
    """

    def setUp(self):
        import http.server
        import threading
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.tree = Path(self.temp.name)
        self.home = self.tree/'relay'
        (self.home/'local_auth').mkdir(parents=True)
        self.ops = []
        ops = self.ops

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.headers.get('X-Relay-Control') != 'control-token':
                    self.send_response(403); self.end_headers(); return
                ops.append(body)
                raw = json.dumps(raw_state()).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        (self.home/'local_auth/control.json').write_text(json.dumps(
            {'port': server.server_address[1], 'token': 'control-token'}), encoding='utf-8')
        self.agent = SimpleNamespace(tree=self.tree, config=self.tree/'helene.json')
        self.agent.config.write_text('{}')
        self.owner = SimpleNamespace(current=lambda: self.agent)

    def generation(self):
        from localharness.desktop_supervisor import _relay_login_generation
        return _relay_login_generation(SimpleNamespace(cwd=self.home))

    def test_starting_either_login_does_not_restart_the_relay(self):
        python_relay.changed(self.home)
        before = self.generation()
        for method in ('device_code', 'browser'):
            view = python_relay.invoke(self.owner, 'relay_login', {'slot': 'secondary', 'method': method})
            self.assertEqual(self.generation(), before, method)
            self.assertTrue(view['login']['pending'], method)
        python_relay.invoke(self.owner, 'relay_cancel_login', {'slot': 'secondary', 'login_id': LOGIN_ID})
        self.assertEqual(self.generation(), before)
        self.assertEqual([op['op'] for op in self.ops], ['login_device', 'login', 'cancel_login_id'])

    def test_completed_account_changes_still_refresh_the_relay(self):
        for command in ('relay_logout', 'relay_switch'):
            before = self.generation()
            time.sleep(0.002)
            try:
                python_relay.invoke(self.owner, command, {'slot': 'primary'})
            except ValueError:
                pass  # fixture reports primary as active already; marker is what matters
            self.assertNotEqual(self.generation(), before, command)


@unittest.skipUnless(sys.platform.startswith('linux') and hasattr(socket,'SO_PEERCRED'), 'Linux peer credentials')
class ServerBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.tree = Path(self.temp.name); self.config = self.tree/'helene.json'
        self.config.write_text(json.dumps({'relay':{'enabled':True}}))

    def start(self, **kwargs):
        server = relay_control.start(self.tree, self.config, **kwargs)
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return server

    def test_real_socket_returns_public_state_and_rejects_commands(self):
        self.start()
        with patch.object(python_relay,'control',return_value=raw_state()) as invoke:
            value = relay_control.request(self.tree, {'action':'status'})
            self.assertNotIn('PRIVATE', json.dumps(value)); self.assertFalse(value['browser_login'])
            for action in ('configure','shell','install'):
                invoke.reset_mock()
                with self.assertRaises(ValueError): relay_control.request(self.tree,{'action':action})
                invoke.assert_not_called()

    def test_stop_refuses_mutation_but_retains_status(self):
        self.start(stopped=lambda:True)
        with patch.object(python_relay,'control',return_value=raw_state()) as invoke:
            with self.assertRaisesRegex(ValueError,'стоп'): relay_control.request(self.tree,{'action':'login'})
            invoke.assert_not_called()
            self.assertTrue(relay_control.request(self.tree,{'action':'status'})['available'])

    @unittest.skipUnless(getattr(os,'geteuid',lambda:1)() == 0, 'Root permission fixture')
    def test_real_desk_peer_can_login_agent_cannot_read_socket_or_auth(self):
        os.chmod(self.tree, 0o755)
        secret = self.tree/'relay/local_auth/control.json'; secret.parent.mkdir(parents=True)
        secret.write_text('PRIVATE-CONTROL'); os.chmod(secret,0o600)
        marker=secret.with_name('login-generation');marker.write_text('PRIVATE-GENERATION');os.chmod(marker,0o600)
        self.start(uid=65534,gid=65534)
        script = """import json,socket,sys
s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall(b'{"action":"login","slot":"secondary"}\\n')
print(s.makefile('rb').readline().decode())
"""
        with patch.object(python_relay,'control',return_value=raw_state()) as invoke:
            result = subprocess.run([sys.executable,'-c',script,str(relay_control.address(self.tree))],
                user=65534,group=65534,extra_groups=[],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['ok']);self.assertNotIn('PRIVATE',result.stdout)
            self.assertEqual(invoke.call_args.args[1],'login_device')
            revision=json.loads(result.stdout)['state']['catalog_revision']
            probe="import os,sys;sys.path.insert(0,sys.argv[1]);os.environ['HELENE_SUPERVISOR']='serverboot';from localharness.model_catalog import relay_revision;from pathlib import Path;print(relay_revision(Path(sys.argv[2])))"
            observed=subprocess.run([sys.executable,'-c',probe,str(DESK),str(self.tree)],user=65534,group=65534,extra_groups=[],capture_output=True,text=True,timeout=10)
            self.assertEqual(observed.returncode,0,observed.stderr);self.assertEqual(observed.stdout.strip(),revision)
            denied = subprocess.run([sys.executable,'-c',script,str(relay_control.address(self.tree))],
                user=65533,group=65533,extra_groups=[],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(denied.returncode,0)
            for uid in (65534,65533):
                denied = subprocess.run([sys.executable,'-c','import pathlib,sys;pathlib.Path(sys.argv[1]).read_bytes()',str(secret)],
                    user=uid,group=uid,extra_groups=[],capture_output=True,text=True,timeout=10)
                self.assertNotEqual(denied.returncode,0)


if __name__ == '__main__': unittest.main()
