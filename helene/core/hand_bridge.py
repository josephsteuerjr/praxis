"""Invocation-scoped IPC to the live agent; this module has no agent import."""
from __future__ import annotations
import contextlib
import contextvars
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import struct
import tempfile
import threading
import hand_transport

ENV='PRAXIS_HAND_BRIDGE'
_ACTIVE=contextvars.ContextVar('praxis_hand_bridge',default=None)
_PEER=contextvars.ContextVar('praxis_hand_peer',default=None)
_SCOPE=contextvars.ContextVar('praxis_hand_scope',default=None)


class Scope:
    def __init__(self):
        self.lock=threading.Lock();self.closed=False;self.binding=None

    def register(self,bridge,on_pending):
        with self.lock:
            if not self.closed:
                self.binding=(bridge,on_pending)
                return True
        bridge.close()
        return False

    def close(self):
        with self.lock:
            self.closed=True;binding=self.binding
        if binding:
            bridge,on_pending=binding
            pending=bridge.close()
            if pending:on_pending(pending)

    def images(self):
        with self.lock:binding=self.binding
        return tuple(binding[0].images) if binding else ()


@contextlib.contextmanager
def invocation_scope():
    scope=Scope();token=_SCOPE.set(scope)
    try:yield scope
    finally:
        try:scope.close()
        finally:_SCOPE.reset(token)


def current_scope():
    return _SCOPE.get()


def peer():
    return _PEER.get()


def unix_endpoint(env):
    """Only the current invocation may add its exact socket to a desktop fence."""
    bridge = _ACTIVE.get()
    if bridge is None or bridge.closed or not env:
        return None
    try:
        value = json.loads(env.get(ENV, ''))
        if (value.get('transport') == 'unix' and value.get('socket') == bridge.path
                and hmac.compare_digest(str(value.get('token', '')), bridge.token)):
            return str(Path(bridge.path).resolve())
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _receive(sock, count):
    result=bytearray()
    while len(result)<count:
        chunk=sock.recv(count-len(result))
        if not chunk:raise EOFError('hand connection closed')
        result.extend(chunk)
    return bytes(result)


def receive(sock):
    if getattr(sock, 'is_pipe', False):
        return json.loads(sock.recv_bytes(hand_transport.MAX_FRAME).decode('utf8'))
    length=struct.unpack('!I',_receive(sock,4))[0]
    if length > hand_transport.MAX_FRAME:
        raise ValueError('hand request too large')
    return json.loads(_receive(sock,length).decode('utf8'))


def send(sock,value):
    data=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf8')
    if len(data) > hand_transport.MAX_FRAME:
        raise ValueError('hand response too large')
    if getattr(sock, 'is_pipe', False):
        sock.send_bytes(data)
        return
    sock.sendall(struct.pack('!I',len(data))+data)


def descendant(child, ancestor):
    return hand_transport.descendant(child, ancestor)


def child_environment(env):
    # Never inherit an endpoint accidentally through an unrelated/background launcher.
    values=dict(os.environ if env is None else env);values.pop(ENV,None)
    values.pop('PRAXIS_HAND_FILE_POLICY', None)
    import command_execution
    if callable(command_execution.home_file_policy) and not values.get('PRAXIS_EDIT_CONTEXT'):
        values['PRAXIS_HAND_FILE_POLICY'] = json.dumps(command_execution.home_file_policy())
    bridge=_ACTIVE.get()
    if bridge is not None:
        script=json.loads(values['PRAXIS_EDIT_CONTEXT']) if values.get('PRAXIS_EDIT_CONTEXT') else None
        bridge.bind_script(script)
        values[ENV]=json.dumps({'socket':bridge.path,'token':bridge.token,
                               'transport':'pipe' if os.name == 'nt' else 'unix'})
    return values


if os.name == 'nt':
    _Server = hand_transport.PipeServer
else:
    class _Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads=True
        block_on_close=False


