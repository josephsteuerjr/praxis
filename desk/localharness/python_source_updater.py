"""Standalone updater: preserve owner state, replace working Helene, confirm, clean.

The staged runtime runs this helper while the working runtime is replaced. Each
replaced byte is retained in a sibling recovery directory; migration independently
preserves configured data, agent source, Git history and self-authored changes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def wait_owner(pid):
    if pid <= 0:return  # explicit already-quiescent CLI transaction
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x00100000,False,pid)
        if not handle:
            if ctypes.get_last_error()==87:return
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if kernel.WaitForSingleObject(handle,60000)!=0:
                raise TimeoutError('Old Python owner did not exit; no update applied')
        finally:kernel.CloseHandle(handle)
    else:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:os.kill(pid,0)
            except ProcessLookupError:return
            time.sleep(.2)
        raise TimeoutError('Old Python owner did not exit; no update applied')


def apply_files(source, target, recovery):
    """Overlay package files only; retain overwritten bytes and a rollback map."""
    source,target,recovery=(Path(p).resolve() for p in (source,target,recovery))
    written=[]
    recovery.mkdir()
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    def record():
        path=recovery/'update-files.json';part=path.with_suffix('.tmp')
        part.write_text(json.dumps(written,indent=2),encoding='utf-8')
        deadline=time.monotonic()+3
        while True:
            try:
                part.replace(path)
                break
            except PermissionError:
                if time.monotonic()>=deadline:raise
                time.sleep(.05)
    try:
        for item in sorted(source.rglob('*')):
            if item.is_symlink():raise ValueError('Update contains a symlink: '+str(item))
            if item.is_dir():continue
            relative=item.relative_to(source)
            if '__pycache__' in relative.parts or item.suffix=='.pyc':continue
            destination=target/relative
            if not destination.resolve().is_relative_to(target):
                raise ValueError('Update destination leaves working folder')
            if destination.is_symlink():raise ValueError('Update destination is a symlink')
            previous=recovery/relative
            existed=destination.exists()
            incoming=digest(item)
            if (existed and destination.stat().st_size==item.stat().st_size and digest(destination)==incoming
                    and (os.name == 'nt' or destination.stat().st_mode & 0o777 == item.stat().st_mode & 0o777)):
                continue
            if existed:
                previous.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(destination,previous)
            destination.parent.mkdir(parents=True,exist_ok=True)
            written.append({'path':relative.as_posix(),'existed':existed,'incoming':incoming})
            record()  # recovery is durable before the first changed program byte
            temporary=destination.with_name('.'+destination.name+'.update-part')
            try:
                if relative.parts[:3]==('components','xtts','model') and item.suffix=='.pth' and item.stat().st_dev==destination.parent.stat().st_dev:
                    temporary.unlink(missing_ok=True);os.link(item,temporary)
                else:shutil.copy2(item,temporary)
                temporary.replace(destination)
            finally:temporary.unlink(missing_ok=True)
        record()
    except BaseException:
        for row in reversed(written):
            destination=target/row['path']
            if row['existed']:shutil.copy2(recovery/row['path'],destination)
            else:destination.unlink(missing_ok=True)
        raise


def rollback(target,recovery):
    """Restore only this update's changed files, retaining any later self-edit."""
    target,recovery=Path(target).resolve(),Path(recovery).resolve()
    rows=json.loads((recovery/'update-files.json').read_text(encoding='utf-8'))
    for row in reversed(rows):
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid recovery path')
        destination=target/relative
        if destination.is_file():
            with destination.open('rb') as stream:current=hashlib.file_digest(stream,'sha256').hexdigest()
            if current!=row['incoming']:
                saved=recovery/'later-self-edits'/relative;saved.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(destination,saved)
        if row['existed']:shutil.copy2(recovery/relative,destination)
        else:destination.unlink(missing_ok=True)


