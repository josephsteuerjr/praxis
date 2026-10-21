#!/usr/bin/env python3
"""Confirmed, Python-owned migration transaction used by the installation wizard.

Discovery/unpacking is not a final snapshot. After confirmation the previous
writers stop; only then do we copy data and the actual executable self-edits.
No recursive legacy uninstaller is invoked.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from localharness import app_migration, migration_windows


def transaction(old, new):
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
    if new.exists():
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
    extensions=set(extensions or ())
    bundle=package/'components/xtts/xtts-bundle.json'
    xtts_files=set(json.loads(bundle.read_text(encoding='utf-8'))['files'])|{'xtts-bundle.json'} if bundle.is_file() else None
    def selected(path):
        parts=path.relative_to(package).parts
        if parts[:2]==('components','toolchain'):return 'toolchain' in extensions
        if parts[:2]==('components','xtts'):return 'xtts' in extensions and (xtts_files is None or Path(*parts[2:]).as_posix() in xtts_files)
        if parts[0]=='voice' or parts[:2]==('components','whisper'):return 'whisper' in extensions
        return True
    files=[p for p in package.rglob('*') if p.is_file() and selected(p) and
           p.relative_to(package).parts[0] not in ('data','agents','backups') and
           '__pycache__' not in p.relative_to(package).parts and
           p.name not in ('helene.json','python-migration.json','owner-stop.json','install.log') and not p.name.endswith('.pyc')]
    total=sum(p.stat().st_size for p in files);done=0
    for item in files:
        if item.is_symlink():raise ValueError('Package symlink is unsupported: '+str(item))
        relative=item.relative_to(package);target=new/relative
        if not target.resolve().is_relative_to(new):raise ValueError('Program destination leaves target')
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
        done+=item.stat().st_size
        progress({'stage':'unpacking','bytes':done,'total_bytes':total,'file':relative.as_posix(),'reused':equal})


def retain_attempt_configs(new, note):
    """Keep a failed attempt intact before a fresh stopped-old snapshot."""
    from localharness.essential_backup import configs
    directory=new/'.migration-attempts'/(str(note['id'])+'-'+uuid.uuid4().hex)
    existing=[p for p in configs(new) if p.is_file()]
    if not existing:return
    directory.mkdir(parents=True)
    app_migration.write_note(directory,note)
    for config in existing:
        target=directory/config.relative_to(new);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(config,target)
    # No unique state is removed; every old config now has a recoverable copy.
    for config in existing:config.unlink()


def install_selected_extensions(program,note,extensions,progress):
    """Downloading a voice bundle also prepares it for this selected data tree."""
    if 'xtts' in set(extensions or ()):
        from localharness.xtts_install import install
        progress('installing_xtts')
        for tree in dict.fromkeys(note['data_paths']):install(Path(tree),program=program)


def finish_retirement(old, package, new, note, progress):
    """Repeatable integration cleanup, never a second application launch."""
    migration_windows.quiescent(old)
    progress('retiring_old')
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
            launch=True, progress=None, extensions=None,resume_copy=None):
    old,package,new,data=(p.resolve() for p in (old,package,new,data))
    progress=progress or (lambda stage:None)
    app_migration.discover(old)  # read-only preflight; never reused as final state
    if new.is_relative_to(old) or old.is_relative_to(new) or new==package or package.is_relative_to(new):
        raise ValueError('Old, new and unpacked package must be separate')
    prior=transaction(old,new)
    if prior['state']=='completed':return prior
    if prior['state'] in ('new_app_started','retirement_incomplete'):
        return resume_retirement(old,package,new,prior,progress)
    progress('unpacking')
    copy_program(package,new,progress,extensions)
    from localharness.update_extensions import check
    progress('checking_extensions')
    check(old,new)
    progress('stopping_old')
    before=migration_windows.service(old) if os.name=='nt' else None
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
        note=app_migration.prepare(old,new,data,old_service_stopped=bool(stopped['service']) or stop_service,progress=progress,reuse=reuse)
        install_selected_extensions(new,note,extensions,progress)
        if not launch:return note
        interpreter=new/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
        log=new/'migration-start.log'
        from localharness.desktop_process import JobChild
        progress('starting_new')
        child=JobChild.spawn([str(interpreter),'-s',str(new/'app/python_app.py'),'--root',str(new),
                             '--migration',note['id']],cwd=new,env=dict(os.environ),log=log,stopped=lambda:False)
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
        raise RuntimeError('New startup not confirmed; old files retained')
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


def fresh(package:Path,new:Path,data:Path,*,extensions=None,launch=True,progress=None,shortcuts=None):
    """New ordinary app; no previous service and no model work before first setup."""
    package,new,data=(Path(p).resolve() for p in (package,new,data))
    progress=progress or (lambda value:None)
    if new==package or new.is_relative_to(package) or package.is_relative_to(new):raise ValueError('Use a separate permanent application folder')
    receipt=new/'python-migration.json'
    if receipt.is_file():
        note=json.loads(receipt.read_text(encoding='utf-8'))
        if note.get('kind')!='fresh' or Path(note['new_root']).resolve()!=new:raise ValueError('Folder belongs to another installation transaction')
        if note['state']=='completed':return note
        if note['state']=='new_app_started':
            retained_startup(new,note)
            note.update(shortcuts=migration_windows.install_shortcuts(new,shortcuts),state='completed')
            app_migration.write_note(new,note);return note
    else:
        note=transaction(None,new)
        note.update(kind='fresh',old_root='',data_paths=[],source_paths=[])
        app_migration.write_note(new,note)
    copy_program(package,new,progress,extensions)
    config=new/'helene.json'
    if not config.exists():
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        tree=data/note['id'];tree.mkdir(parents=True,exist_ok=True)
        value={'mode':'local','tree':str(tree),'code':str(new/'tree'),'port':port,
               'python':str(new/'runtime/python.exe'),'app':str(new/'app/deskapp.py'),
               'runner':str(new/'app/localharness/runner.py'),'setup_complete':False,
               'model':{'framework':'openai','base_url':'','model':'','key':''},
               'relay':{'enabled':False},'voice':{'enabled':False},'installed':{'service':False}}
        config.write_text(json.dumps(value,indent=2),encoding='utf-8')
    value=json.loads(config.read_text(encoding='utf-8'))
    note.update(data_paths=[str(Path(value['tree']).resolve())],source_paths=[{'old':'','selected':str(Path(value['code']).resolve())}])
    note['state']='prepared';app_migration.write_note(new,note)
    install_selected_extensions(new,note,extensions,progress)
    if not launch:return note
    from localharness.desktop_process import JobChild
    child=None;started=False
    try:
        progress('starting_new')
        child=JobChild.spawn([str(new/'runtime/python.exe'),'-s',str(new/'app/python_app.py'),'--root',str(new),'--migration',note['id']],
                             cwd=new,env=dict(os.environ),log=new/'migration-start.log',stopped=lambda:False)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            current=json.loads(receipt.read_text(encoding='utf-8'))
            if current.get('state')=='new_app_started':
                started=True
                current.update(shortcuts=migration_windows.install_shortcuts(new,shortcuts),state='completed')
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
def installation_lock(target):
    """Two wizard processes may not copy/switch the same permanent folder."""
    from localharness.phone_tunnel.platform_support import acquire_file_lock,release_file_lock
    target=Path(target).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256(os.path.normcase(str(target)).encode()).hexdigest()[:24]
    lock=acquire_file_lock(target.parent/('.helene-install-'+identity+'.lock'),timeout_sec=0)
    try:yield
    finally:release_file_lock(lock)


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
    p.add_argument('--extensions',default='xtts',help='Optional downloads selected by the owner: xtts,whisper,toolchain')
    a=p.parse_args()
    if not a.confirmed:raise ValueError('Open the migration wizard and confirm the switch')
    package=Path(__file__).resolve().parent.parent
    home=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))
    def progress(stage):print(json.dumps(stage if isinstance(stage,dict) else {'stage':stage}),flush=True)
    with installation_lock(a.new_dir or home/'Helene'):
        if a.in_place:
            if not (a.dir/'python-app.json').is_file():raise ValueError('Selected folder is not a Python application')
            if a.new_dir and a.new_dir.resolve()!=a.dir.resolve():raise ValueError('An in-place update keeps the current working folder')
            staging=a.dir.resolve().parent/('.helene-update-'+uuid.uuid4().hex)
            staged=staging/'Helene';staged.mkdir(parents=True)
            copy_program(package,staged,progress,a.extensions.split(','))
            helper=staging/'python_source_updater.py'
            shutil.copy2(package/'app/localharness/python_source_updater.py',helper)
            from localharness.update_extensions import check
            progress('checking_extensions')
            check(a.dir,staged)
            migration_windows.stop(a.dir);migration_windows.quiescent(a.dir)
            command=[str(staged/'runtime/python.exe'),str(helper),'--old',str(a.dir.resolve()),'--new',str(staged),
                     '--pid','0','--staging',str(staging)]
            got=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=0x08000000 if os.name=='nt' else 0)
            updater_log=staging.with_suffix('.log')
            updater_log.write_bytes(got.stdout or b'')
            if got.returncode:raise RuntimeError('In-place update failed; inspect '+str(updater_log))
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