class Bridge:
    def __init__(self, dispatch):
        self.dispatch=dispatch
        self.context=contextvars.copy_context()
        self.token=secrets.token_hex(32)
        self.directory=tempfile.TemporaryDirectory(prefix='px-hand-')
        os.chmod(self.directory.name,0o700)
        self.path=(r'\\.\pipe\helene-hand-'+secrets.token_hex(16) if os.name == 'nt'
                   else str(Path(self.directory.name).resolve()/'rpc'))
        self.lock=threading.RLock()
        self.state_lock=threading.Lock()
        self.closed=False
        self.accepting=True
        self.inflight=0
        self.active={}
        self.idle=threading.Condition(self.state_lock)
        self.script=None
        self.bound=False
        self.results={}
        self.images=[]
        bridge=self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    self.request.settimeout(30)
                    request=receive(self.request)
                    with hand_transport.peer_identity(self.request) as (pid,same_user):
                        if (not isinstance(request,dict) or not same_user
                                or not hmac.compare_digest(str(request.get('token','')),bridge.token)
                                or not descendant(pid,os.getpid())):
                            send(self.request,{'status':'refused','message':'No live invocation authority.'});return
                        self.request.settimeout(None)
                        response=bridge.invoke(request,pid)
                        send(self.request,response)
                except (OSError,ValueError,EOFError):
                    # Delivery loss does not undo or repeat execution. The cached receipt remains.
                    return
        self.server=_Server(self.path,Handler)
        if os.name != 'nt':
            os.chmod(self.path,0o600)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.05},daemon=True)
        self.thread.start()

    def bind_script(self,script):
        with self.state_lock:
            if self.closed:raise RuntimeError('hand invocation already closed')
            if self.bound and self.script!=script:raise RuntimeError('command scope changed inside an invocation')
            self.script=script;self.bound=True

    def invoke(self,request,pid):
        request_id=request.get('request_id');name=request.get('name');args=request.get('args')
        if (not isinstance(request_id,str) or not request_id or len(request_id)>128
                or not isinstance(name,str) or not isinstance(args,dict)):
            return {'status':'refused','message':'request_id, hand name and object arguments are required.'}
        digest=hashlib.sha256(json.dumps([name,args],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        with self.lock:
            with self.state_lock:
                previous=self.results.get(request_id)
                if previous:
                    if previous[0]!=digest:return {'status':'refused','message':'request_id already names a different intent.'}
                    return previous[1]
                if self.closed or not self.accepting:return {'status':'refused','message':'The outer invocation has ended; no new call started.'}
                pending={'status':'in_doubt','message':'This exact request has started; it will not be executed again.','request_id':request_id}
                self.results[request_id]=(digest,pending);self.inflight+=1;self.active[request_id]=name
            def execute():
                token=_PEER.set({'pid':pid,'script':self.script})
                try:return self.dispatch(request_id,name,args)
                finally:_PEER.reset(token)
            # The durable executor records details through its redaction boundary.
            uncertain={'status':'in_doubt','message':'Nested execution did not return a confirmed receipt. Inspect the run before retrying.','request_id':request_id}
            response=uncertain
            try:
                response=self.context.run(execute)
                images=response.pop('images',())
                self.images.extend(images)
                response['image_count']=len(images)
            except BaseException:
                response=uncertain
            finally:
                with self.state_lock:
                    self.inflight-=1;self.active.pop(request_id,None)
                    self.results[request_id]=(digest,response)
                    self.idle.notify_all()
            return response

    def drain(self,keep_waiting,poll=.25):
        """The command stopped waiting (exit, timeout). Refuse new calls; let started ones finish.

        A started nested hand already owns a durable intent and its own ceiling. Waiting for it
        turns an unknown outcome into a receipt. keep_waiting() is checked between polls, so a
        pause, cancel or status change stops the wait and the old uncertainty path applies.
        Returns (request_id, name, response or None) for calls that were still running.
        """
        with self.state_lock:
            self.accepting=False
            started=dict(self.active)
        while started:
            with self.state_lock:
                if not self.inflight or self.closed:break
                self.idle.wait(poll)
                if not self.inflight or self.closed:break
            if not keep_waiting():break
        with self.state_lock:
            return [(request_id,name,None if request_id in self.active else self.results[request_id][1])
                    for request_id,name in started.items()]

    @contextlib.contextmanager
    def bind(self):
        token=_ACTIVE.set(self)
        try:yield self
        finally:_ACTIVE.reset(token)

    def close(self):
        with self.state_lock:
            if self.closed:return self.pending_on_close
            self.closed=True
            pending=self.pending_on_close=self.inflight
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=1)
        self.directory.cleanup()
        return pending
