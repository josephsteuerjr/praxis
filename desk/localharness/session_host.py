"""Attachable installation/session owner; windows are disposable observers.

The existing Controller/supervisor owns Jobs, the update watcher and cancellation.
This module adds only local authenticated transport and a singleton process lease.
Its descriptor and update work live in the selected persistent agent data, not UI
memory. A lost RPC reply is uncertainty: clients never replay mutating commands.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'localharness'

from . import agents, owner_stop
from .package_notice import read, write
from .python_controller import Controller

FORMAT = 'helene-session-host.v1'


def control_path(root):
    primary = agents.find(Path(root), agents.BASE_ID)
    if primary is None:
        raise ValueError('Select an existing installation/profile before starting its host')
    return primary.tree.resolve() / 'memory/.control/desktop-session.json'


def process_state(value):
    """PID alone is never authority to reuse, replace or stop an owner."""
    import psutil
    try:
        process = psutil.Process(int(value['pid']))
        if process.create_time() != value['birth']:
            return 'changed'
        return 'absent' if process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD) else 'alive'
    except psutil.NoSuchProcess:
        return 'absent'
    except (psutil.Error, KeyError, TypeError, ValueError):
        return 'unknown'


def _origin(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Observer must use an exact local window origin')
    return value


def allowed_origin(tree, origin):
    """Dynamic exact CORS admission, without restarting a channel on UI reopen."""
    value = read(Path(tree) / 'memory/.control/desktop-session.json')
    return (value.get('format') == FORMAT and value.get('tree') == str(Path(tree).resolve())
            and value.get('state') in ('starting', 'running') and process_state(value) == 'alive'
            and origin in value.get('observer_origins', []))


def configured_origin(config, origin):
    """Only this configured installation's authenticated observer registrations."""
    if not config:
        return False
    config = Path(config).resolve()
    root = config.parent.parent.parent if config.parent.parent.name == 'agents' else config.parent
    primary = agents.find(root, agents.BASE_ID)
    if primary is None or config not in {row.config.resolve() for row in agents.roster(root)}:
        return False
    value = read(control_path(root))
    if value.get('program') != str(root):
        return False
    return allowed_origin(primary.tree, origin)


