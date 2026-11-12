"""GUI-owned SSH transfer. Credentials arrive over stdin and never enter agent context."""
from __future__ import annotations
import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

# Embedded Windows Python ignores the script directory in sys.path.
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import paramiko
import boot
import carry
import remote_locator
import owner_stop
from server_destination import destination, accepted
from deskd.connection_checks import check_telegram

class TransferError(RuntimeError):pass
TARGET='/opt/helene'
TOKEN_PATH=TARGET+'/data/.serverboot/desk-token'
MARKER_PATH=TARGET+'/.window-transfer.json'

def receipt_path(config_path):
    config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    return carry.tree_of(config_path,config)/'memory/.state/server-transfer.json'

def saved_transfer(config_path):
    path=receipt_path(config_path)
    legacy=config_path.parent/('.server-transfer-'+hashlib.sha256(str(config_path.resolve()).encode()).hexdigest()[:20]+'.json')
    adjacent=config_path.with_name(config_path.name+'.server-transfer.json')
    if not path.exists() and adjacent.exists():path=adjacent
    if not path.exists() and legacy.exists():path=legacy
    try:
        row=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(row,dict) or not re.fullmatch(r'[0-9a-f]{24}',str(row.get('id',''))):raise ValueError()
        return row
    except FileNotFoundError:return {}
    except (OSError,ValueError):raise TransferError('Квитанция переноса не прочиталась. Местный запуск заблокирован; сохрани архив и проверь сервер') from None

def save_transfer(config_path,row):
    from private_files import write_private
    receipt_path(config_path).parent.mkdir(parents=True,exist_ok=True)
    write_private(receipt_path(config_path),(json.dumps(row,ensure_ascii=False)+'\n').encode())

@contextlib.contextmanager
def custody(config_path):
    # OS lock survives UI/helper races; the kernel releases it on helper death.
    path=receipt_path(config_path).with_suffix('.lock')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise TransferError('Перенос уже выполняется в другом окне') from None
        try:yield
        finally:
            if os.name=='nt':
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(lock,fcntl.LOCK_UN)

@contextlib.contextmanager
def phone_custody(config_path):
    from phone_tunnel.platform_support import acquire_file_lock,release_file_lock,PlatformSupportError
    folder=receipt_path(config_path).parent/'phone-access';folder.mkdir(parents=True,exist_ok=True)
    try:handle=acquire_file_lock(folder/'runtime.lock',timeout_sec=1)
    except PlatformSupportError:raise TransferError('Прежний телефонный канал ещё работает. Дождись Stop и повтори перенос') from None
    try:yield
    finally:release_file_lock(handle)

def same_destination(row,raw):
    return bool(row.get('id')) and row.get('destination')==list(parameters(raw)) and row.get('fingerprint')==raw.get('fingerprint') and row.get('deployment',destination({}))==destination(raw)

def status(config_path):
    row=saved_transfer(config_path)
    return {'ok':True,'pending':bool(row),'phase':row.get('phase'),'host':(row.get('destination') or [''])[0],
            'archive':row.get('archive'),'message':('Серверная копия остановлена. Местный Stop сохраняется до отдельной команды владельца.' if row.get('phase')=='server-stopped' else 'Предыдущий перенос сохранён. Укажи тот же сервер и нажми «Продолжить перенос»; память повторно импортирована не будет.') if row else '',
            **({'deployment':row.get('deployment',destination({})),'ssh_port':row['destination'][1],'login':row['destination'][2]} if row else {})}

def fingerprint(key):return 'SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
def parameters(raw):
    host=str(raw.get('host') or '').strip().strip('[]')
    login=str(raw.get('login') or 'root').strip()
    try:port=int(raw.get('port') or 22)
    except (ValueError,TypeError):raise TransferError('Порт SSH должен быть числом')
    if not host or len(host)>253 or not re.fullmatch(r'[A-Za-z0-9.:-]+',host) or host.startswith('-') or not 1<=port<=65535:
        raise TransferError('Укажи адрес сервера и порт SSH из его панели')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*',login):raise TransferError('Проверь логин сервера')
    return host,port,login

