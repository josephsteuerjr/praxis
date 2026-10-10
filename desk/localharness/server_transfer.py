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
from deskd.connection_checks import check_telegram

class TransferError(RuntimeError):pass
TARGET='/opt/helene'
TOKEN_PATH=TARGET+'/data/.serverboot/desk-token'
MARKER_PATH=TARGET+'/.window-transfer.json'

def receipt_path(config_path):
    identity=hashlib.sha256(str(config_path.resolve()).encode()).hexdigest()[:20]
    return config_path.parent/('.server-transfer-'+identity+'.json')

def saved_transfer(config_path):
    try:
        row=json.loads(receipt_path(config_path).read_text(encoding='utf-8'))
        return row if isinstance(row,dict) else {}
    except (OSError,ValueError):return {}

def save_transfer(config_path,row):
    path=receipt_path(config_path);tmp=path.with_suffix('.part')
    with tmp.open('w',encoding='utf-8') as output:
        if os.name!='nt':os.chmod(tmp,0o600)
        json.dump(row,output,ensure_ascii=False);output.flush();os.fsync(output.fileno())
    os.replace(tmp,path)

def same_destination(row,raw):
    return bool(row.get('id')) and row.get('destination')==list(parameters(raw)) and row.get('fingerprint')==raw.get('fingerprint')

def status(config_path):
    row=saved_transfer(config_path)
    return {'ok':True,'pending':bool(row),'phase':row.get('phase'),'host':(row.get('destination') or [''])[0],
            'archive':row.get('archive'),'message':'Предыдущий перенос сохранён. Укажи тот же сервер и нажми «Продолжить перенос»; память повторно импортирована не будет.' if row else ''}

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

def connect(raw):
    host,port,login=parameters(raw)
    expected=str(raw.get('fingerprint') or '')
    client=paramiko.SSHClient()
    if expected:client.set_missing_host_key_policy(Pin(expected))
    else:
        with contextlib.suppress(OSError):client.load_system_host_keys(str(Path.home()/'.ssh/known_hosts'))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    key=None
    if raw.get('key_text'):
        for kind in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
            try:key=kind.from_private_key(io.StringIO(str(raw['key_text'])),password=str(raw.get('key_password') or '') or None);break
            except (paramiko.SSHException,ValueError):pass
        if key is None:raise TransferError('Файл SSH-ключа не прочитался. Проверь его пароль или выбери другой файл')
    try:
        client.connect(host,port=port,username=login,password=str(raw.get('password') or '') or None,pkey=key,
                       timeout=15,auth_timeout=20,banner_timeout=15,allow_agent=False,look_for_keys=False)
        return client
    except Exception:
        client.close();raise TransferError('Войти на сервер не удалось. Проверь адрес, логин, пароль или SSH-ключ') from None