def install(old, new):
    recover_interrupted(old)
    sys.path.insert(0,str(new/'app'))
    from localharness import app_migration
    from localharness.essential_backup import configs
    for config in configs(new):
        if config.is_file():config.unlink()
    # Existing migration moves internal data/source to independent preserved paths
    # and keeps external paths selected. Do this before replacing program bytes.
    note=app_migration.prepare(old,new,old.parent/'Helene-update-data',preserve_voice=False)
    def relocate(value):
        if isinstance(value,str):
            path=Path(value)
            if path.is_absolute() and path.is_relative_to(new):
                return str(old/path.relative_to(new))
        return value
    for config in configs(new):
        if not config.is_file():continue
        value=json.loads(config.read_text(encoding='utf-8'))
        for key in ('python','app','runner'): 
            if key in value:value[key]=relocate(value[key])
        config.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    note['new_root']=str(old)
    note['update_source']=str(new)
    app_migration.write_note(new,note)
    recovery=old.parent/('.helene-recovery-'+note['id'])
    pending=active_update(old)
    temporary=pending.with_suffix('.tmp')
    temporary.write_text(json.dumps({'target':str(old),'recovery':str(recovery),'migration_id':note['id']}),encoding='utf-8')
    temporary.replace(pending)
    try:
        apply_files(new,old,recovery)
        # A legacy main ZIP may retain only a manifest, not the optional
        # payload. Prepare a voice only when this update actually supplies it.
        if (new/'components/xtts/xtts-bundle.json').is_file():
            from localharness.xtts_install import install as install_xtts
            for tree in dict.fromkeys(note['data_paths']):install_xtts(Path(tree),program=old)
    except BaseException:
        if (recovery/'update-files.json').is_file():rollback(old,recovery)
        pending.unlink(missing_ok=True)
        raise
    return note,recovery


def active_update(old):
    old=Path(old).resolve()
    return old.parent/('.helene-update-active-'+hashlib.sha256(str(old).encode()).hexdigest()[:16]+'.json')


def recover_interrupted(old):
    old=Path(old).resolve()
    pending=active_update(old)
    if pending.is_file():
        value=json.loads(pending.read_text(encoding='utf-8'))
        recovery=Path(value['recovery'])
        if Path(value['target']).resolve()!=old or recovery.parent.resolve()!=old.parent:
            raise ValueError('Interrupted update identity differs')
        note_path=old/'python-migration.json'
        note=json.loads(note_path.read_text(encoding='utf-8')) if note_path.is_file() else {}
        started=(value.get('migration_id') and note.get('id')==value['migration_id']
                 and note.get('state') in ('new_app_started','completed'))
        if not value.get('committed') and not started and (recovery/'update-files.json').is_file():rollback(old,recovery)
        pending.unlink()


def recover_startup(old, migration_id=''):
    """Called before application imports. A bound new generation may confirm;
    ordinary starts recover only while no updater holds the installation lock.
    """
    old=Path(old).resolve()
    pending=active_update(old)
    if migration_id:
        # Both first migration and in-place update hold the target lease while
        # their specifically bound new generation proves readiness.
        note=json.loads((old/'python-migration.json').read_text(encoding='utf-8'))
        if note.get('id')!=migration_id or Path(note.get('new_root','')).resolve()!=old:
            raise ValueError('Update startup identity differs')
        if pending.is_file() and json.loads(pending.read_text(encoding='utf-8')).get('migration_id')!=migration_id:
            raise ValueError('Update journal belongs to another generation')
        return False
    from python_migrate import installation_lock
    # Admission precedes the no-journal fast path: preservation can be lengthy
    # and starts before install() creates the overlay journal.
    with installation_lock(old):
        if not pending.is_file():return False
        recover_interrupted(old)
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--old',type=Path,required=True)
    parser.add_argument('--new',type=Path,required=True)
    parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--staging',type=Path,required=True)
    parser.add_argument('--archive',type=Path,action='append',default=[])
    parser.add_argument('--cleanup',action='store_true')
    parser.add_argument('--browser',action='store_true')
    args=parser.parse_args()
    sys.path.insert(0,str(args.new.resolve()/'app'))
    if args.cleanup:return perform(args)
    from python_migrate import installation_lock
    # Same kernel-backed target lock as migration, held across old-owner exit,
    # overlay, new generation confirmation, and any rollback.
    with installation_lock(args.old):return perform(args)


