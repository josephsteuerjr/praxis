"""Full source/project payload for a reviewed move, independent of code merging.

Every selected root is copied without size/type-name filters. Machine-state
exclusions are explicit plan entries. Links are recorded as root-relative
references, never traversed into unselected directories. Consumers must extract
to a new staging directory; activation and Git rebinding are separate steps.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import zipfile

from . import carry_inventory as checks
from .carry_scope import linked
from .private_files import write_private

FORMAT='helene.payload.v1'
META='helene-payload.json'


def _roots(raw):
    result={}
    for name,value in raw.items():
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name):
            raise ValueError('Некорректное имя раздела переноса.')
        path=Path(value).absolute()
        if linked(path.lstat()) or not path.is_dir():
            raise ValueError('Раздел переноса должен указывать на настоящую папку: '+str(path))
        result[name]=path.resolve()
    if not result:raise ValueError('Не выбрано содержимое переноса.')
    return result


def _excluded(root,rel,exclusions):
    return any(row['root']==root and (rel==row['path'] or rel.startswith(row['path']+'/')) for row in exclusions)


def _entries(roots,exclusions):
    def fail(error):raise error
    for name,root in roots.items():
        for current,dirs,files in os.walk(root,followlinks=False,onerror=fail):
            base=Path(current);keep=[]
            for child in sorted(dirs+files):
                path=base/child;rel=path.relative_to(root).as_posix()
                if _excluded(name,rel,exclusions):continue
                info=path.lstat()
                if linked(info):kind='link'
                elif stat.S_ISDIR(info.st_mode):kind='directory';keep.append(child)
                elif stat.S_ISREG(info.st_mode):kind='file'
                else:raise ValueError('Специальный файл требует отдельного решения: '+str(path))
                yield name,root,rel,path,info,kind
            dirs[:]=keep


def _fingerprint(info):
    # Windows lstat/fstat disagree on ctime (creation/change time on CPython
    # 3.14); inode, size and last-write time remain comparable across both APIs.
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,
            None if os.name=='nt' else info.st_ctime_ns,stat.S_IFMT(info.st_mode))


def _reference(path,roots,exclusions):
    try:target=path.resolve(strict=True)
    except (OSError,RuntimeError):raise ValueError('Ссылка не разрешается; укажи её место при переносе: '+str(path)) from None
    for name,root in sorted(roots.items(),key=lambda item:len(item[1].parts),reverse=True):
        if target.is_relative_to(root):
            relative=target.relative_to(root).as_posix()
            if _excluded(name,relative,exclusions):break
            return {'root':name,'path':relative,'directory':target.is_dir()}
    raise ValueError('Ссылка ведёт за пределы выбранных папок. Добавь проект в план: '+str(path))


def create(destination,raw_roots,*,exclusions=(),metadata=None):
    roots=_roots(raw_roots);destination=Path(destination).absolute()
    if any(destination.resolve().is_relative_to(root) for root in roots.values()):
        raise ValueError('Архив нельзя создавать внутри переносимой папки.')
    exclusions=[dict(row) for row in exclusions]
    for row in exclusions:
        if (row.get('root') not in roots or not checks.safe_name(row.get('path')) or
                row['path'].endswith('/') or not row.get('reason')):
            raise ValueError('Каждое исключение должно иметь путь и объяснение в плане.')
    rows=list(_entries(roots,exclusions));links=[]
    # Resolve the complete link graph before creating any archive with secrets.
    for name,root,rel,path,info,kind in rows:
        if kind=='link':links.append({'root':name,'path':rel,'target':_reference(path,roots,exclusions)})
    meta={'format':FORMAT,'roots':list(roots),'links':links,'exclusions':exclusions,'metadata':metadata or {}}
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name(destination.name+'.part-'+secrets.token_hex(6))
    write_private(temporary,b'')
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for name in roots:archive.writestr('roots/'+name+'/',b'')
            for name,root,rel,path,before,kind in rows:
                if kind=='link':continue
                arc='roots/'+name+'/'+rel+('/' if kind=='directory' else '')
                info=zipfile.ZipInfo(arc);info.create_system=3
                info.external_attr=(stat.S_IMODE(before.st_mode)&0o777 | (stat.S_IFDIR if kind=='directory' else stat.S_IFREG))<<16
                info.compress_type=zipfile.ZIP_DEFLATED
                if kind=='directory':archive.writestr(info,b'');continue
                flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
                descriptor=os.open(path,flags)
                with os.fdopen(descriptor,'rb') as source:
                    if _fingerprint(os.fstat(source.fileno()))!=_fingerprint(before) or not path.resolve().is_relative_to(root):
                        raise ValueError('Файл изменился во время снимка: '+str(path))
                    with archive.open(info,'w',force_zip64=True) as target:
                        while chunk:=source.read(1024*1024):target.write(chunk)
                    if _fingerprint(os.fstat(source.fileno()))!=_fingerprint(before):
                        raise ValueError('Файл изменился во время снимка: '+str(path))
            archive.writestr(META,(json.dumps(meta,ensure_ascii=False)+'\n').encode())
        # Detect removals/additions/replacements, including link retargeting.
        now=list(_entries(roots,exclusions))
        signature=lambda values:[(name,rel,kind,_fingerprint(info)) for name,_,rel,_,info,kind in values]
        if signature(rows)!=signature(now):raise ValueError('Содержимое папок изменилось во время снимка. Архив не завершён.')
        with zipfile.ZipFile(temporary,'a',compression=zipfile.ZIP_DEFLATED) as archive:integrity=checks.seal(archive)
        with temporary.open('r+b') as stream:os.fsync(stream.fileno());digest=hashlib.file_digest(stream,'sha256').hexdigest()
        os.replace(temporary,destination)
        return {'archive':str(destination),'sha256':digest,'integrity':integrity,'roots':list(roots),'links':len(links)}
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def inspect(archive_path,*,windows=None):
    with zipfile.ZipFile(archive_path) as archive:
        integrity=checks.verify(archive,required=True,windows=windows)
        info=archive.getinfo(META)
        if info.file_size>checks.MAX_MANIFEST:raise ValueError('План архива слишком велик.')
        meta=json.loads(archive.read(info))
        if not isinstance(meta,dict) or meta.get('format')!=FORMAT:raise ValueError('Неподдерживаемый полный архив.')
        roots=meta.get('roots')
        if not isinstance(roots,list) or len(set(roots))!=len(roots) or any(not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name) for name in roots):
            raise ValueError('Повреждены разделы архива.')
        names=set(archive.namelist());links=meta.get('links')
        on_windows=os.name=='nt' if windows is None else windows
        normalize=lambda name:checks.checked_name(name,windows=on_windows)
        occupied={normalize(name.rstrip('/')) for name in names}
        if not isinstance(links,list):raise ValueError('Повреждена опись ссылок.')
        destinations=set()
        for link in links:
            if not isinstance(link,dict) or link.get('root') not in roots or not checks.safe_name(link.get('path')):
                raise ValueError('Недопустимая ссылка в плане.')
            path='roots/'+link['root']+'/'+link['path']
            key=normalize(path)
            if key in destinations or key in occupied:raise ValueError('Ссылка пересекается с другим содержимым.')
            destinations.add(key)
            target=link.get('target') or {};rel=target.get('path')
            if target.get('root') not in roots or (rel!='.' and not checks.safe_name(rel)):
                raise ValueError('Ссылка выходит за пределы архива.')
            endpoint='roots/'+target['root']+'/'+('' if rel=='.' else rel)
            if endpoint not in names and endpoint.rstrip('/')+'/' not in names:
                raise ValueError('В архиве нет цели ссылки.')
            directory=endpoint.endswith('/') or endpoint+'/' in names
            if target.get('directory') is not directory:raise ValueError('Тип цели ссылки не совпадает с архивом.')
            if any(normalize(name).startswith(key+'/') for name in names):raise ValueError('Вложенные файлы под ссылкой запрещены.')
        files={normalize(info.filename) for info in archive.infolist() if not info.is_dir()}
        for key in destinations:
            parts=key.split('/')
            if any('/'.join(parts[:index]) in destinations|files for index in range(1,len(parts))):
                raise ValueError('Ссылка вложена в другую ссылку или файл.')
        for name in names:
            if name in (META,checks.NAME):continue
            if not any(name.startswith('roots/'+root+'/') for root in roots):raise ValueError('Файл вне заявленных разделов архива.')
        return {**meta,'integrity':integrity}


def stage(archive_path,destination,*,expected_sha256):
    """Prepare a NEW permanent payload home; never overwrite the former agent.

    The home must not be renamed after creating Windows junctions. Activation
    binds the config to these paths only after separate runtime/Git checks.
    Failed staging is retained for inspection; a retry uses another fresh home.
    """
    destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise ValueError('Папка подготовки уже существует; прежние данные не заменены.')
    if destination.parent.resolve()!=destination.parent:
        raise ValueError('Родитель папки подготовки должен быть настоящей папкой без ссылок.')
    with Path(archive_path).open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=expected_sha256:
            raise ValueError('Архив не совпадает с квитанцией переноса.')
        source.seek(0);meta=inspect(source)
        source.seek(0)
        destination.mkdir(mode=0o700)
        write_private(destination/'.payload-preparing.json',json.dumps({'sha256':expected_sha256}).encode())
        with zipfile.ZipFile(source) as archive:
            manifest=json.loads(archive.read(checks.NAME));expected={row['path']:row for row in manifest['members']}
            for info in archive.infolist():
                if not info.filename.startswith('roots/'):continue
                target=destination/info.filename
                if info.is_dir():target.mkdir(mode=0o700,parents=True,exist_ok=True);continue
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                if target.exists() or target.is_symlink():raise ValueError('Конфликт в новой папке подготовки.')
                write_private(target,b'')
                digest=hashlib.sha256();size=0
                with archive.open(info) as reader,target.open('r+b') as writer:
                    while chunk:=reader.read(1024*1024):writer.write(chunk);digest.update(chunk);size+=len(chunk)
                    writer.flush();os.fsync(writer.fileno())
                if digest.hexdigest()!=expected[info.filename]['sha256'] or size!=expected[info.filename]['bytes']:
                    raise ValueError('Содержимое архива изменилось во время подготовки.')
                # Keep user execute bits while retaining private payload access.
                if os.name!='nt':target.chmod(0o600|((info.external_attr>>16)&0o100))
        roots={name:destination/'roots'/name for name in meta['roots']}
        for link in meta['links']:
            path=roots[link['root']]/link['path'];reference=link['target']
            target=roots[reference['root']]/reference['path']
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if path.exists() or path.is_symlink():raise ValueError('Ссылка конфликтует с подготовленными файлами.')
            if not target.resolve().is_relative_to(destination):raise ValueError('Цель ссылки покидает подготовленные данные.')
            if os.name=='nt' and reference['directory']:
                import _winapi
                _winapi.CreateJunction(str(target),str(path))
            else:
                try:path.symlink_to(os.path.relpath(target,path.parent),target_is_directory=reference['directory'])
                except OSError:raise ValueError('Не удалось восстановить ссылку на этом компьютере; подготовленные данные сохранены: '+link['path']) from None
        receipt={'prepared':True,'activated':False,'sha256':expected_sha256,'roots':{name:str(path) for name,path in roots.items()},
                 'links':len(meta['links']),'metadata':meta['metadata']}
        write_private(destination/'.payload-ready.json',(json.dumps(receipt,ensure_ascii=False)+'\n').encode())
        return receipt