class Pin(paramiko.MissingHostKeyPolicy):
    def __init__(self,expected):self.expected=expected
    def missing_host_key(self,client,hostname,key):
        if fingerprint(key)!=self.expected:raise TransferError('Ключ сервера изменился. Сначала проверь подключение заново')

def connect(raw,*,on_client=None):
    from localharness.server_connections import authentication
    host,port,login=parameters(raw)
    expected=str(raw.get('fingerprint') or '')
    client=paramiko.SSHClient()
    if expected:client.set_missing_host_key_policy(Pin(expected))
    else:
        with contextlib.suppress(OSError):client.load_system_host_keys(str(Path.home()/'.ssh/known_hosts'))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:auth=authentication(raw)
    except ValueError as error:
        client.close();raise TransferError(str(error)) from None
    try:
        if on_client:on_client(client)
        def transport_factory(sock,**options):
            class Pending:
                def close(self):
                    # SSHClient has not assigned _transport yet. Closing only
                    # that client here loses cancellation before the banner.
                    with contextlib.suppress(OSError):sock.shutdown(socket.SHUT_RDWR)
                    sock.close();client.close()
            pending=Pending()
            try:
                if on_client:on_client(pending)
                return paramiko.Transport(sock,**options)
            except BaseException:
                pending.close();raise
        client.connect(host,port=port,username=login,**auth,
                       timeout=15,auth_timeout=20,banner_timeout=15,allow_agent=False,look_for_keys=False,
                       **({'transport_factory':transport_factory} if on_client else {}))
        if on_client:on_client(client)
        client.get_transport().set_keepalive(15)
        return client
    except TransferError:
        client.close();raise
    except Exception:
        client.close();raise TransferError('Войти на сервер не удалось. Проверь адрес, логин, пароль или SSH-ключ') from None

def execute(client,command,timeout=30,check=True,input_data=None):
    stdin,out,_=client.exec_command(command,timeout=timeout)
    if input_data is not None:
        stdin.write(input_data);stdin.flush();stdin.channel.shutdown_write()
    channel=out.channel;values=[];errors=[];total=0;deadline=time.monotonic()+timeout
    while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
        if time.monotonic()>deadline:
            channel.close();raise TransferError('Сервер не закончил этап переноса вовремя. Проверь его перед повтором; местные данные сохранены')
        if channel.recv_ready():
            packet=channel.recv(65536);values.append(packet);total+=len(packet)
            if total>4*1024*1024:values=values[-4:];total=sum(map(len,values))
        if channel.recv_stderr_ready():
            errors.append(channel.recv_stderr(65536));errors=errors[-4:]
        time.sleep(.02)
    value=b''.join(values).decode('utf-8','replace');error=b''.join(errors).decode('utf-8','replace')
    code=channel.recv_exit_status()
    if check and code:raise TransferError('Сервер не выполнил этап переноса. Данные оставлены на месте; проверь подключение и повтори')
    return code,value,error

