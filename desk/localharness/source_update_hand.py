"""Scoped source-tool transport owned by the existing verified coordinator.

No second task process or authority store. The descriptor is a short-lived
capability for one admitted plan; all edits still use the canonical pipeline.
"""
from contextlib import contextmanager
import hmac,json,os,secrets,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from .package_notice import read,write

PROTOCOL=2
NAME='coordinator-hand.json'


def try_request(tree,action,args,expected_program=None):
    """Self-contained client, also embedded in the temporary legacy hand."""
    import json,os
    from pathlib import Path
    import urllib.request,urllib.error
    from urllib.parse import urlsplit
    def load(path):
        try:return json.loads(Path(path).read_text(encoding='utf-8'))
        except FileNotFoundError:return {}
    tree=Path(tree).resolve()
    request=load(tree/'memory/.control/source-update.json')
    if request.get('resolution_owner')!='agent':return False,None
    identity=str(request.get('id') or '')
    directory=Path(request.get('directory') or '.').resolve()
    program=Path(request.get('program') or '.').resolve()
    if len(identity)!=64 or directory not in (program/'.source-updates'/identity,tree/'memory/.source-updates'/identity):
        raise ValueError('Update hand plan leaves its admitted installation/profile')
    descriptor=load(directory/'coordinator-hand.json')
    producer=load(directory/'coordinator-process.json')
    if descriptor.get('state')!='ready':
        # A named producer may be preparing or have an unknown outcome. Never
        # create a competing local tool executor while it owns this plan.
        if producer.get('pid') or producer.get('state')=='starting':
            result=load(directory/'coordinator-result.json')
            settled=all(result.get(key)==producer.get(key) for key in ('pid','birth','invocation'))
            if not settled or result.get('returncode') not in (0,75):
                raise RuntimeError('Incoming coordinator is preparing or requires reconciliation; retained work has not been replaced')
        return False,None
    if (descriptor.get('protocol')!=2 or descriptor.get('plan_id')!=identity
            or descriptor.get('input_sha256')!=request.get('input_sha256')
            or Path(descriptor.get('tree') or '.').resolve()!=tree):
        raise ValueError('Update hand does not match this plan/profile/input')
    if expected_program and Path(descriptor.get('program') or '.').resolve()!=Path(expected_program).resolve():
        raise ValueError('Update hand belongs to another incoming programme')
    if producer and any(descriptor.get(key)!=producer.get(key) for key in ('pid','birth','invocation')):
        raise ValueError('Update hand producer generation changed')
    if producer.get('program') and Path(producer['program']).resolve()!=Path(descriptor.get('program') or '.').resolve():
        raise ValueError('Update hand producer programme changed')
    if descriptor.get('pid')==os.getpid():return False,None
    import psutil
    try:birth=psutil.Process(int(descriptor['pid'])).create_time()
    except (psutil.Error,ValueError,KeyError) as exc:raise RuntimeError('Incoming update hand is unavailable; observe its existing producer') from exc
    if birth!=descriptor.get('birth'):raise ValueError('Update hand PID was reused')
    origin=str(descriptor.get('origin') or '')
    url=urlsplit(origin)
    if (url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.username
            or url.password or url.path or url.query or url.fragment):raise ValueError('Invalid update hand loopback origin')
    token=descriptor.get('token')
    if not isinstance(token,str) or len(token)<32:raise ValueError('Missing update hand capability')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    body=json.dumps({'action':action,'args':args},ensure_ascii=False).encode('utf-8')
    if len(body)>16*1024*1024:raise ValueError('Source-tool request exceeds its bounded payload')
    call=urllib.request.Request(origin+'/source-update',data=body,headers={
        'Content-Type':'application/json','X-Helene-Update':token,'X-Helene-Plan':identity})
    try:
        with http.open(call,timeout=30) as response:value=json.load(response)
    except urllib.error.HTTPError as exc:
        try:error=json.loads(exc.read(65536)).get('error')
        except (ValueError,AttributeError):error=None
        finally:exc.close()
        raise RuntimeError(error or ('Source-tool request refused: '+str(exc.code))) from None
    if not value.get('ok'):raise RuntimeError(str(value.get('error') or 'Source-tool request failed'))
    return True,value['result']


