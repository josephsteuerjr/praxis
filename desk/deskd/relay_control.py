"""Narrow owner-channel to root-relay bridge on Linux servers.

Only the desk UID can connect. The agent cannot read the socket directory or
relay credentials. A separate bounded worker keeps Stop/supervision responsive.
Neither raw relay status nor the private control descriptor crosses this socket.
"""
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import threading
from types import SimpleNamespace

MAX_BYTES = 16384


def address(tree):
    return Path(tree) / '.channel/relay-control.sock'


def request(tree, body):
    raw = json.dumps(body).encode() + b'\n'
    if len(raw) > MAX_BYTES:
        raise ValueError('Слишком большой запрос подписки')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(8)
        stream.connect(str(address(tree)))
        stream.sendall(raw)
        with stream.makefile('rb') as reader:
            data = reader.readline(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.endswith(b'\n'):
        raise ValueError('Надзор не подтвердил результат; перечитай состояние, прежде чем повторять')
    result = json.loads(data)
    if not result.get('ok'):
        raise ValueError(result.get('error') or 'Действие подписки не выполнено')
    return result['state']


def start(tree, config, *, uid=None, gid=None, stopped=lambda: False):
    from localharness import relay_login
    tree, config = Path(tree), Path(config)
    path = address(tree)
    if path.parent.is_symlink():
        raise ValueError('Небезопасная папка канала')
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if uid is not None:
        os.chown(path.parent, uid, gid)
    if os.path.lexists(path):
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise ValueError('Путь управления реле занят другим файлом')
        path.unlink()
    allowed_uid = os.getuid() if uid is None else uid
    gate = threading.BoundedSemaphore(4)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            if not gate.acquire(blocking=False):
                return
            try:
                self.connection.settimeout(6)
                _, peer_uid, _ = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if peer_uid != allowed_uid:
                    return
                raw = self.rfile.readline(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES or not raw.endswith(b'\n'):
                    raise ValueError('Некорректный запрос подписки')
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError('Нужен объект действия подписки')
                if stopped() and body.get('action') != 'status':
                    raise ValueError('Аварийный стоп включён; сначала возобнови приложение')
                cfg = json.loads(config.read_text('utf-8-sig'))
                if not ((cfg.get('relay') or {}).get('enabled') or (cfg.get('images') or {}).get('enabled')):
                    raise ValueError('Сначала сохрани выбор подписки ChatGPT')
                state = relay_login.action(SimpleNamespace(tree=tree, config=config), body, browser=False, timeout_sec=4)
                from localharness.model_catalog import local_relay_revision
                state['catalog_revision'] = local_relay_revision(tree)
                result = {'ok': True, 'state': state}
            except ValueError as exc:
                result = {'ok': False, 'error': str(exc)}
            except (OSError, KeyError):
                result = {'ok': False, 'error': 'Реле не подтвердило результат. Обнови состояние перед повторным действием.'}
            finally:
                gate.release()
            try:
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')
            except OSError:
                pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
        block_on_close = False

    server = Server(str(path), Handler)
    os.chmod(path, 0o600)
    if uid is not None:
        os.chown(path, uid, gid)
    thread = threading.Thread(target=server.serve_forever, name='owner-relay-control', daemon=True)
    thread.start()
    return server
