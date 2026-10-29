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


def wait_owner(pid, stopped=lambda:False):
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
            deadline = time.monotonic()+60
            while kernel.WaitForSingleObject(handle,100)!=0:
                if stopped():raise InterruptedError('Owner stopped before cutover')
                if time.monotonic()>=deadline:raise TimeoutError('Old Python owner did not exit; no update applied')
        finally:kernel.CloseHandle(handle)
    else:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if stopped():raise InterruptedError('Owner stopped before cutover')
            try:os.kill(pid,0)
            except ProcessLookupError:return
            import psutil
            try:
                if psutil.Process(pid).status()==psutil.STATUS_ZOMBIE:return
            except psutil.NoSuchProcess:return
            time.sleep(.2)
        raise TimeoutError('Old Python owner did not exit; no update applied')


def apply_files(source, target, recovery, *, consume=False, verified=None):
    """Compatibility entry: prepare complete preimages/intent, then stream-apply.

    Desktop/CLI production paths prepare before closing the old window and call
    the same overlay apply after a final quiescent validation instead.
    """
    from localharness import update_overlay
    update_overlay.prepare(source, target, recovery, verified)
    return update_overlay.apply(source, target, recovery, consume=consume)


def rollback(target,recovery):
    """Restore only this update's changed files, retaining any later self-edit."""
    from localharness.update_io import measured_digest, copy_verified, remove_created_file
    target,recovery=Path(target).resolve(),Path(recovery).resolve()
    rows=json.loads((recovery/'update-files.json').read_text(encoding='utf-8'))
    previous={}
    # Check the entire recoverable set before the first restoration, including
    # startup recovery after an uncatchable interruption.
    for row in rows:
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid recovery path')
        if row['existed']:
            actual=measured_digest(recovery/relative)['sha256']
            if row.get('previous') and actual!=row['previous']:
                raise ValueError('Recovery preimage changed: '+row['path'])
            previous[row['path']]=actual
    for row in reversed(rows):
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid recovery path')
        destination=target/relative
        if destination.is_file():
            current=measured_digest(destination)['sha256']
            if current!=row['incoming']:
                saved=recovery/'later-self-edits'/relative;saved.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(destination,saved)
        if row['existed']:
            temporary=destination.with_name('.'+destination.name+'.restore-part')
            try:
                copy_verified(recovery/relative,temporary,previous[row['path']])
                temporary.replace(destination)
            finally:temporary.unlink(missing_ok=True)
        else:remove_created_file(destination)


def prepared_install_path(new):
    new = Path(new).resolve()
    identity = hashlib.sha256(str(new).encode()).hexdigest()[:16]
    return new.parent / ('prepared-install-' + identity + '.json')


