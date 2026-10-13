"""Owner-paired desktop relay. No inbound desktop port or owner-key forwarding.

The desktop opens the socket and retains its local body/controller credentials.
The server only forwards whitelisted body RPCs to the explicitly selected device.
Disconnects fail pending calls; mutating work is never automatically replayed.
"""
from __future__ import annotations
import asyncio
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
import uuid
from aiohttp import web, WSMsgType

PREFIX = '/api/computer/remote'
SCOPES = ('computer.read', 'computer.files', 'computer.process', 'computer.apps')
MAX_FRAME = 12 * 1024 * 1024
MAX_BODY = 8 * 1024 * 1024
MAX_DEVICES = 16
ARTIFACT_REQUEST_HEADERS = frozenset(('x-praxis-total-size', 'x-praxis-name-hex',
    'x-praxis-chunk-sha256', 'x-praxis-device-id', 'x-praxis-mime'))
ARTIFACT_RESPONSE_HEADERS = frozenset(('x-praxis-total-size', 'x-praxis-offset'))
DEVICE = re.compile(r'[a-f0-9]{32}')
_CONTROLLER = os.environ.pop('HELENE_REMOTE_CONTROLLER_TOKEN', '')


def scope_for(capability: str) -> str | None:
    # Same authority as the frozen agent's _COMPUTER_ACTION_SCOPES.
    if capability in ('body.status', 'adapter.list'):
        return None  # connection/identity probe; the agent's status hand still requires read
    if capability in ('fs.stat', 'fs.list', 'desktop.element.find'):
        return 'computer.read'
    if capability in ('desktop.status', 'desktop.window.list', 'desktop.window.read',
            'desktop.window.wait', 'desktop.screen.capture', 'desktop.clipboard.read',
            'desktop.clipboard.write', 'desktop.window.activate', 'desktop.input.perform',
            'desktop.input.perform_and_read', 'desktop.element.act', 'os.process.list'):
        return 'computer.apps'
    if capability.startswith('process.'):
        return 'computer.process'
    if capability.startswith('fs.'):
        return 'computer.files'
    raise PermissionError('Unrecognized desktop capability')


def artifact_headers(value, allowed):
    if not isinstance(value, dict) or len(value) > len(allowed):
        raise ValueError('Invalid artifact headers')
    if any(k not in allowed or not isinstance(v, str) or len(v) > 2048 or
           any(ord(c) < 32 or ord(c) > 126 for c in v) for k, v in value.items()):
        raise ValueError('Invalid artifact headers')
    return value


def body_route(method: str, path: str, device: str) -> str:
    """No arbitrary localhost requests, alternate devices, traversal, or headers."""
    if not DEVICE.fullmatch(device):
        raise ValueError('Invalid desktop identity')
    control = '/v1/controller/' + device
    if method == 'POST' and path == control + '/broker':
        return 'broker'
    if method == 'POST' and path == control + '/invoke':
        return 'invoke'
    if method == 'GET' and re.fullmatch(re.escape(control) + r'/requests/[A-Za-z0-9_-]{1,128}', path):
        return 'receipt'
    if re.fullmatch(r'/v1/artifacts/[a-f0-9]{64}', path) and method == 'GET':
        return 'artifact'
    if re.fullmatch(r'/v1/artifacts/[a-f0-9]{64}/(?:offer|complete)', path) and method == 'POST':
        return 'artifact'
    if re.fullmatch(r'/v1/artifacts/[a-f0-9]{64}/chunks/\d{1,16}', path) and method == 'PUT':
        return 'artifact'
    if re.fullmatch(r'/v1/artifacts/[a-f0-9]{64}/content(?:\?offset=\d{1,16}&length=\d{1,8})?', path) and method == 'GET':
        return 'artifact'
    raise ValueError('Unsupported desktop bridge route')


