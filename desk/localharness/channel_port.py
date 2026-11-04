"""Repair a Windows-reserved channel port without displacing another listener."""
import errno,json,os,re,socket,subprocess,sys,uuid
from pathlib import Path


def excluded_ranges():
    done=subprocess.run(['netsh','interface','ipv4','show','excludedportrange','protocol=tcp'],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    result=[]
    for line in done.stdout.decode('utf-8',errors='replace').splitlines():
        match=re.fullmatch(r'\s*(\d+)\s+(\d+)\s*\*?\s*',line)
        if match:
            start,end=map(int,match.groups())
            if 0<start<=end<=65535:result.append((start,end))
    return result


def endpoint_exists(port):
    import psutil
    # Unknown endpoint state is occupied too. A failed census never grants a
    # new address or permits stopping another local programme.
    return any(row.laddr and row.laddr.port==port and row.pid not in (None,0)
               for row in psutil.net_connections(kind='tcp'))


def _commit(config,value,port,stamp):
    from .config_transaction import read_snapshot,replace_config
    _,current=read_snapshot(config)
    if current!=stamp:raise ValueError('Настройки изменились во время проверки порта; перечитай их')
    original=config.read_bytes();value['port']=port
    phone=value.get('phone') or {}
    if phone.get('mode')=='automatic':value['phone']=dict(phone,local_port=port)
    folder=config.parent/'.helene/settings-backups';folder.mkdir(parents=True,exist_ok=True)
    identity=uuid.uuid4().hex
    (folder/('reserved-port-'+identity+'.json')).write_bytes(original)
    staged=config.with_name(config.name+'.port-'+identity+'.tmp')
    try:
        with staged.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        replace_config(staged,config)
    finally:staged.unlink(missing_ok=True)


def ensure_available(config):
    if sys.platform!='win32':return None
    from .config_transaction import transaction,read_snapshot
    config=Path(config).resolve()
    with transaction(config):
        value,stamp=read_snapshot(config)
        port=value.get('port')
        if value.get('mode','local')!='local' or type(port) is not int or not 0<port<=65535:return None
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            try:probe.bind(('127.0.0.1',port))
            except OSError as error:
                if getattr(error,'winerror',None)!=10013:return None
                if endpoint_exists(port):return None
                if not any(start<=port<=end for start,end in excluded_ranges()):
                    raise OSError(errno.EACCES,f'Windows запретила внутренний порт {port}; причина не подтверждена, адрес не изменён') from error
            else:
                phone=value.get('phone') or {}
                if phone.get('mode')=='automatic' and phone.get('local_port')!=port:
                    _commit(config,value,port,stamp)
                    return {'old_port':port,'port':port,'reason':'automatic_phone_port'}
                return None
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as replacement:
            replacement.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            replacement.bind(('127.0.0.1',0));selected=replacement.getsockname()[1]
            # Hold the kernel-selected slot through the atomic settings commit.
            _commit(config,value,selected,stamp)
        return {'old_port':port,'port':selected,'reason':'windows_reserved_port'}