def execute(client,command,timeout=30,check=True):
    _,out,_=client.exec_command(command,timeout=timeout)
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
    transport=None
    try:
        transport=paramiko.Transport((host,port))
        transport.start_client(timeout=15);key=transport.get_remote_server_key();seen=fingerprint(key)
    except Exception:raise TransferError('Сервер не отвечает по SSH. Проверь адрес и порт') from None
    finally:
        if transport:transport.close()
    trusted=False
    known=paramiko.HostKeys()
    with contextlib.suppress(OSError):known.load(str(Path.home()/'.ssh/known_hosts'))
    name=host if port==22 else f'[{host}]:{port}'
    if known.lookup(name):trusted=known.check(name,key)
    if not trusted and (raw.get('confirmed_host') is not True or raw.get('fingerprint')!=seen):
        return {'ok':False,'trust_needed':True,'fingerprint':seen,'message':'Сверь отпечаток с панелью сервера и подтверди, что это твой сервер. Пароль ещё не отправлен.'}
    with contextlib.closing(connect({**raw,'fingerprint':seen})) as client:
        root=execute(client,'id -u')[1].strip()=='0'
        os_info=execute(client,'cat /etc/os-release')[1]
        arch=execute(client,'uname -m')[1].strip()
        docker=execute(client,'docker info --format "{{.ServerVersion}}"',check=False)[0]==0
        compose=docker and execute(client,'docker compose version --short',check=False)[0]==0
        exists=execute(client,f'test -e {TARGET}/helene.json',check=False)[0]==0
        supported=bool(re.search(r'(?m)^ID=(?:"?ubuntu"?|"?debian"?)$',os_info)) and arch=='x86_64'
        occupied=execute(client,'docker inspect helene',check=False)[0]==0
        resumable=False
        if config_path:
            row=saved_transfer(config_path)
            if same_destination(row,{**raw,'fingerprint':seen}):
                code,value,_=execute(client,'cat '+MARKER_PATH,check=False)
                with contextlib.suppress(ValueError):resumable=code==0 and json.loads(value).get('id')==row['id']
        port_busy=execute(client,"if command -v ss >/dev/null 2>&1; then ss -ltnH 'sport = :8094'; else awk 'NR>1 && $2 ~ /:1F9E$/ && $4==\"0A\" {print}' /proc/net/tcp /proc/net/tcp6; fi",check=False)[1].strip()!=''
    okay=root and arch=='x86_64' and (supported or docker and compose)
    message='Сервер готов к переносу.' if okay else 'Для автоматической установки нужен администратор сервера Ubuntu/Debian x64.'
    if port_busy and not resumable and not exists:
        okay=False;message='Порт Hélène на этом сервере занят другой программой. Выбери другой сервер или освободи порт 8094 в его панели.'
    if occupied and not exists and not resumable:
        okay=False;message='Имя контейнера Hélène уже занято другой установкой. Она не изменена; выбери другой сервер.'
    return {'ok':okay,'fingerprint':seen,'trusted':trusted,'root':root,'docker':docker and compose,
            'supported':supported,'existing':exists,'resumable':resumable,'message':message}

def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as data:
        for block in iter(lambda:data.read(1024*1024),b''):value.update(block)
    return value.hexdigest()

def make_program(root:Path,path:Path):
    allowed=('app','tree','server','requirements.txt','helene-relay','helene-build.json')
    if any(not (root/name).exists() for name in allowed):raise TransferError('В поставке нет полного комплекта для сервера. Скачай полную Windows-поставку Hélène')
    def include(info):
        if info.issym() or info.islnk() or any(part in ('.git','__pycache__','node_modules','target') for part in Path(info.name).parts):return None
        return info
    with tarfile.open(path,'w:gz') as target:
        for name in allowed:target.add(root/name,arcname=name,filter=include)

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
    tree=carry.tree_of(config_path,config)
    holder=boot._read_lock(boot.tree_lock_path(tree))
    if boot._lock_is_live(holder):raise TransferError('Местный агент ещё работает. Сначала останови его в окне и повтори перенос')