def prepare_install(old, new, *, verified=None, progress=None, attached_data=None):
    """Prepare before switching; legacy helpers do this after old-owner exit."""
    old, new = Path(old).resolve(), Path(new).resolve()
    from localharness import source_admission
    if prepared_install_path(new).is_file():
        return validate_prepared(old,new,progress)
    if source_admission.activate(new):
        verified = source_admission.current_proofs(verified, new)
    if active_update(old).is_file():
        # Preparation runs beside a live owner. Never repair a mixed prior
        # generation under its feet; ordinary startup recovery owns that path.
        raise RuntimeError('An interrupted update requires startup recovery before another update; previous application remains open')
    sys.path.insert(0,str(new/'app'))
    from localharness import app_migration
    from localharness import update_profile, update_overlay
    from localharness import update_source
    update_source.require(old,new,progress)
    from localharness.update_progress import atomic_json
    from localharness.essential_backup import configs
    before = update_profile.selection(old)
    for config in configs(new):
        if config.is_file():config.unlink()
    # Existing migration moves internal data/source to independent preserved paths
    # and keeps external paths selected. Do this before replacing program bytes.
    metadata=json.loads((new/'python-app.json').read_text(encoding='utf-8')) if (new/'python-app.json').is_file() else {}
    portable=metadata.get('delivery')=='portable'
    from localharness.update_profile import prepare as prepare_profile
    if progress:progress.phase('backup')
    note=prepare_profile(old,new,progress,attached_data=attached_data)
    from localharness.code_transition import prepare as prepare_code
    if progress:progress.phase('code')
    prepare_code(old,new,note,progress=progress)
    def relocate(value):
        if isinstance(value,str):
            path=Path(value)
            if path.is_absolute() and path.is_relative_to(new):
                return str(old/path.relative_to(new))
        return value
    for config in configs(new):
        if not config.is_file():continue
        value=json.loads(config.read_text(encoding='utf-8'))
        for key in ('python','app','runner','tree','code'):
            if key in value:value[key]=relocate(value[key])
        if isinstance(value.get('backup'),dict) and 'dir' in value['backup']:
            value['backup']['dir']=relocate(value['backup']['dir'])
        from localharness.portable_profile import relative_config
        value=relative_config(value,old/config.relative_to(new),old)
        config.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    note['new_root']=str(old)
    if portable or note.get('in_place_profile'):
        for key in ('data_paths','copied_data'):
            note[key]=[relocate(value) for value in note[key]]
        for row in note['source_paths']:row['selected']=relocate(row['selected'])
        for key in ('data_home','backup'):note[key]=relocate(note[key])
        note['preserved_roots']={a:relocate(b) for a,b in note.get('preserved_roots',{}).items()}
    note['update_source']=str(new)
    app_migration.write_note(new,note)
    recovery=old.parent/('.helene-recovery-'+note['id'])
    from localharness.code_transition import validate
    validate(new)
    if update_profile.selection(old) != before:
        raise ValueError('Settings or selected source changed during update preparation; previous application remains open')
    if progress:progress.phase('preimages')
    update_overlay.prepare(new, old, recovery, verified, progress)
    # This second check closes the source-copy/preimage preparation window. The
    # helper checks the small identity before admission; costly final freshness
    # checks run after old-owner exit, before any program replacement.
    if update_profile.selection(old) != before:
        raise ValueError('Settings or selected source changed before update handoff; retry')
    prepared = {'format': 'helene-prepared-install.v1', 'old': str(old), 'new': str(new),
                'recovery': str(recovery), 'selection': before, 'note': note,
                'progress': str(progress.path) if progress else ''}
    atomic_json(prepared_install_path(new), prepared)
    if progress:progress.phase('prepared', preparation_completed=True)
    return prepared


def _prepared_identity(old, new):
    """Bound handoff identity, not an assertion that mutable bytes stayed fresh."""
    old, new = Path(old).resolve(), Path(new).resolve()
    value = json.loads((prepared_install_path(new)).read_text(encoding='utf-8'))
    if value['old'] != str(old) or value['new'] != str(new):
        raise ValueError('Prepared update target differs')
    recovery = Path(value['recovery']).resolve()
    if recovery.parent != old.parent or recovery.name != '.helene-recovery-' + value['note']['id']:
        raise ValueError('Prepared update recovery differs')
    return value


def validate_prepared(old, new, progress=None):
    from localharness import update_profile, update_overlay
    from localharness.code_transition import validate
    old, new = Path(old).resolve(), Path(new).resolve()
    value = _prepared_identity(old, new)
    recovery = Path(value['recovery']).resolve()
    if update_profile.selection(old) != value['selection']:
        raise ValueError('Settings or selected source changed after preparation; retry the update')
    validate(new)
    update_overlay.validate(new, old, recovery, progress)
    return value


