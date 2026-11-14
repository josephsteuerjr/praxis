"""Actual loopback migration/bootstrap and live native menu preferences."""
import json
from pathlib import Path
import tempfile,threading,unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from localharness import ui_language


class NativeMigrationLanguage(unittest.TestCase):
    def test_native_menu_refresh_reads_ui_preference_without_changing_frame(self):
        import python_tray
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);menu=SimpleNamespace(Controls=[SimpleNamespace(),SimpleNamespace()])
            for lang in ('ru','en','fr','zh'):
                config={'lang':lang,'agent':{'frame_language':'fr'},'name':'PRIVATE'}
                (root/'helene.json').write_text(json.dumps(config),encoding='utf-8')
                python_tray._refresh_captions(menu,root)
                self.assertEqual([b.Text for b in menu.Controls],list(python_tray._captions(root)))
                self.assertEqual(menu.Controls[0].Text,ui_language.text('native.tray.open',lang))
                self.assertEqual(menu.Controls[1].AccessibleName,ui_language.text('native.tray.exit',lang))
                self.assertEqual(json.loads((root/'helene.json').read_text()),config)

    def test_real_migration_http_localizes_owned_errors_and_uses_old_ui_preference(self):
        from python_migration_window import MigrationWindow
        resources=Path(__file__).resolve().parents[1]/'resources'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'new';old=Path(tmp)/'old'
            ui=root/'app/resources';ui.mkdir(parents=True);old.mkdir()
            for name in ('python-migration.html','ui-language.js'):
                (ui/name).write_bytes((resources/name).read_bytes())
            config={'lang':'en','agent':{'frame_language':'zh'},'model':{'key':'PRIVATE_KEY'}}
            (old/'helene.json').write_text(json.dumps(config),encoding='utf-8')
            with patch('localharness.program_state.window_control',return_value=root/'control.json'):
                app=MigrationWindow(root,str(old))
            thread=threading.Thread(target=app.server.serve_forever,daemon=True);thread.start()
            try:
                for lang in ('ru','en','fr','zh'):
                    config['lang']=lang
                    (old/'helene.json').write_text(json.dumps(config),encoding='utf-8')
                    body=urlopen(app.origin,timeout=5).read().decode('utf-8')
                    start=body.index('window.HELENE_UI = ')+len('window.HELENE_UI = ')
                    bootstrap=json.JSONDecoder().raw_decode(body[start:])[0]
                    self.assertEqual(bootstrap['lang'],lang);self.assertEqual(bootstrap['frame_language'],'zh')
                    self.assertNotIn('PRIVATE_KEY',body)
                    request=Request(app.origin+'/__python__/invoke',data=b'{"command":"migration_status","args":{"id":"wrong"}}',headers={'Content-Type':'application/json'})
                    with self.assertRaises(HTTPError) as denied:urlopen(request,timeout=5)
                    self.assertEqual(denied.exception.code,403)
                    request.add_header('X-Helene-Window',app.token)
                    try:
                        response=urlopen(request,timeout=5)
                    except HTTPError as error:
                        response=error
                    result=json.loads(response.read())
                    self.assertEqual(result['error'],ui_language.text('native.migration.no_job',lang))
                self.assertFalse((root/'helene.json').exists())
                self.assertEqual(json.loads((old/'helene.json').read_text()),config)
                self.assertFalse(app.running.locked())
                self.assertEqual(urlopen(app.origin+'/ui-language.js',timeout=5).read(),(resources/'ui-language.js').read_bytes())
            finally:
                app.server.shutdown();app.server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
