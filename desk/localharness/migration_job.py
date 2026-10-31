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

from . import desktop_update_request as jobs, owner_stop, program_state
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
    if package!=staging/'Helene' or not program_state.staging_belongs(old,staging):
        raise ValueError('Legacy update package identity differs')
    result=admit(package,old,old=old,source_mode=source_mode,force_extensions=force_extensions,
                 confirmed_by='legacy-update-invocation',_ordinary_update=True)
    # 2.2.x's caller waits for this historical descriptor filename. Its contents
    # explicitly say durable task admission, NOT a shown or long-lived wizard.
    program_state.hidden(staging)
    descriptor=package/f'.python-migrator-{os.getpid()}.json'
    write(descriptor,{'schema':2,'pid':os.getpid(),'root':str(package),
        'admission_kind':'durable-agent-job','wizard_opened':False,'job_id':result['job']['id'],
        'durable_owner':result['job']['admission']})
    program_state.hidden(descriptor)
    if admission is not None:
        admission=Path(admission).resolve()
        if admission!=staging/'updater-admitted.json':raise ValueError('Legacy admission path differs')
        write(admission,{'pid':os.getpid(),'target':str(old),'preparation_completed':False,
            'process_exit_allowed':True,'durable_owner':result['job']['admission'],
            'job_id':result['job']['id'],'job_path':str(jobs.path(result['job']['tree']).resolve())})
    return result


def coordination_tree(package,target,data,*,fresh=False):
    package,target,data=(Path(p).resolve() for p in (package,target,data))
    if fresh:return data
    identity=hashlib.sha256((str(target)+'\0'+str(data)+'\0'+hashlib.sha256((package/'source-inputs.json').read_bytes()).hexdigest()).encode()).hexdigest()[:20]
    from .program_state import migration
    canonical=migration(target,identity)
    legacy=target.parent/('.helene-migration-'+identity)
    previous=jobs.status(legacy);intent=previous.get('migration') or {}
    if (not canonical.exists() and intent.get('package')==str(package)
            and intent.get('target')==str(target) and intent.get('data')==str(data)
            and previous.get('kind')=='migration'):
        return legacy
    return canonical


def existing_observation(package,target,data,*,fresh=False):
    """Reopened wizard attaches to the same request; never buys a new worker."""
    package,target,data=(Path(p).resolve() for p in (package,target,data))
    control=coordination_tree(package,target,data,fresh=fresh)
    value=jobs.status(control);intent=value.get('migration') or {}
    if (value.get('kind')=='migration' and value.get('state') not in ('none','cancelled')
            and intent.get('package')==str(package) and intent.get('target')==str(target)
            and intent.get('data')==str(data) and intent.get('fresh') is fresh):
        observation(package,value)
        return value
    return None


