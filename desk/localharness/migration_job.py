"""Short migration admission into the same persistent Python session host.

The wizard selects data/mode and leaves only after this host owns a durable
request. Existing migration/profile/merge/cutover code does the work; the final
helper has no model or reviewer loop. There is no second scheduler or task DB.
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
import threading
import time
import uuid

if __package__ in (None,''):
    bootstrap = (Path(sys.argv[sys.argv.index('--program')+1])/'app'
                 if '--program' in sys.argv else Path(__file__).resolve().parents[1])
    sys.path.insert(0,str(bootstrap))
    __package__='localharness'

from . import desktop_update_request as jobs, owner_stop
from .package_notice import read,write


def owns_empty_seed(data,identity):
    """Only this admitted bootstrap's own control bytes may preseed fresh data."""
    if not identity:return False
    data=Path(data).resolve();job=jobs.status(data)
    if job.get('id')!=identity or job.get('kind')!='migration' or not job.get('migration',{}).get('fresh'):
        return False
    for path in data.rglob('*'):
        if path.is_symlink():return False
        if not path.is_file():continue
        relative=path.relative_to(data).as_posix()
        if not relative.startswith('memory/.control/'):
            return False
        suffix=relative[len('memory/.control/'):]
        if suffix not in ('desktop-session.json','desktop-session.log','desktop-session.lock','desktop-update-request.json') and not suffix.startswith('desktop-update-jobs/'+identity):
            return False
    return True


def legacy_update(old,package,*,source_mode='preserve',admission=None,force_extensions=False):
    """Old Update invocation hands off a JOB, never a shown-window receipt."""
    old,package=Path(old).resolve(),Path(package).resolve()
    staging=package.parent
    if package!=staging/'Helene' or staging.parent!=old.parent or not staging.name.startswith('.helene-update-'):
        raise ValueError('Legacy update package identity differs')
    result=admit(package,old,old=old,source_mode=source_mode,force_extensions=force_extensions,
                 confirmed_by='legacy-update-invocation')
    # 2.2.x's caller waits for this historical descriptor filename. Its contents
    # explicitly say durable task admission, NOT a shown or long-lived wizard.
    write(package/f'.python-migrator-{os.getpid()}.json',{'schema':2,'pid':os.getpid(),'root':str(package),
        'admission_kind':'durable-agent-job','wizard_opened':False,'job_id':result['job']['id'],
        'durable_owner':result['job']['admission']})
    if admission is not None:
        admission=Path(admission).resolve()
        if admission!=staging/'updater-admitted.json':raise ValueError('Legacy admission path differs')
        write(admission,{'pid':os.getpid(),'target':str(old),'preparation_completed':False,
            'process_exit_allowed':True,'durable_owner':result['job']['admission'],
            'job_id':result['job']['id'],'job_path':str(jobs.path(result['job']['tree']).resolve())})
    return result


