"""Private, install-independent SSH profiles shared by server workflows.

Only summaries leave the controller. Connection secrets are never returned to
the renderer, included in agent context, or copied with a relocated agent.
"""
from __future__ import annotations
import contextlib
import io
import json
import re
import secrets
import socket
from pathlib import Path

import paramiko

FORMAT='helene-server-connections.v1'
AUTH_FIELDS=('password','key_text','key_path','key_password')
KEY_HEADER=re.compile(r'^-----BEGIN (?:OPENSSH|RSA|EC|ENCRYPTED|DSA)? ?PRIVATE KEY-----')


def authentication(raw):
    """Accept an existing key file, key text, or a key pasted in password."""
    password=str(raw.get('password') or '')
    text=str(raw.get('key_text') or '').strip().lstrip('\ufeff')
    path=str(raw.get('key_path') or '').strip()
    if KEY_HEADER.match(password.strip().lstrip('\ufeff')):
        if text or path:raise ValueError('Укажи один SSH-ключ: файлом либо текстом.')
        text=password.strip().lstrip('\ufeff');password=''
    if text and path:raise ValueError('Укажи один SSH-ключ: файлом либо текстом.')
    if path:
        try:
            file=Path(path).expanduser()
            if not file.is_absolute():raise ValueError('Нужен полный путь к SSH-ключу.')
            with file.open('rb') as stream:data=stream.read(65537)
            if len(data)>65536:raise ValueError('Файл SSH-ключа слишком большой.')
            text=data.decode('utf-8-sig').strip()
        except (OSError,UnicodeError):raise ValueError('Не удалось прочитать файл SSH-ключа на этом компьютере.') from None
    key=None
    if text:
        if len(text.encode('utf-8'))>65536 or not KEY_HEADER.match(text):
            raise ValueError('Вставь приватный SSH-ключ целиком, включая BEGIN и END.')
        for kind in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
            try:
                key=kind.from_private_key(io.StringIO(text),password=str(raw.get('key_password') or '') or None)
                break
            except (paramiko.SSHException,ValueError):pass
        if key is None and text.startswith(('-----BEGIN PRIVATE KEY-----','-----BEGIN ENCRYPTED PRIVATE KEY-----')):
            # Generic PEM/PKCS8 keys are common exports; Paramiko's text readers
            # accept algorithm-specific PEM/OpenSSH, so convert only in memory.
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ed25519,rsa,ec
            try:
                password_bytes=str(raw.get('key_password') or '').encode() or None
                decoded=serialization.load_pem_private_key(text.encode(),password=password_bytes)
                kind=paramiko.Ed25519Key if isinstance(decoded,ed25519.Ed25519PrivateKey) else paramiko.RSAKey if isinstance(decoded,rsa.RSAPrivateKey) else paramiko.ECDSAKey if isinstance(decoded,ec.EllipticCurvePrivateKey) else None
                if kind:
                    converted=decoded.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption())
                    key=kind.from_private_key(io.StringIO(converted.decode()))
            except (ValueError,TypeError,paramiko.SSHException):pass
        if key is None:raise ValueError('SSH-ключ не прочитался. Проверь формат и пароль ключа.')
    if key is None and not password:raise ValueError('Укажи пароль сервера или SSH-ключ.')
    return {'password':password or None,'pkey':key}


def root():
    from .component_store import root as components,_plain
    return _plain(components().parent/'server-connections-v1')


def _path(identity):
    if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):
        raise ValueError('Подключение к серверу не найдено.')
    return root()/(identity+'.json')


def read(identity):
    value=json.loads(_path(identity).read_text(encoding='utf-8'))
    if not isinstance(value,dict) or value.get('format')!=FORMAT or value.get('id')!=identity:
        raise ValueError('Сохранённое подключение повреждено; создай новое, прежнее сохранено.')
    return value


def summary(value):
    return {key:value[key] for key in ('id','name','host','port','login','fingerprint','kind') if key in value}


