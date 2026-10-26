"""Focused Python application consumer checks (temporary owners, never live Hélène)."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import Future
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
import python_app
from localharness import app_components, app_migration, essential_backup


class Host:
    def __init__(self):
        self.ready = Future()
        self.ready.set_result({"script": 'window.DESK_CONFIG_OVERRIDE = {"base":"http://127.0.0.1:8094","key":"test-only"};'})
        self.calls = []
    def invoke(self, command, args):
        self.calls.append((command, args))
        return {"ok": True}
    def close(self):
        pass


class PythonApplicationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows native error dialog')
    def test_startup_failure_is_logged_and_shown(self):
        from unittest.mock import patch
        import ctypes
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(python_app, '_main', side_effect=RuntimeError('fixture startup failure')):
                with patch.object(ctypes.windll.user32, 'MessageBoxW') as message:
                    self.assertEqual(python_app.main(['--root', temporary]), 1)
            self.assertIn('fixture startup failure', (Path(temporary) / 'startup-error.log').read_text(encoding='utf-8'))
            self.assertIn('fixture startup failure', message.call_args.args[1])

    def test_automatic_start_does_not_clear_stop_or_create_window(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            with patch('localharness.owner_stop.stopped', return_value=True):
                with patch.object(python_app, 'Application') as application:
                    self.assertEqual(python_app.main(['--root', temporary, '--launch-intent', 'automatic']), 0)
            application.assert_not_called()

    def test_close_hides_but_exit_and_relaunch_destroy(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            app = python_app.Application(Path(temporary), Path(temporary))
            try:
                app.window = Mock()
                app.tray = object()
                app.invoke('python_window', {'action': 'close'})
                app.window.hide.assert_called_once()
                app.window.destroy.assert_not_called()
                self.assertFalse(app.stopping.is_set())
                app.invoke('python_window', {'action': 'exit'})
                app.window.destroy.assert_called_once()
                self.assertTrue(app.exiting)
                self.assertIn("win('exit')", app.bridge())
            finally:
                app.server.server_close()

    def test_migration_proof_checks_base_not_selected_remote_agent(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        primary = SimpleNamespace(id='main')
        remote = SimpleNamespace(id='secondary')
        host = Mock()
        host.readiness.side_effect = lambda **args: ({'remote':True} if args['agent'] is remote
                                                    else {'tree':'main-tree','channel_pid':123})
        with patch('localharness.agents.find', return_value=primary), patch('localharness.agents.raisable', return_value=[primary,remote]):
            proof = python_app.migration_proof(host, Path('fixture'))
        self.assertEqual(proof['tree'], 'main-tree')
        self.assertTrue(proof['agents'][1]['remote'])

    def test_preferences_survive_origin_change_and_are_installation_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = python_app.Application(root/'one', root)
            second = python_app.Application(root/'one', root)
            other = python_app.Application(root/'two', root)
            try:
                self.assertNotEqual(first.origin, second.origin)
                first.invoke('window_preferences', {'key':'frame.panel', 'value':'0'})
                self.assertEqual(second.preferences(), {'frame.panel':'0'})
                self.assertEqual(other.preferences(), {})
                self.assertIn('frame.panel', second.bridge())
                with self.assertRaises(ValueError):
                    first.invoke('window_preferences', {'key':'credential', 'value':'not-allowed'})
                second.invoke('window_preferences', {'key':'frame.panel', 'value':None})
                self.assertEqual(first.preferences(), {})
            finally:
                for app in (first, second, other): app.server.server_close()

    def test_window_fits_logical_work_area_with_taskbar(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        screen = SimpleNamespace(x=0, y=0, width=1536, height=864, scale=1.25,
                                 frame=SimpleNamespace(X=0, Y=0, Width=1536, Height=816))
        with patch.object(python_app.sys, 'platform', 'win32'):
            geometry = python_app.window_geometry(screen)
        self.assertEqual(geometry, {'width':1360, 'height':768, 'x':88, 'y':24})
        screen.frame = SimpleNamespace(X=-1280, Y=40, Width=1280, Height=680)
        with patch.object(python_app.sys, 'platform', 'win32'):
            geometry = python_app.window_geometry(screen)
        self.assertGreaterEqual(geometry['x'], -1280)
        self.assertLessEqual(geometry['y'] + geometry['height'], 720)

    def test_maximize_restores_without_entering_fullscreen(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        class Event:
            def __iadd__(self, callback):
                self.callback = callback
                return self
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = python_app.Application(root, root)
            try:
                app.window = Mock()
                app.window.events = SimpleNamespace(maximized=Event(), restored=Event(), shown=Event())
                python_app.bind_window_state(app)
                app.invoke('python_window', {'action':'maximize'})
                app.window.maximize.assert_called_once()
                app.window.events.maximized.callback()
                app.invoke('python_window', {'action':'maximize'})
                app.window.restore.assert_called_once()
                app.window.events.restored.callback()
                self.assertFalse(app.window_maximized)
                app.window.toggle_fullscreen.assert_not_called()
            finally:
                app.server.server_close()

    def test_real_http_window_bridge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'index.html').write_text('<html><head></head><body>same UI</body></html>')
            host = Host()
            app = python_app.Application(root, root, host)
            app.serve()
            try:
                text = urllib.request.urlopen(app.origin).read().decode()
                self.assertIn("shell:'python'", text)
                self.assertLess(text.index('DESK_CONFIG_OVERRIDE'), text.index('</head>'))
                data = json.dumps({'command': 'config_load', 'args': {}}).encode()
                request = urllib.request.Request(app.origin + '/__python__/invoke', data=data,
                    headers={'Content-Type':'application/json','X-Helene-Window':app.token})
                self.assertEqual(json.load(urllib.request.urlopen(request)), {'result': {'ok': True}})
                self.assertEqual(host.calls, [('config_load', {})])
                request = urllib.request.Request(app.origin + '/__python__/invoke', data=data,
                                                headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertEqual(caught.exception.code, 403)
            finally:
                app.close()

    def test_migration_preserves_resolved_data_and_small_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new, home = root/'old', root/'new', root/'home'
            tree = old/'private-data'
            (tree/'soul').mkdir(parents=True)
            (tree/'soul'/'SOUL.md').write_text('agent words')
            (tree/'models'/'xtts').mkdir(parents=True)
            (tree/'models'/'xtts'/'weight.bin').write_bytes(b'weights')
            (tree/'workspace'/'project').mkdir(parents=True)
            (tree/'workspace'/'project'/'unique.py').write_text('unique work')
            (old/'helene.json').write_text(json.dumps({'tree':'private-data','mode':'local'}))
            (old/'window-preferences.json').write_text('{"frame.panel":"1"}')
            note = app_migration.prepare(old,new,home)
            self.assertEqual(json.loads((new/'window-preferences.json').read_text()),{'frame.panel':'1'})
            config = essential_backup.load(new/'helene.json')
            moved = Path(config['tree'])
            self.assertTrue(moved.is_absolute())
            self.assertEqual((moved/'workspace/project/unique.py').read_text(), 'unique work')
            self.assertTrue((tree/'soul/SOUL.md').exists())
            with zipfile.ZipFile(note['backup']) as archive:
                self.assertIn('data/soul/SOUL.md', archive.namelist())
                self.assertFalse(any('weight.bin' in p for p in archive.namelist()))
            with self.assertRaises(ValueError):
                app_migration.retire_program(new,note['id'],['old.exe'])
            app_migration.confirm_started(new,note['id'],proof={'tree':note['data_paths'][0],'channel_pid':123})
            self.assertEqual(essential_backup.load(new/'python-migration.json')['state'],'new_app_started')

    def test_external_data_stays_in_place(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old=root/'old';old.mkdir();data=root/'external';data.mkdir()
            (old/'helene.json').write_text(json.dumps({'tree':str(data)}))
            note=app_migration.prepare(old,root/'new',root/'home')
            self.assertEqual(note['data_paths'],[str(data.resolve())])
            self.assertEqual(note['copied_data'],[])

    def test_component_inventory_to_installed_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);owner=root/'owner';owner.mkdir()
            entries={}
            for name in ('relay',):
                base=root/'components'/name
                files={}
                for relative,content in [('binary/'+name+'.exe',b'executable'),('source/main.rs',b'fn main() {}'),('toolchain/compiler',b'compiler')]:
                    path=base/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
                    files[relative]=app_components.digest(path)
                entries[name]={'files':files,'executable':'binary/'+name+'.exe','install_name':name+'.exe'}
            (root/'components/components.json').write_text(json.dumps({'format':'helene-components.v1','components':entries}))
            self.assertTrue(app_components.install(root,owner,'relay')['ok'])
            self.assertEqual((owner/'relay.exe').read_bytes(),b'executable')
            self.assertTrue(next(r for r in app_components.status(root,owner) if r['id']=='relay')['installed'])
            (root/'components/relay/source/main.rs').write_text('changed')
            with self.assertRaises(ValueError):app_components.install(root,owner,'relay')

    def test_agent_server_config_uses_the_server_writer(self):
        import os
        from unittest.mock import patch
        sys.path.insert(0, str(DESK.parent/'helene/core'))
        import app_settings
        with tempfile.TemporaryDirectory() as temporary:
            config=Path(temporary)/'helene.json'
            config.write_text(json.dumps({'tree':'private','voice':{'speak':False},'port':8094}),encoding='utf-8')
            with patch.dict(os.environ,{'HELENE_CONFIG':str(config)}):
                before=app_settings.configure()
                self.assertTrue(before['ok'])
                saved=app_settings.configure('save',{'voice':{'speak':True,'tts_speaker':'Ana Florence','tts_language':'ru'}},before['mtime_ns'])
                self.assertTrue(saved['ok'])
                self.assertTrue(app_settings.configure()['config']['voice']['speak'])
                self.assertEqual(json.loads(config.read_text())['port'],8094)
                self.assertEqual(app_settings.configure('save',{'voice':{}},before['mtime_ns'])['code'],'conflict')

    def test_retirement_cannot_remove_old_or_new_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';tree=old/'data';tree.mkdir(parents=True)
            (tree/'unique.txt').write_text('keep')
            (old/'helene.json').write_text(json.dumps({'tree':'data'}))
            note=app_migration.prepare(old,root/'new',root/'home')
            app_migration.confirm_started(root/'new',note['id'],proof={'tree':note['data_paths'][0],'channel_pid':123})
            with self.assertRaises(ValueError):app_migration.retire_program(root/'new',note['id'],['data/unique.txt'])
            self.assertEqual((tree/'unique.txt').read_text(),'keep')

    @unittest.skipUnless(sys.platform=='win32','Windows process owner')
    def test_speech_spec_is_admitted_by_the_real_coordinator(self):
        sys.path.insert(0,str(DESK/'localharness'))
        import desktop_supervisor as owner
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);tree=root/'private';tree.mkdir()
            for relative in ('voice-xtts/python.exe','private/models/xtts/model.pth','deskapp.py'):
                p=root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture')
            config=root/'helene.json'
            config.write_text(json.dumps({'mode':'local','tree':'private','code':'code','runner':'','voice':{'speak':True}}))
            _,specs=owner._specs(config)
            self.assertEqual(specs['main:xtts'].role,'xtts')
            self.assertEqual(Path(specs['main:xtts'].argv[2]), DESK/'localharness/speech_worker.py')
            self.assertFalse((root/'code/xtts_tts_worker.py').exists())
            coordinator=owner.Coordinator({key:spec.role for key,spec in specs.items()})
            actions=coordinator.tick({'seq':1,'now_ms':0,'children':{key:{'state':'absent'} for key in specs}})
            self.assertIn({'id':'main:xtts','action':'spawn'},actions)


if __name__ == '__main__':
    unittest.main()