def probe(raw,config_path=None):
    host,port,login=parameters(raw)
    target=destination(raw);directory=shlex.quote(target['directory']);container=shlex.quote(target['container'])
    transport=None
    try:
        transport=paramiko.Transport((host,port))
        transport.start_client(timeout=15);key=transport.get_remote_server_key();seen=fingerprint(key)
    except Exception:raise TransferError('Сервер не отвечает по SSH. Проверь адрес и порт') from None
    finally:
        if transport:transport.close()
    if raw.get('fingerprint') and raw['fingerprint']!=seen:
        raise TransferError('Отпечаток SSH-сервера изменился. Проверь сервер; прежнее подключение не использовано')
    trusted=False
    known=paramiko.HostKeys()
    with contextlib.suppress(OSError):known.load(str(Path.home()/'.ssh/known_hosts'))
    name=host if port==22 else f'[{host}]:{port}'
    if known.lookup(name):trusted=known.check(name,key)
    if not trusted and (raw.get('confirmed_host') is not True or raw.get('fingerprint')!=seen):
        return {'ok':False,'trust_needed':True,'fingerprint':seen,'message':'Сверь отпечаток с панелью сервера и подтверди, что это твой сервер. Пароль ещё не отправлен.'}
    with contextlib.closing(connect({**raw,'fingerprint':seen})) as client:
        root=execute(client,'id -u')[1].strip()=='0'
        if root and execute(client,'command -v python3 >/dev/null && command -v flock >/dev/null',check=False)[0]:
            raise TransferError('Для восстановления установки на сервере нужны python3 и flock (util-linux)')
        if execute(client,f'test "$(realpath -m {directory})" = {directory}',check=False)[0]:
            raise TransferError('Папка установки проходит через ссылку. Выбери отдельный канонический путь')
        os_info=execute(client,'cat /etc/os-release')[1]
        arch=execute(client,'uname -m')[1].strip()
        docker=execute(client,'docker info --format "{{.ServerVersion}}"',check=False)[0]==0
        compose=docker and execute(client,'docker compose version --short',check=False)[0]==0
        exists=execute(client,f'test -e {directory}/helene.json',check=False)[0]==0
        nonempty=execute(client,f'test -d {directory} && test -n "$(ls -A {directory})"',check=False)[0]==0
        # 11.11: ARM64 servers (aarch64) are a supported target, like x64.
        supported=bool(re.search(r'(?m)^ID=(?:"?ubuntu"?|"?debian"?)$',os_info)) and arch in SERVER_ARCHES
        occupied=execute(client,'docker inspect '+container,check=False)[0]==0
        resumable=False
        if config_path:
            row=saved_transfer(config_path)
            if same_destination(row,{**raw,'fingerprint':seen}):
                code,value,_=execute(client,'cat '+directory+'/.window-transfer.json',check=False)
                with contextlib.suppress(ValueError):resumable=code==0 and json.loads(value).get('id')==row['id'] and json.loads(value).get('archive_sha')==row['archive_sha']
        suffix=format(target['port'],'04X')
        port_busy=execute(client,"if command -v ss >/dev/null 2>&1; then ss -ltnH 'sport = :"+str(target['port'])+"'; else test -r /proc/net/tcp && awk 'NR>1 && $2 ~ /:"+suffix+"$/ && $4==\"0A\" {print}' /proc/net/tcp /proc/net/tcp6; fi",check=False)
        if port_busy[0]!=0:raise TransferError('Не удалось проверить занятость порта: на сервере нужен ss (iproute2)')
        port_busy=bool(port_busy[1].strip())

    okay=root and arch in SERVER_ARCHES and (supported or docker and compose)
    message='Сервер готов к переносу.' if okay else 'Для автоматической установки нужен администратор сервера Ubuntu/Debian x64 или ARM64.'
    if (nonempty or occupied or port_busy) and not resumable:
        okay=False;message='Папка, контейнер или порт уже заняты. Укажи отдельные параметры установки; существующие данные не изменены.'
    if port_busy and not resumable and not exists:
        okay=False;message='Порт Hélène на этом сервере занят другой программой. Выбери другой сервер или освободи выбранный порт в его панели.'
    if occupied and not exists and not resumable:
        okay=False;message='Имя контейнера Hélène уже занято другой установкой. Она не изменена; выбери другой сервер.'
    return {'ok':okay,'fingerprint':seen,'trusted':trusted,'root':root,'docker':docker and compose,
            'supported':supported,'existing':exists,'resumable':resumable,'message':message,'arch':arch,
            'occupied':bool(nonempty or occupied or port_busy)}

def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as data:
        for block in iter(lambda:data.read(1024*1024),b''):value.update(block)
    return value.hexdigest()

SERVER_ARCHES=('x86_64','aarch64')


def make_program(root:Path,path:Path,*,arch:str='x86_64'):
    allowed=('app','tree','server','requirements.txt','helene-build.json')
    if any(not (root/name).exists() for name in allowed):raise TransferError('В поставке нет полного комплекта для сервера. Скачай полную Windows-поставку Hélène')
    def include(info):
        if info.issym() or info.islnk() or any(part in ('.git','__pycache__','node_modules','target') or part=='.env' or part.startswith('.env.') for part in Path(info.name).parts):return None
        return info
    with tarfile.open(path,'w:gz') as target:
        for name in allowed:target.add(root/name,arcname=name,filter=include)
        legacy=root/'helene-relay'
        # The legacy relay binary in a desktop delivery is x86-64 Linux. An ARM64 server
        # gets no foreign binary: the server image builds natively; only the ChatGPT
        # subscription relay needs a native ARM build (server-linux-arm64 delivery).
        if arch=='x86_64' and legacy.is_file() and legacy.read_bytes()[:4]==b'\x7fELF':target.add(legacy,arcname='helene-relay',filter=include)

