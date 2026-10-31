"""Explicit data-only transfer; source retirement requires observed new startup.

No old interpreter/code is imported. Mutable data never shares writable inodes.
The original remains authoritative until the whole copy is verified and bound.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import shutil
import stat
import threading
import uuid

from .package_notice import read,write

FORMAT='helene-data-relocation.v1'
EPHEMERAL={'.desktop-owner.lock','.python-desktop-status',
    'memory/.control/desktop-session.json','memory/.control/desktop-session.lock',
    'memory/.control/desktop-update-request.json'}


def _check(cancelled):
    if cancelled and cancelled.is_set():raise InterruptedError('Owner stopped data transfer')


def _regular_path(path):
    path=Path(path).absolute()
    for parent in (path,*path.parents):
        try:info=parent.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Linked data path: '+str(parent))
    return path.resolve()


def _signature(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
        raise ValueError('Data entry is not a regular file: '+str(path))
    return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]


def _publish_copy(part,target):
    """Atomic publication must not overwrite a file another writer just added."""
    _regular_path(part);_regular_path(target)
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.MoveFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p];kernel.MoveFileW.restype=ctypes.c_int
        if not kernel.MoveFileW(str(part),str(target)):raise ctypes.WinError(ctypes.get_last_error())
    else:
        # This link is only from our own same-directory temporary file. The
        # original data is never linked; publication leaves one writable name.
        os.link(part,target);part.unlink()


def _inventory(root,cancelled=None):
    root=_regular_path(root);files={};directories=[]
    for directory,dirs,names in os.walk(root,followlinks=False):
        _check(cancelled);here=_regular_path(directory)
        for name in sorted(dirs):
            _regular_path(here/name);directories.append((here/name).relative_to(root).as_posix())
        for name in sorted(names):
            path=here/name;_regular_path(path)
            files[path.relative_to(root).as_posix()]=_signature(path)
    return files,sorted(directories)


def plan(rows,source,destination,program,old,receipt):
    """Read-only path admission, before the uninstaller; no old tree traversal."""
    source,destination,program,old=map(_regular_path,(source,destination,program,old))
    if not source.is_dir():raise ValueError('Existing data folder is missing')
    if source==destination:raise ValueError('Choose a different destination to transfer data')
    selected=[];aliases={}
    for row in rows:
        if row['value'].get('mode')=='remote':continue
        before=source if row['relative']=='helene.json' else _regular_path(row['tree'])
        aliases[str(before)]=str(destination) if before==source else str(
            destination.parent/(destination.name+'-agents')/Path(row['relative']).parent)
    # Nested profiles are carried by their containing data tree exactly once.
    for before in sorted(aliases,key=lambda p:len(Path(p).parts)):
        path=Path(before)
        parent=next((entry for entry in selected if path.is_relative_to(Path(entry['source']))),None)
        if parent:
            aliases[before]=str(Path(parent['destination'])/path.relative_to(parent['source']))
        else:selected.append({'source':before,'destination':aliases[before]})
    previous=read(receipt)
    if Path(receipt).exists() and previous.get('format')!=FORMAT:
        raise ValueError('Invalid data transfer receipt; it was retained')
    identity={'format':FORMAT,'source':str(source),'destination':str(destination),'roots':selected,'aliases':aliases}
    if previous and any((([{k:row[k] for k in ('source','destination')} for row in previous.get(key,[])]
                            if key=='roots' else previous.get(key)) != value) for key,value in identity.items()):
        raise ValueError('Data transfer receipt belongs to another source/destination')
    sources=[Path(row['source']) for row in selected]
    if any(old==p or old.is_relative_to(p) or program==p or program.is_relative_to(p) for p in sources):
        raise ValueError('Data source must not contain program files')
    destinations=[_regular_path(row['destination']) for row in selected]
    for target in destinations:
        if any(target==p or target.is_relative_to(p) or p.is_relative_to(target)
               for p in [program,old,*sources]):
            raise ValueError('Data destination must be separate from source and program folders')
        if target.exists() and (not target.is_dir() or (any(target.iterdir()) and not previous)):
            raise ValueError('Data destination must be empty; existing data is never overwritten')
        # A temporary probe is confined to the selected new parent, never old data.
        parent=target.parent
        while not parent.exists():parent=parent.parent
        probe=parent/('.helene-data-access-'+uuid.uuid4().hex)
        try:
            with probe.open('xb') as stream:stream.write(b'probe');stream.flush();os.fsync(stream.fileno())
        finally:probe.unlink(missing_ok=True)
    return previous or dict(identity,state='planned',id=uuid.uuid4().hex)


def remap(path,transfer):
    path=Path(path).resolve()
    for before,after in sorted(transfer['aliases'].items(),key=lambda row:len(row[0]),reverse=True):
        if path.is_relative_to(before):return Path(after)/path.relative_to(before)
    return path


def prepare(transfer,receipt,progress,cancelled=None):
    """Copy and independently hash every selected byte before rebinding config."""
    _check(cancelled);write(receipt,transfer)
    inventories=[];total=0;total_bytes=0
    for row in transfer['roots']:
        source=Path(row['source']);files,dirs=_inventory(source,cancelled)
        inventories.append((row,files,dirs));total+=sum(name not in EPHEMERAL for name in files)
        total_bytes+=sum(sig[2] for name,sig in files.items() if name not in EPHEMERAL)
    free=shutil.disk_usage(next(p for p in (Path(transfer['destination']),*Path(transfer['destination']).parents) if p.exists())).free
    existing=sum((Path(row['destination'])/name).stat().st_size for row,files,_ in inventories
                 for name in files if name not in EPHEMERAL and (Path(row['destination'])/name).is_file())
    if free<max(0,total_bytes-existing):raise OSError('Недостаточно места для проверенной копии данных; исходная папка сохранена')
    count=0;copied_bytes=0;lock=threading.Lock();results={}
    progress({'stage':'transferring_data','files':0,'total_files':total,'bytes':0,'total_bytes':total_bytes})
    work=[]
    for row,files,dirs in inventories:
        source=Path(row['source']);target=_regular_path(row['destination']);target.mkdir(parents=True,exist_ok=True)
        info=target.stat();row['copy_destination_identity']=[info.st_dev,info.st_ino]
        expected=set(files)-EPHEMERAL
        actual,_=_inventory(target,cancelled)
        own_prefix='.helene-transfer-'+transfer['id']+'-'
        leftovers={name for name in actual if Path(name).name.startswith(own_prefix)}
        for name in leftovers:_regular_path(target/name).unlink()
        if set(actual)-expected-leftovers:raise ValueError('Unexpected files in data destination; original data retained')
        for name in dirs:_regular_path(target/name).mkdir(parents=True,exist_ok=True)
        for name,sig in files.items():
            if name not in EPHEMERAL:work.append((row['source'],row['destination'],name,sig))
    def copy(item):
        nonlocal count,copied_bytes
        before,after,name,sig=item;_check(cancelled)
        source=_regular_path(Path(before)/name);target=_regular_path(Path(after)/name)
        if _signature(source)!=sig:raise RuntimeError('Source data changed during transfer')
        digest=hashlib.sha256();part=target.with_name('.helene-transfer-'+transfer['id']+'-'+uuid.uuid4().hex)
        try:
            if target.exists():
                with source.open('rb') as stream:
                    while chunk:=stream.read(1024*1024):_check(cancelled);digest.update(chunk)
            else:
                with source.open('rb') as src,part.open('xb') as dst:
                    info=os.fstat(src.fileno())
                    if [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]!=sig:raise RuntimeError('Source data identity changed')
                    while chunk:=src.read(1024*1024):
                        _check(cancelled);digest.update(chunk);dst.write(chunk)
                    dst.flush();os.fsync(dst.fileno())
                if _signature(source)!=sig:raise RuntimeError('Source data changed during transfer')
                _publish_copy(part,target)
            with target.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=digest.hexdigest():raise RuntimeError('Data destination differs; no configuration switched')
            with lock:
                results[(before,name)]={'sha256':actual,'size':sig[2]};count+=1;copied_bytes+=sig[2]
                progress({'stage':'transferring_data','file':name,'files':count,'total_files':total,
                          'bytes':copied_bytes,'total_bytes':total_bytes})
        finally:part.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=12) as pool:list(pool.map(copy,work))
    roots=[]
    for row,files,dirs in inventories:
        if _inventory(row['source'],cancelled)!=(files,dirs):raise RuntimeError('Source data changed during transfer')
        copied={name:results[(row['source'],name)] for name in files if name not in EPHEMERAL}
        target=_regular_path(row['destination']);info=target.stat()
        if [info.st_dev,info.st_ino]!=row['copy_destination_identity']:
            raise RuntimeError('Data destination directory changed during transfer')
        roots.append(dict(row,files=copied,source_signatures=files,directories=dirs,
                          destination_identity=[info.st_dev,info.st_ino]))
    transfer=dict(transfer,state='verified',roots=roots)
    validate_prepared(transfer,cancelled)
    write(receipt,transfer)
    return transfer


def validate_prepared(transfer,cancelled=None):
    """Recheck destination identity and every accepted byte before config bind."""
    work=[]
    for row in transfer['roots']:
        target=_regular_path(row['destination']);info=target.stat()
        if [info.st_dev,info.st_ino]!=row['destination_identity']:
            raise RuntimeError('Verified data directory changed; no configuration switched')
        actual,dirs=_inventory(target,cancelled)
        if set(actual)!=set(row['files']) or dirs!=row['directories']:
            raise RuntimeError('Verified data inventory changed; no configuration switched')
        work.extend((target/name,record) for name,record in row['files'].items())
    def verify(item):
        _check(cancelled);path,record=item;path=_regular_path(path)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=record['sha256']:
                raise RuntimeError('Verified data changed; no configuration switched')
    with ThreadPoolExecutor(max_workers=12) as pool:list(pool.map(verify,work))
    return transfer


def retire_sources(transfer,*,startup_proof):
    """Only verified startup on the new tree authorizes retiring original data."""
    if not startup_proof or Path(startup_proof.get('tree','')).resolve()!=Path(transfer['destination']):
        raise ValueError('New application has not confirmed the transferred data')
    errors=[];removed=[]
    # Verify ALL roots first. A changed original or missing destination is never deleted.
    for row in transfer['roots']:
        source=_regular_path(row['source']);target=_regular_path(row['destination'])
        files,dirs=_inventory(source)
        if files!=row['source_signatures'] or dirs!=row['directories']:
            raise RuntimeError('Original data changed after transfer; it was retained')
        info=target.stat()
        if [info.st_dev,info.st_ino]!=row['destination_identity']:
            raise RuntimeError('Transferred data directory changed; original retained for recovery')
        # The verified destination is now owned by the confirmed new process.
        # Its history/settings may legitimately have changed after startup.
    for row in transfer['roots']:
        source=Path(row['source']);remaining={}
        for name,sig in row['source_signatures'].items():
            path=_regular_path(source/name)
            if _signature(path)!=sig:raise RuntimeError('Original data changed before retirement')
            try:path.unlink()
            except PermissionError:
                with path.open('rb') as stream:remaining[name]=hashlib.file_digest(stream,'sha256').hexdigest()
        if remaining:
            from .migration_windows import system_action
            system_action(source,'remove_files',remaining)
        for name in sorted(row['directories'],key=lambda name:len(Path(name).parts),reverse=True):
            try:(source/name).rmdir()
            except OSError:pass
        try:source.rmdir()
        except OSError:pass
        if any(source.rglob('*')):errors.append('Не удалось полностью убрать исходную папку данных: '+str(source))
        else:removed.append(str(source))
    return dict(transfer,state='retired' if not errors else 'source_retained',removed_sources=removed,errors=errors)
