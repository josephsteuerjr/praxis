"""Read-only server receipt. Prints no keys, tokens, session contents or logs.

python3 server/doctor.py --install /opt/helene --container helene
Does not save config, send Telegram, restart, update, delete, or execute root work.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import time
from urllib.parse import urlsplit


def config_receipt(path):
    raw = Path(path).read_bytes()
    cfg = json.loads(raw.decode('utf-8-sig'))
    info = Path(path).stat()
    model = cfg.get('model') if isinstance(cfg.get('model'), dict) else {}
    try:
        url = urlsplit(str(model.get('base_url') or ''))
        origin = f'{url.scheme}://{url.hostname or ""}' + (f':{url.port}' if url.port else '')
    except ValueError:
        origin = '[invalid route]'
    def flag(block, key):
        value = cfg.get(block)
        value = value.get(key) if isinstance(value, dict) else None
        return value if isinstance(value, bool) else None
    telegram = cfg.get('telegram') if isinstance(cfg.get('telegram'), dict) else {}
    sensitive = [str(model.get('key') or ''), str(telegram.get('bot_token') or '')]
    def safe(value):
        if not isinstance(value, str): return None
        for secret in sensitive:
            if secret: value = value.replace(secret, '[redacted]')
        return value[:200]
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'inode': info.st_ino, 'uid': info.st_uid,
            'permissions': oct(stat.S_IMODE(info.st_mode)),
            'agent_mode': cfg.get('agent_mode') if isinstance(cfg.get('agent_mode'), str)
                          and cfg['agent_mode'] in {'sandbox', 'interactive', 'service'} else None,
            'sandbox_enabled': flag('sandbox', 'enabled'), 'session0': flag('service', 'session0'),
            'configured_model': safe(model.get('model')), 'configured_framework': safe(model.get('framework')),
            'configured_base_origin': safe(origin), 'relay_enabled': flag('relay', 'enabled')}


def bounded_size(folder, seconds=3, max_files=100000):
    total = count = 0
    end = time.monotonic() + seconds
    pending = [Path(folder)]
    while pending:
        current = pending.pop()
        for entry in os.scandir(current):
            if time.monotonic() > end or count >= max_files:
                return {'bytes_at_least': total, 'files_seen': count, 'complete': False}
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                pending.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
                count += 1
    return {'bytes_at_least': total, 'files_seen': count, 'complete': True}


def backup_receipts(root):
    base = Path(root) / '.updater/backups'
    if base.parent.is_symlink() or base.is_symlink() or not base.exists():
        return []
    rows = []
    for path in sorted(base.iterdir(), key=lambda p: p.name):
        if len(rows) >= 30:
            break
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            continue
        try:
            owner = pwd.getpwuid(info.st_uid).pw_name
        except KeyError:
            owner = str(info.st_uid)
        row = {'path': str(path), 'uid': info.st_uid,
               'owner': owner,
               'mtime_utc': datetime.datetime.fromtimestamp(info.st_mtime, datetime.timezone.utc).isoformat()}
        try:
            row.update(bounded_size(path))
            cfg = path / 'helene.json'
            if cfg.is_file() and not cfg.is_symlink():
                row['config_sha256'] = hashlib.sha256(cfg.read_bytes()).hexdigest()
        except OSError as exc:
            row['error'] = type(exc).__name__
        rows.append(row)
    return rows


def collect(install, container):
    install = Path(install).resolve(strict=True)
    out = {'schema': 'helene.server-doctor.v1', 'read_only': True, 'install': str(install),
           'free_bytes': shutil.disk_usage(install).free, 'host_config': config_receipt(install / 'helene.json'),
           'backups': backup_receipts(install)}
    # Parse selected Docker fields; never print .Config.Env or inspect output as a whole.
    reply = subprocess.run(['docker', 'inspect', container], capture_output=True, text=True, timeout=10)
    if reply.returncode:
        out['container'] = {'available': False}; return out
    box = json.loads(reply.stdout)[0]
    out['container'] = {'available': True, 'id': box['Id'], 'image_id': box['Image'],
        'running': box['State']['Running'], 'host_pid': box['State']['Pid'],
        'mounts': [{key: item.get(key) for key in ('Type', 'Source', 'Destination', 'RW')}
                   for item in box.get('Mounts', []) if item['Destination'] in
                   {'/opt/helene/helene.json', '/opt/helene/data', '/run/helene-host'}]}
    code = '''import ast,json,os,pathlib,stat,hashlib
p=pathlib.Path('/opt/helene/helene.json');raw=p.read_bytes();cfg=json.loads(raw);s=p.stat()
base=p.parent;tree=base/str(cfg.get('code') or 'tree');version=json.loads((base/'helene-build.json').read_text())['version'] if (base/'helene-build.json').exists() else None
names={n.name for n in ast.parse((tree/'work_loop.py').read_bytes()).body if isinstance(n,ast.FunctionDef)}
def flag(block,key):
    value=cfg.get(block);value=value.get(key) if isinstance(value,dict) else None
    return value if isinstance(value,bool) else None
mode=cfg.get('agent_mode');mode=mode if isinstance(mode,str) and mode in {'sandbox','interactive','service'} else None
version=version if isinstance(version,str) and len(version)<40 and all(c in '0123456789.-abcdefghijklmnopqrstuvwxyz' for c in version) else None
print(json.dumps({'version':version,'config_sha256':hashlib.sha256(raw).hexdigest(),'config_inode':s.st_ino,'config_uid':s.st_uid,'config_permissions':oct(stat.S_IMODE(s.st_mode)),'agent_mode':mode,'sandbox_enabled':flag('sandbox','enabled'),'session0':flag('service','session0'),'owner_followup_present':'owner_followup' in names,'host_socket_present':pathlib.Path('/run/helene-host/broker.sock').exists(),'native_socket_present':pathlib.Path('/run/helene/broker.sock').exists()}))'''
    result = subprocess.run(['docker', 'exec', container, 'python', '-c', code],
                            capture_output=True, text=True, timeout=15)
    out['container_source'] = json.loads(result.stdout) if result.returncode == 0 else {'read_failed': True}
    if result.returncode == 0:
        out['config_bytes_match'] = out['host_config']['sha256'] == out['container_source']['config_sha256']
    return out


def main():
    parser = argparse.ArgumentParser(description='Read-only Hélène server evidence without credentials')
    parser.add_argument('--install', type=Path, default=Path('/opt/helene'))
    parser.add_argument('--container', default='helene')
    args = parser.parse_args()
    print(json.dumps(collect(args.install, args.container), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
