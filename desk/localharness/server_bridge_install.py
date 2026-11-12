"""Private reviewed plans and resumable owner jobs; no credentials in UI results."""
from __future__ import annotations
import contextlib
import json
import ipaddress
import os
import secrets
import shlex
import socket
import threading
import time
import weakref
from pathlib import Path

from . import server_connections as connections,server_transfer,server_bridge
from .private_files import write_private
from .component_install_job import identity,alive

_jobs=weakref.WeakValueDictionary()
_jobs_lock=threading.RLock()


def running(row):
    state=alive(row.get('worker'))
    if state is True and row.get('worker',{}).get('pid')==os.getpid():
        # The session can survive a failed worker or failed final receipt write.
        # A live application PID is not evidence that its setup thread is alive.
        with _jobs_lock:
            job=_jobs.get(row['id'])
            return bool(job and job.thread and job.thread.is_alive())
    return state


def path(operation):return connections._path(operation).parent/'bridge-operations'/(operation+'.json')


def save(row):write_private(path(row['id']),(json.dumps(row,ensure_ascii=False)+'\n').encode())


def read(operation):
    row=json.loads(path(operation).read_text(encoding='utf-8'))
    if row.get('id')!=operation:raise ValueError('Неверная операция сервера.')
    return row


def public(row):
    result={key:row[key] for key in ('id','name','phase','message','bridge','at') if key in row}
    if row.get('phase') in ('planned','failed','interrupted'):
        result['plan']={key:row['plan'][key] for key in ('id','sha256','changes')}
    state=running(row) if row.get('phase')=='applying' else False
    result['active']=row.get('phase')=='applying' and state is True
    if row.get('phase')=='applying' and state is None:
        result.update(phase='attention',message='Не удалось подтвердить остановку установщика. Повтор пока недоступен; проверь состояние приложения.')
        return result
    if row.get('phase')=='applying' and not result['active']:
        result.update(phase='interrupted',message='Приложение остановилось во время настройки. Продолжи сохранённый план: сервер проверит уже выполненные этапы.')
        result['plan']={key:row['plan'][key] for key in ('id','sha256','changes')}
    return result


def status():
    folder=connections.root()/'bridge-operations';rows=[]
    if folder.exists():
        for file in folder.glob('*.json'):
            try:rows.append(public(read(file.stem)))
            except (OSError,ValueError):rows.append({'id':file.stem,'phase':'error','message':'Состояние операции повреждено; прежние файлы сохранены.'})
    return rows


def remote(client,request,timeout=60):
    source=Path(__file__).with_name('server_bridge_remote.py').read_text(encoding='utf-8')
    code,out,_=server_transfer.execute(client,'python3 -c '+shlex.quote(source),timeout=timeout,check=False,
                                       input_data=json.dumps(request).encode())
    try:result=json.loads(out)
    except ValueError:raise ValueError('Сервер не подтвердил этап настройки. Состояние сохранено; проверь подключение и продолжи.') from None
    if code or not result.get('ok'):raise ValueError(str(result.get('error') or 'Сервер не завершил этап настройки.'))
    return result['result']


def prepare(raw,agent):
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
    profile=raw.get('server_profile');connection=connections.resolve({'server_profile':profile})
    checked=connections.inspect({'server_profile':profile})
    if not checked.get('ok'):return checked
    operation=secrets.token_hex(16)
    addresses=sorted({row[4][0] for row in socket.getaddrinfo(connection['host'],connection['port'],type=socket.SOCK_STREAM)})
    public_address=str(raw.get('public_address') or '').strip()
    if public_address:
        try:addresses=[str(ipaddress.ip_address(public_address))]
        except ValueError:raise ValueError('Публичный IP сервера должен быть адресом IPv4 или IPv6 из панели сервера.') from None
    request={key:raw[key] for key in ('proxy','https','domain','remote_proxy_port','remote_app_port') if key in raw}
    request.update(action='plan',id=operation,server_addresses=addresses)
    with contextlib.closing(server_transfer.connect(connection)) as client:plan=remote(client,request)
    key=ed25519.Ed25519PrivateKey.generate()
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption()).decode()
    public_key=key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH).decode()
    spec=plan['inventory']['specification'];configuration={}
    if spec['proxy']:configuration.update(local_proxy_port=raw.get('local_proxy_port',8888),remote_proxy_port=spec['remote_proxy_port'])
    if spec['https']:configuration.update(local_app_port=agent.port,remote_app_port=spec['remote_app_port'])
    configuration=server_bridge.ports(configuration)
    row={'id':operation,'name':str(raw.get('name') or connections.read(profile)['name'])[:100],
         'server_profile':profile,'plan':plan,'private_key':private,'public_key':public_key,'phase':'planned',
         'ports':configuration,'at':time.time(),'message':'Проверь конкретные изменения перед установкой.'}
    save(row)
    return public(row)


