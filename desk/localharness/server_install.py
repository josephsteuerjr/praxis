"""Install a new server agent or update an installed one from the window (11.11.2026).

Yegor: the server version is installed and updated directly from the application, without the
person running separate scripts. Both paths reuse the reviewed transfer machinery: the host key
is confirmed before any password is sent, the programme kit is uploaded and its hash checked, and
`server/install.sh` does the work on the server (a fresh installation, or an update through the
installation's own updater with its backups and automatic rollback).

A new agent is found by the window the same way as after a transfer: through the menu button of
the agent's Telegram bot (Yegor chose this, 11.11). Credentials arrive over stdin, are used for
the SSH session only and never enter receipts, logs or the agent's context.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import re
import secrets
import shlex
import tempfile
import time

import server_transfer as st
import remote_locator
from server_destination import destination


class InstallError(st.TransferError):
    pass


BOT = re.compile(r'\d{5,12}:[A-Za-z0-9_-]{30,64}')

# Runs on the server host. Reads the window key there and returns only channel facts.
CHANNEL_CHECK = r'''
import json,sys,urllib.request
root,port=sys.argv[1],int(sys.argv[2])
key=open(root+'/data/.serverboot/desk-token').read().strip()
def get(path):
    request=urllib.request.Request('http://127.0.0.1:%d%s'%(port,path),headers={'Authorization':'Bearer '+key})
    with urllib.request.urlopen(request,timeout=10) as reply:return json.load(reply)
out={}
for path in ('/api/state','/api/supervisor','/api/panic'):
    try:out[path]=get(path)
    except Exception as error:out[path]={'error':type(error).__name__}
print(json.dumps(out))
'''


def receipt_path(config_path):
    return Path(config_path).parent / 'server-install.json'


def saved(config_path) -> dict:
    try:
        value = json.loads(receipt_path(config_path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save(config_path, row):
    from private_files import write_private
    write_private(receipt_path(config_path), (json.dumps(row, ensure_ascii=False) + '\n').encode())


def kit_version(program: Path) -> str:
    return str(json.loads((Path(program) / 'helene-build.json').read_text(encoding='utf-8-sig')).get('version') or '')


def telegram_fields(raw) -> dict:
    tg = raw.get('telegram') or {}
    token = str(tg.get('bot_token') or '').strip()
    owner = str(tg.get('owner_id') or '').strip()
    if not BOT.fullmatch(token) or not owner.isdigit() or int(owner) <= 0:
        raise InstallError('Укажи токен бота нового агента и свой Telegram ID: через кнопку этого бота '
                           'окно будет находить сервер после перезапуска')
    return {'bot_token': token, 'owner_id': int(owner)}


def server_config(program: Path, telegram: dict, name: str) -> dict:
    """The shipped server template with this agent's bot and owner; nothing else changes."""
    cfg = json.loads((Path(program) / 'server/helene.server.json').read_text(encoding='utf-8-sig'))
    cfg['telegram'] = {**(cfg.get('telegram') or {}), **telegram}
    if name:
        cfg['agent'] = {**(cfg.get('agent') or {}), 'name': name}
    return cfg


def channel_ready(facts: dict, expected: str) -> bool:
    """A live channel of the expected version under a fresh server supervisor."""
    state, supervisor = facts.get('/api/state') or {}, facts.get('/api/supervisor') or {}
    if (state.get('desk') or {}).get('version') != expected:
        return False
    if supervisor.get('kind') != 'serverboot' or supervisor.get('alive') is not True:
        return False
    children = {r.get('id'): r for r in supervisor.get('children', []) if isinstance(r, dict)}
    return (children.get('channel') or {}).get('alive') is True


def check_channel(client, directory: str, port: int, expected: str, *, wait: float = 90.0) -> dict:
    deadline = time.monotonic() + wait
    facts = {}
    while True:
        code, out, _ = st.execute(client, 'python3 -c ' + shlex.quote(CHANNEL_CHECK) + ' '
                                  + shlex.quote(directory) + ' ' + str(int(port)), timeout=60, check=False)
        if code == 0:
            try:
                facts = json.loads(out)
            except ValueError:
                facts = {}
            if channel_ready(facts, expected):
                return facts
        if time.monotonic() >= deadline:
            raise InstallError('Установка завершилась, но канал агента на сервере ещё не подтвердил свою версию. '
                               'Ничего не удалено; повтори проверку через минуту')
        time.sleep(3)


