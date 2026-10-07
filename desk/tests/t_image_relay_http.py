"""Real HTTP contract for a keyless local relay; no provider or generation."""
import importlib.util,json,sys,threading,unittest
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path

path=Path(__file__).resolve().parents[2]/'helene/core/imagegen.py'
spec=importlib.util.spec_from_file_location('image_relay_under_test',path)
imagegen=importlib.util.module_from_spec(spec);spec.loader.exec_module(imagegen)

class ImageRelayHTTP(unittest.TestCase):
    def test_keyless_and_keyed_requests_reach_local_relay_once(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path,self.headers.get('Authorization'),self.headers.get('x-codex-image-turn-id'),body))
                raw=b'{"data":[]}'
                self.send_response(200);self.send_header('Content-Length',str(len(raw)))
                self.send_header('x-codex-imagegen-request-id','local-proof');self.end_headers();self.wfile.write(raw)
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for key,auth in (('',None),('   ',None),('loop-key','Bearer loop-key')):
                with self.subTest(key=key):
                    count=len(requests)
                    result,request_id=imagegen._post(f'http://127.0.0.1:{server.server_port}/images/generations',key,{'model':'fixture-only'},'turn-proof')
                    self.assertEqual(result,{'data':[]});self.assertEqual(request_id,'local-proof')
                    self.assertEqual(len(requests),count+1)
                    self.assertEqual(requests[-1],('/images/generations',auth,'turn-proof',{'model':'fixture-only'}))
        finally:
            server.shutdown();thread.join(timeout=5);server.server_close()

if __name__=='__main__': unittest.main()
