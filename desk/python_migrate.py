#!/usr/bin/env python3
"""Confirmed, Python-owned migration transaction used by the installation wizard.

Discovery/unpacking is not a final snapshot. After confirmation the previous
writers stop; only then do we copy data and the actual executable self-edits.
The identified native package is removed with its registered non-purge command
only after the new Python owner confirms the preserved profile.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import time
import uuid
from localharness import app_migration, migration_lifecycle as migration_windows


def transaction(old, new, *, prepared=False):
    """Admit a new target or resume only this exact recorded handoff.

    The native Stop latch predates the application and shares its known folder;
    it must survive installation, not make that folder permanently unusable.
    """
    receipt = new/'python-migration.json'
    if receipt.is_file():
        note = json.loads(receipt.read_text(encoding='utf-8'))
        if ((Path(note.get('old_root','')).resolve()!=old if old is not None else bool(note.get('old_root'))) or
                Path(note.get('new_root','')).resolve()!=new):
            raise ValueError('This folder belongs to another migration; retained without changes')
        return note
    if prepared:
        if (new/'helene.json').exists() or not all((new/p).is_file() for p in ('python-app.json','app/python_app.py',(('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3') if os.name=='nt' else 'runtime/bin/python3'))):
            raise ValueError('Use the unpacked, unconfigured portable application for migration')
    elif new.exists():
        from localharness import owner_stop
        latch = owner_stop.client_file()
        allowed = {'owner-stop.json'} if latch is not None and latch.parent.resolve()==new else set()
        if any(p.name not in allowed for p in new.iterdir()):
            raise FileExistsError('Selected folder is not a recorded migration or an empty application folder')
    new.mkdir(parents=True,exist_ok=True)
    note={'format':'helene-python-migration.v1','id':uuid.uuid4().hex,
          'old_root':str(old) if old is not None else '', 'new_root':str(new),'state':'copying_program'}
    if old is None:note['kind']='fresh'
    app_migration.write_note(new,note)
    return note


def _program_path_identity(path):
    """Compare resolved Windows paths independently of the extended prefix.

    Windows can return either spelling while parents are created concurrently.
    Only the comparison changes; I/O keeps its original path, and resolution
    still detects links that lead outside the selected installation.
    """
    resolved=Path(path).resolve()
    if os.name!='nt':return resolved
    raw=str(resolved)
    if raw.upper().startswith('\\\\?\\UNC\\'):raw='\\\\'+raw[8:]
    elif raw.startswith('\\\\?\\') and len(raw)>6 and raw[5:7]==':\\':raw=raw[4:]
    return PureWindowsPath(raw)


def copy_program(package, new, progress, extensions=None):
    """Restartable, byte-verified program copy; never touch data/config/Stop.

    Only the publisher's named files are replaced. Existing prior program bytes
    are retained on change, including a partially extracted previous version.
    """
    def hash_file(path):
        h=hashlib.sha256()
        with path.open('rb') as stream:
            while chunk:=stream.read(1024*1024):h.update(chunk)
        return h.hexdigest()
    root_identity=_program_path_identity(new)
    extensions=set(extensions or ())
    from localharness.source_admission import copy_exclusions
    preserved=copy_exclusions(package,new)
    bundle=package/'components/xtts/xtts-bundle.json'
    xtts_files=set(json.loads(bundle.read_text(encoding='utf-8'))['files'])|{'xtts-bundle.json'} if bundle.is_file() else None
    def selected(path):
        parts=path.relative_to(package).parts
        if parts[0] in preserved:return False
        if parts[:2]==('components','toolchain'):return False
        if parts[:2]==('components','xtts'):return 'xtts' in extensions and (xtts_files is None or Path(*parts[2:]).as_posix() in xtts_files)
        if parts[0]=='voice' or parts[:2]==('components','whisper'):return 'whisper' in extensions
        return True
    files=[p for p in package.rglob('*') if p.is_file() and selected(p) and
           p.relative_to(package).parts[0] not in ('data','agents','backups','profile','code','code-backup','archives','recovery','.migration-webview','.webview') and
           '__pycache__' not in p.relative_to(package).parts and
           p.name not in ('helene.json','python-migration.json','owner-stop.json','install.log') and not p.name.startswith('.python-') and not p.name.endswith('.pyc')]
    total=sum(p.stat().st_size for p in files);done=0
    from concurrent.futures import ThreadPoolExecutor
    from localharness.parallel_files import WORKERS
    import threading
    lock=threading.Lock()
    def copy(item):
        nonlocal done
        if item.is_symlink():raise ValueError('Package symlink is unsupported: '+str(item))
        relative=item.relative_to(package);target=new/relative
        if not _program_path_identity(target).is_relative_to(root_identity):raise ValueError('Program destination leaves target: '+relative.as_posix())
        expected=hash_file(item)
        equal=target.is_file() and target.stat().st_size==item.stat().st_size and hash_file(target)==expected
        if not equal:
            if target.exists():
                saved=new/'.migration-program-previous'/relative
                if saved.exists() and hash_file(saved)!=hash_file(target):
                    saved=saved.with_name(saved.name+'.'+uuid.uuid4().hex)
                saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved)
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.migration-part')
            # Only publisher-owned checkpoint files share blocks with prepared
            # cache. Mutable source/config always receives independent bytes.
            immutable=(relative.parts[:3]==('components','xtts','model') and item.suffix=='.pth') or (relative.parts[:3]==('components','xtts','runtime') and item.suffix.lower() in ('.dll','.pyd','.exe','.lib'))
            if immutable and item.stat().st_dev==temporary.parent.stat().st_dev:
                temporary.unlink(missing_ok=True);os.link(item,temporary)
            else:shutil.copy2(item,temporary)
            if not temporary.samefile(item) and hash_file(temporary)!=expected:raise IOError('Program copy verification failed: '+str(relative))
            temporary.replace(target)
        with lock:
            done+=item.stat().st_size
            progress({'stage':'unpacking','bytes':done,'total_bytes':total,'file':relative.as_posix(),'reused':equal})

    # Resolve and validate every target before independent copy workers start.
    for item in files:
        if item.is_symlink() or not _program_path_identity(new/item.relative_to(package)).is_relative_to(root_identity):
            raise ValueError('Invalid program copy path')
    with ThreadPoolExecutor(max_workers=max(1,WORKERS-1)) as pool:
        pending=[pool.submit(copy,p) for p in files if p.stat().st_size>=256*1024]
        for item in files:
            if item.stat().st_size<256*1024:copy(item)
        for future in pending:future.result()


def retain_attempt_configs(new, note):
    """Keep a failed attempt intact before a fresh stopped-old snapshot."""
    app_migration.retain_attempt_configs(new,note)


def install_selected_extensions(program,note,extensions,progress):
    """Downloading a voice bundle also prepares it for this selected data tree."""
    if 'xtts' in set(extensions or ()):
        from localharness.xtts_install import install
        progress('installing_xtts')
        for tree in dict.fromkeys(note['data_paths']):install(Path(tree),program=program)


def finish_retirement(old, package, new, note, progress):
    """Repeatable integration cleanup, never a second application launch."""
    if note.get('installation_flow')=='attached':
        from localharness.windows_installation import finish
        return finish(new,note,remove_old=note.get('remove_old',False),progress=progress)
    migration_windows.quiescent(old)
    progress('retiring_old')
    program_markers=('helene.exe','Helene.exe','helene-setup.exe','uninstall.exe',
                     'python-app.json','app/python_app.py','app/deskapp.py','deskapp.py',(('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3') if os.name=='nt' else 'runtime/bin/python3'))
    if (note.get('startup_proof') and note.get('source_policy')=='clean'
            and not any((old/name).exists() for name in program_markers)):
        integrations=migration_windows.retire(old,new)
        note.update(integrations=integrations,retirement={'ok':True,'method':'previous_program_already_absent','purge':False},state='completed')
        app_migration.write_note(new,note);progress('completed');return note
    if note.get('startup_proof') and (command:=migration_windows.registered_uninstall(old)):
        retirement=migration_windows.uninstall_registered(old,command)
        integrations=migration_windows.retire(old,new)
        note.update(integrations=integrations,retirement=retirement,state='completed')
        app_migration.write_note(new,note);progress('completed');return note
    integrations=migration_windows.retire(old,new)
    inventory=old/'python-package-files.json'
    if not inventory.is_file():inventory=package/'migration/legacy-package-files.json'
    retirement={'ok':False,'reason':'publisher_inventory_unavailable','old_program_retained':True}
    if inventory.is_file():
        files=json.loads(inventory.read_text(encoding='utf-8'))['files']
        if not isinstance(files,dict):raise ValueError('Retirement requires hash-bound publisher inventory')
        retirement=app_migration.retire_program(new,note['id'],files)
    note.update(integrations=integrations,retirement=retirement,
                state='completed' if retirement.get('ok') else 'retirement_incomplete')
    app_migration.write_note(new,note)
    progress(note['state'])
    return note


def retained_startup(new,note):
    from localharness import owner_stop
    proof=note.get('startup_proof') or {}
    if proof.get('stopped'):
        if not owner_stop.stopped():raise ValueError('Owner Stop changed; inspect current application before retirement')
    elif proof.get('remote'):
        from localharness.python_controller import Controller
        Controller(new,lambda *args:None).readiness()
    else:
        # A stored PID is not fresh startup proof. Query the current identified
        # channel without launching anything or signalling any process.
        from localharness.essential_backup import load
        from localharness.python_updates import request_json
        from localharness.desktop_supervisor import _token
        cfg=load(new/'helene.json');port=int(cfg.get('port') or 8094)
        who=request_json(f'http://127.0.0.1:{port}/api/who')
        if who.get('product')!='Hélène' or Path(who.get('root','')).resolve()!=new:
            raise ValueError('Current new application identity is unconfirmed; old files retained')
        import urllib.parse
        home=request_json(f'http://127.0.0.1:{port}/api/home?key='+urllib.parse.quote(_token(Path(cfg['tree']))))
        if Path(home.get('tree','')).resolve() not in [Path(p).resolve() for p in note['data_paths']]:
            raise ValueError('Current application data differs; old files retained')


def resume_retirement(old, package, new, note, progress):
    retained_startup(new,note)
    note['state']='new_app_started'
    app_migration.write_note(new,note)
    return finish_retirement(old,package,new,note,progress)


def migrate(old: Path, package: Path, new: Path, data: Path, *, stop_service=False,
            launch=True, progress=None, extensions=None,resume_copy=None,prepared=False,source_mode='preserve',portable=False,
            attached_data=None,remove_old=False,review_cancelled=None,force_extensions=False):
    old,package,new,data=(p.resolve() for p in (old,package,new,data))
    progress=progress or (lambda stage:None)
    if source_mode not in ('preserve','clean'):raise ValueError('Choose preserve or clean migration source')
    if new != package and new.is_relative_to(package):raise ValueError('Choose a working folder outside the unpacked source')
    app_migration.discover(old)  # read-only preflight; never reused as final state
    if new.is_relative_to(old) or old.is_relative_to(new) or (not prepared and (new==package or package.is_relative_to(new))):
        raise ValueError('Old, new and unpacked package must be separate')
    if prepared and new!=package:raise ValueError('Portable migration stays in this unpacked application')
    prior=transaction(old,new,prepared=prepared)
    if prior.get('state')!='copying_program' and prior.get('source_policy','preserve')!=source_mode:
        raise ValueError('Continue this migration with its original code choice')
    prior['source_policy']=source_mode;app_migration.write_note(new,prior)
    if prior['state']=='completed':return prior
    if prior['state'] in ('new_app_started','retirement_incomplete'):
        return resume_retirement(old,package,new,prior,progress)
    if not prepared:
        progress('unpacking')
        copy_program(package,new,progress,extensions)
    from localharness.source_admission import activate
    activate(new,lease_held=True)  # migration CLI owns this target lease
    if source_mode=='clean' or attached_data is not None:
        from localharness.migration_source import verify
        verify(new)  # Reject invalid incoming code before stopping the old owner.
    if attached_data is not None:
        from localharness.windows_installation import WizardProgress
        selected_data=Path(attached_data).resolve()
        if not selected_data.is_dir():raise ValueError('Выбери существующую папку данных агента')
        if selected_data==new or new.is_relative_to(selected_data):raise ValueError('Папка данных не должна содержать новую программу')
    if source_mode=='preserve':
        from localharness.windows_installation import WizardProgress
        from localharness.python_updates import prepare_code
        prepare_code(old,new,WizardProgress(progress),source_mode,cancelled=review_cancelled,
                     require_review=True,resolution_owner='agent',forceExtensions=force_extensions)
    from localharness.update_extensions import check
    progress('checking_extensions')
    check(old,new,force=force_extensions)
    progress('stopping_old')
    before=migration_windows.service(old)
    if before and not stop_service:
        raise RuntimeError('Explicit service handoff is required; prepared package retained')
    child=None
    retiring=False
    try:
        stopped=migration_windows.stop(old) if stop_service else {'service':None}
        migration_windows.quiescent(old)
        reuse=json.loads(Path(resume_copy).read_text(encoding='utf-8')) if resume_copy else prior if prior.get('data_paths') else None
        if reuse:
            migration_windows.quiescent(Path(reuse['new_root']))
        progress('preserving')
        retain_attempt_configs(new,prior)
        if attached_data is not None:
            from localharness.windows_installation import archive_sources,prepare_attached
            archives=archive_sources(old,new,prior['id'],progress)
            note=prepare_attached(old,new,selected_data,prior['id'],previous_sources=archives,source_mode=source_mode,progress=progress)
            note.update(remove_old=bool(remove_old),installation_flow='attached')
            app_migration.write_note(new,note)
        else:
            note=app_migration.prepare(old,new,data,old_service_stopped=bool(stopped['service']) or stop_service,progress=progress,reuse=reuse,
                                       independent=prepared or portable,copy_external=prepared or portable,source_policy=source_mode)
        if source_mode=='preserve' and attached_data is None:
            from localharness.code_transition import prepare,validate
            from localharness.windows_installation import WizardProgress
            prepare(old,new,note,progress=WizardProgress(progress));validate(new)
            note['state']='prepared';app_migration.write_note(new,note)
        if source_mode=='clean' and attached_data is None:
            from localharness.migration_source import prepare as prepare_clean_code
            note=prepare_clean_code(old,new,note)
        install_selected_extensions(new,note,extensions,progress)
        if not launch:return note
        interpreter=new/((('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3') if os.name=='nt' else 'runtime/bin/python3') if os.name=='nt' else 'runtime/bin/python3')
        log=new/'migration-start.log'
        from localharness.desktop_supervisor import JobChild
        progress('starting_new')
        from localharness.python_platform import desktop_argv
        child=JobChild.spawn(desktop_argv(new,['--root',str(new),'--migration',note['id']]),
                             cwd=new,env=dict(os.environ),log=log,stopped=lambda:False)
        # The transaction holds an outer Job until the new Python owner proves its
        # own children. Failure kills it before any return to the previous owner.
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            current=json.loads((new/'python-migration.json').read_text(encoding='utf-8'))
            if current.get('state')=='new_app_started':
                retiring=True
                current=finish_retirement(old,package,new,current,progress)
                # New app must outlive the wizard. Closing only this transaction's
                # outer Job with kill-on-close disabled retains the app's own Jobs.
                child.release()
                return current
            if child.poll() is not None:raise RuntimeError('New app exited before channel/runner proof')
            time.sleep(.3)
        from localharness.package_notice import read
        ready=read(new/'migration-ready.json')
        detail=str(ready.get('error') or '') if ready.get('id')==note['id'] else ''
        raise RuntimeError('New startup not confirmed'+(': '+detail if detail else '')+'; old files retained')
    except BaseException:
        # Once retirement begins, the proven new owner is the only available
        # working owner. A failed shortcut/registry cleanup must not kill it.
        if child is not None and child.handle is not None:
            if retiring:child.release()
            else:child.stop()
        receipt=new/'python-migration.json'
        current=json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {'old_root':str(old),'new_root':str(new)}
        current.update(state='retirement_incomplete' if retiring else 'failed_before_retirement',old_service_was_running=bool(before and before['State']!='Stopped'))
        if not retiring:
            try:current['old_service_restored']=migration_windows.restore_service(old,before)
            except Exception as exc:current['recovery_error']=str(exc)
        app_migration.write_note(new,current)
        raise


def fresh(package:Path,new:Path,data:Path,*,extensions=None,launch=True,progress=None,shortcuts=None,
          prepared=False,data_exact=False,connect_data=False,desktop_shortcut=False,bootstrap_id=''):
    """New ordinary app; no previous service and no model work before first setup."""
    package,new,data=(Path(p).resolve() for p in (package,new,data))
    progress=progress or (lambda value:None)
    if not prepared and (new==package or new.is_relative_to(package) or package.is_relative_to(new)):raise ValueError('Use a separate permanent application folder')
    if prepared and new!=package:raise ValueError('Prepared application must stay in its selected folder')
    from localharness.package_notice import read
    prior=read(new/'python-migration.json')
    if data_exact:
        if data==new or new.is_relative_to(data):raise ValueError('Data folder must not contain the program')
        recorded=prior.get('kind')=='fresh' and prior.get('new_root')==str(new) and str(data) in prior.get('data_paths',[])
        if prior.get('data_paths') and not recorded:raise ValueError('Этот мастер уже привязан к другой папке данных; выбери новую рабочую папку')
        if connect_data:
            if not data.is_dir():raise ValueError('Выбери существующую папку данных агента')
        elif not recorded and data.exists() and any(data.iterdir()):
            from localharness import migration_job
            if not migration_job.owns_empty_seed(data,bootstrap_id):
                raise ValueError('Для нового агента выбери пустую папку данных')
    receipt=new/'python-migration.json'
    if receipt.is_file():
        note=json.loads(receipt.read_text(encoding='utf-8'))
        if note.get('kind')!='fresh' or Path(note['new_root']).resolve()!=new:raise ValueError('Folder belongs to another installation transaction')
        if note['state']=='completed':return note
        if note['state']=='new_app_started':
            retained_startup(new,note)
            note.update(shortcuts=migration_windows.install_shortcuts(new,shortcuts,desktop_only=desktop_shortcut),state='completed')
            app_migration.write_note(new,note);return note
    else:
        note=transaction(None,new,prepared=prepared)
        note.update(kind='fresh',old_root='',data_paths=[],source_paths=[])
        app_migration.write_note(new,note)
    if not prepared:copy_program(package,new,progress,extensions)
    from localharness.source_admission import activate
    activate(new,lease_held=True)  # before any fresh profile/source seeding
    if data_exact:
        from localharness.migration_source import verify
        verify(new)
    config=new/'helene.json'
    if not config.exists():
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        tree=data if data_exact else data/note['id'];tree.mkdir(parents=True,exist_ok=True)
        value={'mode':'local','tree':str(tree),'code':str(new/'tree'),'port':port,
               'python':str(new/(('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3') if os.name=='nt' else 'runtime/bin/python3')),'app':str(new/'app/deskapp.py'),
               'runner':str(new/'app/localharness/runner.py'),'setup_complete':False,
               'model':{'framework':'openai','base_url':'','model':'','key':''},
               'relay':{'enabled':False},'voice':{'enabled':False},'installed':{'service':False}}
        from localharness.portable_profile import relative_config
        value=relative_config(value,config,new)
        config.write_text(json.dumps(value,indent=2),encoding='utf-8')
    value=json.loads(config.read_text(encoding='utf-8'))
    from localharness.essential_backup import tree_path
    note.update(data_paths=[str(tree_path(config,value))],source_paths=[{'old':'','selected':str(app_migration.code_path(config,value))}])
    note['state']='prepared';app_migration.write_note(new,note)
    install_selected_extensions(new,note,extensions,progress)
    if not launch:return note
    from localharness.desktop_supervisor import JobChild
    child=None;started=False
    try:
        progress('starting_new')
        from localharness.python_platform import desktop_argv
        child=JobChild.spawn(desktop_argv(new,['--root',str(new),'--migration',note['id']]),
                             cwd=new,env=dict(os.environ),log=new/'migration-start.log',stopped=lambda:False)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            current=json.loads(receipt.read_text(encoding='utf-8'))
            if current.get('state')=='new_app_started':
                started=True
                current.update(shortcuts=migration_windows.install_shortcuts(new,shortcuts,desktop_only=desktop_shortcut),state='completed')
                app_migration.write_note(new,current);child.release();progress('completed');return current
            if child.poll() is not None:raise RuntimeError('Fresh application exited; installation retained for retry')
            time.sleep(.3)
        raise TimeoutError('New application window is unconfirmed; prepared installation retained')
    except BaseException:
        if child is not None and child.handle is not None:
            if started:child.release()
            else:child.stop()
        raise


@contextmanager
def installation_lock(target, *, timeout_sec=0):
    """Two wizard processes may not copy/switch the same permanent folder."""
    from localharness.phone_tunnel.platform_support import acquire_file_lock,release_file_lock
    target=Path(target).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256(os.path.normcase(str(target)).encode()).hexdigest()[:24]
    if os.name=='nt':state=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')/'Helene'
    elif sys.platform=='darwin':state=Path.home()/'Library/Application Support/Helene'
    else:state=Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'helene'
    directory=state/'installation-locks';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=acquire_file_lock(directory/(identity+'.lock'),timeout_sec=timeout_sec)
    legacy=None
    try:
        # Also fence older helpers whenever their historical parent lock is
        # writable. A root-owned /opt parent cannot host that old lock; all new
        # fresh/update/recovery consumers still share the same user-state lock.
        try:legacy=acquire_file_lock(target.parent/('.helene-install-'+identity+'.lock'),timeout_sec=timeout_sec)
        except Exception as exc:
            import errno
            cause=exc.__cause__
            if not isinstance(cause,OSError) or cause.errno not in (errno.EACCES,errno.EPERM,errno.EROFS):raise
        yield
    finally:
        release_file_lock(legacy);release_file_lock(lock)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--update',action='store_true')
    p.add_argument('--dir',type=Path,required=True)
    p.add_argument('--new-dir',type=Path)
    p.add_argument('--data-home',type=Path)
    p.add_argument('--stop-service',action='store_true')
    p.add_argument('--confirmed',action='store_true',help='explicit wizard confirmation, never inferred from --update')
    p.add_argument('--no-launch',action='store_true')
    p.add_argument('--fresh',action='store_true',help='New per-user installation, no legacy service handoff')
    p.add_argument('--in-place',action='store_true',help='Update an existing Python application in its current folder')
    p.add_argument('--resume-copy',type=Path,help='Explicit journal of an earlier failed, unstarted copy of the same installation')
    p.add_argument('--shortcut-dir',type=Path,help='Optional per-user/portable shortcut folder for a fresh installation')
    p.add_argument('--extensions',default='',help='Optional downloads selected by the owner: xtts,whisper,toolchain')
    a=p.parse_args()
    if not a.confirmed:raise ValueError('Open the migration wizard and confirm the switch')
    package=Path(__file__).resolve().parent.parent
    home=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))
    def progress(stage):print(json.dumps(stage if isinstance(stage,dict) else {'stage':stage}),flush=True)
    with installation_lock(a.dir if a.in_place else (a.new_dir or home/'Helene')):
        if a.in_place:
            if not (a.dir/'python-app.json').is_file():raise ValueError('Selected folder is not a Python application')
            if a.new_dir and a.new_dir.resolve()!=a.dir.resolve():raise ValueError('An in-place update keeps the current working folder')
            staging=a.dir.resolve().parent/('.helene-update-'+uuid.uuid4().hex)
            staged=staging/'Helene';staged.mkdir(parents=True)
            copy_program(package,staged,progress,a.extensions.split(','))
            from localharness.update_extensions import check
            progress('checking_extensions')
            check(a.dir,staged)
            # This CLI already owns the installation lease. Run the shared
            # transaction in this process instead of asking a child to reacquire
            # the same non-reentrant kernel lock after the old owner was stopped.
            from localharness import python_source_updater
            from localharness.update_progress import Progress
            from localharness.update_window import run as observe_update
            import threading
            from types import SimpleNamespace
            import contextlib
            operation=staging.name.removeprefix('.helene-update-')
            receipt=Progress(staging.with_name(staging.name+'.progress.json'),operation,a.dir)
            python_source_updater.prepare_install(a.dir,staged,progress=receipt)
            ready=threading.Event()
            updater_log=staging.with_suffix('.log')
            def switch():
                if not ready.wait(15):raise TimeoutError('Update observer did not open; previous application remains open')
                python_source_updater.validate_prepared(a.dir,staged)
                migration_windows.stop(a.dir);migration_windows.quiescent(a.dir)
                with updater_log.open('w',encoding='utf-8') as output,contextlib.redirect_stdout(output):
                    return python_source_updater.perform(SimpleNamespace(old=a.dir,new=staged,pid=0,
                        staging=staging,archive=[],cleanup=False,browser=False,observer_ready=ready))
            observe_update(switch,receipt.path,ready)
            note=json.loads((a.dir/'python-migration.json').read_text(encoding='utf-8'))
        elif a.fresh:
            note=fresh(package,a.new_dir or home/'Helene',a.data_home or home/'Helene-Data',
                       extensions=a.extensions.split(','),launch=not a.no_launch,progress=progress,shortcuts=a.shortcut_dir)
        else:
            note=migrate(a.dir,package,a.new_dir or home/'Helene',a.data_home or home/'Helene-Data',
                         stop_service=a.stop_service,launch=not a.no_launch,progress=progress,extensions=a.extensions.split(','),resume_copy=a.resume_copy)
    print(json.dumps({k:note[k] for k in ('id','state','old_root','new_root')},ensure_ascii=True),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=True),flush=True)
        raise SystemExit(1)