def admit(package,target,*,old=None,data=None,fresh=False,remove_old=False,source_mode=None,force_extensions=False,progress=None,confirmed_by='migration-window'):
    from . import windows_installation as installation
    from .session_host import process_state
    from .desktop_supervisor import JobChild
    from .update_process import interpreter_for
    from .config_transaction import transaction
    if force_extensions:raise ValueError('Incompatible-extension override is obsolete')
    package,target=Path(package).resolve(),Path(target).resolve()
    old=Path(old).resolve() if old and not fresh else None
    data=Path(data or installation.default_data(old)).resolve()
    mode=source_mode or ('preserve' if old else 'clean')
    if mode not in ('preserve','clean'):raise ValueError('Choose preserve or clean source mode')
    if data==target or target.is_relative_to(data) or data.is_relative_to(package):
        raise ValueError('Select permanent data independently of the incoming program')
    if not fresh and not data.is_dir():raise ValueError('Select an existing data folder or explicitly create a new agent')
    if owner_stop.stopped():raise RuntimeError('Owner Stop forbids migration admission')
    target.mkdir(parents=True,exist_ok=True)
    request_path=jobs.path(data)
    intent={'package':str(package),'target':str(target),'old':str(old) if old else '',
        'data':str(data),'fresh':bool(fresh),'remove_old':bool(remove_old),'source_mode':mode}
    with transaction(request_path):
        previous=jobs.settle_cancel(data,jobs.status(data))
        if previous.get('state')=='cancelled':jobs._save(data,previous)
        if previous.get('kind')=='migration' and previous.get('migration')==intent and previous.get('state') in jobs.ACTIVE|{'admitting'}:
            producer=previous.get('admission') or {}
            if process_state(producer)=='alive':return {'state':'admitted','job':previous,'observer_may_close':True}
            raise RuntimeError('The existing migration admission needs recovery; no duplicate job started')
        if previous.get('state') in jobs.ACTIVE|{'admitting'}:raise ValueError('This profile already has an active update/migration job')
        if fresh and data.exists() and any(data.iterdir()):
            raise ValueError('A new agent needs an empty selected data folder; connect existing data explicitly')
        descriptor=read(data/'memory/.control/desktop-session.json')
        if descriptor and process_state(descriptor) not in ('absent','changed'):
            raise RuntimeError('This profile has a live or unknown session host; use its normal Update or stop it first')
        value={'format':'helene-desktop-update-request.v2','id':uuid.uuid4().hex,'kind':'migration',
            'created':time.time(),'program':str(old if old and target==old else target),'tree':str(data),
            'source_mode':mode,'migration':intent,'state':'admitting','phase':'requested','confirmed_by':confirmed_by}
        jobs._save(data,value)
    command=[str(interpreter_for(package)),'-B','-s',str(package/'app/localharness/session_host.py'),
        '--root',value['program'],'--control-tree',str(data),'--migration-request',str(request_path),
        '--runtime-root',str(package)]
    log=request_path.parent/'desktop-session.log'
    child=JobChild.spawn(command,cwd=package,env=dict(os.environ),log=log,stopped=owner_stop.stopped)
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            current=jobs.status(data)
            producer=current.get('admission') or {}
            if (current.get('id')==value['id'] and producer.get('pid')==child.pid
                    and process_state(producer)=='alive' and current.get('state') in jobs.ACTIVE):
                child.release()
                return {'state':'admitted','job':current,'observer_may_close':True}
            if child.poll() is not None:raise RuntimeError('Migration host refused admission; inspect desktop-session.log')
            time.sleep(.05)
        raise TimeoutError('Migration durable admission unconfirmed; do not repeat it blindly')
    except BaseException:
        if child.handle is not None:child.stop()
        raise


def execute(host,owner,value):
    from .legacy_update_tool import controller_scope
    with controller_scope(owner):
        return _execute(host,owner,value)


def observer_program(root, package, identity):
    """An incoming view may attach only to its exact admitted migration host."""
    from .session_host import control_path, process_state
    root,package=Path(root).resolve(),Path(package).resolve()
    descriptor=read(control_path(root));job=jobs.status(descriptor.get('tree',root))
    if (descriptor.get('program')!=str(root) or process_state(descriptor)!='alive'
            or job.get('id')!=identity or job.get('kind')!='migration'
            or job.get('program')!=str(root) or job.get('migration',{}).get('package')!=str(package)
            or any(job.get('admission',{}).get(k)!=descriptor.get(k) for k in ('pid','birth','generation'))):
        raise ValueError('Migration observer does not match the admitted session')
    return package


def open_observer(host, owner, value, package, work):
    """Render with incoming B while the selected program/source remain intact."""
    from .desktop_supervisor import JobChild
    from .python_platform import desktop_argv
    from .session_host import process_state
    previous=read(work/'observer.json')
    if previous and process_state(previous)=='alive':return previous
    command=desktop_argv(package,['--root',str(owner.root),'--program',str(package),'--session-job',value['id']])
    child=JobChild.spawn(command,cwd=package,env=dict(os.environ),log=work/'observer.log',stopped=owner.update_cancelled)
    try:
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if owner.update_cancelled():raise InterruptedError('Migration observer startup cancelled')
            # Registration is not enough: require the renderer's python_ready.
            view=read(owner.root/f'.python-window-{child.pid}.json')
            if (view.get('pid')==child.pid and view.get('root')==str(owner.root.resolve())
                    and view.get('ui_ready') is True):
                import psutil
                result={'pid':child.pid,'birth':psutil.Process(child.pid).create_time()}
                write(work/'observer.json',result);child.release();return result
            if child.poll() is not None:raise RuntimeError('Migration window exited before it became ready; inspect observer.log')
            time.sleep(.1)
        raise TimeoutError('Migration window startup unconfirmed; inspect observer.log')
    except BaseException:
        if child.handle is not None:child.stop()
        raise


def failed_session(root):
    """Identify RC3's exact failed bootstrap, never an arbitrary live owner."""
    from .session_host import control_path,process_state
    root=Path(root).resolve()
    descriptor=read(control_path(root));job=jobs.status(descriptor.get('tree',root))
    if (descriptor.get('program')==str(root) and process_state(descriptor)=='alive'
            and job.get('program')==str(root) and job.get('kind')=='migration'
            and job.get('state')=='attention' and job.get('phase')=='preparing'
            and job.get('error')=='TimeoutError: Runner ownership heartbeat unconfirmed'
            and job.get('legacy_handoff') and not job.get('helper')
            and all(job.get('admission',{}).get(k)==descriptor.get(k) for k in ('pid','birth','generation'))):
        return {'id':job['id'],'error':job['error']}
    return None


