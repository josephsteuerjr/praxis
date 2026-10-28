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


def receipt(root,operation):return Path(root)/'recovery'/operation_id(operation)/'status.json'


def stage(root,operation):
    """Only passport-owned program files; never walk/copy the agent data tree."""
    from .code_transition import baseline
    from . import source_merge as merge
    root=Path(root).resolve();operation=operation_id(operation)
    upstream=read(root/'code-upstream.json')
    if not upstream.get('files') or not (root/'code-upstream.zip').is_file():
        raise ValueError('Нет проверенного upstream-снимка. Открой мастер из целого архива Hélène.')
    passport=read(root/'python-package-files.json').get('files')
    if not isinstance(passport,dict) or not passport:
        raise ValueError('Нет паспорта программы. Открой мастер из целого архива Hélène.')
    staging=root.parent/('.helene-update-recovery-'+operation)
    staging.mkdir(exist_ok=False);new=staging/'Helene';new.mkdir()
    for name,digest in passport.items():
        merge.relative(name)
        # Source/data/configuration are connected/prepared by the shared updater.
        # Even an older passport must not smuggle mutable profiles into recovery.
        relative=Path(name)
        if relative.parts[0].lower() in {'tree','code','data','profile','agents','backups','archives','recovery','models','memory','.secrets'}:
            continue
        if relative.name in {'helene.json','helene.json.bak','owner-stop.json','python-migration.json','migration-ready.json'}:continue
        source=root/relative
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('Отсутствует файл программы: '+name)
        if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:
            raise ValueError('Изменён файл оболочки: '+name+'. Открой мастер из целого архива Hélène.')
        target=new/relative;target.parent.mkdir(parents=True,exist_ok=True)
        try:os.link(source,target)
        except OSError:shutil.copy2(source,target)
    # These may have been sealed after an older program passport was written.
    for name in ('code-upstream.json','code-upstream.zip'):
        target=new/name
        if not target.exists():shutil.copy2(root/name,target)
    (new/'tree').mkdir()
    baseline(root,new/'tree')  # archive SHA and every member are verified
    if merge.inventory(new/'tree')!=upstream['files']:
        raise ValueError('Upstream-снимок не соответствует сохранённому коммиту')
    required=('python-app.json','app/python_app.py','app/helene_setup.py',('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3'))
    if not all((new/name).is_file() for name in required):
        raise ValueError('Неполная поставка для восстановления; нужен целый архив Hélène')
    return new


def restore(root,*,operation=''):
    from python_migrate import installation_lock
    from . import migration_lifecycle as windows,python_source_updater as updater,update_source
    from .update_progress import Progress
    root=Path(root).resolve();operation=operation_id(operation)
    status=receipt(root,operation)
    def report(state,**values):write(status,{'operation':operation,'root':str(root),'state':state,**values})
    with installation_lock(root):
        before=None;stopped=False
        report('preparing')
        try:
            new=stage(root,operation)
            progress=Progress(new.parent.with_name(new.parent.name+'.progress.json'),operation,root)
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
    entry=root/'app/helene_setup.py'
    if not entry.is_file():raise ValueError('В этой поставке отсутствует мастер восстановления')
    with (root/'upstream-recovery.log').open('ab') as log:
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
            raise RuntimeError('Помощник восстановления не принял операцию. Подробности в upstream-recovery.log')
        time.sleep(.05)
    if not status.exists():child.terminate();child.wait(timeout=5)
    raise TimeoutError('Помощник восстановления не подтвердил запуск; подробности в upstream-recovery.log')
