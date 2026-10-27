"""Deterministic local relay process; no account, internet or production use."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json,os,signal,threading,time,urllib.request,uuid

port=int(os.environ['RELAY_PORT']);home=Path(os.environ['RELAY_AUTH_DIR']);home.mkdir(parents=True,exist_ok=True)
token=uuid.uuid4().hex;journal=Path(os.environ['FIXTURE_RELAY_LIFETIME_LOG'])
def record(kind):
    with journal.open('a',encoding='utf8') as f:f.write(json.dumps({'kind':kind,'pid':os.getpid(),'time':time.time()})+'\n')
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        data=self.rfile.read(int(self.headers.get('Content-Length',0)))
        if self.path=='/_relay/control':
            if self.headers.get('X-Relay-Control')!=token:self.send_error(403);return
            raw=json.dumps({'port':port,'running':True,'accounts':[{'slot':'primary','configured':True,'active':True}]}).encode();kind='application/json'
        else:
            if self.headers.get('Authorization')!='Bearer '+os.environ['RELAY_API_KEY']:self.send_error(401);return
            req=urllib.request.Request(os.environ['FIXTURE_RELAY_UPSTREAM'].rstrip('/')+self.path,data=data,headers={'Content-Type':'application/json'})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=20) as response:raw=response.read();kind=response.headers.get('Content-Type','application/json')
        self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
control=ThreadingHTTPServer(('127.0.0.1',0),Handler)
assert control.server_port!=port
(home/'control.json').write_text(json.dumps({'port':control.server_port,'token':token}),encoding='utf8');record('start')
threading.Thread(target=control.serve_forever,daemon=True).start()
def stop(*args):record('stop');os._exit(0)
signal.signal(signal.SIGTERM,stop)
server.serve_forever()
