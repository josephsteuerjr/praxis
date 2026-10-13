"""Windows desktop publisher, without a local model or Telegram receiver.

The local owner's grant is checked on every request, independent of the server.
Only a fixed native bridge on loopback is reachable. The desktop opens WSS; no
desktop listener, SSH tunnel, window owner key, or native controller token leaves
this process. Local System commands use Hélène's existing approved service.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import time
import urllib.parse
import uuid
import aiohttp

APP = Path(__file__).resolve().parent.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from deskd.remote_computer import (PREFIX, DEVICE, SCOPES, MAX_BODY, MAX_FRAME, body_route,
    artifact_headers, ARTIFACT_REQUEST_HEADERS, ARTIFACT_RESPONSE_HEADERS)
from deskd.remote_computer import scope_for


def secure_base(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('Desktop server address must be a string')
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError('Invalid desktop server address')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('Desktop connection requires HTTPS')
    return value.rstrip('/')


def grant(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    computer = cfg.get('computer') or {}
    remote = computer.get('remote') or {}
    if cfg.get('mode') != 'remote' or remote.get('enabled') is not True:
        raise PermissionError('Remote desktop is disabled by the local owner')
    device, token = remote.get('id'), remote.get('token')
    if not isinstance(device, str) or not DEVICE.fullmatch(device):
        raise ValueError('Desktop pairing is missing')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
        raise ValueError('Desktop credential is missing')
    base = secure_base(str(cfg.get('base') or ''))
    if secure_base(str(remote.get('base') or '')) != base:
        raise PermissionError('Desktop grant belongs to a different server')
    scopes = remote.get('scopes')
    if not isinstance(scopes, list) or any(s not in SCOPES for s in scopes):
        raise ValueError('Invalid local desktop permissions')
    return {'id': device, 'token': token, 'base': base, 'scopes': list(dict.fromkeys(scopes)),
            'system': (cfg.get('service') or {}).get('session0') is True}


class Publisher:
    def __init__(self, config: Path, install: Path, data: Path):
        self.config, self.install, self.data = Path(config), Path(install), Path(data)
        self.body = None
        self.initial = None
        self.permissions = {'requests': {}, 'artifacts': {}}
        self.permissions_path = None

    def load_permissions(self):
        self.permissions_path = self.data / 'body' / ('remote-' + self.initial['id']) / 'rpc-permissions.json'
        try:
            value = json.loads(self.permissions_path.read_text(encoding='utf8'))
            if not isinstance(value.get('requests'), dict) or not isinstance(value.get('artifacts'), dict):
                raise ValueError('Invalid receipt permissions')
            self.permissions = value
        except FileNotFoundError:
            pass

    def save_permissions(self):
        self.permissions_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.permissions_path.with_name('.rpc-permissions-' + uuid.uuid4().hex)
        try:
            temp.write_text(json.dumps(self.permissions), encoding='utf8')
            os.chmod(temp, 0o600)
            os.replace(temp, self.permissions_path)
        finally:
            temp.unlink(missing_ok=True)

    def start_body(self):
        import body
        cfg = json.loads(self.config.read_text(encoding='utf-8-sig'))
        self.initial = grant(self.config)
        self.load_permissions()
        cfg['computer'] = {**(cfg.get('computer') or {}), 'enabled': True}
        self.body = body.Body(self.install, self.data, cfg)
        self.body.home = self.data / 'body' / ('remote-' + self.initial['id'])
        self.body.device = self.initial['id']
        self.body.service = False  # this publisher runs in the Windows owner's session
        if not self.body.start():
            raise RuntimeError('Local desktop body could not start')

    def stop_body(self):
        if self.body is not None:
            self.body.stop()
            self.body = None

    def authorize(self, request: dict) -> tuple[dict, str, bytes, str | None]:
        from localharness import owner_stop
        if owner_stop.stopped():
            raise PermissionError('Computer stopped by owner')
        current = grant(self.config)
        if not self.initial or any(current[k] != self.initial[k] for k in ('id', 'token', 'base')):
            raise PermissionError('Desktop pairing changed')
        if request.get('device') != current['id']:
            raise PermissionError('Wrong desktop target')
        deadline = request.get('deadline')
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline) \
                or deadline <= time.time() or deadline > time.time() + 125:
            raise PermissionError('Desktop request expired')
        method, path = request.get('method'), request.get('path')
        if not isinstance(method, str) or not isinstance(path, str):
            raise ValueError('Invalid desktop request')
        kind = body_route(method, path, current['id'])
        artifact_headers(request.get('headers', {}), ARTIFACT_REQUEST_HEADERS)
        encoded = request.get('body')
        if not isinstance(encoded, str) or len(encoded) > MAX_FRAME:
            raise ValueError('Invalid desktop request body')
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_BODY:
            raise ValueError('Desktop request too large')
        required = None
        if kind in ('invoke', 'broker'):
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('Invalid invocation')
            if kind == 'broker':
                required = 'computer.process'
                if payload.get('op') == 'exec' and not current['system']:
                    raise PermissionError('Local owner has not granted Windows System commands')
            else:
                cap = payload.get('capability')
                if not isinstance(cap, str):
                    raise ValueError('Missing native capability')
                required = scope_for(cap)
                if payload.get('execution') not in (None, 'interactive'):
                    raise PermissionError('Use the selected Windows broker for System execution')
                ident = payload.get('request_id')
                if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ident):
                    raise ValueError('Native request identity is required')
                previous = self.permissions['requests'].get(ident)
                if ident in self.permissions['requests'] and previous != required:
                    raise PermissionError('Native request identity changed permission')
                self.permissions['requests'][ident] = required
        elif kind == 'receipt':
            ident = path.rsplit('/', 1)[1]
            if ident not in self.permissions['requests']:
                raise PermissionError('Receipt does not belong to this desktop grant')
            required = self.permissions['requests'][ident]
        elif kind == 'artifact':
            sha = path.split('/')[3]
            if method != 'GET':
                required = 'computer.files'
                self.permissions['artifacts'][sha] = required
            else:
                if sha not in self.permissions['artifacts']:
                    if 'computer.files' not in current['scopes']:
                        raise PermissionError('Artifact does not belong to an allowed desktop observation')
                    required = 'computer.files'
                else:
                    required = self.permissions['artifacts'][sha]
        if required and required not in current['scopes']:
            raise PermissionError('Local owner has revoked ' + required)
        return current, kind, raw, required

    def record_artifacts(self, value, required):
        if isinstance(value, dict):
            artifacts = value.get('artifacts')
            if isinstance(artifacts, list):
                for item in artifacts:
                    sha = item.get('sha256') if isinstance(item, dict) else None
                    if isinstance(sha, str) and re.fullmatch(r'[a-f0-9]{64}', sha):
                        self.permissions['artifacts'][sha] = required
            for item in value.values():
                self.record_artifacts(item, required)
        elif isinstance(value, list):
            for item in value:
                self.record_artifacts(item, required)

    async def execute(self, request: dict, http: aiohttp.ClientSession) -> dict:
        ident = request.get('id')
        response = {'kind': 'response', 'id': ident, 'status': 403, 'content_type': 'application/json'}
        try:
            current, kind, raw, required = self.authorize(request)
            if self.body is None:
                raise ConnectionError('Local desktop is not running')
            if kind == 'broker':
                from localharness import broker
                cfg = json.loads(self.config.read_text(encoding='utf-8-sig'))
                root = broker.install_root()
                service_cfg = json.loads((root / 'helene.json').read_text(encoding='utf-8-sig'))
                broker_tree = Path(service_cfg.get('tree') or 'data')
                if not broker_tree.is_absolute():
                    broker_tree = root / broker_tree
                args = json.loads(raw)
                cmd, argv = str(args.get('cmd') or ''), args.get('args') or []
                if args.get('shell') is not None:
                    if args.get('shell') != 'powershell' or not isinstance(args.get('command'), str):
                        raise ValueError('Unsupported Windows broker shell')
                    cmd = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' /
                              'WindowsPowerShell/v1.0/powershell.exe')
                    argv = ['-NoProfile', '-NonInteractive', '-Command', args['command']]
                receipt = await asyncio.to_thread(broker.Broker(broker_tree, cfg).ask,
                    str(args.get('op') or 'ping'), cmd, argv,
                    str(args.get('why') or ''), min(120, max(1, int(request['deadline'] - time.time())),
                        max(1, int(args.get('timeout_sec') or 60))), 0)
                raw = json.dumps({'receipt': receipt, 'host': 'windows', 'device_id': current['id']}).encode()
                response['status'] = 200
            else:
                self.save_permissions()  # record authority before starting any native effect
                mime = request.get('content_type') or 'application/json'
                if mime not in ('application/json', 'application/octet-stream'):
                    raise ValueError('Unsupported desktop request content type')
                url = f'http://127.0.0.1:{self.body.port}' + request['path']
                remaining = min(120, max(0.1, request['deadline'] - time.time()))
                async with http.request(request['method'], url, data=raw,
                        headers={**request.get('headers', {}), 'Authorization': 'Bearer ' + self.body.controller_token, 'Content-Type': mime},
                        allow_redirects=False, timeout=aiohttp.ClientTimeout(total=remaining)) as result:
                    collected = bytearray()
                    async for chunk in result.content.iter_chunked(65536):
                        collected.extend(chunk)
                        if len(collected) > MAX_BODY:
                            raise ValueError('Native desktop response too large')
                    raw = bytes(collected)
                    response.update(status=result.status, content_type=result.headers.get('Content-Type', 'application/octet-stream'))
                    response['headers'] = {k: result.headers[k] for k in ARTIFACT_RESPONSE_HEADERS if k in result.headers}
                if response['content_type'].startswith('application/json'):
                    self.record_artifacts(json.loads(raw), required)
                    self.save_permissions()
        except PermissionError as error:
            raw = json.dumps({'ok': False, 'code': 'owner_refused', 'error': str(error)}).encode()
        except Exception as error:
            response['status'] = 502
            raw = json.dumps({'ok': False, 'code': 'desktop_unavailable', 'error': type(error).__name__}).encode()
        response['body'] = base64.b64encode(raw).decode('ascii')
        return response

    async def connection(self):
        current = grant(self.config)
        self.initial = current
        url = current['base'].replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
        url += PREFIX + '/connect/' + current['id']
        tasks = set()
        semaphore = asyncio.Semaphore(1)
        async with aiohttp.ClientSession(trust_env=False) as local, aiohttp.ClientSession(trust_env=True,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15)) as external:
            async with external.ws_connect(url, headers={'Authorization': 'Bearer ' + current['token']},
                    heartbeat=20, max_msg_size=MAX_FRAME,
                    timeout=aiohttp.ClientWSTimeout(ws_receive=65, ws_close=5)) as ws:
                if grant(self.config) != current:
                    raise PermissionError('Local owner changed the grant while connecting')
                self.start_body()
                await ws.send_json({'kind': 'hello', 'identity': {'platform': 'windows',
                    'name': socket.gethostname(), 'version': '1.6.0', 'scopes': current['scopes'],
                    'system': current['system']}})
                ready = await ws.receive_json(timeout=15)
                if ready.get('kind') != 'ready' or ready.get('device') != current['id']:
                    raise ConnectionError('Server did not confirm this desktop')

                async def supervise():
                    from localharness import owner_stop
                    last_beat = 0
                    while not ws.closed:
                        await asyncio.sleep(1)
                        try:
                            changed = grant(self.config) != current or owner_stop.stopped()
                        except Exception:
                            changed = True
                        if changed:
                            await ws.close(code=1008, message=b'local owner changed desktop grant')
                            await asyncio.to_thread(self.stop_body)
                            return
                        if time.monotonic() - last_beat >= 20:
                            await ws.send_json({'kind': 'heartbeat'})
                            last_beat = time.monotonic()

                async def answer(value):
                    async with semaphore:
                        if ws.closed:
                            return
                        response = await self.execute(value, local)
                        if not ws.closed:
                            await ws.send_json(response)

                watcher = asyncio.create_task(supervise())
                try:
                    async for message in ws:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            if message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                                break
                            continue
                        value = json.loads(message.data)
                        if not isinstance(value, dict):
                            raise ValueError('Invalid server desktop frame')
                        if value.get('kind') == 'heartbeat':
                            continue
                        if value.get('kind') != 'request' or not re.fullmatch(r'[a-f0-9]{32}', str(value.get('id') or '')):
                            raise ValueError('Invalid server desktop request')
                        if len(tasks) >= 16:
                            raise ConnectionError('Desktop request queue is full')
                        task = asyncio.create_task(answer(value))
                        tasks.add(task); task.add_done_callback(tasks.discard)
                finally:
                    watcher.cancel()
                    for task in list(tasks):
                        task.cancel()
                    await asyncio.gather(watcher, *tasks, return_exceptions=True)
                    await asyncio.to_thread(self.stop_body)

    async def run(self):
        delay = 1
        while True:
            try:
                grant(self.config)
                await self.connection()
                delay = 1
            except FileNotFoundError:
                self.stop_body()
                return
            except (PermissionError, ValueError):
                self.stop_body()
                try:
                    cfg = json.loads(self.config.read_text(encoding='utf-8-sig'))
                    if cfg.get('mode') != 'remote':
                        return
                except FileNotFoundError:
                    return
                except ValueError:
                    pass
                await asyncio.sleep(2)
                continue  # idle publisher: a later local owner grant needs no service restart
            except asyncio.CancelledError:
                self.stop_body()
                raise
            except aiohttp.WSServerHandshakeError as error:
                self.stop_body()
                if error.status in (401, 403):
                    print(json.dumps({'state': 'pairing_required', 'http': error.status}), flush=True)
                    previous = self.initial
                    while True:
                        await asyncio.sleep(2)
                        try:
                            if grant(self.config) != previous:
                                break
                        except (PermissionError, ValueError):
                            continue
                        except FileNotFoundError:
                            return
                    continue
                print(json.dumps({'state': 'reconnecting', 'http': error.status}), flush=True)
            except Exception as error:
                self.stop_body()
                print(json.dumps({'state': 'reconnecting', 'error': type(error).__name__}), flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


async def pair(config: Path, scopes: list[str], system: bool | None = None, seen: str | None = None):
    """Explicit local owner action; stdout contains public status only."""
    before = config.read_bytes()
    if seen is not None and str(config.stat().st_mtime_ns) != seen:
        raise ValueError('Settings changed before desktop pairing started')
    cfg = json.loads(before.decode('utf-8-sig'))
    if cfg.get('mode') != 'remote':
        raise ValueError('Connect this window to its server first')
    base = secure_base(str(cfg.get('base') or ''))
    key = cfg.get('key')
    if not isinstance(key, str) or not key.strip():
        raise ValueError('The saved server connection has no window key')
    if any(s not in SCOPES for s in scopes):
        raise ValueError('Invalid local owner permissions')
    headers = {'Authorization': 'Bearer ' + key}
    async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=20)) as http:
        async with http.post(base + PREFIX + '/pair', headers=headers,
                             json={'name': socket.gethostname(), 'scopes': scopes}, allow_redirects=False) as response:
            if response.status != 200:
                raise ConnectionError('Server refused desktop pairing: HTTP ' + str(response.status))
            credential = await response.json()
        device, token = credential.get('id'), credential.get('token')
        if not isinstance(device, str) or not DEVICE.fullmatch(device) or not isinstance(token, str) \
                or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
            raise ValueError('Server returned an invalid desktop credential')
        try:
            after = None
            computer = cfg.get('computer') or {}
            cfg['computer'] = {**computer, 'remote': {'enabled': True, 'id': device,
                'token': token, 'base': base, 'scopes': list(dict.fromkeys(scopes))}}
            if system is not None:
                if not isinstance(system, bool) or (system and 'computer.process' not in scopes):
                    raise ValueError('Windows System commands require local process permission')
                cfg['service'] = {**(cfg.get('service') or {}), 'session0': system}
            after = (json.dumps(cfg, ensure_ascii=False, indent=2) + '\n').encode('utf8')
            if config.read_bytes() != before:
                raise ValueError('Settings changed while pairing; retry from the current window')
            # Preserve the existing owner/SYSTEM ACL instead of replacing it with a new inode.
            with config.open('r+b') as output:
                output.write(after); output.truncate(); output.flush(); os.fsync(output.fileno())
            async with http.post(base + PREFIX + '/select', headers=headers,
                                 json={'device': device}, allow_redirects=False) as response:
                if response.status != 200:
                    raise ConnectionError('Desktop paired but server selection failed: HTTP ' + str(response.status))
        except Exception:
            # A lost server connection must not skip rollback of the local grant.
            try:
                async with http.post(base + PREFIX + '/revoke', headers=headers,
                                     json={'device': device}, allow_redirects=False) as response:
                    await response.read()
            finally:
                if after is not None and config.read_bytes() == after:
                    with config.open('r+b') as output:
                        output.write(before); output.truncate(); output.flush(); os.fsync(output.fileno())
            raise
        previous = (json.loads(before.decode('utf-8-sig')).get('computer') or {}).get('remote') or {}
        # Replacing this profile's pairing must not consume one server slot per click.
        if previous.get('id') != device and previous.get('base') == base and DEVICE.fullmatch(str(previous.get('id') or '')):
            try:
                async with http.post(base + PREFIX + '/revoke', headers=headers,
                                     json={'device': previous['id']}, allow_redirects=False) as response:
                    await response.read()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass  # new grant is committed; the owner can revoke the offline old row
    return {'ok': True, 'id': device, 'name': socket.gethostname(), 'scopes': scopes,
            'server': base, 'enabled': True, 'system': (cfg.get('service') or {}).get('session0') is True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('pair', 'run'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--data', default='')
    parser.add_argument('--scopes', default='')
    parser.add_argument('--system', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--mtime-ns', default=None)
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('This desktop publisher requires Windows')
    config = Path(args.config).resolve(strict=True)
    os.environ.pop('HELENE_TOKEN', None)
    os.environ.pop('PRAXIS_DESK_TOKEN', None)
    if args.action == 'pair':
        print(json.dumps(asyncio.run(pair(config, [s for s in args.scopes.split(',') if s], args.system, args.mtime_ns))))
        return
    import boot
    boot.watch_parent('remote desktop publisher')
    data = Path(args.data).resolve() if args.data else config.parent / 'data'
    data.mkdir(parents=True, exist_ok=True)
    # OS lock, not stale-PID guessing: the service and window cannot export this
    # agent's desktop twice. The lock is released even on abrupt process death.
    import msvcrt
    with (data / '.remote-computer.lock').open('a+b') as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b'1'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        publisher = Publisher(config, APP.parent, data)
        try:
            asyncio.run(publisher.run())
        finally:
            publisher.stop_body()


if __name__ == '__main__':
    main()
