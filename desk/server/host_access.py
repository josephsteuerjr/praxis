"""Explicit owner provisioning of host root, independent of agent configuration.

sudo python3 server/host_access.py enable --install /opt/helene
sudo python3 server/host_access.py disable --install /opt/helene
sudo python3 server/host_access.py status --install /opt/helene

Enable prepares a separate root-owned executor and starts its systemd unit.
It never changes agent_mode or service.session0 and never restarts the agent.
The owner recreates the container to connect the socket, then requests the
already-granted host access through that agent's connected settings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import socket
import struct
import subprocess
import time
import tempfile

STATE = Path('/var/lib/helene-host-broker')
UNIT_DIR = Path('/etc/systemd/system')


def instance(container):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', container):
        raise ValueError('Invalid Docker container name')
    return STATE / container


def root_directory(path, mode):
    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError(f'Expected a root-owned directory without group/other write: {path}')
    path.chmod(mode)


def write_root(path, data):
    if path.is_symlink():
        raise ValueError(f'Refusing symlink: {path}')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
        os.chown(path, 0, 0); path.chmod(0o600)
    finally:
        tmp.unlink(missing_ok=True)


def provision(install, container):
    install = Path(install).resolve(strict=True)
    program = shutil.which('python3')
    if not program:
        raise ValueError('Host Python 3 is required')
    env = install / 'server/.env'
    if env.is_symlink():
        raise ValueError('Refusing server/.env symlink')
    content = env.read_text() if env.exists() else ''
    inspect = subprocess.run(['docker', 'inspect', container], check=True,
                             capture_output=True, text=True, timeout=10)
    box = json.loads(inspect.stdout)[0]
    if not box['State']['Running']:
        raise ValueError('The intended agent container must be running during provisioning')
    subprocess.run(['docker', 'top', container, '-eo', 'pid'], check=True,
                   capture_output=True, text=True, timeout=10)
    mounts = {m['Destination']: m['Source'] for m in box['Mounts'] if m['Type'] == 'bind'}
    for dest, rel in [('/opt/helene/helene.json', 'helene.json'), ('/opt/helene/data', 'data')]:
        if Path(mounts.get(dest, '/missing')).resolve() != (install / rel).resolve():
            raise ValueError(f'{container} does not belong to {install}: {dest} differs')
    uid = int(subprocess.check_output(['docker', 'exec', container, 'id', '-u', 'helene'], timeout=10))
    gid = int(subprocess.check_output(['docker', 'exec', container, 'id', '-g', 'helene'], timeout=10))
    if uid == 0 or gid == 0:
        raise ValueError('The agent must run as its own non-root account')
    root_directory(STATE, 0o700)
    home = instance(container)
    root_directory(home, 0o700)
    socket_dir = home / 'socket'
    root_directory(socket_dir, 0o750)
    os.chown(socket_dir, 0, gid)
    policy = home / 'policy.json'
    if policy.exists():
        previous = json.loads(policy.read_text())
        if previous.get('install') != str(install):
            raise ValueError('This container name already has a grant for another installation')
    data = {'schema': 'helene.host-root.v1', 'enabled': False, 'install': str(install),
            'container': container, 'agent_uid': uid, 'agent_gid': gid}
    # Reprovisioning revokes the previous grant before replacing privileged code.
    # Any failure from this point leaves root disabled, even if the old unit survives.
    write_root(policy, (json.dumps(data, indent=2) + '\n').encode())
    write_root(home / 'host_broker.py', Path(__file__).with_name('host_broker.py').read_bytes())
    # Only the socket directory is mounted; policy and privileged source stay outside.
    lines = [line for line in content.splitlines() if not line.startswith('HELENE_HOST_BROKER_DIR=')]
    lines.append('HELENE_HOST_BROKER_DIR=' + str(socket_dir))
    write_root(env, ('\n'.join(lines) + '\n').encode())
    unit = f'helene-host-broker-{container}.service'
    text = ('[Unit]\nDescription=Hélène owner-granted host root broker\nAfter=docker.service\n'
            '[Service]\nType=simple\nUser=root\nUMask=0077\n'
            f'ExecStart={program} {home}/host_broker.py --policy {policy} --socket {socket_dir}/broker.sock\n'
            'Restart=on-failure\nRestartSec=2\nKillMode=control-group\n'
            '[Install]\nWantedBy=multi-user.target\n')
    path = UNIT_DIR / unit
    write_root(path, text.encode())
    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
    subprocess.run(['systemctl', 'enable', unit], check=True, timeout=30)
    subprocess.run(['systemctl', 'restart', unit], check=True, timeout=30)
    subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=True, timeout=10)
    endpoint = socket_dir / 'broker.sock'
    service_pid = int(subprocess.check_output(
        ['systemctl', 'show', '--property=MainPID', '--value', unit], timeout=10))
    if service_pid <= 0:
        raise ValueError('Host broker service has no running process')
    deadline = time.monotonic() + 5
    while True:
        try:
            with socket.socket(socket.AF_UNIX) as peer:
                peer.settimeout(0.2); peer.connect(str(endpoint))
                pid, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid != service_pid or uid != 0:
                raise ValueError('Socket does not belong to the root broker service')
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise ValueError('Host broker socket did not become ready') from None
            time.sleep(0.05)
    info = endpoint.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != 0o660):
        raise ValueError('Host broker did not create its root-owned socket')
    data['enabled'] = True
    write_root(policy, (json.dumps(data, indent=2) + '\n').encode())
    return {'enabled': True, 'scope': 'host', 'unit': unit, 'socket': str(socket_dir / 'broker.sock'),
            'broker_sha256': hashlib.sha256((home / 'host_broker.py').read_bytes()).hexdigest(),
            'note': 'Host grant is ready. Recreate this agent container to connect the socket, '
                    'then request host access in its server settings. Agent mode/config were preserved.'}


def main():
    parser = argparse.ArgumentParser(description='Owner-controlled host root for one server agent')
    parser.add_argument('action', choices=('enable', 'disable', 'status'))
    parser.add_argument('--install', type=Path, default=Path('/opt/helene'))
    parser.add_argument('--container', default='helene')
    args = parser.parse_args()
    home = instance(args.container)
    if args.action == 'status':
        policy = home / 'policy.json'
        row = json.loads(policy.read_text()) if policy.exists() else {}
        print(json.dumps({'enabled': row.get('enabled') is True, 'scope': 'host',
                          'install': row.get('install'), 'socket_present': (home / 'socket/broker.sock').exists()}))
        return
    if os.geteuid() != 0:
        raise SystemExit('This changes host privileges: run the explicit owner command with sudo')
    if args.action == 'enable':
        result = provision(args.install, args.container)
    else:
        policy = home / 'policy.json'
        if policy.exists():
            row = json.loads(policy.read_text())
            if row.get('install') != str(args.install.resolve()):
                raise SystemExit('Grant belongs to another installation')
            row['enabled'] = False
            write_root(policy, (json.dumps(row, indent=2) + '\n').encode())
            subprocess.run(['systemctl', 'disable', '--now', f'helene-host-broker-{args.container}.service'],
                           check=True, timeout=30)
        result = {'enabled': False, 'scope': 'host'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