def run(raw,config_path:Path,program:Path):
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
    row=saved_transfer(config_path)
    continuing=same_destination(row,{**raw,'fingerprint':checked['fingerprint']})
    if row and not continuing:raise TransferError('Есть незавершённый перенос на другой сервер. Сначала продолжи его: так не появится второй работающий агент')
    if continuing:
        if row.get('version')!=expected:raise TransferError('Перенос начат другой версией приложения. Подключись к уже перенесённому серверу; его данные не заменены')
        archive=Path(row['archive'])
        if not archive.is_file() or digest(archive)!=row.get('archive_sha'):raise TransferError('Сохранённый архив переноса не найден или изменён. Сервер и местная память оставлены на месте')
    else:
        require_cold(config_path,config)
        archive_root=Path(tempfile.mkdtemp(prefix='helene-transfer-archive-'))
        archive=Path(carry.export(config_path,archive_root/('agent-'+secrets.token_hex(12)+'.zip'))['archive'])
        row={'id':secrets.token_hex(12),'destination':list(parameters(raw)),'fingerprint':checked['fingerprint'],
             'archive':str(archive),'archive_sha':digest(archive),'version':expected,'phase':'archive'}
        save_transfer(config_path,row)
    stage='/opt/helene-transfer-'+row['id']
    with tempfile.TemporaryDirectory(prefix='helene-server-kit-') as temporary:
        kit=Path(temporary)/'program.tar.gz';make_program(program,kit)
        with contextlib.closing(connect({**raw,'fingerprint':checked['fingerprint']})) as client:
            if not checked['docker']:install_docker(client)
            execute(client,f'mkdir -p -m 700 {shlex.quote(stage)}')
            with client.open_sftp() as sftp:
                sftp.put(str(kit),stage+'/program.tar.gz');sftp.put(str(archive),stage+'/agent.zip')
            for local,remote in ((kit,stage+'/program.tar.gz'),(archive,stage+'/agent.zip')):
                reported=execute(client,'sha256sum '+shlex.quote(remote))[1].split()[0]
                if reported!=digest(local):raise TransferError('Архив при передаче повредился. Установка не начата; повтори перенос')
            execute(client,f'mkdir -p {stage}/program && tar -xzf {stage}/program.tar.gz -C {stage}/program')
            # Existing installations and unrelated containers are not touched by this path.
            if not checked.get('resumable'):
                execute(client,f'test ! -e {TARGET}/helene.json && test ! -e {MARKER_PATH} && mkdir -p -m 700 {TARGET}')
                with client.open_sftp() as sftp:
                    with sftp.file(MARKER_PATH,'w') as marker:marker.write(json.dumps({'id':row['id'],'archive_sha':row['archive_sha']}))
                    sftp.chmod(MARKER_PATH,0o600)
            row['phase']='installing';save_transfer(config_path,row)
            command=f'cd {stage}/program && HELENE_DIR={TARGET} HELENE_AUTOMATIC_CONNECTION=1 sh server/install.sh {stage}/agent.zip'
            execute(client,command,timeout=2400)
            token=execute(client,f'cat {TOKEN_PATH}')[1].strip()
            version=json.loads(execute(client,f'cat {TARGET}/helene-build.json')[1]).get('version')
            row['phase']='connecting';save_transfer(config_path,row)
    if version!=expected:raise TransferError('На сервере обнаружена другая версия. Местная память сохранена; проверь сервер перед подключением')
    # Give the owner menu time to acquire its current automatic HTTPS address.
    for _ in range(30):
        try:
            base=remote_locator.locate({**config,'key':token})
            request=urllib.request.Request(base+'/api/state',headers={'Authorization':'Bearer '+token})
            state=remote_locator.read(request)
            if state.get('runner',{}).get('alive') is True:
                row['phase']='ready';save_transfer(config_path,row)
                return {'ok':True,'base':base,'key':token,'version':version,'archive':str(archive),'server_locator':'telegram-menu'}
        except Exception:pass
        time.sleep(2)
    raise TransferError('Перенос завершён, но защищённое подключение ещё не подтвердилось. Не запускай местного агента одновременно: проверь сервер и повтори подключение')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('probe','run','locate','status','telegram-check'));parser.add_argument('--config',required=True);parser.add_argument('--program',required=True)
    args=parser.parse_args()
    try:
        payload=sys.stdin.read(32769)
        if len(payload.encode('utf-8'))>32768:raise TransferError('Данные подключения слишком велики')
        raw=json.loads(payload or '{}')
        if not isinstance(raw,dict):raise TransferError('Не удалось прочитать настройки подключения')
        if args.action=='probe':result=probe(raw,Path(args.config))
        elif args.action=='run':result=run(raw,Path(args.config),Path(args.program))
        elif args.action=='status':result=status(Path(args.config))
        elif args.action=='telegram-check':result=check_telegram(json.loads(Path(args.config).read_text(encoding='utf-8-sig')))
        else:result={'ok':True,'base':remote_locator.locate(json.loads(Path(args.config).read_text(encoding='utf-8-sig')))}
    except TransferError as error:result={'ok':False,'message':str(error),'local_data_kept':True}
    except Exception:result={'ok':False,'message':'Подключение или перенос прервался. Местные данные сохранены; проверь сервер перед повтором.','local_data_kept':True}
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
