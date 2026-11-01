"""Explicit owner Resume, including a stop left by a removed native version."""
from pathlib import Path
import hashlib,json,os,stat,uuid
from . import owner_stop


def _stamp(value):return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns)


def _snapshot(path):
    if path is None:return None
    from .first_start import plain
    path=plain(path)
    try:before=path.stat()
    except FileNotFoundError:return None
    if not stat.S_ISREG(before.st_mode) or before.st_size>65536 or before.st_nlink!=1:
        raise RuntimeError('Необычный файл аварийного стопа; состояние сохранено')
    with path.open('rb') as stream:
        raw=stream.read(65537);opened=os.fstat(stream.fileno())
    after=plain(path).stat()
    if _stamp(before)!=_stamp(opened) or _stamp(before)!=_stamp(after) or len(raw)>65536:
        raise RuntimeError('Аварийный стоп изменился; повтори явное возобновление')
    return {'identity':_stamp(before),'sha256':hashlib.sha256(raw).hexdigest()}


def _restore(claimed,path):
    """Restore a newly observed Stop without replacing any newer marker."""
    from .data_files import unlink_regular
    try:os.link(claimed,path)
    except FileExistsError:pass
    except OSError:
        try:
            with path.open('xb') as output:
                output.write(claimed.read_bytes());output.flush();os.fsync(output.fileno())
        except FileExistsError:pass
    if not owner_stop.present(path):raise RuntimeError('Не подтверждено сохранение нового аварийного стопа')
    unlink_regular(claimed)


def _remove(path,expected):
    if path is None:return False
    path=Path(path)
    if expected is None:
        if owner_stop.present(path):raise RuntimeError('Появился новый аварийный стоп; запуск отменён')
        return False
    from .first_start import plain
    from .data_files import unlink_regular
    plain(path)
    claimed=path.with_name(path.name+'.resume-'+uuid.uuid4().hex)
    path.rename(claimed)
    try:
        if _snapshot(claimed)!=expected:
            raise RuntimeError('Аварийный стоп обновлён; запуск отменён')
        unlink_regular(claimed)
    except BaseException:
        if os.path.lexists(claimed):_restore(claimed,path)
        raise
    return True


def _clear_machine(expected):
    """Elevate only deletion of the OS-known legacy control file."""
    from .migration_windows import run_privileged
    script=r'''$ErrorActionPreference='Stop'
$common=[Environment]::GetFolderPath([Environment+SpecialFolder]::CommonApplicationData)
if(!$common){throw 'Common application data is unavailable'}
$path=Join-Path $common 'Helene\stop.json'
$cursor=$path
while($cursor){if(Test-Path -LiteralPath $cursor){$item=Get-Item -LiteralPath $cursor -Force;if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked stop path refused'}};$next=Split-Path -Parent $cursor;if($next -eq $cursor){break};$cursor=$next}
if(Test-Path -LiteralPath $path){
 $item=Get-Item -LiteralPath $path -Force
 if($item.PSIsContainer -or $item.Length -gt 65536 -or $item.LinkType){throw 'Unexpected stop file'}
 $claim=$path+'.resume-'+[Guid]::NewGuid().ToString('N')
 [IO.File]::Move($path,$claim)
 try{
  if($env:HELENE_STOP_SHA256 -and (Get-FileHash -LiteralPath $claim -Algorithm SHA256).Hash.ToLowerInvariant() -ne $env:HELENE_STOP_SHA256){throw 'Stop changed while waiting for permission'}
  Remove-Item -LiteralPath $claim -ErrorAction Stop
 }catch{
  if(Test-Path -LiteralPath $claim){if(!(Test-Path -LiteralPath $path)){[IO.File]::Move($claim,$path)}}
  throw
 }
}
'''
    try:
        run_privileged(script,{'HELENE_STOP_SHA256':expected['sha256'] if expected else ''},
                       cwd=os.environ.get('SYSTEMROOT'))
    except RuntimeError as error:
        raise RuntimeError('Windows не разрешила снять общий аварийный стоп. Движок остаётся остановлен.') from error


def clear(*,cancelled=lambda:False):
    """Stop stays latched until explicit Resume commits all captured markers."""
    machine=owner_stop.seed_file() if os.name=='nt' else None
    client=owner_stop.client_file()
    legacy=owner_stop.legacy_client_file() if os.name=='nt' else None
    if legacy==client:legacy=None
    def check():
        if cancelled():raise InterruptedError('Выход из программы отменил возобновление')
    check()
    unreadable=False
    try:system=_snapshot(machine)
    except PermissionError:system=None;unreadable=True
    local=_snapshot(client);old=_snapshot(legacy)
    if system is None and local is None and old is None and not unreadable:return []
    if client is None:raise RuntimeError('Не определена папка аварийного стопа владельца')
    client=Path(client)
    # Hold a user-level Stop through an optional Windows permission dialog.
    # A concurrent Panic replaces this generation and therefore cancels Resume.
    if local is None:
        from .first_start import plain
        plain(client);client.parent.mkdir(parents=True,exist_ok=True)
        try:
            raw=json.dumps({'id':uuid.uuid4().hex,'via':'resume-in-progress','by':'owner'}).encode('utf-8')
            with client.open('xb') as stream:
                stream.write(raw)
                stream.flush();os.fsync(stream.fileno())
                local={'identity':_stamp(os.fstat(stream.fileno())),'sha256':hashlib.sha256(raw).hexdigest()}
        except FileExistsError:raise RuntimeError('Появился новый аварийный стоп; запуск отменён')
        if _snapshot(client)!=local:raise RuntimeError('Появился новый аварийный стоп; запуск отменён')
    removed=[]
    check()
    if system is not None or unreadable:
        try:
            if unreadable:raise PermissionError('Legacy system Stop requires Windows permission')
            _remove(machine,system)
        except PermissionError:_clear_machine(system)
        if owner_stop.present(machine):raise RuntimeError('Windows не подтвердила снятие общего аварийного стопа')
        removed.append('legacy-machine')
    check()
    if _remove(legacy,old):removed.append('legacy-user')
    check()
    if _remove(client,local):removed.append('user')
    if owner_stop.stopped():raise RuntimeError('Сохраняется новый аварийный стоп; запуск отменён')
    check()
    return removed