def kit_signature(path):
    rows=[]
    with tarfile.open(path) as kit:
        for info in kit:
            if info.isfile():
                with kit.extractfile(info) as stream:
                    rows.append((info.name,info.mode,hashlib.file_digest(stream,'sha256').hexdigest()))
    return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()

def install_docker(client):
    # Official apt repository. Never uninstall/replace an existing Docker installation.
    script='''set -eu
if command -v docker >/dev/null 2>&1; then exit 30; fi
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) exit 31;; esac
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$ID" "${UBUNTU_CODENAME:-$VERSION_CODENAME}" "$(dpkg --print-architecture)" >/etc/apt/sources.list.d/docker.sources
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl start docker
'''
    execute(client,'sh -c '+shlex.quote(script),timeout=1200)

def require_cold(config_path,config):
    if not owner_stop.stopped():raise TransferError('Сначала включи Stop в приложении; перенос не возобновляет агента')
    tree=carry.tree_of(config_path,config)
    holder=boot._read_lock(boot.tree_lock_path(tree))
    if boot._lock_is_live(holder):raise TransferError('Местный агент ещё работает. Сначала останови его в окне и повтори перенос')

@contextlib.contextmanager
def snapshot_custody(config_path,config):
    """Hold the runner's own tree lease until the durable VPS fence is saved."""
    tree=carry.tree_of(config_path,config);path=boot.tree_lock_path(tree)
    busy=boot.claim_tree(tree,role='server-transfer')
    if busy:raise TransferError('Память занята другим процессом; архив переноса не начат')
    token=boot._LOCK.get('token')
    try:
        holder=boot._read_lock(path)
        if not token or boot._LOCK.get('path')!=path or holder.get('token')!=token or holder.get('pid')!=os.getpid():
            raise TransferError('Не подтверждено владение памятью; архив переноса не начат')
        # Reuse the process-generation contract of explicit first-create. A
        # large archive must not outlive a TTL and look like an abandoned lock.
        import psutil
        from private_files import write_private
        holder['birth']=psutil.Process(os.getpid()).create_time()
        write_private(path,(json.dumps(holder,ensure_ascii=False)+'\n').encode())
        yield
    finally:
        if token and boot._LOCK.get('path')==path and boot._LOCK.get('token')==token:boot.release_tree()

def run(raw,config_path:Path,program:Path):
    with custody(config_path),phone_custody(config_path):return _run(raw,config_path,program)

