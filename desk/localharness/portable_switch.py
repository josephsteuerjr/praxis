"""Reviewed in-app folder switch; old bytes survive a five-minute trial.

Manual archive startup uses the same generation proof and receipt retirement.
"""
from concurrent.futures import ThreadPoolExecutor,wait,CancelledError
from dataclasses import dataclass
import datetime as dt
import hashlib,json,os
from pathlib import Path
import subprocess,time
import sys
import threading
from .package_notice import read,write

FORMAT='helene-portable-switch.v1'
TRIAL_SECONDS=300
LEGACY_CLEAN='helene-legacy-portable-clean.v1'


def enabled(root):
    if os.name!='nt':return False
    from .first_start import ready
    return ready(root)


def payload(program,*,runtime=True):
    from .source_merge import inventory
    from .update_composition import app_inventory
    from .first_start import plain
    from .validation_read import current,checkpoint,digest,parallel
    check=current();checkpoint(check)
    def measured_path(path):
        checkpoint(check)
        with plain(path).open('rb') as stream:return digest(stream,check)
    program=Path(program)
    from .essential_backup import configs
    result={'tree':inventory(program/'tree'),'app':app_inventory(program/'app'),
        'settings':{p.relative_to(program).as_posix():measured_path(p) for p in configs(program) if p.is_file()},
        'upstream':measured_path(program/'code-upstream.zip'),
        'passport':measured_path(program/'python-package-files.json')}
    # The working generation can stay healthy after its immutable next-update
    # base is damaged. Bind that base before retiring every previous copy.
    metadata=read(plain(program/'python-app.json'))
    baseline_files=('python-app.json','code-upstream.json','app-upstream.zip',
                    'app-upstream.json','source-update-baseline.json')
    if metadata.get('source_update_protocol') or any((program/name).exists() for name in baseline_files[2:]):
        from .authored_baseline import validate as validate_baseline
        for name in baseline_files:plain(program/name)
        validate_baseline(program)
        result['baseline']={name:measured_path(program/name) for name in baseline_files}
    if runtime:
        names=[name for name in read(program/'python-package-files.json')['files'] if name.startswith(('runtime/','bin/')) or name=='helene.exe']
        actual=set()
        from .first_start import plain
        for here,dirs,files in os.walk(program/'runtime',followlinks=False):
            checkpoint(check)
            for name in [*dirs,*files]:checkpoint(check);plain(Path(here)/name)
            for name in files:
                checkpoint(check)
                path=Path(here)/name
                if path.suffix=='.pyc' and path.parent.name=='__pycache__':
                    import importlib.util
                    try:
                        if Path(importlib.util.source_from_cache(str(path))).is_file():continue
                    except ValueError:pass
                actual.add(path.relative_to(program).as_posix())
        if actual!={name for name in names if name.startswith('runtime/')}:
            raise ValueError('Portable runtime contains undeclared files or import hooks')
        def measured(name):
            from .first_start import plain
            checkpoint(check)
            with plain(program/name).open('rb') as stream:return name,digest(stream,check)
        result['runtime']=dict(parallel(measured,names,workers=os.cpu_count() or 1))
        declared=read(program/'python-package-files.json')['files']
        expected={name:declared[name]['sha256'] if isinstance(declared[name],dict) else declared[name] for name in names}
        if result['runtime']!=expected:raise ValueError('Portable runtime differs from admitted distribution')
    return result


