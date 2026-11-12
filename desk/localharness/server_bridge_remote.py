"""Small stdlib-only SSH-side bridge installer. Inspection performs no writes.

Invoked with an explicit JSON request on stdin, not shell-interpolated inputs.
Only owned operation paths and an acknowledged Caddy import may be changed.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

FORMAT='helene-server-bridge-plan.v1'


def digest(value):return hashlib.sha256(value).hexdigest()


def command(argv,*,timeout=30,check=True):
    result=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          timeout=timeout,env={**os.environ,'LC_ALL':'C','DEBIAN_FRONTEND':'noninteractive'})
    if len(result.stdout)>2*1024*1024:raise ValueError('Ответ серверной команды слишком большой.')
    if check and result.returncode:raise ValueError('Серверная команда не выполнена: '+Path(argv[0]).name+'. Подробности доступны в журнале сервера.')
    return result.returncode,result.stdout.decode('utf-8','replace')


def specification(raw):
    identity=str(raw.get('id') or '')
    if not re.fullmatch('[a-f0-9]{32}',identity):raise ValueError('Неверный номер операции сервера.')
    proxy=raw.get('proxy') is True;https=raw.get('https') is True
    if not proxy and not https:raise ValueError('Выбери прокси, постоянный HTTPS или оба подключения.')
    domain=str(raw.get('domain') or '').strip().lower().rstrip('.')
    if https and (len(domain)>253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain)):
        raise ValueError('Для сертификата нужен домен, например helene.example.com, без https:// и пути.')
    selected={}
    for name,default in (('remote_proxy_port',8888),('remote_app_port',18766)):
        value=raw.get(name,default)
        if isinstance(value,bool) or not str(value).isdigit():raise ValueError('Неверный порт сервера.')
        try:value=int(value)
        except (ValueError,TypeError):raise ValueError('Неверный порт сервера.') from None
        if not 1024<=value<=65535:raise ValueError('Для моста выбери серверные порты 1024–65535.')
        selected[name]=value
    if proxy and https and selected['remote_proxy_port']==selected['remote_app_port']:raise ValueError('Прокси и HTTPS-мосту нужны разные серверные порты.')
    return {'id':identity,'proxy':proxy,'https':https,'domain':domain if https else '',**selected}


def _bytes(path,limit=2*1024*1024,*,metadata=False):
    if path.is_symlink() and not metadata:raise ValueError('Настройка сервера находится по символической ссылке; требуется ручная проверка.')
    try:
        with path.open('rb') as stream:value=stream.read(limit+1)
    except FileNotFoundError:return b''
    if len(value)>limit:raise ValueError('Настройка сервера слишком большая для автоматического изменения.')
    return value


def inspect(raw):
    spec=specification(raw);identity=spec['id']
    if sys.platform!='linux':raise ValueError('Автоматическая настройка моста поддерживает сервер Linux.')
    # /etc/os-release normally links to /usr/lib/os-release on Debian/Ubuntu.
    # It is read-only metadata; mutable configs still reject links.
    os_info=_bytes(Path('/etc/os-release'),metadata=True).decode('utf-8','replace')
    supported=bool(re.search(r'^ID=(?:"?debian"?|"?ubuntu"?)$',os_info,re.M))
    if os.geteuid()!=0:raise ValueError('Для автоматической установки нужен администратор сервера. Уже настроенный мост можно подключить обычным SSH-пользователем.')
    if not supported or not Path('/run/systemd/system').is_dir():
        raise ValueError('Для автоматической установки нужен Ubuntu/Debian с systemd. Для другого сервера доступна самостоятельная настройка.')
    _,listeners=command(['ss','-ltnH'])
    occupied=[]
    for row in listeners.splitlines():
        columns=row.split()
        if len(columns)<4:continue
        match=re.search(r':(\d+)$',columns[3])
        if match:occupied.append(int(match[1]))
    caddy=bool(shutil.which('caddy'));tinyproxy=bool(shutil.which('tinyproxy'))
    sshd=shutil.which('sshd') or '/usr/sbin/sshd'
    command([sshd,'-t'])
    ssh_service=next((unit for unit in ('ssh','sshd') if command(['systemctl','is-active',unit],check=False)[0]==0),'')
    if not ssh_service:raise ValueError('SSH не управляется службой systemd. Используй самостоятельную настройку моста.')
    ssh_config=_bytes(Path('/etc/ssh/sshd_config'))
    caddy_file=Path('/etc/caddy/Caddyfile');before=_bytes(caddy_file)
    caddy_active=command(['systemctl','is-active','caddy'],check=False)[0]==0
    effective_hash='';names=[]
    if spec['https'] and caddy_active:
        _,start=command(['systemctl','show','caddy','--property=ExecStart','--value'])
        if '/etc/caddy/Caddyfile' not in start or '--resume' in start:
            raise ValueError('Работающий Caddy использует другую конфигурацию. Подключи уже настроенный HTTPS или настрой отдельный сервер.')
        _,effective=command(['caddy','adapt','--config',str(caddy_file),'--adapter','caddyfile'])
        effective_hash=digest(effective.encode())
        def hosts(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key=='host' and isinstance(item,list):names.extend(v for v in item if isinstance(v,str))
                    else:hosts(item)
            elif isinstance(value,list):
                for item in value:hosts(item)
        hosts(json.loads(effective))
        if any(name==spec['domain'] or name.startswith('*.') and spec['domain'].endswith(name[1:]) for name in names):
            raise ValueError('Этот домен уже обслуживается Caddy. Выбери отдельное имя; работающий сайт не изменён.')
    if spec['https'] and ({80,443}&set(occupied)) and not caddy_active:
        raise ValueError('Порты 80 или 443 заняты другой службой. Подключи её готовый HTTPS или выбери отдельный сервер; служба не изменена.')
    if spec['proxy'] and spec['remote_proxy_port'] in occupied:raise ValueError('Порт прокси уже занят. Подключи существующий tinyproxy либо выбери другой порт.')
    if spec['https'] and spec['remote_app_port'] in occupied:raise ValueError('Порт обратного туннеля занят. Выбери другой порт моста.')
    user='hb'+identity[:12]
    if command(['getent','passwd',user],check=False)[0]==0:raise ValueError('Имя пользователя моста уже занято. Начни новую операцию; существующая учётная запись не изменена.')
    for path in (Path('/var/lib/helene-bridge')/identity,Path('/etc/helene-bridge')/identity,
                 Path('/etc/caddy/helene-sites')/(identity+'.caddy'),Path('/etc/systemd/system')/('helene-proxy-'+identity+'.service')):
        if path.exists() or path.is_symlink():raise ValueError('Папка операции уже существует. Продолжи сохранённую операцию вместо новой установки.')
    addresses=[]
    if spec['https']:
        try:addresses=sorted({row[4][0] for row in socket.getaddrinfo(spec['domain'],443,type=socket.SOCK_STREAM)})
        except socket.gaierror:raise ValueError('Домен ещё не находится через DNS. Создай запись A/AAAA на адрес этого сервера и повтори.') from None
        expected=set(str(ip) for ip in raw.get('server_addresses',[]))
        if not expected or not set(addresses)&expected:
            raise ValueError('DNS домена пока не указывает на адрес SSH-сервера. Проверь запись A/AAAA в панели домена.')
    return {'specification':spec,'tinyproxy':tinyproxy,'caddy':caddy,'caddy_active':caddy_active,
            'caddy_file_exists':caddy_file.exists(),'caddy_file_sha256':digest(before),
            'caddy_effective_sha256':effective_hash,'dns':addresses,
            'ports':sorted(set(occupied)),'user':user,'sshd':sshd,'ssh_service':ssh_service,
            'sshd_config_sha256':digest(ssh_config)}


def plan(raw):
    state=inspect(raw);spec=state['specification'];identity=spec['id']
    changes=[{'kind':'account','message':f'Создать отдельного пользователя {state["user"]} и ключ только для нужных пробросов; доступ к оболочке отключён.'},
             {'kind':'ssh','message':'Сохранить прежний sshd_config и добавить правила только для пользователя моста: разрешённые адреса и направления проброса, без пароля, оболочки и передачи SSH-агента. Проверить конфигурацию и перечитать службу SSH без остановки текущих соединений.'}]
    if spec['proxy']:
        changes.append({'kind':'proxy','message':('Использовать установленный tinyproxy' if state['tinyproxy'] else 'Установить tinyproxy-bin из репозитория системы')+
            f'; создать отдельную службу helene-proxy-{identity}, слушающую только 127.0.0.1:{spec["remote_proxy_port"]}. Разрешить HTTPS CONNECT к портам 443 и 7844.'})
    if spec['https']:
        changes.append({'kind':'https','message':('Добавить отдельный сайт в работающий Caddy' if state['caddy_active'] else 'Настроить Caddy')+
            f' для {spec["domain"]} → 127.0.0.1:{spec["remote_app_port"]}. Сохранить прежний Caddyfile, добавить import отдельного файла и проверить перед перезагрузкой.'})
        changes.append({'kind':'certificate','message':'Caddy выпустит и будет автоматически продлевать сертификат. DNS должен оставаться правильным, порты 80/443 — доступными извне.'})
    value={'format':FORMAT,'id':identity,'inventory':state,'changes':changes}
    value['sha256']=digest(json.dumps(value,sort_keys=True,separators=(',',':')).encode())
    return value


def safe_directory(path,mode=0o700):
    for parent in reversed((path,*path.parents)):
        if parent.is_symlink():raise ValueError('Путь настройки проходит через символическую ссылку; установка остановлена.')
    path.mkdir(parents=True,exist_ok=True,mode=mode)


def atomic(path,data,*,mode=0o600,validate=None):
    safe_directory(path.parent)
    if path.is_symlink():raise ValueError('Настройка заменена ссылкой; установка остановлена.')
    descriptor,name=tempfile.mkstemp(prefix='.helene-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            os.fchmod(stream.fileno(),mode);stream.write(data);stream.flush();os.fsync(stream.fileno())
        if validate:validate(temporary)
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:temporary.unlink(missing_ok=True)


def owned_file(path,data,*,mode=0o644):
    if path.exists():
        if _bytes(path)!=data:raise ValueError('Файл моста изменён отдельно: '+str(path)+'. Прежний файл сохранён.')
        return
    atomic(path,data,mode=mode)


def append_config(path,expected,addition,backup,validate):
    """CAS against retained original or our exact result; safe after lost ack."""
    if not backup.exists():
        before=_bytes(path)
        if digest(before)!=expected:raise ValueError('Настройки сервера изменились после проверки. Перечитай план; ничего не перезаписано.')
        atomic(backup,before)
    before=_bytes(backup)
    if digest(before)!=expected:raise ValueError('Сохранённая копия настроек не совпадает с планом.')
    desired=before.rstrip(b'\n')+b'\n'+addition.encode()+b'\n'
    current=_bytes(path)
    if current==desired:
        validate(path)
        return
    if current!=before:raise ValueError('Настройки сервера изменены отдельно. Сохранены исходная копия и частичный результат; нужна повторная проверка.')
    atomic(path,desired,mode=0o644,validate=validate)


def validate_ssh(path,binary,username,spec):
    """Earlier Match blocks can override an appended block even when -t passes."""
    command([binary,'-t','-f',str(path)])
    expected={'allowtcpforwarding':'yes' if spec['proxy'] and spec['https'] else 'local' if spec['proxy'] else 'remote',
              'permitopen':f'127.0.0.1:{spec["remote_proxy_port"]}' if spec['proxy'] else 'none',
              'permitlisten':f'127.0.0.1:{spec["remote_app_port"]}' if spec['https'] else 'none',
              'allowagentforwarding':'no','x11forwarding':'no','permittty':'no','permittunnel':'no',
              'gatewayports':'no','passwordauthentication':'no','kbdinteractiveauthentication':'no',
              'forcecommand':'/bin/false','pubkeyauthentication':'yes','disableforwarding':'no'}
    # Check both the actual management peer and the default loopback case. The
    # authorized key independently restricts every source to exact target ports.
    peers={'127.0.0.1'}
    connection=os.environ.get('SSH_CONNECTION','').split()
    if connection:
        import ipaddress
        try:peers.add(str(ipaddress.ip_address(connection[0])))
        except ValueError:raise ValueError('Не удалось определить адрес SSH-соединения для проверки правил.') from None
    for peer in sorted(peers):
        _,output=command([binary,'-T','-f',str(path),'-C',f'user={username},host={peer},addr={peer}'])
        actual=dict(line.split(None,1) for line in output.splitlines() if len(line.split(None,1))==2)
        wrong=[key for key,value in expected.items() if actual.get(key)!=value]
        if wrong:raise ValueError('Существующие правила SSH мешают безопасному подключению моста: '+', '.join(wrong)+'. Конфигурация не перезаписана; проверь прежние Match/Include.')


def apply(request):
    """Resume the exact acknowledged plan, retaining every completed stage."""
    import base64
    import fcntl
    import pwd
    selected=request.get('plan') or {}
    identity=selected.get('id');state=selected.get('inventory') or {};spec=specification(state.get('specification') or {})
    unsigned={key:value for key,value in selected.items() if key!='sha256'}
    expected=digest(json.dumps(unsigned,sort_keys=True,separators=(',',':')).encode())
    if (selected.get('format')!=FORMAT or identity!=spec['id'] or selected.get('sha256')!=expected
            or request.get('accepted_sha256')!=expected):raise ValueError('Нужен неизменённый подтверждённый план сервера.')
    if os.geteuid()!=0:raise ValueError('Установка требует администратора сервера.')
    public=str(request.get('public_key') or '').strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,2}',public):raise ValueError('Нужен открытый ключ моста Ed25519.')
    base64.b64decode(public.split()[1],validate=True)
    shared=Path('/var/lib/helene-bridge');safe_directory(shared)
    lock_path=shared/'install.lock'
    if lock_path.is_symlink():raise ValueError('Файл блокировки заменён ссылкой.')
    with lock_path.open('a+b') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Настройка сервера уже выполняется. Дождись её результата и обнови состояние.') from None
        folder=shared/identity;record_path=folder/'operation.json'
        if record_path.exists():
            row=json.loads(_bytes(record_path))
            if row.get('sha256')!=expected or row.get('public_key')!=public:raise ValueError('На сервере сохранена другая операция. Она не изменена.')
        else:
            current=inspect({**spec,'server_addresses':state['dns']})
            if current!=state:raise ValueError('Состояние сервера изменилось после просмотра плана. Подготовь новый план.')
            safe_directory(folder)
            row={'sha256':expected,'public_key':public,'done':[]}
            atomic(record_path,json.dumps(row).encode())
        def checkpoint(stage):
            if stage not in row['done']:row['done'].append(stage)
            row.update(at=time.time(),stage=stage)
            atomic(record_path,json.dumps(row).encode())
        packages=[]
        if spec['proxy'] and not state['tinyproxy']:packages.append('tinyproxy-bin')
        if spec['https'] and not state['caddy']:packages.append('caddy')
        if 'packages' not in row['done']:
            if packages:
                command(['apt-get','update'],timeout=300)
                command(['apt-get','install','-y','--no-install-recommends',*packages],timeout=600)
            checkpoint('packages')
        username='hb'+identity[:12];home=Path('/var/lib/helene-bridge-users')/username
        if 'account' not in row['done']:
            safe_directory(home.parent,0o755)
            try:account=pwd.getpwnam(username)
            except KeyError:
                command(['useradd','--system','--create-home','--home-dir',str(home),'--shell','/usr/sbin/nologin',username])
                account=pwd.getpwnam(username)
            if account.pw_dir!=str(home) or account.pw_shell!='/usr/sbin/nologin':raise ValueError('Учётная запись моста отличается от плана; она не изменена.')
            safe_directory(home);home.chmod(0o700);os.chown(home,account.pw_uid,account.pw_gid)
            ssh=home/'.ssh';safe_directory(ssh);ssh.chmod(0o700);os.chown(ssh,account.pw_uid,account.pw_gid)
            options=['restrict','port-forwarding','command="/bin/false"']
            if spec['proxy']:options.append(f'permitopen="127.0.0.1:{spec["remote_proxy_port"]}"')
            if spec['https']:options.append(f'permitlisten="127.0.0.1:{spec["remote_app_port"]}"')
            keys=ssh/'authorized_keys';owned_file(keys,(','.join(options)+' '+public+'\n').encode(),mode=0o600)
            os.chown(keys,account.pw_uid,account.pw_gid);checkpoint('account')
        if 'ssh' not in row['done']:
            direction='yes' if spec['proxy'] and spec['https'] else 'local' if spec['proxy'] else 'remote'
            opened=f'127.0.0.1:{spec["remote_proxy_port"]}' if spec['proxy'] else 'none'
            listening=f'127.0.0.1:{spec["remote_app_port"]}' if spec['https'] else 'none'
            block=(f'# Helene bridge {identity}\nMatch User {username}\n'
                   f'    AllowTcpForwarding {direction}\n    PermitOpen {opened}\n    PermitListen {listening}\n'
                   '    AllowAgentForwarding no\n    X11Forwarding no\n    PermitTTY no\n    PermitTunnel no\n'
                   '    GatewayPorts no\n    PasswordAuthentication no\n    KbdInteractiveAuthentication no\n    ForceCommand /bin/false\n')
            append_config(Path('/etc/ssh/sshd_config'),state['sshd_config_sha256'],block,folder/'sshd.before',
                          lambda path:validate_ssh(path,state['sshd'],username,spec))
            command(['systemctl','reload',state['ssh_service']]);checkpoint('ssh')
        else:validate_ssh(Path('/etc/ssh/sshd_config'),state['sshd'],username,spec)
        if spec['proxy'] and 'proxy' not in row['done']:
            config=Path('/etc/helene-bridge')/identity/'tinyproxy.conf'
            safe_directory(config.parent,0o755)
            owned_file(config,(f'Port {spec["remote_proxy_port"]}\nListen 127.0.0.1\nTimeout 600\nMaxClients 64\n'
                'Allow 127.0.0.1\nConnectPort 443\nConnectPort 7844\nLogLevel Error\nSyslog On\nDisableViaHeader Yes\n').encode())
            unit='helene-proxy-'+identity
            binary=shutil.which('tinyproxy')
            if not binary:raise ValueError('Пакет tinyproxy установлен, но исполняемый файл не найден.')
            service=(f'[Unit]\nDescription=Helene server proxy {identity}\nAfter=network-online.target\n'
                f'[Service]\nType=simple\nUser={username}\nExecStart={binary} -d -c {config}\n'
                'Restart=on-failure\nRestartSec=3\nNoNewPrivileges=true\nPrivateTmp=true\nProtectHome=true\nProtectSystem=strict\n'
                '[Install]\nWantedBy=multi-user.target\n')
            owned_file(Path('/etc/systemd/system')/(unit+'.service'),service.encode())
            command(['systemctl','daemon-reload']);command(['systemctl','enable','--now',unit])
            command(['systemctl','is-active',unit]);checkpoint('proxy')
        if spec['https'] and 'https' not in row['done']:
            site=Path('/etc/caddy/helene-sites')/(identity+'.caddy');safe_directory(site.parent,0o755)
            owned_file(site,(spec['domain']+' {\n    reverse_proxy 127.0.0.1:'+str(spec['remote_app_port'])+'\n}\n').encode())
            before=state['caddy_file_sha256']
            if not state['caddy_file_exists']:
                # Keep the package's own default site too; do not replace it.
                baseline=folder/'caddy-package.before'
                if not baseline.exists():atomic(baseline,_bytes(Path('/etc/caddy/Caddyfile')))
                before=digest(_bytes(baseline))
            append_config(Path('/etc/caddy/Caddyfile'),before,'import '+str(site),folder/'caddy.before',
                          lambda path:command(['caddy','validate','--config',str(path),'--adapter','caddyfile']))
            command(['systemctl','enable','caddy'])
            if command(['systemctl','is-active','caddy'],check=False)[0]==0:command(['systemctl','reload','caddy'])
            else:command(['systemctl','start','caddy'])
            command(['systemctl','is-active','caddy']);command(['systemctl','is-enabled','caddy']);checkpoint('https')
        checkpoint('installed')
        return {'installed':True,'verified':False,'username':username,'stages':row['done'],
                'certificate_automation':bool(spec['https']),
                'message':'Службы настроены. Теперь нужно подключить мост и проверить выход через прокси, публичный HTTPS и сертификат из приложения.'}


def main():
    raw=sys.stdin.buffer.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Запрос настройки слишком большой.')
    request=json.loads(raw)
    if request.get('action')=='plan':return plan(request)
    if request.get('action')=='apply':return apply(request)
    raise ValueError('Неизвестный этап настройки сервера.')


if __name__=='__main__':
    try:print(json.dumps({'ok':True,'result':main()},ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok':False,'error':str(error) if isinstance(error,ValueError) else type(error).__name__},ensure_ascii=False));sys.exit(1)
