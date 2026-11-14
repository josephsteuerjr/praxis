"""Native captions share the canonical catalogs without carrying profile data."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from localharness import ui_language


class NativeLanguage(unittest.TestCase):
    def test_all_sixteen_ui_frame_pairs_and_private_fields_stay_separate(self):
        for ui in ('ru','en','fr','zh'):
            for frame in ('ru','en','fr','zh'):
                config={'lang':ui,'agent':{'frame_language':frame,'name':'PRIVATE_NAME'},
                        'model':{'key':'PRIVATE_KEY'},'soul':'PRIVATE_SOUL'}
                before=copy.deepcopy(config)
                result=ui_language.bootstrap_data(Path('unused'),config=config,tags=['fr-FR'])
                self.assertEqual(result['lang'],ui);self.assertEqual(result['frame_language'],frame)
                self.assertEqual(result['system_language'],'fr')
                self.assertEqual(config,before)
                self.assertNotIn('PRIVATE_',json.dumps(result))
                for language,catalog in result['catalogs'].items():
                    self.assertEqual(catalog['language.interface'],ui_language.text('language.interface',language))

    def test_parameters_are_strict_and_values_are_never_translated(self):
        key='ui.ui.kit.window.release.notice.5dc8127aa8'
        raw='AUTHORED 日本語 <script>inert</script>'
        for ui in ('ru','en','fr','zh'):
            self.assertIn(raw,ui_language.text(key,ui,{'p0':raw}))
            with self.assertRaises(ValueError):ui_language.text(key,ui,{})
            with self.assertRaises(ValueError):ui_language.text(key,ui,{'p0':raw,'extra':'value'})
        with self.assertRaises(KeyError):ui_language.text('UNKNOWN_CAPTION','en')

    def test_automatic_interface_choice_does_not_invent_a_frame_choice(self):
        value=ui_language.bootstrap_data(Path('unused'),config={},tags=['de-DE','zh-Hans-CN'])
        self.assertEqual(value['lang'],'zh');self.assertEqual(value['preference'],'')
        self.assertIsNone(value['frame_language'])
        self.assertEqual(ui_language.bootstrap_data(Path('unused'),config={},tags=['zh-Hant'])['lang'],'en')

    def test_script_only_includes_ui_metadata_from_actual_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'helene.json').write_text(json.dumps({'lang':'en','agent':{'frame_language':'fr'},
                'model':{'key':'DO_NOT_EXPOSE'},'authored':'DO_NOT_EXPOSE'}),encoding='utf-8')
            script=ui_language.bootstrap_script(root)
            self.assertTrue(script.startswith('window.HELENE_UI = '))
            data=json.loads(script[len('window.HELENE_UI = '):-1])
            self.assertEqual(data['lang'],'en');self.assertEqual(data['frame_language'],'fr')
            self.assertNotIn('DO_NOT_EXPOSE',script)
            self.assertNotIn('</script',script.lower())

    def test_real_first_start_http_serves_catalogs_and_requires_window_token(self):
        from first_start_window import FirstStartWindow
        resources=Path(__file__).resolve().parents[1]/'resources'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);ui=root/'app/resources';ui.mkdir(parents=True)
            for name in ('first-start.html','ui-language.js'):
                (ui/name).write_bytes((resources/name).read_bytes())
            config={'lang':'zh','agent':{'frame_language':'fr'},'model':{'key':'PRIVATE_KEY'}}
            (root/'helene.json').write_text(json.dumps(config),encoding='utf-8')
            with patch('localharness.program_state.window_control',return_value=root/'control.json'):
                app=FirstStartWindow(root,None)
            thread=threading.Thread(target=app.server.serve_forever,daemon=True);thread.start()
            try:
                body=urlopen(app.origin,timeout=5).read().decode('utf-8')
                start=body.index('window.HELENE_UI = ')+len('window.HELENE_UI = ')
                data=json.JSONDecoder().raw_decode(body[start:])[0]
                self.assertEqual(data['lang'],'zh');self.assertEqual(data['frame_language'],'fr')
                self.assertNotIn('PRIVATE_KEY',body)
                self.assertEqual(urlopen(app.origin+'/ui-language.js',timeout=5).read(),(resources/'ui-language.js').read_bytes())
                request=Request(app.origin+'/__python__/invoke',data=b'{"command":"first_status","args":{}}',
                                headers={'Content-Type':'application/json'})
                with self.assertRaises(HTTPError) as denied:urlopen(request,timeout=5)
                self.assertEqual(denied.exception.code,403)
                request.add_header('X-Helene-Window',app.token)
                status=json.loads(urlopen(request,timeout=5).read())
                self.assertEqual(status['result']['stage'],'starting')
                self.assertIsNone(app.worker)
                self.assertEqual(json.loads((root/'helene.json').read_text(encoding='utf-8')),config)
            finally:
                app.server.shutdown();app.server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