def profiles():
    rows=[]
    if root().exists():
        for path in sorted(root().glob('*.json')):
            if not re.fullmatch(r'[a-f0-9]{32}',path.stem):continue
            try:rows.append(summary(read(path.stem)))
            except (OSError,ValueError):rows.append({'id':path.stem,'name':'Подключение требует проверки','error':True})
    return rows


def resolve(raw):
    """Reuse secrets without trusting editable public fields to retarget them."""
    identity=raw.get('server_profile')
    if not identity:return dict(raw)
    stored=read(identity)
    for name in ('host','port','login','fingerprint'):
        if name in raw and str(raw[name])!=str(stored[name]):
            raise ValueError('Сервер изменился. Выбери сохранённое подключение заново или создай новое.')
    for name in AUTH_FIELDS:
        if raw.get(name):raise ValueError('Для нового ключа создай новое подключение к серверу.')
    return {**raw,**{key:value for key,value in stored.items() if key in
            ('host','port','login','fingerprint',*AUTH_FIELDS)},'confirmed_host':True}


def inspect(raw,*,on_client=None):
    """Host key first, authentication second; no commands or remote writes."""
    from . import server_transfer as st
    raw=resolve(raw);host,port,_=st.parameters(raw)
    transport=None;peer=None
    try:
        peer=socket.create_connection((host,port),timeout=10)
        class Pending:
            def close(self):
                with contextlib.suppress(OSError):peer.shutdown(socket.SHUT_RDWR)
                peer.close()
                if transport:transport.close()
        if on_client:on_client(Pending())
        try:transport=paramiko.Transport(peer)
        except BaseException:peer.close();raise
        transport.start_client(timeout=15)
        key=transport.get_remote_server_key();seen=st.fingerprint(key)
    except Exception:raise ValueError('Сервер не отвечает по SSH. Проверь адрес и порт.') from None
    finally:
        if transport:transport.close()
        if peer:peer.close()
    known=paramiko.HostKeys()
    with contextlib.suppress(OSError):known.load(str(Path.home()/'.ssh/known_hosts'))
    name=host if port==22 else f'[{host}]:{port}'
    trusted=known.check(name,key)
    pinned=str(raw.get('fingerprint') or '')
    if pinned and pinned!=seen:
        raise ValueError('Отпечаток SSH-сервера изменился. Подключение остановлено; проверь сервер в его панели.')
    if not trusted and (raw.get('confirmed_host') is not True or pinned!=seen):
        return {'ok':False,'trust_needed':True,'fingerprint':seen,
                'message':'Сверь отпечаток с панелью сервера. Пароль и ключ ещё не отправлены.'}
    with contextlib.closing(st.connect({**raw,'fingerprint':seen},on_client=on_client)):pass
    return {'ok':True,'fingerprint':seen,'message':'Подключение по SSH проверено.'}


def save(raw,*,identity=None,on_client=None):
    from . import server_transfer as st
    from .private_files import write_private
    checked=inspect(raw,on_client=on_client)
    if not checked.get('ok'):return checked
    resolved=resolve(raw);host,port,login=st.parameters(resolved)
    identity=identity or secrets.token_hex(16)
    password=str(resolved.get('password') or '')
    auth={key:str(resolved[key]) for key in AUTH_FIELDS if resolved.get(key)}
    if KEY_HEADER.match(password.strip().lstrip('\ufeff')):
        auth.pop('password',None);auth['key_text']=password.strip().lstrip('\ufeff')
    value={'format':FORMAT,'id':identity,'host':host,'port':port,'login':login,
           'name':str(raw.get('name') or host).strip()[:100],
           'fingerprint':checked['fingerprint'],'kind':'key' if auth.get('key_text') or auth.get('key_path') else 'password',**auth}
    if _path(identity).exists():
        if read(identity)!=value:raise ValueError('Сохранённое подключение отличается от операции; оно не перезаписано.')
    else:write_private(_path(identity),(json.dumps(value,ensure_ascii=False)+'\n').encode('utf-8'))
    return {'ok':True,'profile':summary(value),'message':'Подключение сохранено на этом компьютере.'}