def _rpc(descriptor, action, args=None, *, timeout=30):
    parsed = urllib.parse.urlsplit(descriptor.get('origin', ''))
    _origin(descriptor.get('origin', ''))
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *unused, **kwargs):
            return None
    body = json.dumps({'generation': descriptor['generation'], 'action': action,
                       'args': args or {}}, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(descriptor['origin'] + '/session', data=body,
        headers={'Content-Type': 'application/json', 'X-Helene-Session': descriptor['token']})
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with http.open(request, timeout=timeout) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            value = json.load(exc)
        except (ValueError, OSError):
            raise RuntimeError('Session host refused the request') from exc
        raise RuntimeError(value.get('error') or 'Session host refused the request') from exc
    if value.get('generation') != descriptor['generation']:
        raise RuntimeError('Session host generation changed; reattach, do not replay effects')
    return value['result']


class SessionClient(Controller):
    """Compatibility-shaped client, never a second supervisor in the window."""
    def __init__(self, root, descriptor, event, origin='', agent='', browser_mode=False):
        super().__init__(Path(root), event, origin, agent)
        self.descriptor = descriptor
        self.browser_mode = browser_mode
        self.observer = secrets.token_hex(16)
        self._detached = threading.Event()
        self._cursor = 0
        self._request('attach', {'observer': self.observer, 'origin': origin,
                                'agent': self.selected, 'browser_mode': bool(browser_mode),
                                'pid': os.getpid(), 'birth': __import__('psutil').Process().create_time()})
        self._events = threading.Thread(target=self._observe, name='helene-session-observer', daemon=True)
        self._events.start()

    def _request(self, action, args=None, timeout=30):
        return _rpc(self.descriptor, action, {'agent': self.selected, **(args or {})}, timeout=timeout)

    def _observe(self):
        while not self._detached.wait(.35):
            try:
                result = self._request('events', {'after': self._cursor, 'observer': self.observer})
                for row in result['events']:
                    self._cursor = row['seq']
                    if not row.get('agent') or row['agent'] == self.selected:
                        self.event(row['name'], row['data'])
            except (OSError, ValueError, RuntimeError) as exc:
                if not self._detached.is_set():
                    self.event('session-unavailable', {'error': str(exc), 'generation': self.descriptor['generation']})
                return  # retain last UI state; no replacement host or replay here

    def start(self):
        return self._request('start')

    def status(self):
        return self._request('status')

    def channel(self):
        return self._request('channel')

    def readiness(self, timeout=30, require_runner=False, agent=None):
        return self._request('readiness', {'agent': agent.id if agent is not None else self.selected,
            'timeout': timeout, 'require_runner': require_runner}, timeout=max(5, timeout + 5))

    def refresh(self):
        return self._request('refresh')

    def invoke(self, command, args):
        result = self._request('invoke', {'command': command, 'arguments': args}, timeout=180)
        if command == 'switch_agent' and isinstance(result, dict):
            self.selected = result['id']
        return result

    def close(self):
        """Detach only. UI close is not job cancellation or process-owner Exit."""
        if self._detached.is_set():
            return
        self._detached.set()
        try:
            self._request('detach', {'observer': self.observer}, timeout=3)
        except (OSError, ValueError, RuntimeError):
            pass  # disposal made no claim about process exit
        if threading.current_thread() is not self._events:
            self._events.join(4)

    def shutdown(self):
        result = self._request('exit', timeout=45)
        self.close()
        return result


def attach(root, event, origin='', agent='', browser_mode=False, *, timeout=30):
    """Rejoin exact owner or start once after proving the former birth absent."""
    from .config_transaction import transaction
    from .desktop_supervisor import JobChild
    from .update_process import interpreter_for
    root = Path(root).resolve()
    target = control_path(root)
    with transaction(target.with_name('desktop-session-launch')):
        descriptor = read(target)
        state = process_state(descriptor) if descriptor else 'absent'
        if state == 'unknown':
            raise RuntimeError('Earlier session owner identity is unknown; inspect it before starting another')
        if state == 'alive':
            if Path(descriptor.get('program', '')).resolve() != root:
                raise RuntimeError('This data is still owned from another program folder; stop that owner before moving it')
            _rpc(descriptor, 'status', timeout=5)
        else:
            # Keep the former descriptor as evidence, not an authorization token.
            if descriptor:
                write(target.with_name('desktop-session-previous.json'), descriptor)
            child = JobChild.spawn([str(interpreter_for(root)), '-B', '-s',
                str(root / 'app/localharness/session_host.py'), '--root', str(root)],
                cwd=root, env=dict(os.environ), log=target.with_name('desktop-session.log'),
                stopped=lambda:False)  # control endpoint may open under Stop; Controller cannot admit workers
            try:
                deadline = time.monotonic() + timeout
                while True:
                    descriptor = read(target)
                    if descriptor.get('pid') == child.pid and process_state(descriptor) == 'alive':
                        _rpc(descriptor, 'status', timeout=3)
                        child.release()  # installation host now owns its own internal Jobs
                        break
                    if child.poll() is not None:
                        raise RuntimeError('Session host did not admit ownership; inspect desktop-session.log')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Session host admission unconfirmed; no replacement owner started')
                    time.sleep(.05)
            except BaseException:
                if child.handle is not None:
                    child.stop()
                raise
    return SessionClient(root, descriptor, event, origin, agent, browser_mode)


class Host:
    def __init__(self, root, *, controller_factory=Controller, control_tree=None, migration_request=None):
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        from socketserver import TCPServer
        import psutil
        self.root = Path(root).resolve()
        self.path = (Path(control_tree).resolve()/'memory/.control/desktop-session.json'
                     if control_tree is not None else control_path(self.root))
        self.control_tree = Path(control_tree).resolve() if control_tree is not None else agents.find(self.root,agents.BASE_ID).tree.resolve()
        self.migration_request = Path(migration_request) if migration_request else None
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.observers = {}
        self.events = deque(maxlen=512)
        self.sequence = 0
        self.owner = controller_factory(self.root, self.event)
        self.owner.session_host = True
        self.owner.shutdown_session = self.request_exit
        class Server(ThreadingHTTPServer):
            daemon_threads = True
            def server_bind(server):
                TCPServer.server_bind(server)
                server.server_name = 'localhost'
                server.server_port = server.server_address[1]
        self.server = Server(('127.0.0.1', 0), self._handler(BaseHTTPRequestHandler))
        self.descriptor = {'format': FORMAT, 'program': str(self.root), 'tree': str(self.control_tree),
            'pid': os.getpid(), 'birth': psutil.Process().create_time(),
            'generation': secrets.token_hex(24), 'token': secrets.token_urlsafe(32),
            'origin': f'http://127.0.0.1:{self.server.server_port}', 'observer_origins': [],
            'state': 'starting', 'started': time.time()}

    def publish(self, state=None):
        with self.lock:
            if state:
                self.descriptor['state'] = state
            self.descriptor['observer_origins'] = sorted({r['origin'] for r in self.observers.values() if r['origin']})
            self.descriptor['observers'] = list(self.observers.values())
            write(self.path, self.descriptor)
            if os.name != 'nt':
                self.path.chmod(0o600)

    def event(self, name, data):
        with self.lock:
            self.sequence += 1
            self.events.append({'seq': self.sequence, 'name': name, 'data': data,
                                'agent': self.owner.selected if hasattr(self, 'owner') else ''})

    def request_exit(self):
        self.stop.set()

    def cancel_owned_jobs(self,reason):
        """Exit/Stop intent survives process death; observer detach never calls this."""
        from . import desktop_update_request as jobs
        roots={self.root.resolve(),self.owner.root.resolve()}
        trees={self.control_tree,*(row.tree.resolve() for row in agents.roster(self.owner.root))}
        observations={}
        pending=False
        for tree in trees:
            value=jobs.status(tree)
            if value.get('state') not in jobs.ACTIVE or Path(value.get('program','')).resolve() not in roots:continue
            jobs.request(tree,'cancel',root=Path(value['program']),identity=value['id'],reason=reason,by='window')
            active,facts=jobs.producers_pending(value)
            pending=pending or active;observations[value['id']]=facts
        return pending,observations

    def dispatch(self, action, args):
        if action in ('job_status','cancel_job','pause_job','resume_job'):
            from . import desktop_update_request as jobs
            job=jobs.status(self.control_tree)
            if action=='job_status':return job
            if action=='pause_job':
                write(self.control_tree/'memory/.control/autonomy-paused.json',{'at':time.time(),'via':'session-control'})
                return jobs.checkpoint(self.owner,job,state='paused')
            if action=='resume_job':
                if job.get('cancel_requested') or jobs.cancelled(self.control_tree,job.get('id','')):
                    raise ValueError('Cancelled work cannot be revived')
                (self.control_tree/'memory/.control/autonomy-paused.json').unlink(missing_ok=True)
                if self.migration_request and getattr(self,'migration_thread',None) and self.migration_thread.is_alive():
                    return jobs.checkpoint(self.owner,job,state='running')
                result=jobs.request(self.control_tree,'resume',root=self.root,identity=job.get('id'))
                if self.migration_request:
                    from . import migration_job
                    self.migration_thread=threading.Thread(target=migration_job.execute,args=(self,self.owner,result),
                        name='helene-migration-work',daemon=True)
                    self.migration_thread.start()
                return result
            return jobs.request(self.control_tree,'cancel',root=self.root,identity=job.get('id'),by='window')
        selected = args.get('agent') or self.owner._default()
        if agents.find(self.root, selected) is None:
            raise ValueError('Selected agent is unavailable')
        with self.owner.selection(selected):
            if action == 'attach':
                origin = _origin(args['origin']) if args.get('origin') else ''
                identity = str(args['observer'])
                if not identity.isalnum() or len(identity) > 64:
                    raise ValueError('Invalid observer identity')
                with self.lock:
                    if process_state(args) != 'alive':
                        raise ValueError('Observer process birth is not current')
                    self.observers[identity] = {'origin': origin, 'agent': selected,
                        'pid': args['pid'], 'birth': args['birth']}
                    self.publish()
                return {'generation': self.descriptor['generation'], 'pid': os.getpid()}
            if action == 'detach':
                with self.lock:
                    self.observers.pop(args.get('observer'), None)
                    self.publish()
                return {'detached': True, 'owner_continues': True}
            if action == 'events':
                with self.lock:
                    return {'events': [row for row in self.events if row['seq'] > int(args.get('after') or 0)]}
            if action == 'status':
                try:
                    status = self.owner.status()
                except RuntimeError as exc:
                    status = {'agents': {}, 'engine_error': str(exc)}
                return {**status, 'session': {k: self.descriptor[k] for k in ('pid', 'birth', 'generation', 'state')}}
            if action == 'channel':
                return self.owner.channel()
            if action == 'start':
                self.owner.start()
                self.publish('owner-stopped' if owner_stop.stopped() else 'running')
                return {'started': not owner_stop.stopped(), 'generation': self.descriptor['generation']}
            if action == 'readiness':
                return self.owner.readiness(timeout=float(args.get('timeout', 30)),
                    require_runner=args.get('require_runner') is True, agent=self.owner.current())
            if action == 'refresh':
                self.owner.refresh()
                return {'refreshed': True}
            if action == 'exit':
                pending,observations=self.cancel_owned_jobs('Owner explicitly exited the application')
                self.owner.close()
                self.publish('stopped')
                self.stop.set()
                return {'stop_requested':True,'stopped':not pending,'producers':observations}
            if action == 'invoke':
                command, arguments = args.get('command'), args.get('arguments')
                if not isinstance(command, str) or not isinstance(arguments, dict):
                    raise ValueError('Expected a desktop command and object arguments')
                if command == 'update_install':
                    from .desktop_update_request import admit_install
                    return admit_install(self.owner, **arguments)
                return self.owner.invoke(command, arguments)
        raise ValueError('Unknown session operation')

    def _handler(self, base):
        host = self
        class Handler(base):
            def log_message(self, *args):
                pass
            def reply(self, code, value):
                raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # effect/result remains in its canonical job state
            def do_POST(self):
                origin = host.descriptor['origin']
                if (self.path != '/session' or self.headers.get('Host') != urllib.parse.urlsplit(origin).netloc
                        or self.headers.get('Origin', origin) != origin
                        or not secrets.compare_digest(self.headers.get('X-Helene-Session', ''), host.descriptor['token'])):
                    return self.reply(403, {'error': 'Session owner authentication required'})
                try:
                    count = int(self.headers.get('Content-Length') or 0)
                    if not 0 < count <= 90 * 1024 * 1024:
                        raise ValueError('Invalid session request length')
                    body = json.loads(self.rfile.read(count))
                    if body.get('generation') != host.descriptor['generation'] or not isinstance(body.get('args'), dict):
                        raise ValueError('Session generation/arguments differ')
                    result = host.dispatch(body['action'], body['args'])
                    return self.reply(200, {'generation': host.descriptor['generation'], 'result': result})
                except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as exc:
                    return self.reply(400, {'error': str(exc), 'generation': host.descriptor['generation']})
        return Handler

    def run(self):
        from .phone_tunnel.platform_support import acquire_file_lock, release_file_lock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lease = acquire_file_lock(self.path.with_name('desktop-session.lock'), timeout_sec=1)
        try:
            previous = read(self.path)
            if previous and process_state(previous) not in ('absent', 'changed'):
                raise RuntimeError('Another live or unknown session owner already holds this profile')
            if self.migration_request is None:
                self.owner.start()
            self.publish('running')
            server = threading.Thread(target=self.server.serve_forever, name='helene-session-control', daemon=True)
            server.start()
            if self.migration_request is not None:
                from . import migration_job, desktop_update_request as jobs
                value=read(self.migration_request)
                if (self.migration_request.resolve()!=jobs.path(self.control_tree).resolve()
                        or value.get('kind')!='migration' or value.get('state')!='admitting'):
                    raise ValueError('Migration bootstrap request differs')
                jobs.checkpoint(self.owner,value,state='running',phase='preparing',
                    admission={key:self.descriptor[key] for key in ('pid','birth','generation')})
                self.migration_thread=threading.Thread(target=migration_job.execute,args=(self,self.owner,value),
                    name='helene-migration-work',daemon=True)
                self.migration_thread.start()
            while not self.stop.wait(.2):
                if owner_stop.stopped() and self.descriptor['state'] != 'owner-stopped':
                    self.cancel_owned_jobs('Owner Stop')
                    self.owner.close()
                    self.publish('owner-stopped')
                    # Keep a control endpoint so explicit Resume can still act.
            self.owner.close()
            self.publish('stopped')
            self.server.shutdown()
            server.join(5)
        finally:
            self.server.server_close()
            release_file_lock(lease)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--control-tree',type=Path)
    parser.add_argument('--migration-request',type=Path)
    parser.add_argument('--runtime-root',type=Path)
    parser.add_argument('--control',choices=('status','stop','pause','resume','exit'))
    args = parser.parse_args(argv)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['HELENE_ROOT'] = str(args.root.resolve())
    if args.control:
        descriptor=read(args.control_tree/'memory/.control/desktop-session.json') if args.control_tree else read(control_path(args.root))
        if descriptor.get('program')!=str(args.root.resolve()):raise ValueError('Selected host belongs to another program')
        if process_state(descriptor)!='alive':
            from . import desktop_update_request as jobs
            tree=Path(descriptor['tree']);job=jobs.status(tree)
            if args.control=='status':
                print(json.dumps({'job':job,'host':process_state(descriptor),
                    'helper':process_state(job.get('helper') or {})},ensure_ascii=False));return 0
            if args.control in ('stop','exit'):
                result=jobs.request(tree,'cancel',root=args.root,identity=job.get('id'),by='window')
            elif args.control=='pause':
                write(tree/'memory/.control/autonomy-paused.json',{'at':time.time(),'via':'durable-session-control'})
                result=jobs.checkpoint(None,job,state='paused')
            else:
                if job.get('cancel_requested') or jobs.cancelled(tree,job.get('id','')):raise ValueError('Cancelled job cannot resume')
                if process_state(job.get('helper') or {})!='alive':raise RuntimeError('No live mechanical producer; reopen migration/session to resume saved work')
                (tree/'memory/.control/autonomy-paused.json').unlink(missing_ok=True)
                result=jobs.checkpoint(None,job,state='handed_off')
            print(json.dumps(result,ensure_ascii=False));return 0
        action={'status':'job_status','stop':'cancel_job','pause':'pause_job','resume':'resume_job','exit':'exit'}[args.control]
        print(json.dumps(_rpc(descriptor,action),ensure_ascii=False));return 0
    if args.runtime_root:
        from .source_admission import activate
        activate(args.runtime_root)
    Host(args.root,control_tree=args.control_tree,migration_request=args.migration_request).run()
    return 0


def retire_observers(root, timeout=25):
    """Close only authenticated registered readers, not the session owner/job."""
    from .migration_windows import request_python_exit
    root = Path(root).resolve()
    descriptor = read(control_path(root))
    observers = descriptor.get('observers', [])
    for observer in observers:
        state = process_state(observer)
        if state in ('absent', 'changed'):
            continue
        if state != 'alive':
            raise RuntimeError('Observer process identity unknown; cutover not admitted')
        request_python_exit(root, observer['pid'], observer_only=True)
    deadline = time.monotonic() + timeout
    while True:
        remaining = [row for row in observers if process_state(row) not in ('absent', 'changed')]
        if not remaining:
            return {'readers_exited': True, 'observed': len(observers)}
        if time.monotonic() >= deadline:
            raise RuntimeError('Old window readers did not confirm exit; no application replacement admitted')
        time.sleep(.1)


if __name__ == '__main__':
    raise SystemExit(main())