def install(old, new, *, stopped=lambda:False):
    """Final quiescent check and short switch; direct legacy callers prepare here."""
    old, new = Path(old).resolve(), Path(new).resolve()
    if not (prepared_install_path(new)).is_file():
        recover_interrupted(old)  # this compatibility entry is already quiescent
        prepare_install(old, new)
    from localharness import update_overlay
    from localharness.update_progress import Progress
    value = json.loads((prepared_install_path(new)).read_text(encoding='utf-8'))
    progress = Progress(value['progress']) if value.get('progress') else None
    if progress:progress.phase('freshness')
    value = validate_prepared(old, new, progress)
    note, recovery = value['note'], Path(value['recovery'])
    pending=active_update(old)
    temporary=pending.with_suffix('.tmp')
    temporary.write_text(json.dumps({'target':str(old),'recovery':str(recovery),'migration_id':note['id']}),encoding='utf-8')
    temporary.replace(pending)
    applied = False
    try:
        if progress:progress.phase('switching')
        update_overlay.apply(new, old, recovery, progress=progress, stopped=stopped)
        applied = True
        # A legacy main ZIP may retain only a manifest, not the optional
        # payload. Prepare a voice only when this update actually supplies it.
        if (new/'components/xtts/xtts-bundle.json').is_file():
            from localharness.xtts_install import install as install_xtts
            for tree in dict.fromkeys(note['data_paths']):install_xtts(Path(tree),program=old)
    except BaseException as failure:
        # apply() can fail after its first replacement, including during its own
        # restoration. An unreturned apply is NOT evidence of no effects.
        restored = getattr(failure, 'restoration_completed', False)
        if applied:
            rollback(old,recovery)
            restored = True
        if restored:pending.unlink(missing_ok=True)
        raise
    return note,recovery


def active_update(old):
    return old.parent/('.helene-update-active-'+hashlib.sha256(str(old).encode()).hexdigest()[:16]+'.json')


def recover_interrupted(old):
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
        if not value.get('committed') and not started:
            if not (recovery/'update-files.json').is_file():
                raise RuntimeError('Interrupted update recovery journal is missing; program restoration is unconfirmed')
            rollback(old,recovery)
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
    parser.add_argument('--source-mode',choices=('preserve','clean'))
    parser.add_argument('--job',type=Path)
    parser.add_argument('--job-id',default='')
    parser.add_argument('--admission-name',default='updater-admitted.json')
    args=parser.parse_args()
    sys.path.insert(0,str(args.new.resolve()/'app'))
    if args.cleanup:return perform(args)
    validate_staging(args)
    if Path(args.admission_name).name != args.admission_name:
        raise ValueError('Updater admission name leaves staging')
    mechanical = prepared_install_path(args.new).is_file()
    if not mechanical:
        from localharness.migration_job import legacy_update
        legacy_update(args.old,args.new,source_mode=args.source_mode or 'preserve',
                      admission=args.staging/args.admission_name)
        return 0  # the confirmed persistent owner continues; browser/native alike
    if args.source_mode:
        from localharness.update_source import selected
        choice=selected(args.old,args.new)
        if not choice or choice['mode']!=args.source_mode:
            raise ValueError('Prepared source policy differs from the mechanical invocation')
    from python_migrate import installation_lock
    # No UI or model work occurs while this short switch owns the target lock.
    with installation_lock(args.old):
        try:
            return perform(args)
        except BaseException as exc:
            _job_report(args,'attention',error=f'{type(exc).__name__}: {exc}')
            raise


def validate_staging(args):
    old,new,staging=args.old.resolve(),args.new.resolve(),args.staging.resolve()
    if new!=staging/'Helene' or staging.parent!=old.parent or not staging.name.startswith('.helene-update-'):
        raise ValueError('Unexpected update staging directory')
    if any(p.resolve().parent!=old.parent for p in args.archive):
        raise ValueError('Update archive must be beside Helene')


def _job_report(args,state,**details):
    job=getattr(args,'job',None)
    if not job:return
    from localharness import desktop_update_request as jobs
    value=json.loads(Path(job).read_text(encoding='utf-8'))
    if value.get('id')!=args.job_id or jobs.path(value['tree']).resolve()!=Path(job).resolve():
        raise ValueError('Terminal cutover job identity differs')
    from localharness import owner_stop
    if owner_stop.stopped() and not jobs.cancelled(value['tree'],value['id']):
        jobs.request(value['tree'],'cancel',root=args.old,identity=value['id'],reason='Owner Stop',by='window')
    cancelled=jobs.cancelled(value['tree'],value['id'])
    value.update(details,state='cancelled' if cancelled and state!='completed' else state,
                 phase=state,terminal_at=time.time())
    if cancelled:value['cancel_requested']=True
    jobs._save(value['tree'],value)