def _upload_kit(client, program: Path, stage: str, arch: str):
    with tempfile.TemporaryDirectory(prefix='helene-server-kit-') as temporary:
        kit = Path(temporary) / 'program.tar.gz'
        st.make_program(program, kit, arch=arch)
        st.execute(client, f'umask 077; mkdir -p -m 700 {shlex.quote(stage)}; test ! -L {shlex.quote(stage)}')
        with client.open_sftp() as sftp:
            sftp.put(str(kit), stage + '/program.tar.gz')
        reported = st.execute(client, 'sha256sum ' + shlex.quote(stage + '/program.tar.gz'))[1].split()[0]
        if reported != st.digest(kit):
            raise InstallError('Комплект программы при передаче повредился. Установка не начата; повтори')
    st.execute(client, f'rm -rf {shlex.quote(stage)}/program && mkdir -p {shlex.quote(stage)}/program && '
                       f'tar -xzf {shlex.quote(stage)}/program.tar.gz -C {shlex.quote(stage)}/program')


def _install_script(client, stage: str, target: dict, *, timeout: int):
    environment = {'HELENE_DIR': target['directory'], 'HELENE_PROJECT': target['project'],
                   'HELENE_CONTAINER': target['container'], 'HELENE_PORT': str(target['port'])}
    command = (f'cd {shlex.quote(stage)}/program && '
               + ' '.join(k + '=' + shlex.quote(v) for k, v in environment.items()) + ' sh server/install.sh')
    code, out, err = st.execute(client, command, timeout=timeout, check=False)
    if code:
        # install.sh speaks Russian to a person; its last lines are the reason.
        tail = '\n'.join((err or out or '').strip().splitlines()[-6:])
        raise InstallError('Установка на сервере остановилась: ' + (tail or f'код {code}'))
    return out


def install_new(raw: dict, config_path: Path, program: Path) -> dict:
    """A new, empty server agent. Its first-run screen opens in the window afterwards."""
    program = Path(program)
    telegram = telegram_fields(raw)
    name = str(raw.get('agent_name') or '').strip()[:100]
    target = destination(raw); directory = target['directory']
    checked = st.probe(raw)
    if checked.get('trust_needed'):
        return checked
    if checked.get('arch') not in st.SERVER_ARCHES:
        raise InstallError('Сервер должен быть Linux x64 или ARM64')
    expected = kit_version(program)
    row = saved(config_path)
    resumable = (bool(row) and row.get('phase') != 'ready' and row.get('fingerprint') == checked['fingerprint']
                 and row.get('destination') == list(st.parameters(raw)) and row.get('deployment') == target)
    if not resumable:
        if checked.get('existing'):
            raise InstallError('На сервере уже стоит Hélène. Подключи окно кнопкой «Сервер уже работает» '
                               'или обнови её кнопкой «Обновить сервер»; установка не тронута')
        if not checked.get('ok'):
            raise InstallError(checked.get('message') or 'Сервер не готов к установке')
        row = {'id': secrets.token_hex(12), 'destination': list(st.parameters(raw)), 'deployment': target,
               'fingerprint': checked['fingerprint'], 'version': expected, 'arch': checked['arch'],
               'phase': 'prepared', 'kind': 'fresh'}
        save(config_path, row)
    elif row.get('version') != expected:
        raise InstallError('Установка начата другой версией приложения. Обнови её кнопкой «Обновить сервер»')
    if not checked.get('docker') and not raw.get('install_docker'):
        raise InstallError('На сервере нет Docker. Разреши приложению подготовить его и повтори')
    stage = directory + '.install-' + row['id']
    marker = directory + '/.window-install.json'
    with contextlib.closing(st.connect({**raw, 'fingerprint': checked['fingerprint']})) as client:
        if not checked.get('docker'):
            st.install_docker(client)
        _upload_kit(client, program, stage, checked['arch'])
        if row['phase'] == 'prepared':
            st.execute(client, f'umask 077; test ! -L {shlex.quote(directory)}; mkdir -p -m 700 {shlex.quote(directory)}; '
                               f'test -z "$(ls -A {shlex.quote(directory)})"')
            with client.open_sftp() as sftp:
                with sftp.file(marker, 'wx') as handle:
                    handle.write(json.dumps({'id': row['id'], 'kind': 'fresh'}))
                with sftp.file(directory + '/helene.json', 'wx') as handle:
                    handle.write(json.dumps(server_config(program, telegram, name), ensure_ascii=False, indent=2) + '\n')
            # The bot token lives in helene.json: owner-only, whatever the sftp-server umask is.
            st.execute(client, 'chmod 600 ' + shlex.quote(marker) + ' ' + shlex.quote(directory + '/helene.json'))
            row['phase'] = 'installing'; save(config_path, row)
        else:
            code, value, _ = st.execute(client, 'cat ' + shlex.quote(marker), check=False)
            try:
                own = code == 0 and json.loads(value).get('id') == row['id']
            except ValueError:
                own = False
            if not own:
                raise InstallError('Папка установки на сервере изменилась после начала. Ничего не тронуто; '
                                   'выбери отдельную папку')
        _install_script(client, stage, target, timeout=3600)
        installed = json.loads(st.execute(client, 'cat ' + shlex.quote(directory + '/helene-build.json'))[1]).get('version')
        if installed != expected:
            raise InstallError(f'На сервере оказалась версия {installed}, ожидалась {expected}. Проверь сервер')
        check_channel(client, directory, target['port'], expected)
        key = st.execute(client, 'cat ' + shlex.quote(directory + '/data/.serverboot/desk-token'))[1].strip()
        st.execute(client, 'rm -rf ' + shlex.quote(stage), check=False)
    row['phase'] = 'installed'; save(config_path, row)
    return {'ok': True, 'key': key, 'version': installed, 'telegram': telegram, 'name': name,
            'arch': checked['arch'], 'id': row['id']}