class Job:
    def __init__(self,owner,row):
        self.owner=owner;self.row=row;self.stop=threading.Event();self.client=None;self.thread=None;self.lock=threading.Lock()
    def start(self):
        self.thread=threading.Thread(target=self.run,name='helene-server-setup',daemon=True);self.thread.start()
    def cancel(self):
        self.stop.set()
        with self.lock:
            if self.client:self.client.close()
        if self.thread:self.thread.join(25)
        if self.thread and self.thread.is_alive():raise RuntimeError('Остановка настройки сервера ещё не подтверждена.')
    def run(self):
        row=self.row
        try:
            def own(client):
                with self.lock:
                    if self.stop.is_set():raise RuntimeError('Server setup interrupted')
                    self.client=client
            connection=connections.resolve({'server_profile':row['server_profile']})
            with contextlib.closing(server_transfer.connect(connection,on_client=own)) as client:
                result=remote(client,{'action':'apply','plan':row['plan'],'accepted_sha256':row['plan']['sha256'],
                                      'public_key':row['public_key']},timeout=1200)
            if self.stop.is_set():raise RuntimeError('Server setup interrupted')
            # Verify the newly restricted account before retaining it for runtime.
            runtime=connections.save({'host':connection['host'],'port':connection['port'],'login':result['username'],
                'key_text':row['private_key'],'fingerprint':connection['fingerprint'],'confirmed_host':True,
                'name':row['name']+' · мост'},identity=row['id'],on_client=own)
            if not runtime.get('ok'):raise ValueError('Службы настроены, но вход отдельным ключом моста ещё не подтверждён. Продолжи проверку.')
            if self.stop.is_set():raise RuntimeError('Server setup interrupted')
            spec=row['plan']['inventory']['specification']
            bridge={'format':server_bridge.FORMAT,'id':row['id'],'name':row['name'],
                    'server_profile':runtime['profile']['id'],'ports':row['ports'],
                    'external':'https://'+spec['domain'] if spec['https'] else '',
                    'phase':'configured','certificate_automation':result.get('certificate_automation') is True}
            write_private(server_bridge.path(bridge['id']),(json.dumps(bridge,ensure_ascii=False)+'\n').encode())
            row.update(phase='installed',bridge=bridge['id'],message=result['message'])
        except Exception as error:
            row.update(phase='interrupted' if self.stop.is_set() else 'failed',
                message='Операция прервана. Выполненные этапы сохранены; продолжи этот план.' if self.stop.is_set() else
                        str(error) if isinstance(error,ValueError) else 'Настройка остановилась ('+type(error).__name__+'). Данные сохранены; продолжи этот план.')
        finally:
            row['at']=time.time()
            try:save(row)
            except OSError:
                # Keep the reviewed plan on disk. running() exposes this as
                # interrupted once this thread ends, allowing idempotent resume.
                pass
            finally:self.owner.transfer_lock.release()


def start(owner,raw):
    from .config_transaction import transaction
    # Serialize claims across windows/processes as well as this owner's thread.
    with transaction(path(raw.get('id')).with_suffix('.claim')):
        with _jobs_lock:return _start(owner,raw)


def _start(owner,raw):
    row=read(raw.get('id'))
    if raw.get('accepted_sha256')!=row['plan']['sha256']:raise ValueError('Сначала просмотри и подтверди этот план изменений.')
    if row['phase']=='installed':return public(row)
    if row['phase']=='applying' and running(row) is not False:
        raise RuntimeError('Эта операция ещё выполняется или её остановка не подтверждена.')
    with owner.lock:
        if owner.exiting:raise RuntimeError('Приложение закрывается.')
        if not owner.transfer_lock.acquire(blocking=False):raise RuntimeError('Серверная операция уже выполняется; дождись её результата.')
        try:
            row.update(phase='applying',worker=identity(),at=time.time(),message='Настраиваю сервер. Можно закрыть окно; приложение продолжит работу в трее.')
            save(row);job=Job(owner,row)
            if not hasattr(owner,'server_setup_jobs'):owner.server_setup_jobs={}
            owner.server_setup_jobs[row['id']]=job;_jobs[row['id']]=job;job.start()
        except BaseException:
            row.update(phase='interrupted',message='Установщик не запустился. Сохранённый план можно продолжить.')
            with contextlib.suppress(OSError):save(row)
            owner.transfer_lock.release();raise
    return public(row)


def close(owner):
    failures=[]
    for job in getattr(owner,'server_setup_jobs',{}).values():
        try:job.cancel()
        except RuntimeError as error:failures.append(str(error))
    if failures:raise RuntimeError('; '.join(failures))