def perform(args):
    old,new=args.old.resolve(),args.new.resolve()
    presentation=['--browser'] if args.browser else []
    def reopen_previous():
        from localharness.python_platform import desktop_argv
        command=desktop_argv(old,['--root',str(old),*presentation])
        with (old/'python-update-recovery.log').open('ab') as output:
            # The restored version may predate startup recovery. A current
            # staging-runtime trampoline waits for our lease to be released,
            # then execs the unmodified old entry point (no new CLI required).
            staging_python=new/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
            trampoline="import os,sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from python_migrate import installation_lock\nprint('Waiting for update installation lease',flush=True)\nwith installation_lock(Path(sys.argv[2]),timeout_sec=60):\n pass\nos.chdir(sys.argv[2])\ncommand=json.loads(sys.argv[3]);os.execv(command[0],command)"
            subprocess.Popen([str(staging_python),'-s','-c',trampoline,str(new/'app'),str(old),json.dumps(command)],
                cwd=new,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                creationflags=0x00000200 if os.name=='nt' else 0,start_new_session=os.name!='nt')
    staging=args.staging.resolve()
    if new!=staging/'Helene' or staging.parent!=old.parent or not staging.name.startswith('.helene-update-'):
        raise ValueError('Unexpected update staging directory')
    if any(p.resolve().parent!=old.parent for p in args.archive):
        raise ValueError('Update archive must be beside Helene')
    if not args.cleanup:
        admitted=staging/'updater-admitted.json'
        part=admitted.with_suffix('.tmp')
        part.write_text(json.dumps({'pid':os.getpid(),'target':str(old)}),encoding='utf-8');part.replace(admitted)
    wait_owner(args.pid)
    if args.cleanup:
        # This second process uses the final runtime, after the staging runtime
        # has exited and Windows has released its DLLs/executable.
        shutil.rmtree(staging)
        for archive in args.archive:archive.unlink(missing_ok=True)
        return 0
    try:
        note,recovery=install(old,new)
    except BaseException:
        reopen_previous()
        raise
    executable=old/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
    log=old/'python-update-start.log'
    child=None
    try:
        from localharness.desktop_supervisor import JobChild
        from localharness.python_platform import desktop_argv
        child=JobChild.spawn(desktop_argv(old,['--root',str(old),'--migration',note['id'],*presentation]),
                             cwd=old,env=dict(os.environ),log=log,stopped=lambda:False)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            state=json.loads((old/'python-migration.json').read_text(encoding='utf-8'))
            if state.get('id')==note['id'] and state.get('state')=='new_app_started':break
            if child.poll() is not None:raise RuntimeError('Updated application exited before startup confirmation')
            time.sleep(.3)
        else:raise TimeoutError('Updated startup unconfirmed')
    except BaseException as failure:
        graceful_error = None
        if child is not None and child.handle is not None:
            try:
                if child.poll() is None:
                    from localharness.migration_windows import close_python
                    close_python(old)
            except Exception as exc:
                graceful_error = exc
            finally:
                # poll can settle/clear the retained Job. A failed stop MUST
                # retain incoming bytes and journal, never race a live owner.
                if child.handle is not None:
                    child.stop()
        if graceful_error is not None:
            failure.add_note('Graceful exit failed; owned update Job stopped: ' + str(graceful_error))
        rollback(old,recovery)
        active_update(old).unlink(missing_ok=True)
        print('Update failed; previous program restored, recovery retained:',recovery,flush=True)
        reopen_previous()
        raise
    pending=active_update(old)
    if pending.is_file():
        value=json.loads(pending.read_text(encoding='utf-8'));value['committed']=True
        temporary=pending.with_suffix('.tmp');temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(pending)
    child.release()
    state['state']='completed'
    from localharness import app_migration
    app_migration.write_note(old,state)
    pending.unlink(missing_ok=True)
    print('Python update launched',child.pid,'root',old,'recovery',recovery,flush=True)
    helper=recovery/'cleanup-update.py'
    shutil.copy2(Path(__file__),helper)
    command=[str(executable),str(helper),'--old',str(old),'--new',str(new),
             '--staging',str(staging),'--pid',str(os.getpid()),'--cleanup']
    for archive in args.archive:command.extend(['--archive',str(archive)])
    with log.open('ab') as output:
        subprocess.Popen(command,cwd=old.parent,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                         creationflags=0x00000200 if os.name=='nt' else 0,start_new_session=os.name!='nt')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