def release_failed_session(root, identity):
    """Explicit recovery cancels that failed job, then proves its leases gone."""
    from .session_host import control_path,process_state,_rpc
    from .migration_windows import owner_lease_available
    root=Path(root).resolve();failed=failed_session(root)
    if not failed or failed['id']!=identity:raise ValueError('No matching failed legacy handoff to release')
    descriptor=read(control_path(root))
    _rpc(descriptor,'exit',timeout=45)
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        if process_state(descriptor) in ('absent','changed'):
            if not owner_lease_available(Path(descriptor['tree'])/'.desktop-owner.lock'):
                raise RuntimeError('Previous data lease is still held; recovery not confirmed')
            return {'released':True,'id':identity}
        time.sleep(.1)
    raise TimeoutError('Previous session exit unconfirmed; no replacement started')


def _execute(host,owner,value):
    """One admitted work item in the session host, not a window-owned thread."""
    from . import windows_installation as installation, python_updates, update_composition
    from .update_progress import Progress
    from .update_process import interpreter_for,background_flags
    from python_migrate import installation_lock
    request=value['migration'];package=Path(request['package']);target=Path(request['target'])
    old=Path(request['old']) if request['old'] else None
    data=Path(value['tree'])
    work=data/'memory/.control/desktop-update-jobs'/value['id'];work.mkdir(parents=True,exist_ok=True)
    progress=Progress(target.parent/('.helene-update-'+value['id']+'.progress.json'),value['id'],target,event=owner.event)
    owner.bootstrap=True
    owner.update_job=value
    owner.update_cancelled=lambda:owner.stop.is_set() or owner_stop.stopped() or jobs.cancelled(data,value['id'])
    owner.migration_data=data
    jobs.bind_incoming(value,package if old is not None and old==target else target)
    previous_service=None
    legacy_stopped=False
    try:
        if old is not None:
            from . import migration_lifecycle
            from .source_update_host import verified_runtime
            runtime_binding=verified_runtime(package)
            # Durable host already owns this job. Release the legacy UI/service
            # data lease, then host the SAME agent/source/data with current B.
            # No old UI, old tool module or model session owns this continuation.
            previous_service=migration_lifecycle.service(old)
            stopped_old=migration_lifecycle.stop(old)
            legacy_stopped=True
            migration_lifecycle.quiescent(old)
            jobs.checkpoint(owner,value,legacy_handoff={'old':str(old),'service':previous_service,
                'stop':stopped_old,'runtime':runtime_binding,'at':time.time()})
            owner.root=old
            owner.runtime_program=package
            owner.start()
            jobs.checkpoint(owner,value,phase='starting_agent')
            progress.phase('starting_agent')
            open_observer(host,owner,value,package,work)
            # Cold legacy runners import the preserved agent, start its body,
            # project its brain and connect transports before the first reader
            # heartbeat. The view is already usable; Stop remains responsive.
            owner.readiness(timeout=180,require_runner=True,cancelled=owner.update_cancelled)
            jobs.checkpoint(owner,value,phase='preparing')
        if old is not None and old==target:
            if not (old/'python-app.json').is_file():raise ValueError('A native legacy installation must migrate into the already unpacked program folder')
            if package!=package.parent/'Helene' or package.parent.parent!=old.parent or not package.parent.name.startswith('.helene-update-'):
                raise ValueError('In-place migration requires the admitted incoming update package beside the current program')
            result=python_updates._prepare_handoff(owner,package.parent,{},progress,False,request['source_mode'])
            jobs.checkpoint(owner,value,state='handed_off',phase='handoff',result=result)
            return  # the existing pinned updater takes custody before this host exits
        note=installation.run(package,target,old=old,data=data,fresh=request['fresh'],
            remove_old=request['remove_old'],launch=False,progress=progress,source_mode=request['source_mode'],
            cancelled=python_updates._Cancellation(owner.update_cancelled),bootstrap_id=value['id'])
        if owner.update_cancelled():raise InterruptedError('Owner stopped migration before startup handoff')
        # Any reviewed app/resource M is committed with the same complete preimages.
        # The program/runtime stay in their chosen folder; no AppData install.
        mapping=update_composition.selected_files(target)
        recovery=None
        if mapping:
            from . import update_overlay,code_transition
            code_transition.validate(target)
            recovery=work/'app-recovery'
            update_overlay.prepare(target,target,recovery)
            update_overlay.apply(target,target,recovery,stopped=owner.update_cancelled)
        jobs.checkpoint(owner,value,state='running',phase='prepared',migration_id=note['id'],
            migration_recovery=str(recovery) if recovery else '')
        helper=work/'migration_startup.py'
        shutil.copy2(Path(__file__),helper)
        command=[str(interpreter_for(target)),'-B','-s',str(helper),'--complete',str(jobs.path(data)),
                 '--job-id',value['id'],'--pid',str(os.getpid()),'--program',str(target)]
        with (work/'startup.log').open('ab') as output:
            process=subprocess.Popen(command,cwd=target,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                creationflags=background_flags(),start_new_session=os.name!='nt')
        admitted=python_updates.await_admission(process,work,target)
        jobs.checkpoint(owner,value,state='handed_off',phase='handoff',helper=admitted)
        host.stop.set()  # this bootstrap owns no agent; successor attaches after its exit
    except BaseException as exc:
        try:
            jobs.checkpoint(owner,value,state='cancelled' if jobs.cancelled(data,value['id']) else 'attention',
                error=f'{type(exc).__name__}: {exc}')
            owner.event('migration-attention',{'id':value['id'],'error':str(exc)})
        finally:
            # Even a receipt write failure cannot strand a bootstrap owner.
            if legacy_stopped:
                released=False
                try:
                    owner.close()
                    from . import migration_lifecycle
                    restored=migration_lifecycle.restore_service(old,previous_service)
                    released=True
                    jobs.checkpoint(owner,value,legacy_recovery={'released':True,'service_restored':restored})
                except BaseException as cleanup:
                    owner.event('migration-attention',{'id':value['id'],'error':str(cleanup)})
                    if not released:
                        jobs.checkpoint(owner,value,legacy_recovery={'released':False,'error':str(cleanup)})
                finally:
                    if released:host.stop.set()


