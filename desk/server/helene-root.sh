#!/bin/sh
# Run on the VPS: sh helene-root.sh
# The explicit owner invocation grants host-root to one Hélène agent.
set -eu
if [ "$(id -u)" != 0 ]; then
    command -v sudo >/dev/null 2>&1 || { printf '%s\n' 'Нужны права владельца VPS: запусти этот файл от root.' >&2; exit 1; }
    exec sudo -- sh "$0" "$@"
fi
command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'На VPS нужен Python 3.8 или новее.' >&2; exit 1; }
exec python3 - "$@" <<'PY'
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

p = argparse.ArgumentParser(description='Включить root-брокер VPS для установленной Hélène одной командой.')
p.add_argument('--container', help='имя контейнера, если на VPS несколько Hélène')
p.add_argument('--check', action='store_true', help='только проверить доступ')
p.add_argument('--disable', action='store_true', help='отозвать root, не трогая память и остановку агента')
args = p.parse_args()
log = None
backup = None

def say(text):
    print(text, flush=True)

def run(argv, timeout=30, cwd=None, env=None):
    result = subprocess.run(argv, cwd=cwd, env=env, timeout=timeout,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if log is not None:
        with log.open('a', encoding='utf-8') as target:
            target.write(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError('Не выполнилась команда ' + Path(argv[0]).name
                           + '; подробности в защищённом логе.' if log else
                           'Docker недоступен: проверь, запущен ли он.')
    return result.stdout

def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise RuntimeError('Нужен обычный файл без симлинка: ' + str(path))
    return info

def stopped(install):
    return any(os.path.lexists(str(install / 'data' / name)) for name in
               ('.owner-stop/stop.json', 'memory/.control/panic.json'))

def atomic_config(path, previous, replacement, original_info):
    regular(path)
    if path.read_bytes() != previous:
        raise RuntimeError('Настройки изменились во время работы. Повтори запуск.')
    fd, name = tempfile.mkstemp(prefix='.helene-root-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as target:
            os.fchown(target.fileno(), original_info.st_uid, original_info.st_gid)
            os.fchmod(target.fileno(), stat.S_IMODE(original_info.st_mode))
            target.write(replacement); target.flush(); os.fsync(target.fileno())
        if path.read_bytes() != previous:
            raise RuntimeError('Настройки изменились во время работы. Повтори запуск.')
        os.replace(name, str(path))
        folder = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(folder)
        finally: os.close(folder)
    finally:
        if os.path.exists(name): os.unlink(name)

PROBE = """import json, socket
def call(request):
    with socket.socket(socket.AF_UNIX) as peer:
        peer.settimeout(8); peer.connect('/run/helene-host/broker.sock')
        peer.sendall(json.dumps(request).encode()+b'\\n')
        with peer.makefile('rb') as stream: return json.loads(stream.readline(1048576))
try:
    ping=call({'op':'ping'})
    reply=call({'op':'exec','argv':['/usr/bin/id','-u'],'timeout_sec':5}) if ping.get('ok') else {}
    ok=ping.get('ok') is True and ping.get('scope')=='host' and ping.get('uid')==0 and reply.get('ok') is True and reply.get('scope')=='host' and reply.get('uid')==0 and reply.get('code')==0 and reply.get('out','').strip()=='0'
    print(json.dumps({'ok':ok,'scope':reply.get('scope'),'uid':reply.get('uid')}))
except (OSError, ValueError): print(json.dumps({'ok':False}))
"""

def probe(container):
    try:
        answer = run(['docker', 'exec', '--user', 'helene', container,
                      'python3', '-c', PROBE], timeout=20)
        return json.loads(answer).get('ok') is True
    except (RuntimeError, subprocess.SubprocessError, ValueError):
        return False

try:
    if sys.version_info < (3, 8):
        raise RuntimeError('На VPS нужен Python 3.8 или новее.')
    if os.geteuid() != 0:
        raise RuntimeError('Запусти от владельца VPS: sudo sh helene-root.sh')
    if args.check and args.disable:
        raise RuntimeError('Выбери --check или --disable.')
    if not shutil.which('docker') or not shutil.which('systemctl'):
        raise RuntimeError('Нужна серверная установка Hélène с Docker и systemd.')
    if args.container and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', args.container):
        raise RuntimeError('Неверное имя контейнера.')
    ids = [args.container] if args.container else run(['docker', 'ps', '-aq']).split()
    boxes = json.loads(run(['docker', 'inspect', *ids])) if ids else []
    found = []
    for box in boxes:
        mounts = {m['Destination']: Path(m['Source']) for m in box['Mounts'] if m['Type']=='bind'}
        config = mounts.get('/opt/helene/helene.json')
        if config is None or mounts.get('/opt/helene/data') != config.parent / 'data': continue
        install = config.parent.resolve()
        if not (install / 'server/host_access.py').is_file(): continue
        found.append((box, install))
    if len(found) != 1:
        names = ', '.join(row[0]['Name'].lstrip('/') for row in found)
        raise RuntimeError('Найдено несколько установок (' + names + '). Запусти с --container ИМЯ.'
                           if found else 'Установка Hélène с root-брокером не найдена. Нужен серверный пакет с server/host_access.py.')
    box, install = found[0]
    container = box['Name'].lstrip('/')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', container):
        raise RuntimeError('Неверное имя контейнера Hélène.')
    helper = install / 'server/host_access.py'
    regular(helper); regular(install / 'server/host_broker.py')
    host = [sys.executable, str(helper)]
    grant_args = ['--install', str(install), '--container', container]
    say('Hélène: ' + str(install) + ' · контейнер ' + container)
    if args.disable:
        run([*host, 'disable', *grant_args], timeout=60)
        say('Root-доступ к VPS отозван. Память и остановка агента сохранены.')
        sys.exit(0)
    if args.check:
        ok = probe(container)
        say('Root на хосте VPS проверен: uid=0.' if ok else 'Root на хосте VPS пока недоступен.')
        sys.exit(0 if ok else 1)
    if stopped(install):
        raise RuntimeError('Агент остановлен владельцем (panic). Остановка сохранена; этот скрипт её не снимает.')
    if not box['State']['Running']:
        raise RuntimeError('Контейнер Hélène остановлен. Этот скрипт не возобновляет остановленного агента.')
    if probe(container):
        say('Уже готово: агент выполняет команды root на хосте VPS, uid=0. Перезапуск не нужен.')
        sys.exit(0)
    labels = box['Config'].get('Labels') or {}
    project = labels.get('com.docker.compose.project', '')
    service = labels.get('com.docker.compose.service', '')
    files = labels.get('com.docker.compose.project.config_files', '').split(',')
    if not project or not service or not all(files):
        raise RuntimeError('Контейнер создан без Docker Compose. Автоматически пересоздать его безопасно не получилось.')
    for file in files:
        path = Path(file).resolve(strict=True)
        if install != path and install not in path.parents:
            raise RuntimeError('Compose-файл находится вне установки Hélène: ' + str(path))
        regular(path)
    run(['docker', 'compose', 'version'])
    config = install / 'helene.json'
    info = regular(config); original = config.read_bytes()
    settings = json.loads(original.decode('utf-8-sig'))
    if not isinstance(settings, dict) or not isinstance(settings.get('service', {}), dict):
        raise RuntimeError('Неверная структура helene.json; файл сохранён.')
    grant = json.loads(run([*host, 'status', *grant_args]))
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
    backup = Path('/var/lib/helene-host-broker') / container / 'owner-setup' / stamp
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    saved = backup / 'helene.json.before'
    saved.write_bytes(original); saved.chmod(0o600)
    log = backup / 'setup.log'; log.touch(mode=0o600)
    settings.setdefault('service', {})['session0'] = True
    replacement = (json.dumps(settings, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    wrote = False
    try:
        say('1/3 Включаю отдельную root-службу VPS…')
        run([*host, 'enable', *grant_args], timeout=90)
        if stopped(install): raise RuntimeError('Владелец остановил агента; остановка сохранена.')
        atomic_config(config, original, replacement, info); wrote = True
        say('2/3 Подключаю сокет и перезапускаю только эту Hélène…')
        compose = ['docker', 'compose', '--project-name', project,
                   '--env-file', str(install / 'server/.env')]
        for file in files: compose.extend(['-f', file])
        env = dict(os.environ)
        # The grant just written by host_access must not be shadowed by shell env.
        env.pop('HELENE_HOST_BROKER_DIR', None)
        run([*compose, 'up', '-d', '--no-build', '--pull', 'never', '--no-deps',
             '--force-recreate', service], timeout=120, cwd=str(install), env=env)
        say('3/3 Проверяю команду /usr/bin/id -u через агентский сокет…')
        deadline = time.monotonic() + 45
        while not probe(container):
            if stopped(install): raise RuntimeError('Владелец остановил агента; остановка сохранена.')
            if time.monotonic() > deadline: raise RuntimeError('Root-команда не получила подтверждение от хоста VPS.')
            time.sleep(2)
    except Exception:
        if wrote and config.read_bytes() == replacement:
            atomic_config(config, replacement, original, info)
        if grant.get('enabled') is not True:
            try: run([*host, 'disable', *grant_args], timeout=60)
            except Exception: pass
        raise
    say('Готово: агент получил root на самом VPS; проверено uid=0, scope=host. Окно Hélène можно закрыть.')
    say('Отозвать доступ: sudo sh helene-root.sh --container ' + container + ' --disable')
except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
    say('Не завершено: ' + str(exc))
    if backup is not None: say('Защищённая копия настроек и лог: ' + str(backup))
    sys.exit(1)
PY
