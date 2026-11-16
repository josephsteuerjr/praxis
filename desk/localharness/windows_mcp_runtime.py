"""Authenticated loopback MCP, owned by the desktop session's existing Job."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from . import windows_mcp_words as words

PROTOCOL = '2025-11-25'
MAX_REPLY = 32 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def rpc(port, key, method, params=None, *, timeout=30):
    identity = secrets.token_hex(12)
    payload = dict(jsonrpc='2.0', id=identity, method=method, params=params or {})
    request = urllib.request.Request(f'http://127.0.0.1:{int(port)}/mcp',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                 'Authorization': 'Bearer ' + key, 'MCP-Protocol-Version': PROTOCOL})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_REPLY + 1)
            if len(raw) > MAX_REPLY:
                raise ValueError('Windows MCP response exceeds 32 MiB')
            content_type = response.headers.get('Content-Type', '')
    except urllib.error.HTTPError as exc:
        exc.close()
        raise
    if 'text/event-stream' in content_type:
        values = []
        for event in raw.decode('utf-8').replace('\r\n', '\n').split('\n\n'):
            data = '\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith('data:'))
            if data:
                values.append(json.loads(data))
        reply = next((v for v in values if isinstance(v, dict) and v.get('id') == identity), None)
    else:
        reply = json.loads(raw)
    if not isinstance(reply, dict) or reply.get('id') != identity or reply.get('jsonrpc') != '2.0':
        raise ValueError('Invalid Windows MCP response identity')
    if 'error' in reply:
        raise RuntimeError('Windows MCP: ' + str(reply['error'].get('message', 'RPC failed')))
    if not isinstance(reply.get('result'), dict):
        raise ValueError('Invalid Windows MCP result')
    return reply['result']


class Manager:
    def __init__(self, owner):
        self.owner = owner
        self.lock = threading.RLock()
        self.child = None
        self.thread = None
        self.state = 'stopped'
        self.error = ''
        self.failure = {}
        self.tools = {}
        self.port = 0
        self.key = ''
        self.generation = None
        self.restart_waiter = None

    def start(self):
        from .windows_mcp_component import status
        if not status(self.owner.root)['installed']:
            return
        with self.lock:
            if self.thread and self.thread.is_alive():
                # A fast Stop/Start can replace the session while the previous
                # handshake is returning. Wait for that worker without holding
                # the controller lock, then admit exactly one fresh generation.
                if self.generation is not self.owner.stop and not (self.restart_waiter and self.restart_waiter.is_alive()):
                    old, wanted = self.thread, self.owner.stop
                    def resume():
                        old.join()
                        if self.owner.stop is wanted and not wanted.is_set():self.start()
                    self.restart_waiter=threading.Thread(target=resume,name='helene-windows-mcp-restart',daemon=True)
                    self.restart_waiter.start()
                return
            if self.child is not None and self.child.handle is not None:
                if self.child.poll() is None:
                    return
                self.child.stop()  # Reap the previous Job before a new generation.
            stop = self.owner.stop
            if stop.is_set() or self.owner.exiting:
                return
            self.state, self.error, self.tools = 'starting', '', {}
            self.failure = {}
            self.generation = stop
            self.thread = threading.Thread(target=self._start, args=(stop,), name='helene-windows-mcp', daemon=True)
            self.thread.start()

    def _start(self, stop):
        from .windows_mcp_component import directory, interpreter
        from .program_state import directory as state_directory
        from .update_process import permanent_environment
        child = None
        try:
            state = state_directory(self.owner.root, 'windows-mcp')
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            key = secrets.token_urlsafe(32)
            env = permanent_environment()
            env.update(WINDOWS_MCP_AUTH_KEY=key, ANONYMIZED_TELEMETRY='false', POSTHOG_API_KEY='',
                       WINDOWS_MCP_WATCHDOG='false', HELENE_WINDOWS_MCP_STATE=str(state),
                       PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
                       # The control consent prompt speaks the owner's interface language.
                       HELENE_WINDOWS_MCP_CONSENT_TITLE='Hélène · Windows-MCP',
                       HELENE_WINDOWS_MCP_CONSENT_TEXT=words.text(self.owner.root,'consent_text',seconds='{seconds}'))
            argv = [interpreter(directory(self.owner.root)), '-B',
                    str(Path(__file__).with_name('windows_mcp_entry.py')), 'serve',
                    '--transport', 'streamable-http', '--host', '127.0.0.1', '--port', str(port), '--stateless-http']
            with self.owner.lock:
                if stop.is_set() or self.owner.stop is not stop or self.owner.exiting:
                    return
                child = self.owner.background(argv, state / 'server.log', env)
            with self.lock:
                self.child, self.port, self.key = child, port, key
            deadline = time.monotonic() + 60
            while not stop.is_set():
                if child.handle is None or child.poll() is not None:
                    raise words.Error(self.owner.root,'start_exited',log=str(state/'server.log'))
                try:
                    rpc(port, key, 'initialize', dict(protocolVersion=PROTOCOL, capabilities={},
                        clientInfo=dict(name='Helene', version='1')), timeout=2)
                    result = rpc(port, key, 'tools/list', timeout=5)
                    rows = result.get('tools', [])
                    from .windows_mcp_remote import catalog
                    try:admitted=catalog(rows)
                    except ValueError:raise words.Error(self.owner.root,'invalid_catalog') from None
                    with self.lock:
                        self.tools = admitted
                        self.state = 'running'
                    return
                except (OSError, ValueError, RuntimeError) as exc:
                    if time.monotonic() >= deadline:
                        raise words.Error(self.owner.root,'not_ready',
                            cause=exc.spec if isinstance(exc,words.Error) else None,
                            detail='' if isinstance(exc,words.Error) else str(exc)) from exc
                stop.wait(.25)
        except Exception as exc:
            with self.lock:
                self.state, self.error = 'error', str(exc)
                self.failure = exc.spec if isinstance(exc,words.Error) else {}
            if child is not None and child.handle is not None:
                try:
                    child.stop()
                except (OSError, RuntimeError) as custody:
                    child.custody_uncertain = True
                    self.owner.error = str(custody)

    def status(self):
        with self.lock:
            running = self.child is not None and self.child.handle is not None and self.child.poll() is None
            state = self.state
            if self.owner.stop.is_set():
                state = 'stopped'
            elif state == 'running' and not running:
                state = 'error'
            return dict(state=state, running=state == 'running', tools=len(self.tools) if running else 0,
                        error=(words.render(self.owner.root,self.failure) if self.failure else self.error)
                        or (words.text(self.owner.root,'exited') if state == 'error' else ''))

    def call(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ValueError('Windows MCP arguments must be an object')
        with self.lock:
            if not self.status()['running']:
                return words.failure(self.owner.root,'not_running')
            if name not in self.tools:
                raise ValueError('Unknown Windows MCP tool')
            port, key = self.port, self.key
        try:
            # A transport failure may follow a real desktop action. Never replay.
            return rpc(port, key, 'tools/call', dict(name=name, arguments=arguments), timeout=180)
        except (OSError, ValueError, RuntimeError) as exc:
            return words.failure(self.owner.root,'unconfirmed',in_doubt=True,detail=str(exc))


def manager(owner):
    with owner.lock:
        if not hasattr(owner, 'windows_mcp'):
            owner.windows_mcp = Manager(owner)
        return owner.windows_mcp


def component_status(owner):
    from .windows_mcp_component import status
    row = status(owner.root)
    current = getattr(owner, 'windows_mcp', None)
    row['runtime'] = current.status() if current else dict(state='stopped', running=False, tools=0, error='')
    return row


def after_install(owner, child):
    stop = owner.stop
    def wait():
        while not stop.wait(.25):
            if child.handle is None or child.poll() is not None:
                if owner.stop is stop:
                    manager(owner).start()
                return
    threading.Thread(target=wait, name='helene-windows-mcp-install', daemon=True).start()