def complete(args):
    """Pinned mechanical startup/retirement only, after bootstrap host exit."""
    sys.path.insert(0,str(args.program/'app'))
    from localharness.python_source_updater import wait_owner,rollback
    from localharness.desktop_supervisor import JobChild
    from localharness.python_platform import desktop_argv
    from localharness import windows_installation as installation, app_migration
    from python_migrate import installation_lock
    import psutil
    value=read(args.complete)
    if value.get('id')!=args.job_id or value.get('kind')!='migration':raise ValueError('Migration completion identity differs')
    root=args.program.resolve();data=Path(value['tree']);work=args.complete.parent/'desktop-update-jobs'/value['id']
    def stopped():return owner_stop.stopped() or jobs.cancelled(data,value['id'])
    with installation_lock(root):
        write(work/'updater-admitted.json',{'pid':os.getpid(),'birth':psutil.Process().create_time(),
            'target':str(root),'job_id':value['id'],'runtime':str(Path(sys.executable).resolve()),
            'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        child=None
        try:
            wait_owner(args.pid,stopped)
            if stopped():raise InterruptedError('Owner stopped migration before new startup')
            note=read(root/'python-migration.json')
            if note.get('id')!=value['migration_id']:raise ValueError('Prepared migration changed')
            from localharness.update_process import permanent_environment
            child=JobChild.spawn(desktop_argv(root,['--root',str(root),'--migration',note['id']]),
                cwd=root,env=permanent_environment(),log=work/'new-start.log',stopped=stopped)
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                if stopped():raise InterruptedError('Owner stopped migration startup')
                note=read(root/'python-migration.json')
                if note.get('id')==value['migration_id'] and note.get('state')=='new_app_started':break
                if child.poll() is not None:raise RuntimeError('New application exited before migration confirmation')
                time.sleep(.2)
            else:raise TimeoutError('New application migration startup is unconfirmed')
            if value['migration'].get('old'):
                note=installation.finish(root,note,remove_old=value['migration']['remove_old'],progress=lambda *args:None)
            else:
                from localharness import migration_lifecycle
                note['shortcuts']=migration_lifecycle.install_shortcuts(root,desktop_only=True)
                note['state']='completed';app_migration.write_note(root,note)
            child.release()
            value.update(state='completed',phase='completed',result={'effect':'committed','migration_id':note['id']})
        except BaseException as exc:
            if child is not None and child.handle is not None:child.stop()
            if value.get('migration_recovery'):rollback(root,Path(value['migration_recovery']))
            value.update(state='cancelled' if stopped() else 'attention',phase='startup_failed',error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            jobs._save(data,value)
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--complete',type=Path,required=True);p.add_argument('--job-id',required=True)
    p.add_argument('--pid',type=int,required=True);p.add_argument('--program',type=Path,required=True)
    return complete(p.parse_args())


if __name__=='__main__':raise SystemExit(main())
