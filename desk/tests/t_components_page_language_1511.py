"""The actual pre-session route renders the UI axis without assuming readiness."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import python_app
from localharness import components_page,ui_language


class ComponentsPage(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)

    def test_actual_http_route_uses_interface_language_for16_independent_pairs(self):
        app=python_app.Application(self.root,self.root);app.serve()
        self.addCleanup(app.server.server_close);self.addCleanup(app.server.shutdown)
        for ui in ('ru','en','fr','zh'):
            for frame in ('ru','en','fr','zh'):
                config={'lang':ui,'agent':{'frame_language':frame,'name':'Literal Русский 中文'}}
                path=self.root/'helene.json';path.write_text(json.dumps(config),encoding='utf8');before=path.read_bytes()
                with urllib.request.urlopen(app.origin+'/',timeout=3) as response:
                    self.assertEqual(response.status,200);document=response.read().decode('utf8')
                data=json.loads(re.search(r'<script id="component-words" type="application/json">(.*?)</script>',document).group(1))
                self.assertIn('<html lang="'+ui+'">',document)
                for key in components_page.KEYS:self.assertEqual(data['words'][key],ui_language.text('native.components.'+key,ui))
                for code,params in ui_language.NATIVE_ERRORS.items():
                    self.assertEqual(data['errors'][code],{'text':ui_language.text('native.app.error.'+code,ui),'params':list(params)})
                self.assertIn('window.__HELENE__ =',document);self.assertEqual(path.read_bytes(),before)

    def test_catalog_and_bridge_substitutions_are_single_pass_and_inert(self):
        (self.root/'helene.json').write_text('{"lang":"en"}',encoding='utf8')
        raw='WORDS BRIDGE LANGUAGE </script><script>globalThis.bad=1</script> {name}'
        with patch.object(ui_language,'text',side_effect=lambda *args:raw):
            document=components_page.page(self.root,'globalThis.marker="WORDS BRIDGE LANGUAGE </script>";')
        encoded=re.search(r'<script id="component-words" type="application/json">(.*?)</script>',document).group(1)
        self.assertNotIn('<',encoded);self.assertEqual(json.loads(encoded)['words']['intro'],raw)
        self.assertIn('globalThis.marker="WORDS BRIDGE LANGUAGE <\\/script>";',document)
        self.assertNotIn('<script>globalThis.bad=1',document)


if __name__=='__main__':unittest.main()