def admit(package,target,*,old=None,data=None,fresh=False,remove_old=False,source_mode=None,force_extensions=False,progress=None,confirmed_by='migration-window',_ordinary_update=False,data_destination=None):
    from . import windows_installation as installation
    from .session_host import process_state
    from .desktop_supervisor import JobChild
    from .update_process import interpreter_for
    from .config_transaction import transaction
    if force_extensions:raise ValueError('Incompatible-extension override is obsolete')
    package,target=Path(package).resolve(),Path(target).resolve()
    old=Path(old).resolve() if old and not fresh else None
    data=Path(data or installation.default_data(old)).resolve()
    archive_update=bool(old and old!=target and installation.is_python_program(old))
    if archive_update:
        from .python_archive_update import require_idle_data
        require_idle_data(data)
    mode=jobs.new_source_mode(source_mode) if _ordinary_update or archive_update else (source_mode or 'clean')
    kind='source-update' if _ordinary_update else 'migration'
    if _ordinary_update:
        if old!=target or not (old/'python-app.json').is_file():raise ValueError('Ordinary update requires the existing Python installation')
        if mode not in ('clean','preserve'):raise ValueError('Invalid update source mode')
    elif not archive_update and mode!='clean':raise ValueError('Migration installs clean code and keeps data; preserving code edits is unavailable')
    if not _ordinary_update and old is not None and (target==old or target.is_relative_to(old) or old.is_relative_to(target)):
        raise ValueError('Select a separate new program folder for migration')
    if data==target or target.is_relative_to(data) or data.is_relative_to(package):
        raise ValueError('Select permanent data independently of the incoming program')
    if not fresh and not data.is_dir():raise ValueError('Select an existing data folder or explicitly create a new agent')
    if data_destination:
        if fresh or _ordinary_update or old is None:raise ValueError('Data transfer requires a selected previous installation')
        from .data_relocation import plan
        from .app_migration import discover
        plan(discover(old)['agents'],data,data_destination,target,old,target/'data-relocation.json')
    if owner_stop.stopped():raise RuntimeError('Owner Stop forbids migration admission')
    target.mkdir(parents=True,exist_ok=True)
    # A legacy supervisor may still own data here. Bootstrap coordination lives
    # inside the NEW program's private state, never into old data.
    control=data
    if not fresh and not _ordinary_update:
        control=coordination_tree(package,target,data)
        for directory in (control,control/'memory',control/'memory/.control'):
            if directory.resolve()!=directory or directory.is_symlink() or (hasattr(directory,'is_junction') and directory.is_junction()):
                raise ValueError('Migration coordination must not follow links into existing data')
    request_path=jobs.path(control)
    intent={'package':str(package),'target':str(target),'old':str(old) if old else '',
        'data':str(data),'fresh':bool(fresh),'remove_old':bool(old) and not _ordinary_update,'source_mode':mode}
    if data_destination:intent['data_destination']=str(Path(data_destination).resolve())
    if archive_update:intent['python_update']=True
    with transaction(request_path):
        previous=jobs.settle_cancel(control,jobs.status(control))
        if previous.get('state')=='cancelled':jobs._save(control,previous)
        if (previous.get('kind')==kind and previous.get('migration')==intent
                and previous.get('state')=='attention' and not previous.get('helper')
                and not previous.get('cancel_requested') and not jobs.cancelled(control,previous['id'])
                and process_state(previous.get('admission') or {}) in ('absent','changed')):
            descriptor=read(control/'memory/.control/desktop-session.json')
            if (descriptor.get('state')=='stopped' and process_state(descriptor) in ('absent','changed')
                    and all(descriptor.get(k)==previous.get('admission',{}).get(k) for k in ('pid','birth','generation'))):
                previous.update(state='superseded',superseded_reason='owner requested another clean migration attempt')
                jobs._save(control,previous)
        if previous.get('kind')==kind and previous.get('migration')==intent and previous.get('state') in jobs.ACTIVE|{'admitting'}:
            producer=previous.get('admission') or {}
            if process_state(producer)=='alive':return {'state':'admitted','job':previous,'observer_may_close':True}
            raise RuntimeError('The existing migration admission needs recovery; no duplicate job started')
        if previous.get('state') in jobs.ACTIVE|{'admitting'}:raise ValueError('This profile already has an active update/migration job')
        if fresh and data.exists() and any(data.iterdir()):
            raise ValueError('A new agent needs an empty selected data folder; connect existing data explicitly')
        descriptor=read(control/'memory/.control/desktop-session.json')
        if descriptor and process_state(descriptor) not in ('absent','changed'):
            raise RuntimeError('This profile has a live or unknown session host; use its normal Update or stop it first')
        value={'format':'helene-desktop-update-request.v2','id':uuid.uuid4().hex,'kind':kind,
            # The bootstrap serves the OLD configuration until preparation is
            # accepted. The future target is not its observer/session identity.
            'created':time.time(),'program':str(old if _ordinary_update else package if not fresh else target),'tree':str(control),
            'source_mode':mode,'migration':intent,'state':'admitting','phase':'requested','confirmed_by':confirmed_by}
        jobs._save(control,value)
    command=[str(interpreter_for(package)),'-B','-s',str(package/'app/localharness/session_host.py'),
        '--root',value['program'],'--control-tree',str(control),'--migration-request',str(request_path),
        '--runtime-root',str(package)]
    log=request_path.parent/'desktop-session.log'
    child=JobChild.spawn(command,cwd=package,env=dict(os.environ),log=log,stopped=owner_stop.stopped)
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            current=jobs.status(control)
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