def locate(key: str, telegram: dict, local_config: dict, *, wait: float = 120.0) -> str:
    """The new agent's bot menu leads to its channel; the address proves it knows the key."""
    proxy = (local_config.get('telegram') or {}).get('proxy')
    config = {'key': key, 'telegram': {**telegram, **({'proxy': proxy} if proxy else {})}}
    deadline = time.monotonic() + wait
    last = ''
    while time.monotonic() < deadline:
        try:
            return remote_locator.locate(config)
        except Exception as error:
            last = str(error)
            time.sleep(4)
    raise InstallError('Агент на сервере установлен и работает, но его бот ещё не сообщил адрес. '
                       + (last + '. ' if last else '') + 'Проверь, что токен бота верный, и повтори подключение')


def update(raw: dict, program: Path) -> dict:
    """Bring an installed server to this application's version through its own updater."""
    program = Path(program)
    target = destination(raw); directory = target['directory']
    checked = st.probe(raw)
    if checked.get('trust_needed'):
        return checked
    if not checked.get('root'):
        raise InstallError('Для обновления нужен вход администратора сервера (root)')
    if not checked.get('existing'):
        raise InstallError('В этой папке на сервере Hélène не найдена. Проверь папку установки в параметрах')
    if checked.get('arch') not in st.SERVER_ARCHES:
        raise InstallError('Сервер должен быть Linux x64 или ARM64')
    expected = kit_version(program)
    stage = directory + '.update-' + secrets.token_hex(8)
    with contextlib.closing(st.connect({**raw, 'fingerprint': checked['fingerprint']})) as client:
        before = json.loads(st.execute(client, 'cat ' + shlex.quote(directory + '/helene-build.json'))[1]).get('version')
        _upload_kit(client, program, stage, checked['arch'])
        try:
            _install_script(client, stage, target, timeout=5400)
        finally:
            st.execute(client, 'rm -rf ' + shlex.quote(stage), check=False)
        after = json.loads(st.execute(client, 'cat ' + shlex.quote(directory + '/helene-build.json'))[1]).get('version')
        updater = st.execute(client, 'docker exec ' + shlex.quote(target['container'] + '-updater') + ' python3 '
                             + shlex.quote(directory + '/server/updater/updater.py') + ' status', check=False)[0] == 0
    if after != expected and before == after:
        message = (f'На сервере стоит {after}, новее этой версии приложения ({expected}); её не трогала. '
                   'Исполнитель обновлений поднят.' if updater else f'На сервере стоит {after}.')
    elif after == expected and before == expected:
        message = f'Сервер уже на {after}. Исполнитель обновлений ' + ('поднят: дальше кнопка «Обновить» в окне.' if updater else 'не отвечает.')
    else:
        message = f'Сервер обновлён: {before} → {after}.' + (' Дальше обновления — кнопкой «Обновить» в окне.' if updater else '')
    return {'ok': updater and after in (expected, before), 'before': before, 'after': after,
            'updater': updater, 'message': message, 'arch': checked['arch']}
