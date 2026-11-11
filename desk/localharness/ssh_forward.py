"""Windowless Windows SSH forwarding worker, suitable for a pythonw logon task.

Configuration holds paths to an existing identity and pinned known_hosts, never
passwords. No shell or console is created. The child Job dies with this worker.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import time


def command(config):
    host = str(config['host'])
    user = str(config['user'])
    if not host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in host):
        raise ValueError('Invalid SSH host')
    if not user or user.startswith('-') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in user):
        raise ValueError('Invalid SSH user')
    def port(name, default=None):
        value = config.get(name, default)
        if type(value) is not int or not 1 <= value <= 65535:
            raise ValueError('Invalid ' + name)
        return value
    ssh = str(Path(config['ssh']).resolve(strict=True))
    identity = str(Path(config['identity']).resolve(strict=True))
    hosts = str(Path(config['known_hosts']).resolve(strict=True))
    argv = [ssh, '-F', 'NUL', '-N', '-T', '-p', str(port('port', 22)), '-i', identity]
    for option in ('IdentitiesOnly=yes', 'BatchMode=yes', 'ConnectTimeout=10',
                   'ConnectionAttempts=1', 'StrictHostKeyChecking=yes',
                   'HostKeyAlgorithms=ssh-ed25519', 'GlobalKnownHostsFile=NUL',
                   'UserKnownHostsFile="' + hosts.replace('\\', '/') + '"', 'ExitOnForwardFailure=yes',
                   'ServerAliveInterval=15', 'ServerAliveCountMax=2'):
        argv += ['-o', option]
    argv += ['-L', f"127.0.0.1:{port('listen_port')}:127.0.0.1:{port('target_port')}"]
    if config.get('socks_port') is not None:
        argv += ['-D', f"127.0.0.1:{port('socks_port')}"]
    return argv + [user + '@' + host]


class AlreadyRunning(OSError):
    pass


class Lease:
    """One supervisor per state directory, released by the kernel after death."""
    def __init__(self, path):
        import msvcrt
        self.file = Path(path).open('a+b')
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b'\0')
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            self.file.close()
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise AlreadyRunning('Another forwarding worker holds the lease') from None
            raise

    def close(self):
        self.file.close()


def status(folder, state, **details):
    target = folder / 'status.json'
    temporary = folder / ('status.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(dict(pid=os.getpid(), at=time.time(),
                                        state=state, **details)), encoding='utf-8')
    os.replace(temporary, target)


def run(config, folder):
    try:
        from .desktop_process import JobChild, CustodyFault
    except ImportError:
        from desktop_process import JobChild, CustodyFault
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        lease = Lease(folder / 'worker.lock')
    except AlreadyRunning:
        return 0
    child = None
    attempt = 0
    try:
        argv = command(config)
        while True:
            attempt += 1
            status(folder, 'connecting', attempt=attempt)
            try:
                log = folder / 'ssh.log'
                if log.exists() and log.stat().st_size > 1024 * 1024:
                    os.replace(log, folder / 'ssh.previous.log')
                child = JobChild.spawn(argv, cwd=folder, env=dict(os.environ),
                                       log=log, stopped=lambda: False)
                while True:
                    code = child.poll()
                    if code is not None:
                        child = None
                        status(folder, 'retrying', attempt=attempt, exit_code=code)
                        break
                    # "running" is process state, not proof of network access.
                    status(folder, 'running', attempt=attempt, ssh_pid=child.pid)
                    time.sleep(2)
            except CustodyFault:
                raise  # Never start another child while custody is uncertain.
            except OSError as error:
                if child is not None:
                    child.stop()
                    child = None
                status(folder, 'retrying', attempt=attempt, error=type(error).__name__)
            time.sleep(5)
    finally:
        if child is not None:
            child.stop()
        lease.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    folder = args.config.resolve().parent
    try:
        return run(json.loads(args.config.read_text(encoding='utf-8-sig')), folder)
    except Exception as error:
        status(folder, 'failed', error=type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