def prepare(old,new,progress=None,*,verified=None,attached_data=None):
    from . import code_transition,update_composition,update_profile
    from .first_start import _bind,_write,FORMAT as FIRST_FORMAT,receipt
    from .python_source_updater import prepared_install_path
    from .essential_backup import configs,load
    from .portable_profile import relative_config
    old,new=Path(old).resolve(),Path(new).resolve()
    if old==new or new.is_relative_to(old) or old.is_relative_to(new):raise ValueError('The new portable programme must be independent of the old folder')
    from .first_start import Places,ready,legacy_plan,configurations
    from .windows_first_start import Backend
    places=Places.current()
    if not ready(old,places):raise ValueError('Прежняя программа не подтверждена квитанцией данных в профиле')
    legacy=legacy_plan(new,places,Backend(new))
    if old not in legacy['retained']:
        legacy['retained'].append(old);legacy['protected'].append(old);legacy['all_roots'].append(old)
        legacy['receipts'][str(old)]=hashlib.sha256(receipt(old).read_bytes()).hexdigest()
        if not any(old.is_relative_to(p) for p in legacy['retained_roots']):legacy['retained_roots'].append(old)
    before=update_profile.selection(old)
    code_transition.validate(new)
    note=update_profile.prepare(old,new,progress,attached_data=attached_data)
    note['portable_folder_switch']=True
    for data in note['data_paths']:
        path=Path(data).resolve()
        if path==old or path.is_relative_to(old) or path==new or path.is_relative_to(new):
            raise ValueError('Portable update data must be outside both programme folders')
    code_transition.prepare(old,new,note,progress=progress)
    # Shared harness changes are part of the same reviewed composition.
    for name,row in update_composition.selected_files(new).items():
        from .source_merge import relative
        relative(name);destination=new/name
        if row.get('delete'):destination.unlink(missing_ok=True)
        else:
            from .update_io import copy_verified
            copy_verified(Path(row['source']),destination,row['sha256'])
    prepared=[]
    for config in configs(new):
        if not config.is_file():continue
        value=load(config);selected=value.get('code')
        data=Path(value['tree']).resolve()
        value=_bind(value,config.parent,new,data);value['code']=selected
        relative=config.relative_to(new)
        prepared.append({'program':str(new),'directory':str((old/relative).parent),
                         'relative':relative.as_posix(),'tree':str(data),'config':value})
    from .first_start_assets import prepare as prepare_assets,retain as retain_assets
    profiles=configurations(legacy['retained'],places)
    assets=prepare_assets(places.data,legacy['retained'],[places.data],profiles+prepared,{},None)
    retain_assets(assets,keep_sources=legacy['retained'])
    retiring=[*legacy['old_roots'],*legacy['retained'],*[p for p in legacy['siblings'] if p.is_dir()]]
    for row in prepared:
        config=new/row['relative'];value=row['config']
        from .standalone_relay import prepare_migration
        callback=(lambda item:progress.phase(item['stage'],**{key:entry for key,entry in item.items() if key!='stage'})) if progress else None
        for previous in retiring:value=prepare_migration(previous,new,[value],callback)[0]
        def independent(item):
            if isinstance(item,dict):
                for child in item.values():independent(child)
            elif isinstance(item,list):
                for child in item:independent(child)
            elif isinstance(item,str):
                path=Path(item)
                if path.is_absolute() and any(path.resolve().is_relative_to(p) for p in retiring):
                    raise ValueError('New settings still depend on the old programme; it cannot be retired')
        independent(value)
        _write(config,relative_config(value,config,new),private=True)
    if update_profile.selection(old)!=before:raise ValueError('Old settings/source changed during preparation')
    note.update(normal_python_update=True,source_policy=note.get('source_policy','preserve'),remove_old=True,
        installation_flow='portable-folder-switch',data_home=note['data_paths'][0])
    write(new/'python-migration.json',note)
    # Preparation is complete; the success trial below has separate authority.
    _write(receipt(new),{'format':FIRST_FORMAT,'state':'completed',
        'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'data':note['data_paths'][0]})
    from .portable_retirement import capture,seal,authority
    retirement=capture(new,legacy,places,assets,mode='update');seal(new,retirement)
    value={'format':FORMAT,'old':str(old),'new':str(new),'selection':before,'note':note,
        'payload':payload(new),'progress':str(progress.path) if progress else '',
        'relocations':[],'retirement':{'id':retirement['id'],'sha256':authority(retirement)},
        'scratch':scratch_scope(old,new,required=False)}
    write(prepared_install_path(new),value)
    if progress:progress.phase('prepared',preparation_completed=True)
    return value


def identity(old,new,value):
    from .first_start import plain
    old,new=plain(old),plain(new)
    if value.get('format')!=FORMAT or value.get('old')!=str(old) or value.get('new')!=str(new):
        raise ValueError('Portable switch identity differs')
    if old==new or new.is_relative_to(old) or old.is_relative_to(new):raise ValueError('Portable folders overlap')
    note=value['note']
    from .program_state import _identity
    _identity(note['id'])
    if note.get('old_root')!=str(old) or note.get('new_root')!=str(new) or not note.get('portable_folder_switch'):
        raise ValueError('Portable switch receipt differs')
    for raw in note['data_paths']:
        data=plain(raw)
        if data==old or data.is_relative_to(old) or data==new or data.is_relative_to(new):raise ValueError('Cannot retire a folder containing data')
    for row in value.get('relocations',[]):
        source,destination=plain(row['source']),plain(row['destination'])
        if source==old or not source.is_relative_to(old):raise ValueError('Asset source leaves former programme')
        if not any(destination.is_relative_to(plain(p)/'retained-assets'/note['id']) for p in note['data_paths']):
            raise ValueError('Asset destination leaves admitted data')
    if _legacy_prepared(value):_admit_legacy_clean(old,new,value)
    return value


def _legacy_prepared(value):
    return (isinstance(value.get('selection'),dict)
            and set(value['selection'])=={'settings','sources','git'} and not value.get('retirement'))


def _legacy_material(value):
    material={key:entry for key,entry in value.items() if key!='legacy_clean'}
    return hashlib.sha256(json.dumps(material,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _prepared_payload(new,value):
    current=payload(new)
    if _legacy_prepared(value):current.pop('baseline',None)  # Exact accepted old ABI, not a new app-source claim.
    return current


def _admit_legacy_clean(old,new,value):
    """The old helper cannot attest app preservation or all-copy retirement.

    This runs at the incoming helper's pre-exit identity check. The added app
    snapshot binds explicit discard consent; it never manufactures an old A.
    """
    from . import authored_baseline,code_transition,update_profile,update_source
    from .update_composition import app_inventory
    from .first_start import Places,plain,ready
    from .first_start_assets import snapshot
    from .python_source_updater import prepared_install_path
    note=value['note'];choice=read(update_source.choice_path(new))
    if not choice or choice.get('mode')!='clean' or choice.get('by') not in ('window','owner-words'):
        raise ValueError('Прежняя программа не имеет полного исходного снимка. До закрытия окна явно выбери чистую замену кода с сохранением данных. Если прежняя кнопка не предлагает этот режим, распакуй свежий архив в отдельную папку.')
    choice=update_source.selected(old,new)
    if (note.get('source_policy')!='clean' or note.get('source_choice')!=choice
            or read(new/'code-transition-plan.json').get('format')!='helene-clean-code-plan.v1'):
        raise ValueError('Прежняя программа не имеет полного исходного снимка. До закрытия окна явно выбери чистую замену кода с сохранением данных.')
    baseline=authored_baseline.validate(new)
    if app_inventory(new/'app')!=baseline['app']['files']:
        raise ValueError('Legacy clean update requires the exact incoming application')
    current=update_profile.selection(old)
    if {key:current[key] for key in ('settings','sources','git')}!=value['selection']:
        raise ValueError('Old settings/source changed after review')
    if value.get('scratch')!=scratch_scope(old,new):raise ValueError('Portable updater scratch identity differs')
    places=Places.current()
    if not ready(old,places):raise ValueError('Прежняя программа не подтверждена квитанцией данных в профиле')
    if not ready(new,places):raise ValueError('Подготовленная программа не подтверждена квитанцией данных в профиле')
    if any(plain(raw)!=places.data and not plain(raw).is_relative_to(places.data) for raw in note['data_paths']):
        raise ValueError('Данные подготовленной программы должны находиться в профиле')
    code_transition.validate(new)
    if _prepared_payload(new,value)!=value['payload']:raise ValueError('The prepared portable payload changed')
    assets={};destinations={}
    for row in value.get('relocations',[]):
        source,destination=plain(row['source']),plain(row['destination'])
        if destination.exists():raise ValueError('Legacy deferred asset destination already exists')
        if str(destination) in destinations and destinations[str(destination)]!=str(source):
            raise ValueError('Legacy asset destinations overlap')
        destinations[str(destination)]=str(source);assets[str(source)]=snapshot(source)
    from .portable_retirement import programme_snapshot
    proof={'format':LEGACY_CLEAN,'input_sha256':_legacy_material(value),'selection':current,
           'programme':programme_snapshot(old,places),
           'assets':assets,'baseline':{name:hashlib.sha256((new/name).read_bytes()).hexdigest()
                for name in ('python-app.json','source-update-baseline.json','app-upstream.zip','app-upstream.json','code-upstream.json')}}
    admitted=value.get('legacy_clean')
    if admitted is not None and admitted!=proof:raise ValueError('Legacy clean admission changed before shutdown')
    prepared=plain(prepared_install_path(new))
    if read(prepared)!=value:raise ValueError('Prepared legacy receipt changed before admission')
    if admitted is None:
        value['legacy_clean']=proof;write(prepared,value)
    return proof


def _retain_old(legacy,old):
    """The selected, canonical-receipted owner belongs to the captured batch."""
    from .first_start import receipt
    if old not in legacy['retained']:
        legacy['retained'].append(old);legacy['protected'].append(old);legacy['all_roots'].append(old)
        legacy['receipts'][str(old)]=hashlib.sha256(receipt(old).read_bytes()).hexdigest()
        if not any(old.is_relative_to(p) for p in legacy['retained_roots']):legacy['retained_roots'].append(old)


def promote_legacy_clean(old,new,value,progress=None):
    """Only after old-owner quiescence: replace deferred moves with copies."""
    if not _legacy_prepared(value):return value
    validate(old,new,value)
    from .first_start import Places,legacy_plan,configurations,_write,plain
    from .windows_first_start import Backend
    from .essential_backup import configs,load
    from .portable_profile import relative_config
    from .first_start_assets import prepare as prepare_assets,retain as retain_assets
    from . import portable_retirement,update_profile
    from .python_source_updater import prepared_install_path
    places=Places.current();legacy=legacy_plan(new,places,Backend(new));_retain_old(legacy,old)
    remap={str(plain(row['destination'])):str(plain(row['source'])) for row in value.get('relocations',[])}
    prepared=[];used=set()
    for config in configs(new):
        if not config.is_file():continue
        item=load(config)
        for block,field in (('backup','dir'),('voice','tts_reference')):
            entry=item.get(block)
            if not isinstance(entry,dict) or not entry.get(field):continue
            path=Path(entry[field]);path=plain(path if path.is_absolute() else config.parent/path)
            if str(path) in remap:entry[field]=remap[str(path)];used.add(str(path))
        relative=config.relative_to(new)
        prepared.append({'program':str(new),'directory':str((old/relative).parent),
                         'relative':relative.as_posix(),'tree':item['tree'],'config':item})
    if used!=set(remap):raise ValueError('Legacy deferred asset is not referenced by the prepared settings')
    profiles=configurations(legacy['retained'],places)
    assets=prepare_assets(places.data,legacy['retained'],[places.data],profiles+prepared,{},None)
    retain_assets(assets,keep_sources=legacy['retained'])
    retiring=[*legacy['old_roots'],*legacy['retained'],*[p for p in legacy['siblings'] if p.is_dir()]]
    from .standalone_relay import prepare_migration
    callback=(lambda row:progress.phase(row['stage'],**{key:entry for key,entry in row.items() if key!='stage'})) if progress else None
    for row in prepared:
        config=new/row['relative'];item=row['config']
        for previous in retiring:item=prepare_migration(previous,new,[item],callback)[0]
        _write(config,relative_config(item,config,new),private=True)
    if update_profile.selection(old)!=value['legacy_clean']['selection']:
        raise ValueError('Old settings/source changed during clean admission')
    retirement=portable_retirement.capture(new,legacy,places,assets,mode='update')
    portable_retirement.seal(new,retirement)
    portable_retirement.validate(new,retirement,places)
    value.update(selection=value['legacy_clean']['selection'],payload=payload(new),relocations=[],
        retirement={'id':retirement['id'],'sha256':portable_retirement.authority(retirement)})
    write(prepared_install_path(new),value)
    return value


def relocate_assets(value,*,restore=False):
    from .first_start import _move,plain
    rows=reversed(value.get('relocations',[])) if restore else value.get('relocations',[])
    for row in rows:
        source,destination=plain(row['source']),plain(row['destination'])
        if restore:source,destination=destination,source
        if source.exists():_move(source,destination)


def validate(old,new,value):
    from . import code_transition,update_profile
    identity(old,new,value)
    if value.get('scratch')!=scratch_scope(old,new):raise ValueError('Portable updater scratch identity differs')
    expected=value['legacy_clean']['selection'] if _legacy_prepared(value) else value['selection']
    if update_profile.selection(old)!=expected:raise ValueError('Old settings/source changed after review')
    code_transition.validate(new)
    if _prepared_payload(new,value)!=value['payload']:raise ValueError('The prepared portable payload changed')
    return value


@dataclass
class HealthWindow:
    """Monotonic continuous observations, never restored from wall time."""
    since:float|None=None
    previous:float|None=None
    identity:str|None=None
    def observe(self,identity,now):
        if identity is None:
            self.since=None;self.previous=None;self.identity=None;return False
        if (self.previous is None or now<self.previous or now-self.previous>15 or identity!=self.identity):
            self.since=now
        self.previous=now;self.identity=identity
        return now-self.since>=TRIAL_SECONDS


class TrialPaused(RuntimeError):
    """Explicit owner pause, distinct from missing or unconfirmed health."""


class TrialRestart(ValueError):
    """A changed generation or observation gap requires a new working trial."""


class _TrialIncomplete(ValueError):
    """A continuous but still short interval grants no retirement authority."""


VALIDATION_HEALTH_INTERVAL=5.0
VALIDATION_STOP_POLL=.2


def monitored_validation(program,window,validator,*,stopped=None):
    """Keep real health observations while one owned worker only reads bytes.

    No successful scan survives a pause/reset. Cancellation reaches per-file
    and per-chunk checkpoints, and all scan readers join before any return.
    """
    from .validation_read import ReadCancelled,scope
    stopped=stopped or (lambda:False)
    negative_at=None;checkpoint_at=None;stop_latched=False
    def stop_check(*,force=False):
        nonlocal negative_at,checkpoint_at,stop_latched
        now=time.monotonic();reversed_clock=checkpoint_at is not None and now<checkpoint_at
        checkpoint_at=now
        if not stop_latched and (force or reversed_clock or negative_at is None or now-negative_at>=VALIDATION_STOP_POLL):
            if stopped():stop_latched=True
            else:
                # Exit lookup can reread the agent roster. Cache only its
                # negative answer, from completion, never health or byte proof.
                negative_at=time.monotonic();checkpoint_at=max(now,negative_at)
        if stop_latched:
            window.observe(None,now)
            raise InterruptedError('Owner stopped final programme validation')
    stop_check(force=True)
    cancelled=threading.Event();expected=window.identity
    reading=threading.Event();reading.set()
    def read_check():
        while not reading.wait(.05):
            if cancelled.is_set():raise ReadCancelled('Final read validation was revoked')
        if cancelled.is_set():raise ReadCancelled('Final read validation was revoked')
    def scan():
        with scope(read_check):return validator()
    def sample():
        # Let current local read chunks finish, then give caller health priority
        # over the scan and its nested readers at their existing checkpoints.
        reading.clear()
        try:
            stop_check(force=True)
            # readiness stays on the caller, never the scan worker. Its bounded
            # RPC and byte reads retain Stop checks too.
            with scope(stop_check):observed=health(program)
            stop_check(force=True)
            if not observed:raise RuntimeError('Final working generation is unconfirmed')
            now=time.monotonic();previous=window.previous
            reset=previous is None or now<previous or now-previous>15 or observed!=expected
            admitted=window.observe(observed,now)
            if reset:
                raise TrialRestart('Final validation observed a gap or a new generation; restart the working trial')
            if not admitted:raise _TrialIncomplete('Five continuous healthy minutes are not confirmed')
        finally:reading.set()
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='helene-final-read')
    next_sample=time.monotonic()+VALIDATION_HEALTH_INTERVAL
    future=pool.submit(scan)
    try:
        while True:
            stop_check()
            done,_=wait((future,),timeout=VALIDATION_STOP_POLL)
            if done:
                result=future.result()  # A real validation error precedes readiness.
                sample()
                stop_check(force=True)
                return result
            if time.monotonic()>=next_sample:
                sample_started=time.monotonic();sample();next_sample=sample_started+VALIDATION_HEALTH_INTERVAL
    except BaseException as pending:
        cancelled.set();reading.set()
        # observe() already started a fresh interval for a positive new
        # generation/gap. Preserve that first observation, never the old credit.
        if not isinstance(pending,(_TrialIncomplete,TrialRestart)):window.observe(None,time.monotonic())
        pool.shutdown(wait=True,cancel_futures=True)
        try:future.result()
        except (ReadCancelled,CancelledError):pass
        except BaseException:
            stop_check(force=True)  # Owner Stop has priority, other validation errors stay real.
            raise
        stop_check(force=True)
        raise pending
    finally:
        cancelled.set();reading.set();pool.shutdown(wait=True,cancel_futures=True)


def trial_paused(program):
    """Read explicit pause intent for these agents without waking any runner."""
    from . import agents,owner_stop
    for agent in agents.raisable(Path(program).resolve()):
        generation=owner_stop.pause_generation(agent.tree)
        if owner_stop.present(agent.tree/'memory/.control/autonomy-paused.json') or (generation is not None and generation['paused']):
            return True
    return False


def health(new):
    """Exact live generation with fresh, explicitly working per-agent readers."""
    import psutil
    from .validation_read import checkpoint
    checkpoint()
    from . import session_host,agents,owner_stop
    new=Path(new).resolve();descriptor=read(session_host.control_path(new))
    if Path(descriptor.get('program','')).resolve()!=new or session_host.process_state(descriptor)!='alive':
        raise RuntimeError('The admitted new session is not running')
    proofs=[]
    for agent in agents.raisable(new):
        checkpoint()
        pause=agent.tree/'memory/.control/autonomy-paused.json'
        generation=owner_stop.pause_generation(agent.tree)
        if owner_stop.present(pause) or (generation is not None and generation['paused']):
            raise TrialPaused('Trial agent is paused; former programmes retained')
        proof=session_host._rpc(descriptor,'readiness',{'agent':agent.id,'timeout':2,'require_runner':True},timeout=7)
        checkpoint()
        if not proof.get('runner_pid'):raise RuntimeError('A window/channel alone does not confirm a working agent tree')
        if Path(proof.get('tree','')).resolve()!=agent.tree.resolve():raise ValueError('Trial data identity differs')
        code=Path(agents.read_config(agent.config)['code']);code=code if code.is_absolute() else agent.config.parent/code
        if not code.resolve().is_relative_to(new):raise ValueError('Trial still imports former code')
        if proof.get('runner_pid') and Path(proof.get('code','')).resolve()!=code.resolve():raise ValueError('Trial runner imports another tree')
        reader=read(agent.tree/'memory/.control/desk_inbox/.reader.json')
        if (type(reader.get('pid')) is not int or reader['pid']!=proof['runner_pid']
                or not isinstance(reader.get('started'),str) or not reader['started'].strip()):
            raise RuntimeError('Trial runner reader identity is unconfirmed')
        try:fresh=0<=time.time()-float(reader.get('at',0))<15
        except (TypeError,ValueError,OverflowError):fresh=False
        if not fresh:raise RuntimeError('Trial runner working heartbeat is unconfirmed')
        if Path(reader.get('code','')).resolve()!=code.resolve():raise ValueError('Trial reader imports another tree')
        autonomy=reader.get('autonomy')
        if (not isinstance(autonomy,dict) or type(autonomy.get('protocol')) is not int
                or autonomy['protocol']!=1 or autonomy.get('paused') is not False or autonomy.get('stopped') is not False):
            raise RuntimeError('Trial runner is paused, stopped or its autonomy is unconfirmed')
        try:births={key:psutil.Process(proof[key]).create_time() for key in ('channel_pid','runner_pid') if proof.get(key)}
        except (psutil.NoSuchProcess,psutil.AccessDenied) as exc:
            raise RuntimeError('Trial process birth is unconfirmed; former programmes retained') from exc
        latest_generation=owner_stop.pause_generation(agent.tree)
        if owner_stop.present(pause) or (latest_generation is not None and latest_generation['paused']):
            raise TrialPaused('Trial agent is paused; former programmes retained')
        if latest_generation!=generation:
            raise RuntimeError('Trial autonomy changed during readiness; former programmes retained')
        # Heartbeat/activity timestamps are observations, never generation
        # identity: an idle working runner must be able to earn five minutes.
        proofs.append({**proof,'births':births,'reader':{'pid':reader['pid'],'started':reader['started'],
            'code':str(code.resolve()),'autonomy':{key:autonomy[key] for key in ('protocol','paused','stopped')},
            'autonomy_generation':generation['id'] if generation is not None else None}})
    if not proofs:raise RuntimeError('No agent tree is running')
    material={'generation':descriptor['generation'],'pid':descriptor['pid'],'birth':descriptor['birth'],
              'proofs':proofs,'code':payload(new,runtime=False)}
    return hashlib.sha256(json.dumps(material,sort_keys=True).encode()).hexdigest()


def finish(old,new,value,window,*,stopped=None):
    """Only called by the live trial owner after HealthWindow admits success."""
    if stopped and stopped():raise InterruptedError('Owner stopped before final programme validation')
    # Old ABI adoption can write a prepared receipt. It must already have been
    # promoted by the caller before entering this strictly read-only worker.
    if _legacy_prepared(value):raise ValueError('Legacy clean update must be promoted before final validation')
    identity(old,new,value)
    if value.get('scratch')!=scratch_scope(old,new):raise ValueError('Portable updater scratch identity differs')
    from .update_profile import selection
    def checked():
        validate(old,new,value)
        if selection(old)!=value['selection']:raise ValueError('Former programme changed during the trial; cleanup requires attention')
    monitored_validation(new,window,checked,stopped=stopped)
    if stopped and stopped():raise InterruptedError('Owner stopped before programme retirement')
    old,new=Path(old),Path(new)
    if value.get('retirement'):
        from . import portable_retirement
        retirement=read(portable_retirement.path(new))
        if value['retirement']!={'id':retirement.get('id'),'sha256':portable_retirement.authority(retirement)}:
            raise ValueError('Retirement batch changed after preparation')
        portable_retirement.commit(new,retirement,window)
        cleanup_work(new,value)
        from .first_start import receipt,_write
        completed=read(receipt(new));completed['update']={'id':value['note']['id'],
            'confirmed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'healthy_seconds':TRIAL_SECONDS}
        _write(receipt(new),completed)
        note=read(new/'python-migration.json');note.update(state='completed',healthy_seconds=TRIAL_SECONDS)
        write(new/'python-migration.json',note)
        return
    from .windows_first_start import Backend
    backend=Backend(new);backend.roots=[old]
    # Do not classify the NEW owner as an orphan of shared data during cleanup.
    backend.protected=[];backend.keep=[Path(p) for p in value['note']['data_paths']]
    backend.assert_quiet([old])
    backend.shortcuts([old],new)
    cleanup_work(new,value)
    backend.remove(old)
    if old.exists():raise RuntimeError('Former programme still exists')
    from .first_start import receipt,_write
    completed=read(receipt(new));completed['update']={'id':value['note']['id'],
        'confirmed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'healthy_seconds':TRIAL_SECONDS}
    _write(receipt(new),completed)
    note=read(new/'python-migration.json');note.update(state='completed',healthy_seconds=TRIAL_SECONDS)
    write(new/'python-migration.json',note)


def cleanup_work(new,value):
    """Exact finished update/review tasks only; other owner tasks remain."""
    import re
    from .windows_first_start import Backend
    from .first_start import plain
    from . import source_update
    new=Path(new);record=read(new/'code-transition-plan.json')
    for row in record.get('plans',[]):
        directory=plain(row['directory']);operation=read(directory/'pipeline.json')
        tree=plain(directory.parents[2])
        if operation.get('tree') and plain(operation['tree'])!=tree:raise ValueError('Update work tree identity differs')
        if tree not in [plain(p) for p in value['note']['data_paths']] or directory.parent!=tree/'memory/.source-updates':
            raise ValueError('Update work directory leaves admitted data scope')
        ids={str(operation.get('task_id') or '')}
        reviews={str(operation.get('review_id') or '')}
        for attempt in operation.get('attempts',[]):
            ids.add(str(attempt.get('task_id') or ''));reviews.add(str(attempt.get('review_id') or ''))
        paths=[directory]
        for review in reviews- {''}:
            if not re.fullmatch(r'review-[0-9a-f]{16}',review):raise ValueError('Invalid update review identity')
            isolated=(operation.get('coordinator') or {}).get('review_store')=='isolated-v1'
            store=tree/'memory'/('.source-update-reviews' if isolated else '.self-review')/review
            request=read(store/'request.json');ids.add(str(request.get('task_id') or ''));paths.append(store)
        for task in ids- {''}:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',task):raise ValueError('Invalid update task identity')
            paths.append(tree/'memory/.forge/tasks'/task)
        current=read(source_update.path(tree))
        if operation.get('id') and current.get('id')==operation['id']:source_update.path(tree).unlink(missing_ok=True)
        for path in paths:
            if not path.exists():continue
            backend=Backend(new);backend.roots=[path];backend.stop([path]);backend.remove(path)


def scratch_scope(old,new,*,required=True):
    """Only the dedicated in-app staging folder grants scratch custody."""
    from .first_start import plain,branded
    from .program_state import _identity
    old,new=plain(old),plain(new)
    parent=old.parent
    if old.name.casefold()=='helene' and branded(parent.name):parent=parent.parent
    prefix='Helene-update-';name=new.parent.name
    if new.name!='Helene' or not name.startswith(prefix):
        if required:raise ValueError('Portable update scratch must be a dedicated staging folder')
        return None
    operation=_identity(name[len(prefix):])
    if new.parent!=parent/(prefix+operation):
        if required:raise ValueError('Portable update scratch leaves the former programme parent')
        return None
    return {'format':'helene-portable-scratch.v1','folder':str(new.parent),'operation':operation}


def scratch_paths(args,value):
    """Validate all mechanical entries before closing a stream or deleting anything."""
    import re
    from .first_start import plain
    from .python_source_updater import prepared_install_path
    old,new=plain(args.old),plain(args.new)
    identity(old,new,value)
    proof=scratch_scope(old,new)
    if value.get('scratch')!=proof or plain(args.staging)!=new.parent:raise ValueError('Updater scratch custody differs')
    admission=args.admission_name
    if not isinstance(admission,str) or not re.fullmatch(r'updater-admitted(?:-[0-9a-f]{32})?\.json',admission):
        raise ValueError('Invalid updater admission receipt name')
    paths=[plain(new.parent/name) for name in ('python_source_updater.py','update.log',admission,prepared_install_path(new).name)]
    for path in paths:
        if path.exists() and not path.is_file():raise ValueError('Updater scratch entry is not a regular file')
    helper,log,admitted,prepared=paths
    if helper.is_file():
        original=plain(new/'app/localharness/python_source_updater.py')
        if not original.is_file() or hashlib.sha256(helper.read_bytes()).digest()!=hashlib.sha256(original.read_bytes()).digest():
            raise ValueError('Updater helper receipt changed')
    if admitted.is_file():
        row=read(admitted)
        if row.get('target')!=str(old) or row.get('pid')!=os.getpid():raise ValueError('Updater admission owner changed')
    if prepared.is_file():
        row=read(prepared)
        if any(row.get(key)!=value.get(key) for key in ('format','old','new')) or (row.get('note') or {}).get('id')!=value['note']['id']:
            raise ValueError('Prepared updater receipt changed')
    if log.is_file():
        observed=log.stat()
        if not any(_same_file(number,observed) for number in (1,2)):raise ValueError('Updater does not own its mechanical log')
    return paths


def _same_file(number,expected):
    try:actual=os.fstat(number)
    except OSError:return False
    return (actual.st_dev,actual.st_ino)==(expected.st_dev,expected.st_ino)


def cleanup_staging(args,value):
    """The candidate's outer folder remains; its mechanical scratch goes."""
    from .windows_first_start import Backend
    new=args.new.resolve();paths=scratch_paths(args,value);backend=Backend(new)
    log=new.parent/'update.log';observed=log.stat() if log.is_file() else None
    for number,stream in ((1,sys.stdout),(2,sys.stderr)):
        if observed is None or not _same_file(number,observed):continue
        try:
            if stream is not None:stream.close()
        except (OSError,ValueError):pass
        # Python's standard FileIO uses closefd=False. Closing the wrapper
        # suppresses final-flush errors; closing the CRT descriptor releases
        # the Windows handle so our own log can actually disappear.
        try:os.close(number)
        except OSError:pass
    for path in paths:
        backend.roots=[path]  # an outer-folder scope would include the NEW owner
        if path.exists():backend.remove(path)


def run(args,value,report,stopped):
    """The admitted helper runs from NEW runtime, outside the old directory."""
    old,new=args.old.resolve(),args.new.resolve();validate(old,new,value);scratch_paths(args,value)
    from .update_progress import Progress
    from .program_state import progress as progress_path
    progress=Progress(progress_path(new,value['note']['id']),value['note']['id'],new)
    try:return _trial(args,value,progress,stopped)
    except BaseException as error:
        progress.phase('failed',error=str(error),new_tree_retained=True,
            cleanup_required=bool(value.get('_retirement_started')))
        raise


def _trial(args,value,progress,stopped):
    old,new=args.old.resolve(),args.new.resolve()
    report=progress.phase
    from .python_source_updater import _job_report
    _job_report(args,'handed_off',progress=str(progress.path))
    from . import migration_lifecycle
    migration_lifecycle.stop(old);migration_lifecycle.quiescent(old)
    if stopped():raise InterruptedError('Owner stopped before portable switch')
    promote_legacy_clean(old,new,value,progress)
    if value.get('retirement'):
        from .first_start import sweep_legacy
        sweep_legacy(new,progress=lambda row:report(row['stage'],**{key:entry for key,entry in row.items() if key!='stage'}))
        validate(old,new,value)
    relocate_assets(value)
    if value.get('retirement'):
        from . import retirement_update
        with retirement_update.owner(args,value):return _trial_admitted(args,value,progress,stopped)
    return _trial_admitted(args,value,progress,stopped)


def _trial_admitted(args,value,progress,stopped):
    old,new=args.old.resolve(),args.new.resolve();report=progress.phase
    from .python_source_updater import _job_report
    if stopped():raise InterruptedError('Owner stopped before new portable launch')
    report('starting',label='Открываем новую версию')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    process=subprocess.Popen([str(new/'helene.exe'),'--migration',value['note']['id']],cwd=new,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
    deadline=time.monotonic()+120
    while True:
        if stopped():raise InterruptedError('Owner stopped new-tree trial')
        note=read(new/'python-migration.json')
        if note.get('id')==value['note']['id'] and note.get('state')=='new_app_started':break
        if process.poll() not in (None,0):raise RuntimeError('New portable window failed')
        if time.monotonic()>deadline:raise TimeoutError('New tree startup was not confirmed')
        time.sleep(.2)
    window=HealthWindow();deadline=time.monotonic()+TRIAL_SECONDS*2
    while True:
        if stopped():raise InterruptedError('Owner stopped new-tree trial; former folder retained')
        now=time.monotonic()
        # A suspended host can resume hours later. It earns no healthy time,
        # but the next fresh trial still gets its five actual working minutes.
        if window.previous is not None and now-window.previous>TRIAL_SECONDS:
            deadline=now+TRIAL_SECONDS*2
        if trial_paused(new):
            window.observe(None,now);deadline=now+TRIAL_SECONDS*2
            report('paused',label='Проверка приостановлена',old_programmes_retained=True)
            time.sleep(.2)
            continue
        try:observed=health(new)
        except TrialPaused:
            window.observe(None,now);deadline=now+TRIAL_SECONDS*2
            continue  # A rapid Resume does not erase the observed pause.
        except (OSError,RuntimeError,ValueError,KeyError):
            window.observe(None,now)
            if trial_paused(new):continue
            raise
        if window.observe(observed,time.monotonic()):
            try:
                if health(new)!=window.identity:raise ValueError('New tree changed before retirement')
                if stopped():raise InterruptedError('Owner stopped before retirement')
                scratch_paths(args,value)
                value['_retirement_started']=True
                report('probation',label='Проверяем файлы новой версии',seconds=None,total=None)
                finish(old,new,value,window,stopped=stopped)
            except (TrialPaused,TrialRestart):
                # commit() marks its journal before any backend effects. A
                # pause during effects must remain partial, never restart a
                # trial or roll back to a potentially deleted former tree.
                if value.get('retirement'):
                    from . import portable_retirement
                    if read(portable_retirement.path(new)).get('state') in ('commit-started','partial','completed'):raise
                value.pop('_retirement_started',None)
                window.observe(None,time.monotonic());deadline=time.monotonic()+TRIAL_SECONDS*2
                continue
            break
        report('probation',label='Проверяем работу нового дерева',seconds=int(time.monotonic()-(window.since or time.monotonic())),total=TRIAL_SECONDS)
        if time.monotonic()>deadline:raise TimeoutError('New-tree generation keeps changing; former folder retained')
        time.sleep(5)
    report('completed',new_ready_observed_at=time.time(),installed_root=str(new))
    _job_report(args,'completed',result={'installed_root':str(new),'effect':'committed','healthy_seconds':TRIAL_SECONDS})
    cleanup_staging(args,value)
    return 0