def observation(package,admitted):
    """Observe this exact durable job without owning or restarting its worker."""
    from .session_host import process_state
    from .update_progress import status
    package=Path(package).resolve();control=Path(admitted['tree']).resolve()
    value=jobs.status(control);intent=value.get('migration') or {}
    if (value.get('id')!=admitted['id'] or value.get('kind')!='migration'
            or intent.get('package')!=str(package)
            or intent!=admitted.get('migration') or value.get('tree')!=str(control)):
        raise ValueError('Migration observer identity changed; no repeated admission')
    progress=status(Path(intent['target']),operation_id=value['id'])
    producer=value.get('helper') if value.get('state')=='handed_off' else value.get('admission')
    return {'id':value['id'],'state':value['state'],'phase':value.get('phase'),
        'live':process_state(producer or {})=='alive','progress':progress,
        'error':value.get('error',''),'diagnostic':value.get('diagnostic',''),
        'result':value.get('result',{})}


def observer_descriptor(root, package, identity, *, tree=None):
    """Resolve the admitted control tree, independently of the old config tree."""
    from .session_host import control_path, process_state
    root,package=Path(root).resolve(),Path(package).resolve()
    selected=Path(tree).resolve() if tree is not None else control_path(root).parents[2]
    descriptor=read(selected/'memory/.control/desktop-session.json');job=jobs.status(selected)
    intent=job.get('migration') or {}
    independent_staged=(job.get('kind')=='migration'
                  and (intent.get('source_mode')=='clean' or
                       (intent.get('python_update') is True and intent.get('source_mode')=='preserve'))
                  and job.get('program')==str(package) and intent.get('data')!=str(selected)
                  and program_state.migration_belongs(
                      Path(intent.get('target','')),selected))
    expected_program=str(package) if independent_staged else intent.get('old')
    if (descriptor.get('program')!=str(root) or process_state(descriptor)!='alive'
            or descriptor.get('tree')!=str(selected) or job.get('tree')!=str(selected)
            or job.get('id')!=identity or job.get('kind') not in ('migration','source-update')
            or job.get('program')!=str(root) or job.get('migration',{}).get('package')!=str(package)
            or (intent.get('data') is not None and not independent_staged and intent['data']!=str(selected))
            or (expected_program and expected_program!=str(root))
            or any(job.get('admission',{}).get(k)!=descriptor.get(k) for k in ('pid','birth','generation'))):
        raise ValueError('Migration observer does not match the admitted session')
    return descriptor


def observer_program(root, package, identity, *, tree=None):
    """An incoming view may attach only to its exact admitted migration host."""
    observer_descriptor(root,package,identity,tree=tree)
    package=Path(package).resolve()
    return package