class Relay:
    def __init__(self, tree: Path):
        private = os.environ.get('HELENE_DESK_STATE')
        self.path = (Path(private) if private else Path(tree) / 'memory/.state') / 'remote-computers.json'
        self.connections: dict[str, web.WebSocketResponse] = {}
        self.pending: dict[str, tuple[str, asyncio.Future]] = {}
        self.last_seen: dict[str, float] = {}
        self.identities: dict[str, dict] = {}

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf8'))
        except FileNotFoundError:
            return {'schema': 1, 'selected': '', 'devices': []}
        if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('devices'), list):
            raise ValueError('Invalid desktop registry')
        return data

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name('.remote-computers-' + uuid.uuid4().hex)
        try:
            with tmp.open('x', encoding='utf8') as f:
                os.chmod(tmp, 0o600)
                json.dump(data, f, ensure_ascii=False)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def pair(self, name: str, scopes: list[str]):
        if not isinstance(scopes, list) or any(s not in SCOPES for s in scopes):
            raise ValueError('Invalid desktop permissions')
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(ord(c) < 32 for c in name):
            raise ValueError('Invalid desktop name')
        data = self.load()
        if len(data['devices']) >= MAX_DEVICES:
            raise ValueError('Desktop limit reached; revoke an unused device')
        device = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        row = {'id': device, 'name': name.strip(), 'scopes': list(dict.fromkeys(scopes)),
               'key_hash': hashlib.sha256(token.encode()).hexdigest(),
               'created_utc': dt.datetime.now(dt.UTC).isoformat()}
        data['devices'].append(row)
        self.save(data)
        return {'id': device, 'token': token, 'scopes': row['scopes']}

    def authenticate(self, device: str, token: str) -> bool:
        if not DEVICE.fullmatch(device) or not isinstance(token, str) or len(token) > 256:
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        try:
            return any(row.get('id') == device and secrets.compare_digest(digest, str(row.get('key_hash') or ''))
                       for row in self.load()['devices'])
        except (OSError, ValueError, TypeError):
            return False

    def controller(self, supplied: str) -> bool:
        return bool(_CONTROLLER and isinstance(supplied, str) and secrets.compare_digest(supplied.encode(), _CONTROLLER.encode()))

    def snapshot(self):
        data = self.load()
        return {'selected': data.get('selected', ''), 'devices': [
            {**{k: row.get(k) for k in ('id', 'name', 'scopes', 'created_utc')},
             'connected': row.get('id') in self.connections and time.monotonic() - self.last_seen.get(row.get('id'), 0) < 60,
             'identity': self.identities.get(row.get('id'), {})}
            for row in data['devices']]}

    def select(self, device: str):
        data = self.load()
        if device and not any(row.get('id') == device for row in data['devices']):
            raise ValueError('Desktop is not paired')
        data['selected'] = device
        self.save(data)
        return self.snapshot()

    async def revoke(self, device: str):
        data = self.load()
        data['devices'] = [row for row in data['devices'] if row.get('id') != device]
        if data.get('selected') == device:
            data['selected'] = ''
        self.save(data)
        socket = self.connections.get(device)
        if socket:
            await socket.close(code=1008, message=b'owner revoked desktop')
        self.drop(device)
        return self.snapshot()

    def drop(self, device: str, socket=None):
        if socket is not None and self.connections.get(device) is not socket:
            return
        self.connections.pop(device, None)
        self.identities.pop(device, None)
        self.last_seen.pop(device, None)
        for ident, (owner, future) in list(self.pending.items()):
            if owner == device:
                if not future.done():
                    future.set_exception(ConnectionError('Desktop disconnected; verify any started operation before retry'))
                self.pending.pop(ident, None)

    async def connect(self, request):
        device = request.match_info['device']
        token = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if not self.authenticate(device, token):
            raise web.HTTPUnauthorized()
        if device in self.connections:
            raise web.HTTPConflict(text='Desktop already connected')
        socket = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_FRAME)
        await socket.prepare(request)
        self.connections[device] = socket
        self.last_seen[device] = time.monotonic()
        try:
            async for msg in socket:
                if not self.authenticate(device, token):
                    await socket.close(code=1008)
                    break
                if msg.type != WSMsgType.TEXT:
                    if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                    continue
                value = json.loads(msg.data)
                if not isinstance(value, dict):
                    raise ValueError('Invalid desktop frame')
                self.last_seen[device] = time.monotonic()
                if value.get('kind') == 'hello':
                    identity = value.get('identity') or {}
                    if not isinstance(identity, dict) or identity.get('platform') != 'windows':
                        raise ValueError('Expected a Windows desktop identity')
                    actual_scopes = identity.get('scopes', [])
                    if not isinstance(actual_scopes, list) or any(s not in SCOPES for s in actual_scopes):
                        raise ValueError('Invalid local desktop permissions')
                    self.identities[device] = {k: identity.get(k) for k in ('platform', 'name', 'session', 'version', 'system')}
                    self.identities[device]['scopes'] = list(dict.fromkeys(actual_scopes))
                    await socket.send_json({'kind': 'ready', 'device': device})
                elif value.get('kind') == 'heartbeat':
                    await socket.send_json({'kind': 'heartbeat'})
                elif value.get('kind') == 'response':
                    ident = str(value.get('id') or '')
                    entry = self.pending.get(ident)
                    if entry and entry[0] == device and not entry[1].done():
                        entry[1].set_result(value)
                else:
                    raise ValueError('Invalid desktop frame kind')
        except (ValueError, TypeError):
            await socket.close(code=1008, message=b'invalid desktop frame')
        finally:
            self.drop(device, socket)
        return socket

    async def rpc(self, method: str, path: str, body: bytes, content_type: str, timeout=90, headers=None):
        data = self.load()
        device = str(data.get('selected') or '')
        body_route(method, path, device)
        headers = artifact_headers(headers or {}, ARTIFACT_REQUEST_HEADERS)
        if len(body) > MAX_BODY:
            raise ValueError('Desktop request too large')
        socket = self.connections.get(device)
        if not socket or device not in self.identities or time.monotonic() - self.last_seen.get(device, 0) >= 60:
            raise ConnectionError('Selected Windows desktop is offline')
        kind = body_route(method, path, device)
        if kind in ('invoke', 'broker'):
            payload = json.loads(body)
            if not isinstance(payload, dict): raise ValueError('Invalid native invocation')
            cap = payload.get('capability')
            if kind == 'invoke' and not isinstance(cap, str): raise ValueError('Missing native capability')
            required = 'computer.process' if kind == 'broker' else scope_for(cap)
            row = next(r for r in data['devices'] if r['id'] == device)
            actual = self.identities[device]
            if required and (required not in row['scopes'] or required not in actual['scopes']):
                raise PermissionError('Desktop owner has not granted ' + required)
        if sum(owner == device for owner, _ in self.pending.values()) >= 16:
            raise ConnectionError('Desktop request queue is full')
        ident = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = (device, future)
        try:
            await socket.send_json({'kind': 'request', 'id': ident, 'device': device,
                                    'method': method, 'path': path, 'content_type': content_type,
                                    'headers': headers,
                                    'body': base64.b64encode(body).decode('ascii'),
                                    'deadline': time.time() + min(max(float(timeout), 1), 120)})
            result = await asyncio.wait_for(future, timeout=min(max(float(timeout), 1), 120))
            encoded = result.get('body')
            if not isinstance(encoded, str) or len(encoded) > MAX_FRAME:
                raise ValueError('Invalid desktop response')
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_BODY:
                raise ValueError('Desktop response too large')
            status = result.get('status')
            if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status <= 599:
                raise ValueError('Invalid desktop response status')
            mime = str(result.get('content_type') or 'application/octet-stream')
            if any(c in mime for c in '\r\n') or len(mime) > 128:
                raise ValueError('Invalid desktop response content type')
            returned = artifact_headers(result.get('headers', {}), ARTIFACT_RESPONSE_HEADERS)
            return status, raw, mime, returned
        finally:
            self.pending.pop(ident, None)

    async def close(self, app=None):
        for socket in list(self.connections.values()):
            await socket.close(code=1001, message=b'server shutdown')
        for device in list(self.connections):
            self.drop(device)
