"""Restore installed pristine upstream through the ordinary clean-code updater.

No reset of a running Git worktree, no download and no data-folder snapshot.
Current data paths/settings and old code preimages use the existing transaction.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid

from .package_notice import read,write


def operation_id(value=''):
    value=value or uuid.uuid4().hex
    if len(value)!=32 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Invalid upstream recovery identity')
    return value


def receipt(root,operation):
    from .program_state import directory
    operation=operation_id(operation)
    return directory(root,'recovery')/('upstream-'+operation+'.json')


def modern(root):
    from .portable_switch import enabled
    return enabled(root)


def stage(root,operation,*,check=None,progress=None):
    """Only passport-owned program files; never walk/copy the agent data tree."""
    from .code_transition import baseline
    from . import source_merge as merge,authored_baseline
    from .first_start import plain
    root=Path(root).resolve();operation=operation_id(operation)
    upstream=read(root/'code-upstream.json')
    if not upstream.get('files') or not (root/'code-upstream.zip').is_file():
        raise ValueError('Нет проверенного upstream-снимка. Открой мастер из целого архива Hélène.')
    passport=read(root/'python-package-files.json').get('files')
    if not isinstance(passport,dict) or not passport:
        raise ValueError('Нет паспорта программы. Открой мастер из целого архива Hélène.')
    v2=bool(read(root/'python-app.json').get('source_update_protocol')) or any(
        (root/name).exists() for name in ('app-upstream.zip','app-upstream.json','source-update-baseline.json'))
    if check:check()
    if v2:authored_baseline.validate(root)
    if check:check()
    from .program_state import staging as private_staging
    staging=private_staging(root,'recovery-'+operation)
    staging.mkdir(exist_ok=False)
    try:
        new=staging/'Helene';new.mkdir()
        return _populate(root,new,passport,upstream,v2,check=check,progress=progress)
    except BaseException:
        from .python_updates import _discard_extraction
        _discard_extraction(SimpleNamespace(root=root),staging)
        raise


def _populate(root,new,passport,upstream,v2,*,check=None,progress=None):
    from .code_transition import baseline
    from . import source_merge as merge,authored_baseline
    from .first_start import plain
    entries=[]
    for name,digest in passport.items():
        merge.relative(name)
        # Source/data/configuration are connected/prepared by the shared updater.
        # Even an older passport must not smuggle mutable profiles into recovery.
        relative=Path(name)
        if v2 and relative.parts[0].lower()=='app':continue
        if relative.parts[0].lower() in {'tree','code','data','profile','agents','backups','archives','recovery','models','memory','.secrets'}:
            continue
        if relative.name in {'helene.json','helene.json.bak','owner-stop.json','python-migration.json','migration-ready.json'}:continue
        entries.append((name,relative,digest))
    last_report=-float('inf')
    def report_files(done,*,force=False):
        nonlocal last_report
        now=time.monotonic()
        if progress and (force or now-last_report>=1):
            progress.phase('preparing',label='Готовлю чистую программу',
                           program_files_done=done,program_files_total=len(entries))
            last_report=now
    report_files(0,force=True)
    for done,(name,relative,digest) in enumerate(entries,1):
        if check:check()
        source=plain(root/relative)
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('Отсутствует файл программы: '+name)
        digest=digest.get('sha256') if isinstance(digest,dict) else digest
        if not isinstance(digest,str) or hashlib.sha256(source.read_bytes()).hexdigest()!=digest:
            raise ValueError('Изменён файл оболочки: '+name+'. Открой мастер из целого архива Hélène.')
        if check:check()
        target=new/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if relative.parts[0].lower()=='runtime':
            try:os.link(source,target)
            except OSError:shutil.copy2(source,target)
        else:shutil.copy2(source,target)
        report_files(done)
    report_files(len(entries),force=True)
    # These may have been sealed after an older program passport was written.
    carriers=('code-upstream.json','code-upstream.zip')+(
        ('app-upstream.zip','app-upstream.json','source-update-baseline.json') if v2 else ())
    for name in carriers:
        if check:check()
        target=new/name
        if not target.exists():shutil.copy2(plain(root/name),target)
    # A passport cannot include its own digest. Preserve its exact declaration.
    shutil.copy2(plain(root/'python-package-files.json'),new/'python-package-files.json')
    (new/'tree').mkdir()
    if check:check()
    if progress:progress.phase('code',label='Извлекаю чистые исходники программы')
    if v2:
        authored_baseline.extract(root,'tree',new/'tree')
        if check:check()
        authored_baseline.extract(root,'app',new/'app')
        if check:check()
        authored_baseline.validate(new)
    else:baseline(root,new/'tree')  # archive SHA and every member are verified
    if merge.inventory(new/'tree')!=upstream['files']:
        raise ValueError('Upstream-снимок не соответствует сохранённому коммиту')
    declaration=read(new/'source-admission.json')
    if declaration:
        from .source_admission import FORMAT,_upstream
        verified,_=_upstream(new,declaration)
        if merge.inventory(new/'tree')!=verified['files']:
            raise ValueError('Восстановленный код не соответствует эталону выпуска')
        # This transaction just constructed and verified B itself. Carry that
        # fact into the new helper; its tree is no longer a legacy custody view.
        write(new/'source-admission-state.json',{'format':FORMAT,
            'identity':merge.signature(declaration),'state':'ready',
            'provenance':'verified upstream recovery staging'})
    required=('python-app.json','app/python_app.py','app/helene_setup.py',('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3'))
    if not all((new/name).is_file() for name in required):
        raise ValueError('Неполная поставка для восстановления; нужен целый архив Hélène')
    return new


def _require_owner(owner):
    from . import owner_stop,session_exit
    stop=getattr(owner,'stop',None);cancelled=getattr(owner,'update_cancelled',None)
    if (getattr(owner,'exiting',False) or (stop is not None and stop.is_set())
            or owner_stop.stopped() or session_exit.requested(owner.root) or (cancelled and cancelled())):
        raise InterruptedError('Владелец остановил восстановление. Прежняя программа сохранена.')


def restore_owned(owner,*,operation=''):
    """Prepare in the authenticated owner; only NEW runtime owns the trial."""
    if not modern(owner.root):return launch(owner.root)
    from . import python_updates,program_state
    from .update_progress import Progress
    root=Path(owner.root).resolve();job=getattr(owner,'update_job',None)
    if job:
        if (job.get('operation')!='restore_upstream' or job.get('kind')!='install'
                or job.get('source_mode')!='clean' or job.get('program')!=str(root)
                or operation and operation!=job['id']):
            raise ValueError('Restore differs from the admitted clean installation job')
        operation=job['id']
    operation=operation_id(operation)
    target=os.path.normcase(str(root))
    with python_updates._update_guard:
        if target in python_updates._update_targets:
            raise RuntimeError('Python update is already preparing or handed off for this installation')
        python_updates._update_targets.add(target)
    new=None;progress=None
    try:
        _require_owner(owner)
        progress=Progress(program_state.progress(root,operation),operation,root,event=getattr(owner,'event',None))
        progress.phase('preparing',label='Возвращаю чистые исходники')
        if job:
            from .desktop_update_request import checkpoint
            checkpoint(owner,job,phase='preparing',
                staging=str(program_state.staging(root,'recovery-'+operation)),progress=str(progress.path))
        new=stage(root,operation,check=lambda:_require_owner(owner),progress=progress)
        _require_owner(owner)
        return python_updates._prepare_handoff(owner,new.parent,{},progress,False,sourceMode='clean')
    except BaseException as exc:
        if new is not None and new.parent.exists():python_updates._discard_extraction(owner,new.parent)
        with python_updates._update_guard:python_updates._update_targets.discard(target)
        if progress:progress.phase('failed',error=str(exc),previous_application_open=True)
        raise


def restore(root,*,operation=''):
    from python_migrate import installation_lock
    from . import migration_lifecycle as windows,python_source_updater as updater,update_source
    from .update_progress import Progress
    root=Path(root).resolve();operation=operation_id(operation)
    if modern(root):
        raise ValueError('В портативной программе восстановление запускается из её окна через владельца сеанса. Открой приложение или новый целый архив Hélène.')
    status=receipt(root,operation)
    def report(state,**values):write(status,{'operation':operation,'root':str(root),'state':state,**values})
    with installation_lock(root):
        before=None;stopped=False
        report('preparing')
        try:
            new=stage(root,operation)
            from .program_state import progress as private_progress
            progress=Progress(private_progress(root,operation),operation,root)
            update_source.select(root,new,'clean',by='window')
            updater.prepare_install(root,new,progress=progress)
            updater.validate_prepared(root,new)
            report('prepared',upstream_source_sha256=read(root/'code-upstream.json')['sha256'])
            before=windows.service(root)
            stopped=True  # restoration is also required after a partial stop
            windows.stop(root);windows.quiescent(root)
            ready=threading.Event();ready.set()
            updater.perform(SimpleNamespace(old=root,new=new,pid=0,staging=new.parent,
                archive=[],cleanup=False,browser=False,observer_ready=ready))
            report('completed',upstream_source_sha256=read(root/'code-upstream.json')['sha256'],data_preserved=True)
            return 0
        except BaseException as exc:
            extra={}
            if stopped:
                try:extra['service_restored']=windows.restore_service(root,before)
                except Exception as recovery:extra['service_error']=str(recovery)
            report('failed',error=str(exc),**extra)
            raise


def launch(root):
    """The live window hands recovery to a helper that can outlive its exit."""
    from .update_process import interpreter_for,background_flags
    root=Path(root).resolve();operation=operation_id()
    if modern(root):
        raise ValueError('Восстановление портативной программы требует действующего владельца сеанса. Открой приложение или новый целый архив Hélène.')
    entry=root/'app/helene_setup.py'
    if not entry.is_file():raise ValueError('В этой поставке отсутствует мастер восстановления')
    from .program_state import directory
    log_path=directory(root,'logs')/'upstream-recovery.log'
    with log_path.open('ab') as log:
        child=subprocess.Popen([str(interpreter_for(root)),'-s',str(entry),'--root',str(root),
            '--restore-upstream','--confirmed','--operation',operation],cwd=root,stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,creationflags=background_flags(),start_new_session=os.name!='nt')
    status=receipt(root,operation);deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        value=read(status)
        if value.get('operation')==operation and value.get('root')==str(root):
            if value.get('state')=='failed':raise RuntimeError(value.get('error') or 'Восстановление не началось')
            return {'state':value['state'],'operation':operation,'pid':child.pid,'status':str(status)}
        if child.poll() is not None:
            raise RuntimeError('Помощник восстановления не принял операцию. Подробности: '+str(log_path))
        time.sleep(.05)
    if not status.exists():child.terminate();child.wait(timeout=5)
    raise TimeoutError('Помощник восстановления не подтвердил запуск; подробности: '+str(log_path))