def open_observer(host, owner, value, package, work):
    """Render with incoming B while the selected program/source remain intact."""
    from .desktop_supervisor import JobChild
    from .python_platform import desktop_argv
    from .session_host import process_state
    previous=read(work/'observer.json')
    if previous and process_state(previous)=='alive':return previous
    command=desktop_argv(package,['--root',str(owner.root),'--program',str(package),
        '--session-job',value['id'],'--session-tree',str(Path(value['tree']).resolve())])
    child=JobChild.spawn(command,cwd=package,env=dict(os.environ),log=work/'observer.log',stopped=owner.update_cancelled)
    try:
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if owner.update_cancelled():raise InterruptedError('Migration observer startup cancelled')
            # Registration is not enough: require the renderer's python_ready.
            view=read(program_state.window_control(owner.root,child.pid))
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
    from . import desktop_update_request as jobs
    jobs.request(Path(descriptor['tree']),'cancel',root=root,identity=identity,
                 reason='Explicit recovery of failed session',by='window')
    _rpc(descriptor,'exit',{'owner_exit':False},timeout=45)
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
    if value.get('kind')=='source-update':return _execute_update(host,owner,value)
    from . import windows_installation as installation, python_updates, update_composition
    from .update_progress import Progress
    from .update_process import interpreter_for,background_flags
    from python_migrate import installation_lock
    request=value['migration'];package=Path(request['package']);target=Path(request['target'])
    old=Path(request['old']) if request['old'] else None
    data=Path(value['tree'])
    work=data/'memory/.control/desktop-update-jobs'/value['id'];work.mkdir(parents=True,exist_ok=True)
    progress=Progress(program_state.progress(target,value['id']),value['id'],target,event=owner.event)
    owner.bootstrap=True
    owner.update_job=value
    owner.update_cancelled=lambda:owner.stop.is_set() or owner_stop.stopped() or jobs.cancelled(value['tree'],value['id'])
    owner.migration_data=Path(request['data'])
    jobs.bind_incoming(value,package if old is not None and old==target else target)
    try:
        if not request.get('python_update') and request.get('source_mode') != 'clean':
            raise ValueError('Migration supports clean code with existing data only')
        data=Path(request['data'])
        note=installation.run(package,target,old=old,data=data,fresh=request['fresh'],
            remove_old=request['remove_old'],launch=False,progress=progress,source_mode=request['source_mode'],
            cancelled=python_updates._Cancellation(owner.update_cancelled),bootstrap_id=value['id'],
            **({'data_destination':request['data_destination']} if request.get('data_destination') else {}))
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
        command=[str(interpreter_for(target)),'-B','-s',str(helper),'--complete',str(jobs.path(value['tree'])),
                 '--job-id',value['id'],'--pid',str(os.getpid()),'--program',str(target)]
        with (work/'startup.log').open('ab') as output:
            process=subprocess.Popen(command,cwd=target,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                creationflags=background_flags(),start_new_session=os.name!='nt')
        admitted=python_updates.await_admission(process,work,target)
        jobs.checkpoint(owner,value,state='handed_off',phase='handoff',helper=admitted)
        host.stop.set()  # this bootstrap owns no agent; successor attaches after its exit
    except BaseException as exc:
        import traceback
        error=f'{type(exc).__name__}: {exc}'
        diagnostic=work/'migration-error.log'
        try:diagnostic.write_text(traceback.format_exc(),encoding='utf-8')
        except OSError:pass
        cancelled=jobs.cancelled(value['tree'],value['id'])
        try:progress.phase('cancelled' if cancelled else 'failed',error=error,diagnostic=str(diagnostic))
        except (OSError,ValueError,TypeError):pass
        try:
            jobs.checkpoint(owner,value,state='cancelled' if cancelled else 'attention',error=error,
                            diagnostic=str(diagnostic))
            owner.event('migration-attention',{'id':value['id'],'error':str(exc)})
        finally:
            # Migration never revives the old supervisor. A retry keeps using
            # the new immutable executor and the recorded settings/data paths.
            try:owner.close()
            except BaseException as cleanup:
                jobs.checkpoint(owner,value,bootstrap_cleanup={'released':False,'error':str(cleanup)})
            else:
                jobs.checkpoint(owner,value,bootstrap_cleanup={'released':True})
                host.stop.set()


def _execute_update(host,owner,value):
    """Existing in-place Python source update, distinct from clean migration."""
    from . import python_updates,migration_lifecycle
    from .source_update_host import verified_runtime
    from .update_progress import Progress
    request=value['migration'];old=Path(request['old']);package=Path(request['package'])
    if value.get('kind')!='source-update' or old!=Path(request['target']):raise ValueError('Invalid ordinary update identity')
    work=Path(value['tree'])/'memory/.control/desktop-update-jobs'/value['id'];work.mkdir(parents=True,exist_ok=True)
    progress=Progress(program_state.progress(old,value['id']),value['id'],old,event=owner.event)
    owner.bootstrap=True;owner.update_job=value
    owner.update_cancelled=lambda:owner.stop.is_set() or owner_stop.stopped() or jobs.cancelled(value['tree'],value['id'])
    owner.migration_data=Path(value['tree']);jobs.bind_incoming(value,package)
    before=None;stopped=False
    try:
        runtime=verified_runtime(package)
        before=migration_lifecycle.service(old)
        stopped_old=migration_lifecycle.stop(old);stopped=True;migration_lifecycle.quiescent(old)
        jobs.checkpoint(owner,value,legacy_handoff={'old':str(old),'service':before,'stop':stopped_old,'runtime':runtime,'at':time.time()})
        owner.root=old;owner.runtime_program=package
        if request['source_mode']=='preserve':
            owner.start();jobs.checkpoint(owner,value,phase='starting_agent');progress.phase('starting_agent')
            open_observer(host,owner,value,package,work)
            owner.readiness(timeout=180,require_runner=True,cancelled=owner.update_cancelled)
        jobs.checkpoint(owner,value,phase='preparing')
        if not (old/'python-app.json').is_file():raise ValueError('Native legacy installations use clean migration into a separate folder')
        if package!=package.parent/'Helene' or not program_state.staging_belongs(old,package.parent):
            raise ValueError('Ordinary update requires its admitted staging folder')
        result=python_updates._prepare_handoff(owner,package.parent,{},progress,False,request['source_mode'])
        jobs.checkpoint(owner,value,state='handed_off',phase='handoff',result=result)
    except BaseException as exc:
        import traceback
        try:(work/'update-error.log').write_text(traceback.format_exc(),encoding='utf8')
        except OSError:pass
        jobs.checkpoint(owner,value,state='cancelled' if jobs.cancelled(value['tree'],value['id']) else 'attention',error=f'{type(exc).__name__}: {exc}')
        if stopped:
            try:
                owner.close();restored=migration_lifecycle.restore_service(old,before)
                jobs.checkpoint(owner,value,legacy_recovery={'released':True,'service_restored':restored})
            except BaseException as cleanup:
                jobs.checkpoint(owner,value,legacy_recovery={'released':False,'error':str(cleanup)})
            else:host.stop.set()