def _run(raw,config_path:Path,program:Path):
    target=destination(raw);directory=target['directory']
    marker_path=directory+'/.window-transfer.json';token_path=directory+'/data/.serverboot/desk-token'
    config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config.get('mode')=='remote':raise TransferError('Этот агент уже на сервере. Подключи окно к нему или выполняй перенос с его компьютера')
    require_cold(config_path,config)
    tg=config.get('telegram') or {}
    if not tg.get('bot_token') or not str(tg.get('owner_id') or '').isdigit():
        raise TransferError('Сначала подключи своего Telegram-бота и ID владельца: его кнопка поможет окну находить сервер после перезапуска')
    checked=probe(raw,config_path)
    if not checked.get('ok') or checked.get('trust_needed'):raise TransferError(checked['message'])
    if checked.get('existing') and not checked.get('resumable'):raise TransferError('На сервере уже есть Hélène. Подключись к ней кнопкой «Сервер уже работает»; существующий агент не заменён')
    if not checked.get('docker') and not raw.get('install_docker'):raise TransferError('Для переноса требуется подготовить Docker. Разреши приложению сделать это в шаге подключения')
    expected=json.loads((program/'helene-build.json').read_text(encoding='utf-8-sig')).get('version')
    if checked.get('arch')!='x86_64' and ((config.get('relay') or {}).get('enabled') or str((config.get('model') or {}).get('framework') or '')=='chatgpt'):
        raise TransferError('Агент работает через подписку ChatGPT, а её реле в этой поставке собрано только для x64. '
                            'На ARM-сервер его можно перенести с ключом API или выбрать x64-сервер; местная память не тронута')
    row=saved_transfer(config_path)
    if row.get('phase')=='server-stopped':
        if same_destination(row,{**raw,'fingerprint':checked['fingerprint']}):
            raise TransferError('Этот перенос закрыт для возврата. Для нового переноса выбери отдельную папку/проект; прежний архив и серверные данные сохраняются')
        from private_files import write_private
        history=receipt_path(config_path).parent/'server-transfer-history';history.mkdir(exist_ok=True)
        write_private(history/(row['id']+'.json'),(json.dumps(row,ensure_ascii=False)+'\n').encode())
        row={}
    continuing=same_destination(row,{**raw,'fingerprint':checked['fingerprint']})
    if row and not continuing:raise TransferError('Есть незавершённый перенос на другой сервер. Сначала продолжи его: так не появится второй работающий агент')
    if continuing:
        if row.get('version')!=expected:raise TransferError('Перенос начат другой версией приложения. Подключись к уже перенесённому серверу; его данные не заменены')
        archive=Path(row['archive'])
        if not archive.is_file() or digest(archive)!=row.get('archive_sha'):raise TransferError('Сохранённый архив переноса не найден или изменён. Сервер и местная память оставлены на месте')
    else:
        require_cold(config_path,config)
        with snapshot_custody(config_path,config):
            archive_root=receipt_path(config_path).parent/'server-transfer-archives'/secrets.token_hex(12)
            archive_root.mkdir(parents=True,mode=0o700)
            archive=Path(carry.export(config_path,archive_root/('agent-'+secrets.token_hex(12)+'.zip'),preserve_phone_lease=True)['archive'])
            row={'id':secrets.token_hex(12),'destination':list(parameters(raw)),'fingerprint':checked['fingerprint'],
                 'archive':str(archive),'archive_sha':digest(archive),'version':expected,'phase':'archive','deployment':target}
            save_transfer(config_path,row)
    stage=directory+'.transfer-'+row['id']
    with tempfile.TemporaryDirectory(prefix='helene-server-kit-') as temporary:
        kit=Path(temporary)/'program.tar.gz';make_program(program,kit,arch=checked.get('arch') or 'x86_64')
        signature=kit_signature(kit)
        if row.get('program_sha') and row['program_sha']!=signature:
            raise TransferError('Исходники программы изменились после начала переноса. Сохранённая серверная установка не заменена')
        if not row.get('program_sha'):
            row['program_sha']=signature;save_transfer(config_path,row)
        with contextlib.closing(connect({**raw,'fingerprint':checked['fingerprint']})) as client:
            if not checked['docker']:install_docker(client)
            execute(client,f'umask 077; mkdir -p -m 700 {shlex.quote(stage)}; test ! -L {shlex.quote(stage)}')
            with client.open_sftp() as sftp:
                sftp.put(str(kit),stage+'/program.tar.gz');sftp.put(str(archive),stage+'/agent.zip')
            for local,remote in ((kit,stage+'/program.tar.gz'),(archive,stage+'/agent.zip')):
                reported=execute(client,'sha256sum '+shlex.quote(remote))[1].split()[0]
                if reported!=digest(local):raise TransferError('Архив при передаче повредился. Установка не начата; повтори перенос')
            execute(client,f'mkdir -p {stage}/program && tar -xzf {stage}/program.tar.gz -C {stage}/program')
            # Existing installations and unrelated containers are not touched by this path.
            if not checked.get('resumable'):
                execute(client,f'umask 077; test ! -L {directory}; mkdir -p -m 700 {directory}; test -z "$(ls -A {directory})"')
                with client.open_sftp() as sftp:
                    with sftp.file(marker_path,'wx') as marker:marker.write(json.dumps({'id':row['id'],'archive_sha':row['archive_sha']}))
                    sftp.chmod(marker_path,0o600)
            row['phase']='installing';save_transfer(config_path,row)
            environment={'HELENE_DIR':directory,'HELENE_PROJECT':target['project'],'HELENE_CONTAINER':target['container'],'HELENE_PORT':str(target['port']),'HELENE_AUTOMATIC_CONNECTION':'1'}
            command=f'cd {stage}/program && '+ ' '.join(k+'='+shlex.quote(v) for k,v in environment.items())+f' sh server/install.sh {stage}/agent.zip'
            execute(client,command,timeout=2400)
            token=execute(client,f'cat {token_path}')[1].strip()
            version=json.loads(execute(client,f'cat {directory}/helene-build.json')[1]).get('version')
            row['phase']='connecting';save_transfer(config_path,row)
    if version!=expected:raise TransferError('На сервере обнаружена другая версия. Местная память сохранена; проверь сервер перед подключением')
    # Give the owner menu time to acquire its current automatic HTTPS address.
    for _ in range(30):
        try:
            base=remote_locator.locate({**config,'key':token})
            request=urllib.request.Request(base+'/api/state',headers={'Authorization':'Bearer '+token})
            state=remote_locator.read(request)
            supervisor=remote_locator.read(urllib.request.Request(base+'/api/supervisor',headers={'Authorization':'Bearer '+token}))
            panic=remote_locator.read(urllib.request.Request(base+'/api/panic',headers={'Authorization':'Bearer '+token}))
            if accepted(state,supervisor,panic,expected):
                row['phase']='ready';save_transfer(config_path,row)
                return {'ok':True,'base':base,'key':token,'version':version,'archive':str(archive),'server_locator':'telegram-menu','stopped':panic.get('stopped') is True}
        except Exception:pass
        time.sleep(2)
    raise TransferError('Перенос завершён, но защищённое подключение ещё не подтвердилось. Не запускай местного агента одновременно: проверь сервер и повтори подключение')

