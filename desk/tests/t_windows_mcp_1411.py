"""Optional component and authenticated local transport; no desktop actions."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import windows_mcp_component as component, windows_mcp_runtime as runtime
from localharness import windows_mcp_access as access, component_store as store


class OptionalWindowsMCP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.object(store, 'root', return_value=self.root/'store'))

    def test_release_carries_exact_hashes_and_license_inventory(self):
        rows = component.artifacts()
        self.assertGreater(len(rows), 20)
        self.assertEqual(component.PIN['license'], 'MIT')
        for row in component.PIN['wheels']:
            self.assertTrue(row['license_files'] or row['package'] == 'fastmcp-slim', row['name'])
        bad = json.loads(json.dumps(component.PIN))
        bad['runtime']['url'] = 'https://attacker.invalid/python.zip'
        with self.assertRaises(ValueError):component.artifacts(bad)

    def test_wheel_layout_retains_licenses_and_rejects_traversal(self):
        bundle = self.root/'fixture.whl'
        with zipfile.ZipFile(bundle, 'w') as z:
            z.writestr('fixture/__init__.py', 'VALUE=1')
            z.writestr('fixture-1.dist-info/licenses/LICENSE', 'fixture license')
            z.writestr('fixture-1.data/purelib/extra.py', 'VALUE=2')
        component.unpack(bundle, self.root/'payload', wheel=True)
        self.assertEqual((self.root/'payload/Lib/site-packages/extra.py').read_text(), 'VALUE=2')
        self.assertTrue((self.root/'payload/Lib/site-packages/fixture-1.dist-info/licenses/LICENSE').is_file())
        for member in ('../escape', '/escape', 'C:/escape', '..\\escape'):
            # ZipInfo normally normalizes backslashes on Windows; construct
            # the raw archive spelling to exercise an actual hostile member.
            info=zipfile.ZipInfo('placeholder');info.filename=member
            with zipfile.ZipFile(bundle, 'w') as z:z.writestr(info, 'bad')
            with self.subTest(member=member), self.assertRaises(ValueError):component.unpack(bundle, self.root/'payload', wheel=True)
        self.assertFalse((self.root/'escape').exists())

    def test_bad_cache_is_retained_and_an_explicit_retry_verifies_replacement(self):
        raw = b'exact fixture'
        row = dict(name='fixture.whl', url='https://files.pythonhosted.org/fixture.whl', size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        cached = store.cache()/(row['sha256'][:12]+'-'+row['name'])
        cached.write_bytes(b'corrupt')
        def download(url, target, sha, progress):
            self.assertEqual(sha, row['sha256']);target.write_bytes(raw)
            return dict(bytes=len(raw))
        with patch('localharness.python_updates._download', side_effect=download) as call:
            self.assertEqual(component.fetch(row).read_bytes(), raw)
            component.fetch(row)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(list(cached.parent.glob('*.invalid-*'))), 1)

    def test_module_pin_survives_update_without_copying_or_changing_the_old_pin(self):
        payload = self.root/'payload';payload.mkdir();(payload/'python.exe').write_bytes(b'fixture')
        old, new = self.root/'old', self.root/'new'
        old.mkdir();new.mkdir()
        ref, _ = store.import_directory(payload, program=old)
        store.pin(old, component.RELATIVE, ref)
        store.prepare_update(old, new)
        self.assertEqual(store.path(old, component.RELATIVE), store.path(new, component.RELATIVE))
        self.assertEqual(store.export_references(old)['pins'], store.export_references(new)['pins'])

    def test_scope_switches_apply_to_mcp_and_mutations_keep_the_sovereign_gate(self):
        self.assertTrue(access.refusal({'computer':{'enabled':False}}, 'Snapshot', {}))
        cfg={'computer':{'enabled':True,'scopes':['computer.read']}}
        self.assertEqual(access.refusal(cfg,'Snapshot',{}), '')
        self.assertTrue(access.refusal(cfg,'Snapshot',{'save_path':'C:/capture.png'}))
        self.assertTrue(access.refusal(cfg,'PowerShell',{'command':'echo fixture'}))
        self.assertFalse(access.rights('FileSystem',{'mode':'read'})[1])
        self.assertTrue(access.rights('FileSystem',{'mode':'write'})[1])
        self.assertTrue(access.rights('Registry',{'mode':'set'})[1])

    def test_transport_uses_auth_parses_sse_and_never_follows_redirects(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append((self.headers.get('Authorization'),body))
                if body['method']=='redirect':
                    self.send_response(302);self.send_header('Location','http://127.0.0.1:1/credential-leak');self.end_headers();return
                result={'jsonrpc':'2.0','id':body['id'],'result':{'content':[{'type':'text','text':'fixture'}]}}
                data=('event: message\ndata: '+json.dumps(result)+'\n\n').encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.assertEqual(runtime.rpc(server.server_port,'fixture-secret','tools/list')['content'][0]['text'],'fixture')
            with self.assertRaises(OSError):runtime.rpc(server.server_port,'fixture-secret','redirect')
            self.assertEqual(len(seen),2)
            self.assertTrue(all(auth=='Bearer fixture-secret' for auth,_ in seen))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