def publish_completed_migration(root,value):
    """Publish terminal success without leaving the failed RC5 pointer active."""
    from .session_host import process_state
    from .config_transaction import transaction
    from .update_progress import Progress
    root=Path(root).resolve();data=Path(value['migration']['data']).resolve()
    note=read(root/'python-migration.json')
    if value['migration'].get('data_destination'):
        transfer=note.get('data_relocation') or {}
        selected=Path(value['migration']['data_destination']).resolve()
        if (transfer.get('source')!=str(data) or transfer.get('destination')!=str(selected)
                or transfer.get('state') not in ('verified','retired','source_retained')
                or note.get('data_home')!=str(selected)):
            raise ValueError('Completed data transfer differs from its admitted source/destination')
        data=selected
    if (value.get('state')!='completed' or not note.get('startup_proof')
            or note.get('id')!=value.get('result',{}).get('migration_id')):
        raise ValueError('A confirmed new startup is required before publishing completion')
    if data!=Path(value['tree']).resolve():
        with transaction(jobs.path(data)):
            previous=jobs.status(data)
            publish=True
            if previous and previous!={'state':'none'} and previous.get('id')!=value['id']:
                settled=(previous.get('kind')=='migration' and previous.get('state')=='attention'
                    and previous.get('source_mode')=='clean' and not previous.get('helper')
                    and previous.get('program')==value['migration'].get('old')
                    and previous.get('legacy_recovery',{}).get('released') is True
                    and process_state(previous.get('admission') or {}) in ('absent','changed'))
                terminal=previous.get('state') in ('completed','cancelled','declined','superseded')
                publish=settled or terminal  # histories remain by ID; never replace active work
                if settled:
                    previous.update(state='superseded',superseded_by=value['id'])
                    jobs._save(data,previous)
            if publish:
                terminal=dict(value,tree=str(data),program=str(root),bootstrap_control=value['tree'])
                jobs._save(data,terminal)
    progress=Progress(program_state.progress(root,value['id']),value['id'],root)
    warnings=list(value.get('result',{}).get('cleanup_errors') or [])
    progress.phase('completed',attached_data=str(data),new_program=str(root),cleanup_errors=warnings,
                   label='Программа запущена · остались прежние файлы' if warnings else 'Обновление завершено')


def committed_result(note):
    """Successful startup and incomplete cleanup are distinct visible facts."""
    if not note.get('startup_proof') or note.get('state') not in ('completed','retirement_incomplete'):
        raise ValueError('A committed migration requires confirmed startup')
    return {'effect':'committed','migration_id':note['id'],
            'cleanup_errors':list(note.get('cleanup_errors') or [])}


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
            value.update(state='completed',phase='completed',result=committed_result(note))
            value.pop('error',None);value.pop('diagnostic',None)
        except BaseException as exc:
            if child is not None and child.handle is not None:child.stop()
            if value.get('migration_recovery'):rollback(root,Path(value['migration_recovery']))
            value.update(state='cancelled' if stopped() else 'attention',phase='startup_failed',error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            jobs._save(data,value)
            if value.get('state')=='completed':publish_completed_migration(root,value)
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--complete',type=Path,required=True);p.add_argument('--job-id',required=True)
    p.add_argument('--pid',type=int,required=True);p.add_argument('--program',type=Path,required=True)
    return complete(p.parse_args())


if __name__=='__main__':raise SystemExit(main())