def perform(args):
    if not args.cleanup:
        from localharness.update_process import interpreter_for, background_flags
    old,new=args.old.resolve(),args.new.resolve()
    presentation=['--browser'] if args.browser else []
    def stopped():
        from localharness import owner_stop
        if owner_stop.stopped():return True
        job = getattr(args, 'job', None)
        if job:
            from localharness.desktop_update_request import cancelled
            current = json.loads(Path(job).read_text(encoding='utf-8'))
            if current.get('id') != args.job_id:raise ValueError('Cutover job identity changed')
            pause=Path(current['tree'])/'memory/.control/autonomy-paused.json'
            while pause.exists():
                if owner_stop.stopped() or cancelled(current['tree'],args.job_id):return True
                time.sleep(.1)
            return cancelled(current['tree'], args.job_id) or current.get('cancel_requested') is True
        return False
    prepared_path = prepared_install_path(new)
    progress_path = getattr(args, 'progress_path', '')
    if not args.cleanup and prepared_path.is_file():
        progress_path = json.loads(prepared_path.read_text(encoding='utf-8')).get('progress', '')
    def report(stage, **details):
        if progress_path:
            from localharness.update_progress import Progress
            Progress(progress_path).phase(stage, **details)
    def reopen_previous():
        executable=interpreter_for(old)
        with (old/'python-update-recovery.log').open('ab') as output:
            # The restored version may predate startup recovery. A current
            # staging-runtime trampoline waits for our lease to be released,
            # then execs the unmodified old entry point (no new CLI required).
            staging_python=interpreter_for(new)
            trampoline="import os,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from python_migrate import installation_lock\nprint('Waiting for update installation lease',flush=True)\nwith installation_lock(Path(sys.argv[2]),timeout_sec=60):\n pass\nos.chdir(sys.argv[2])\nos.execv(sys.argv[3],[sys.argv[3],sys.argv[4],'--root',sys.argv[2],*sys.argv[5:]])"
            subprocess.Popen([str(staging_python),'-s','-c',trampoline,str(new/'app'),str(old),str(executable),str(old/'app/python_app.py'),*presentation],
                cwd=new,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                creationflags=background_flags(),start_new_session=os.name!='nt')
    staging=args.staging.resolve()
    validate_staging(args)
    legacy_prepare = not args.cleanup and not prepared_path.is_file() and (new/'python-app.json').is_file()
    if not args.cleanup:
        if stopped():raise InterruptedError('Owner stopped before mechanical handoff')
        if legacy_prepare and active_update(old).exists():
            raise RuntimeError('An interrupted update requires startup recovery before another update; previous application remains open')
        # Admission is a bounded custody acknowledgement, not the expensive
        # full-package check. install() performs that after quiescence; a late
        # source/preimage change still refuses before the first replacement.
        if prepared_path.is_file():_prepared_identity(old, new)
        observer_ready = getattr(args, 'observer_ready', None)
        if observer_ready is not None and not observer_ready.wait(15):
            raise TimeoutError('Update progress window did not become ready; previous application remains open')
        admission_name=getattr(args,'admission_name','updater-admitted.json')
        if Path(admission_name).name!=admission_name:raise ValueError('Updater admission name leaves staging')
        admitted=staging/admission_name
        part=admitted.with_suffix('.tmp')
        import psutil
        part.write_text(json.dumps({'pid':os.getpid(),'birth':psutil.Process().create_time(),'target':str(old),
            'job_id':getattr(args,'job_id',''),'runtime':str(Path(sys.executable).resolve()),
            'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'preparation_completed':not legacy_prepare}),encoding='utf-8');part.replace(admitted)
    report('waiting_owner', label='Закрываю прежнее окно')
    wait_owner(args.pid, stopped=stopped)
    if stopped():raise InterruptedError('Owner stopped before program replacement')
    if not args.cleanup:report('freshness', old_exit_observed_at=time.time())
    if args.cleanup:
        # This second process uses the final runtime, after the staging runtime
        # has exited and Windows has released its DLLs/executable.
        # Import while staging/app still exists: the callback may run after
        # rmtree has already removed this helper's module source.
        from localharness.update_io import remove_created_file
        def remove_readonly_file(function, path, failure):
            if function is not os.unlink or not isinstance(failure[1], PermissionError):
                raise failure[1]
            remove_created_file(path)  # retries only read-only Windows files
        shutil.rmtree(staging, onerror=remove_readonly_file)
        for archive in args.archive:archive.unlink(missing_ok=True)
        return 0
    if legacy_prepare:
        from localharness.app_migration import discover
        from localharness.migration_lifecycle import owner_lease_available
        # PID exit does not prove that a separately hosted profile owner left.
        # Refuse before preparation without launching a duplicate previous app.
        for agent in discover(old)['agents']:
            if agent['value'].get('mode')!='remote' and not owner_lease_available(Path(agent['tree'])/'.desktop-owner.lock'):
                report('failed', error='Previous Python owner still holds its data lease',
                       previous_program_retained=True, previous_reopen_requested=False)
                raise RuntimeError('Previous Python owner still holds its data lease; no update applied')
    try:
        if legacy_prepare:
            from localharness.update_progress import Progress
            progress = Progress(progress_path) if progress_path else None
            prepare_install(old,new,progress=progress)
        note,recovery=install(old,new,stopped=stopped)
    except BaseException as exc:
        if active_update(old).exists():
            report('failed', error=str(exc), rollback_completed=False, recovery_required=True,
                   previous_program_retained=False, previous_reopen_requested=False)
            raise
        report('reopening', error=str(exc), previous_program_retained=True)
        reopen_previous()
        from localharness.update_source import SourceChoiceCancelled
        if isinstance(exc,SourceChoiceCancelled):
            report('cancelled',error='',message=str(exc),previous_reopen_requested=True)
            return 0
        from localharness.agent_source_update import AgentResolutionPending
        if isinstance(exc,AgentResolutionPending):
            report('needs_resolution',error='',message=str(exc),previous_reopen_requested=True)
            return 0
        report('failed', error=str(exc), previous_reopen_requested=True)
        raise
    executable=interpreter_for(old)
    log=old/'python-update-start.log'
    child=None
    try:
        report('starting')
        from localharness.desktop_supervisor import JobChild
        from localharness.update_process import permanent_environment
        child=JobChild.spawn([str(executable),str(old/'app/python_app.py'),'--root',str(old),'--migration',note['id'],*presentation],
                             cwd=old,env=permanent_environment(),log=log,stopped=lambda:False)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if stopped():raise InterruptedError('Owner stopped updated startup; restoring previous version')
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
                    from localharness.migration_lifecycle import close_python
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
        report('rollback', error=str(failure))
        rollback(old,recovery)
        active_update(old).unlink(missing_ok=True)
        print('Update failed; previous program restored, recovery retained:',recovery,flush=True)
        reopen_previous()
        report('failed', error=str(failure), rollback_completed=True, previous_reopen_requested=True)
        raise
    pending=active_update(old)
    if stopped():
        if child is not None and child.handle is not None:child.stop()
        rollback(old,recovery);pending.unlink(missing_ok=True)
        reopen_previous()
        raise InterruptedError('Owner stopped before startup commit; previous version restored')
    if pending.is_file():
        value=json.loads(pending.read_text(encoding='utf-8'));value['committed']=True
        temporary=pending.with_suffix('.tmp');temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(pending)
    child.release()
    state['state']='completed'
    from localharness import app_migration
    app_migration.write_note(old,state)
    pending.unlink(missing_ok=True)
    report('completed', new_ready_observed_at=time.time(), recovery=str(recovery))
    _job_report(args,'completed',result={'installed_root':str(old),'recovery':str(recovery),
                                       'startup_receipt':note['id'],'effect':'committed'})
    print('Python update launched',child.pid,'root',old,'recovery',recovery,flush=True)
    helper=recovery/'cleanup-update.py'
    shutil.copy2(Path(__file__),helper)
    command=[str(executable),str(helper),'--old',str(old),'--new',str(new),
             '--staging',str(staging),'--pid',str(os.getpid()),'--cleanup']
    for archive in args.archive:command.extend(['--archive',str(archive)])
    with log.open('ab') as output:
        subprocess.Popen(command,cwd=old.parent,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                         creationflags=background_flags(),start_new_session=os.name!='nt')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