def public_context(directory):
    """Never return the authentication capability to a model or UI."""
    value=read(Path(directory)/NAME)
    return {key:value.get(key) for key in ('protocol','state','plan_id','input_sha256','program',
        'version','pid','birth','invocation','core_sha256','app_sha256')}


@contextmanager
def serve(directory,tree,program,binding):
    from . import source_update as pipeline,agent_source_update,owner_stop
    import psutil
    directory,tree,program=(Path(p).resolve() for p in (directory,tree,program))
    operation=read(directory/'pipeline.json');producer=read(directory/'coordinator-process.json')
    if operation.get('coordinator')!=binding or binding.get('program')!=str(program):
        raise ValueError('Source-tool transport requires its verified incoming binding')
    identity={'pid':os.getpid(),'birth':psutil.Process().create_time(),'invocation':producer.get('invocation','')}
    if producer and any(producer.get(key)!=identity[key] for key in ('pid','birth')):
        raise ValueError('Source-tool transport does not own the named coordinator')
    token=secrets.token_urlsafe(32);closed=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.close_connection=True;self.connection.settimeout(10)
            code=200
            try:
                if self.path!='/source-update' or closed.is_set():raise ValueError('Update hand is closed')
                if (not hmac.compare_digest(self.headers.get('X-Helene-Update',''),token)
                        or self.headers.get('X-Helene-Plan')!=directory.name):
                    raise PermissionError('Source-tool capability refused')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16*1024*1024:raise ValueError('Invalid source-tool payload length')
                request=json.loads(self.rfile.read(length))
                if not isinstance(request,dict):raise ValueError('Source-tool payload must be an object')
                action=request.get('action');args=request.get('args')
                if action not in pipeline.TOOL['input_schema']['properties']['action']['enum'] or not isinstance(args,dict):
                    raise ValueError('Unsupported source-tool action')
                current=read(pipeline.path(tree));latest=read(directory/'pipeline.json')
                if (current.get('id')!=directory.name or current.get('input_sha256')!=operation['input_sha256']
                        or latest.get('input_sha256')!=operation['input_sha256']):
                    raise ValueError('Current update identity changed')
                if action=='status':result=agent_source_update.command(tree,action,**args)
                else:
                    if (owner_stop.stopped() or owner_stop.paused() or pipeline._cancelled(directory)
                            or pipeline.job_blocked(latest)):raise ValueError('Stop/Pause or retired update forbids source edits')
                    if not pipeline._LOCK.acquire(blocking=False):
                        raise ValueError('Verification/review is executing; read status and wait for its actual result')
                    try:result=agent_source_update.command(tree,action,**args)
                    finally:pipeline._LOCK.release()
                value={'ok':True,'result':result}
            except PermissionError as exc:code=403;value={'ok':False,'error':str(exc)}
            except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:code=409;value={'ok':False,'error':str(exc)}
            raw=json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,name='helene-source-hand',daemon=True);thread.start()
    descriptor={'protocol':PROTOCOL,'state':'ready','plan_id':directory.name,'input_sha256':operation['input_sha256'],
        'tree':str(tree),'program':str(program),'version':read(program/'python-app.json').get('version'),
        'origin':'http://127.0.0.1:'+str(server.server_port),'token':token,**identity,
        **{key:binding.get(key) for key in ('core_sha256','app_sha256')}}
    write(directory/NAME,descriptor)
    try:yield descriptor
    finally:
        closed.set();server.shutdown();server.server_close();thread.join(timeout=5)
        if thread.is_alive():raise RuntimeError('Source-tool transport shutdown unconfirmed')
        current=read(directory/NAME)
        if current.get('token')==token:write(directory/NAME,{**descriptor,'state':'closed'})
