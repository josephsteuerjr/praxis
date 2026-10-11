"""Opt-in host-root executor for one Docker Hélène installation.

This file is installed as a root-owned copy OUTSIDE app/tree by host_access.py.
SO_PEERCRED and Docker's actual process list identify the granted container.
The owner grant is independent of helene.json and survives product updates.
The agent's session0 request and persistent panic latch are checked each call.
No TCP listener, Docker socket mount, privileged container, or root runner.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
import uuid

MAX_REQUEST = 65536
MAX_OUTPUT = 65536
MAX_TIMEOUT = 600
MAX_HANDLERS = 16


def process_birth(pid):
    """Kernel start ticks distinguish the original peer from a recycled PID."""
    raw = Path(f'/proc/{pid}/stat').read_text()
    # comm may contain spaces and parentheses; fields after its final ')' start at 3.
    return raw.rsplit(')', 1)[1].split()[19]


def trusted_json(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Host grant must be a root-owned file without group/other write')
        raw = source.read(MAX_REQUEST + 1)
    if len(raw) > MAX_REQUEST:
        raise ValueError('Host grant is too large')
    row = json.loads(raw)
    if not isinstance(row, dict):
        raise ValueError('Host grant must be an object')
    return row


class HostBroker:
    def __init__(self, policy):
        self.policy = Path(policy)
        self.busy = threading.Lock()

    def audit(self, request_id, peer, op, phase, result=None):
        path = self.policy.with_name('audit.jsonl')
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'ab') as target:
            info = os.fstat(target.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Host audit must be root-owned without group/other write')
            row = {'at': time.time(), 'id': request_id, 'peer_pid': peer[0], 'peer_uid': peer[1],
                   'op': op, 'phase': phase, 'scope': 'host'}
            if result is not None:
                row.update(ok=result.get('ok'), code=result.get('code'))
            # No argv, output, credentials or private owner input in this audit.
            target.write(json.dumps(row).encode() + b'\n'); target.flush(); os.fsync(target.fileno())

    @staticmethod
    def stopped(install):
        root = Path(install) / 'data'
        # Any pending panic stops root effects immediately, before serverboot ACK.
        return any(os.path.lexists(root / rel) for rel in (
            '.owner-stop/stop.json', 'memory/.control/panic.json'))

    def authorize(self, peer, *, check_container=True):
        row = trusted_json(self.policy)
        if row.get('enabled') is not True:
            raise ValueError('Host root is disabled by the server owner')
        pid, uid, _gid = peer
        if uid != row['agent_uid'] or uid == 0 or pid <= 0:
            raise ValueError('This peer is not the granted agent')
        install = Path(row['install'])
        if self.stopped(install):
            raise ValueError('Hélène is stopped; only the server owner may resume it')
        config = json.loads((install / 'helene.json').read_text('utf-8-sig'))
        if not isinstance(config, dict):
            raise ValueError('Agent configuration must be an object')
        service = config.get('service')
        if not isinstance(service, dict) or service.get('session0') is not True:
            raise ValueError('Host root is not requested in this agent configuration')
        # A host account / another container with the same numeric UID is not enough.
        # Docker top returns HOST PIDs and follows container recreation under this name.
        if check_container:
            result = subprocess.run(['docker', 'top', row['container'], '-eo', 'pid'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode:
                raise ValueError('The granted container is unavailable')
            pids = {int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()}
            if pid not in pids:
                raise ValueError('This peer is outside the granted container')
        elif not Path(f'/proc/{pid}').exists():
            raise ValueError('The agent that requested this command has exited')
        return row

    def allowed(self, peer, grant=None, birth=None):
        try:
            current = self.authorize(peer, check_container=False)
            if grant is not None and any(current.get(k) != grant.get(k)
                                         for k in ('install', 'container', 'agent_uid')):
                return False
            if birth is not None and process_birth(peer[0]) != birth:
                return False
            return True
        except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
            return False

    @staticmethod
    def kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def execute(self, request, peer, grant):
        argv = request.get('argv')
        if (not isinstance(argv, list) or not argv or len(argv) > 128
                or not all(isinstance(a, str) and '\0' not in a for a in argv)
                or not os.path.isabs(argv[0])):
            raise ValueError('exec requires an absolute program and a string argv array')
        raw_timeout = request.get('timeout_sec', 120)
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
            raise ValueError('timeout_sec must be a number')
        try:
            timeout = float(raw_timeout)
        except OverflowError:
            raise ValueError('timeout_sec exceeds the supported range') from None
        if not 0 < timeout <= MAX_TIMEOUT:
            raise ValueError(f'timeout_sec must be between 0 and {MAX_TIMEOUT}')
        if not self.busy.acquire(blocking=False):
            raise ValueError('Host broker is busy; wait for the current command')
        proc = None
        try:
            # Recheck after taking the execution slot; grant can have changed meanwhile.
            grant = self.authorize(peer)
            birth = process_birth(peer[0])
            proc = subprocess.Popen(argv, cwd='/', stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                     'HOME': '/root', 'LANG': 'C.UTF-8', 'USER': 'root'})
            start = time.monotonic()
            chunks = {'out': bytearray(), 'err': bytearray()}
            truncated = False
            stopped_reason = ''
            stopped_at = None
            with selectors.DefaultSelector() as watch:
                for stream, key in [(proc.stdout, 'out'), (proc.stderr, 'err')]:
                    os.set_blocking(stream.fileno(), False)
                    watch.register(stream, selectors.EVENT_READ, key)
                next_check = start
                while watch.get_map() or proc.poll() is None:
                    now = time.monotonic()
                    if now - start >= timeout:
                        stopped_reason = 'Host command timed out'
                    if now >= next_check:
                        next_check = now + 0.25
                        # Revocation/session0/panic all stop already-running effects.
                        if not self.allowed(peer, grant, birth):
                            stopped_reason = 'Host access revoked or Hélène stopped'
                    if stopped_reason:
                        stopped_at = stopped_at or now
                        self.kill(proc)
                    for key, _ in watch.select(0.05):
                        block = os.read(key.fileobj.fileno(), 8192)
                        if not block:
                            watch.unregister(key.fileobj)
                            continue
                        dest = chunks[key.data]
                        remaining = MAX_OUTPUT - len(dest)
                        dest.extend(block[:remaining])
                        truncated |= len(block) > remaining
                    if stopped_at is not None and time.monotonic() - stopped_at > 2:
                        break
            proc.wait(timeout=5)
            return {'ok': not stopped_reason, 'code': proc.returncode,
                    **{k: v.decode('utf-8', 'replace') for k, v in chunks.items()},
                    'error': stopped_reason, 'truncated': truncated,
                    'ms': round((time.monotonic() - start) * 1000),
                    'scope': 'host', 'uid': os.geteuid(),
                    'note': (stopped_reason or 'Команда выполнена root на хосте VPS')
                            + ('; вывод ограничен 64 КиБ на поток' if truncated else '')}
        finally:
            if proc is not None:
                self.kill(proc)
                proc.wait(timeout=5)
                proc.stdout.close(); proc.stderr.close()
            self.busy.release()

    def handle(self, request, peer):
        try:
            if not isinstance(request, dict):
                raise ValueError('Request must be an object')
            grant = self.authorize(peer)
            if request.get('op') == 'ping':
                return {'ok': True, 'scope': 'host', 'uid': os.geteuid(),
                        'note': 'Брокер root на хосте VPS отвечает; команды проверяются отдельно'}
            if request.get('op') != 'exec':
                raise ValueError('Host broker supports ping and exec')
            request_id = uuid.uuid4().hex
            self.audit(request_id, peer, 'exec', 'start')
            try:
                result = self.execute(request, peer, grant)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                result = {'ok': False, 'scope': 'host', 'error': str(exc)}
            try:
                self.audit(request_id, peer, 'exec', 'result', result)
            except (OSError, ValueError) as exc:
                result.update(ok=False, error=f'Command ran but its audit result could not be saved: {exc}')
            result['request_id'] = request_id
            return result
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            return {'ok': False, 'scope': 'host', 'error': str(exc)}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        peer = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        raw = self.rfile.readline(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST or not raw.endswith(b'\n'):
            reply = {'ok': False, 'error': 'Incomplete or oversized request'}
        else:
            try:
                reply = self.server.broker.handle(json.loads(raw), peer)
            except (ValueError, UnicodeError):
                reply = {'ok': False, 'error': 'Invalid request JSON'}
        self.wfile.write(json.dumps(reply, ensure_ascii=False).encode() + b'\n')


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.handlers = threading.BoundedSemaphore(MAX_HANDLERS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # Bound threads before starting them, including clients that never send a line.
        if not self.handlers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.handlers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.handlers.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--socket', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Host broker must be started by the server owner as root')
    grant = trusted_json(args.policy)
    # Root-owned parent is provisioned by host_access.py, never from an agent path.
    info = args.socket.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit('Socket directory must be owned by root without group/other write')
    if args.socket.is_symlink():
        raise SystemExit('Refusing a socket symlink')
    args.socket.unlink(missing_ok=True)
    with Server(str(args.socket), Handler) as server:
        server.broker = HostBroker(args.policy)
        os.chown(args.socket, 0, grant['agent_gid'])
        os.chmod(args.socket, 0o660)
        server.serve_forever(poll_interval=0.25)


if __name__ == '__main__':
    main()