def recover_local(raw,config_path):
    """Explicitly stop this transfer's two containers; retain every byte.

    The local owner must still choose Resume separately. Failure or uncertainty
    keeps the durable local fence. Credentials and host pin are checked anew.
    """
    with custody(config_path):
        row=saved_transfer(config_path)
        if not same_destination(row,raw):raise TransferError('Укажи тот же сервер, отпечаток и параметры сохранённого переноса')
        helper=next((p for p in (Path(__file__).resolve().parents[1]/'server/transfer_recover.py',Path(__file__).resolve().parents[2]/'server/transfer_recover.py') if p.is_file()),None)
        if helper is None:raise TransferError('В поставке нет помощника восстановления VPS')
        with contextlib.closing(connect(raw)) as client:
            _,value,_=execute(client,'python3 -c '+shlex.quote(helper.read_text(encoding='utf-8'))+' '+shlex.quote(json.dumps(row)),timeout=240)
            reply=json.loads(value)
            if reply.get('ok') is not True:raise TransferError(reply.get('message') or 'Сервер не подтвердил остановку. Местный Stop сохранён')
        row['phase']='server-stopped';save_transfer(config_path,row)
        return {'ok':True,'message':'Серверная копия остановлена, все данные сохранены. Местная копия содержит снимок до переноса; возобнови её отдельной командой только если это нужная память.'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('probe','run','recover-local','locate','status','telegram-check'));parser.add_argument('--config',required=True);parser.add_argument('--program',required=True)
    args=parser.parse_args()
    try:
        payload=sys.stdin.read(32769)
        if len(payload.encode('utf-8'))>32768:raise TransferError('Данные подключения слишком велики')
        raw=json.loads(payload or '{}')
        if not isinstance(raw,dict):raise TransferError('Не удалось прочитать настройки подключения')
        if args.action=='probe':result=probe(raw,Path(args.config))
        elif args.action=='run':result=run(raw,Path(args.config),Path(args.program))
        elif args.action=='recover-local':result=recover_local(raw,Path(args.config))
        elif args.action=='status':result=status(Path(args.config))
        elif args.action=='telegram-check':result=check_telegram(json.loads(Path(args.config).read_text(encoding='utf-8-sig')))
        else:result={'ok':True,'base':remote_locator.locate(json.loads(Path(args.config).read_text(encoding='utf-8-sig')))}
    except TransferError as error:result={'ok':False,'message':str(error),'local_data_kept':True}
    except Exception:result={'ok':False,'message':'Подключение или перенос прервался. Местные данные сохранены; проверь сервер перед повтором.','local_data_kept':True}
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
