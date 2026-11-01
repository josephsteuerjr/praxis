"""One bounded, receipt-derived five-minute retirement for archive and Update.

The immutable plan is sealed by first-start admission. Progress never restores
elapsed wall time, and a commit never discovers additional deletion targets.
"""
from pathlib import Path
from contextlib import contextmanager
import datetime as dt
import copy,hashlib,json,os,subprocess,sys,time,uuid
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]));__package__='localharness'
from .package_notice import read

FORMAT='helene-portable-retirement.v1'


def path(program):return Path(program)/'.helene/retirement.json'


def authority(value):
    material={key:value[key] for key in ('format','id','program','data','mode','targets','assets','payload')}
    for key in ('completion','supersession'):
        if key in value:material[key]=value[key]
    return hashlib.sha256(json.dumps(material,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def programme_snapshot(program,places):
    from .validation_read import checkpoint,digest
    checkpoint()
    from .first_start import configurations,plain,ready,receipt
    from .source_merge import inventory
    program=plain(program)
    if not ready(program,places):raise ValueError('Прежняя программа не подтверждена квитанцией')
    from .update_composition import app_inventory
    with receipt(program).open('rb') as stream:receipt_sha=digest(stream)
    result={'receipt':receipt_sha,'configs':{},'code':{},
            'app':app_inventory(program/'app')}
    for row in configurations([program],places):
        checkpoint()
        data=Path(row['tree'])
        if data!=places.data and not data.is_relative_to(places.data):
            raise ValueError('Прежняя квитанция не соответствует расположению данных в профиле')
        config=plain(Path(row['directory'])/'helene.json')
        with config.open('rb') as stream:result['configs'][config.relative_to(program).as_posix()]=digest(stream)
        code=Path(row['config'].get('code') or 'tree')
        code=plain(code if code.is_absolute() else config.parent/code)
        if not code.is_relative_to(program):raise ValueError('Прежний код находится вне папки программы')
        result['code'][str(code)]=inventory(code) if code.is_dir() else None
    return result


def programme_payload(program,mode):
    from .portable_switch import payload
    from .validation_read import checkpoint,digest
    checkpoint()
    value=copy.deepcopy(payload(program))
    if mode=='archive':value.pop('settings',None)  # First agent setup is legitimate during a manual trial.
    value['transitions']={}
    for name in ('code-transition-plan.json','code-transition.json'):
        checkpoint();source=Path(program)/name
        if source.is_file():
            with source.open('rb') as stream:value['transitions'][name]=digest(stream)
    return value


def capture(program,plan,places,assets,*,mode):
    from .first_start import plain,validate_legacy_plan,_write,guard_cleanup_data
    if mode not in ('archive','update'):raise ValueError('Unknown retirement caller')
    program=plain(program);validate_legacy_plan(plan,places)
    roots=[]
    for root in sorted(set(plan.get('retained_roots',plan['retained'])),key=lambda p:len(p.parts)):
        root=plain(root)
        if any(root.is_relative_to(parent) for parent in roots):continue
        if root==places.data or root.is_relative_to(places.data) or places.data.is_relative_to(root):
            raise ValueError('Retirement container contains profile data')
        if root==program or root.is_relative_to(program) or program.is_relative_to(root):
            # Never retire a shared outer directory containing the new version.
            members=[p for p in plan['retained'] if p.is_relative_to(root)]
            for member in members:
                if member!=program and not (program.is_relative_to(member) or member.is_relative_to(program)):
                    roots.append(member)
                else:raise ValueError('Распакуй новую версию в отдельную папку, вне прежней программы')
            continue
        roots.append(root)
    targets=[]
    guard_cleanup_data(roots,places,[places.data])
    for root in roots:
        programs=[p for p in plan['retained'] if p==root or p.is_relative_to(root)]
        if not programs:raise ValueError('Retirement container has no admitted programme')
        stamp=root.stat()
        targets.append({'root':str(root),'directory':[stamp.st_dev,stamp.st_ino],
            'programs':{str(p):programme_snapshot(p,places) for p in programs}})
    copied=[{key:row[key] for key in ('source','destination','snapshot')}
            for row in assets.get('moves',[]) if row.get('state')=='copied']
    value={'format':FORMAT,'id':uuid.uuid4().hex,'program':str(program),'data':str(plain(places.data)),
        'mode':mode,'targets':targets,'assets':copied,'removed':[],
        'payload':programme_payload(program,mode) if targets else None,
        'state':'prepared' if targets else 'completed'}
    from . import update_supersession
    from .first_start import configurations
    profiles=configurations(plan['retained'],places)
    value['supersession']=update_supersession.capture(program,plan['retained'],
        list(dict.fromkeys([places.data,*[Path(row['tree']) for row in profiles]])))
    _write(path(program),value,private=True)
    return value


def seal(program,value):
    from .first_start import receipt,load,_write
    record=load(receipt(program));record['retirement']={'id':value['id'],'sha256':authority(value)}
    _write(receipt(program),record,private=True)


def validate(program,value,places):
    from .validation_read import checkpoint
    checkpoint()
    from .first_start import plain,ready,receipt,load,guard_cleanup_data
    program=plain(program)
    if (value.get('format')!=FORMAT or value.get('program')!=str(program)
            or value.get('data')!=str(plain(places.data)) or not ready(program,places)):
        raise ValueError('Retirement programme/data identity differs')
    if load(receipt(program)).get('retirement')!={'id':value['id'],'sha256':authority(value)}:
        raise ValueError('Retirement plan differs from its admission receipt')
    removed=value.get('removed',[])
    if len(removed)!=len(set(removed)) or set(removed)-{row['root'] for row in value['targets']}:
        raise ValueError('Invalid retirement completion record')
    deleting=value.get('deleting')
    if deleting is not None and (deleting not in {row['root'] for row in value['targets']} or deleting in removed):
        raise ValueError('Invalid in-flight retirement record')
    if value['targets'] and programme_payload(program,value['mode'])!=value.get('payload'):
        raise ValueError('Prepared new programme/runtime changed during the trial')
    from .essential_backup import configs
    retired=[plain(row['root']) for row in value['targets']]
    def independent(item,directory):
        checkpoint()
        if isinstance(item,dict):
            for child in item.values():independent(child,directory)
        elif isinstance(item,list):
            for child in item:independent(child,directory)
        elif isinstance(item,str) and item and '://' not in item:
            candidate=Path(item)
            if candidate.is_absolute() or '/' in item or '\\' in item:
                candidate=(candidate if candidate.is_absolute() else directory/candidate).resolve()
                if any(candidate==root or candidate.is_relative_to(root) for root in retired):
                    raise ValueError('Новые настройки ещё используют прежнюю папку программы')
    if not (program/'helene.json').is_file():raise ValueError('Current programme settings are missing')
    for config in configs(program):
        checkpoint()
        if config.is_file():independent(load(config),config.parent)
    for row in value['targets']:
        checkpoint()
        root=plain(row['root'])
        if any(root==p or root.is_relative_to(p) or p.is_relative_to(root) for p in (program,places.data)):
            raise ValueError('Retirement overlaps current programme or profile data')
        if str(root) in removed:
            if root.exists():raise ValueError('A retired path has been reused; it is retained')
            continue
        if str(root)==deleting and not root.exists():continue
        guard_cleanup_data([root],places,[places.data])
        stamp=root.stat()
        if [stamp.st_dev,stamp.st_ino]!=row['directory']:raise ValueError('Retirement directory was replaced')
        if not row['programs']:raise ValueError('No receipt protects the captured programme')
        for raw,snapshot in row['programs'].items():
            checkpoint()
            member=plain(raw)
            if member!=root and not member.is_relative_to(root):raise ValueError('Receipt leaves retirement container')
            if programme_snapshot(member,places)!=snapshot:raise ValueError('Прежняя программа изменилась во время проверки')
    from .first_start_assets import snapshot
    for asset in value['assets']:
        checkpoint()
        source,destination=plain(asset['source']),plain(asset['destination'])
        owners=[row for row in value['targets'] if source.is_relative_to(plain(row['root']))]
        if not owners or not destination.is_relative_to(places.data/'retained-assets'):
            raise ValueError('Retained trial assets leave admitted scope')
        source_gone=owners[0]['root'] in removed or (owners[0]['root']==deleting and not Path(deleting).exists())
        if not source_gone and snapshot(source)!=asset['snapshot']:
            raise ValueError('Прежние файлы изменились во время проверки')
        if snapshot(destination)!=asset['snapshot']:raise ValueError('Copied trial assets changed')
    return value


def _validate_target(row,value,places):
    """Recheck only the next old target across slow unrelated cleanup steps."""
    from .first_start import plain,guard_cleanup_data
    from .first_start_assets import snapshot
    from .validation_read import checkpoint
    checkpoint()
    root=plain(row['root']);guard_cleanup_data([root],places,[places.data]);stamp=root.stat()
    if [stamp.st_dev,stamp.st_ino]!=row['directory']:raise ValueError('Retirement directory was replaced')
    for raw,previous in row['programs'].items():
        checkpoint()
        if programme_snapshot(plain(raw),places)!=previous:raise ValueError('Former programme changed before deletion')
    for asset in value['assets']:
        checkpoint()
        source=plain(asset['source'])
        if source.is_relative_to(root):
            if snapshot(source)!=asset['snapshot'] or snapshot(plain(asset['destination']))!=asset['snapshot']:
                raise ValueError('Retained asset changed before programme deletion')


def commit(program,value,window,*,places=None,backend=None):
    from .first_start import Places,plain,_write,archive_profiles,configurations
    from .portable_switch import monitored_validation,TRIAL_SECONDS
    places=places or Places.current();program=plain(program)
    from . import owner_stop,session_exit
    if owner_stop.stopped() or session_exit.requested(program):raise InterruptedError('Owner stopped programme retirement')
    from . import retirement_update
    def stopped():return owner_stop.stopped() or session_exit.requested(program) or retirement_update.cancelled(value)
    # Whole batch before any effects, with live observations while all bytes
    # are re-read. The callback never stops or mutates a programme.
    monitored_validation(program,window,lambda:validate(program,value,places),stopped=stopped)
    if retirement_update.cancelled(value):raise InterruptedError('Owner cancelled this update; former programmes retained')
    if backend is None:
        from .windows_first_start import Backend
        backend=Backend(program)
    backend.excluded=[program]
    deleting=value.get('deleting')
    if deleting and not Path(deleting).exists():value['removed'].append(deleting);value.pop('deleting',None)
    roots=[plain(row['root']) for row in value['targets'] if row['root'] not in value['removed']]
    value.update(state='commit-started',healthy_seconds=TRIAL_SECONDS)
    _write(path(program),value,private=True)
    try:
        backend.stop(roots,())  # Canonical data never grants killing affinity.
        monitored_validation(program,window,lambda:validate(program,value,places),stopped=stopped)
        if value.get('supersession'):
            from . import update_supersession
            settlement=update_supersession.retire(value['supersession'])
            value['supersession_result']=settlement
            _write(path(program),value,private=True)
            if settlement['blocked']:raise RuntimeError('Previous update producers remain unconfirmed; former programmes retained')
        profiles=configurations([Path(p) for row in value['targets'] if row['root'] not in value['removed'] for p in row['programs']],places)
        from .first_start_assets import prepare as prepare_assets,retain as retain_assets
        assets=prepare_assets(places.data,roots,[places.data],profiles,{},None)
        retain_assets(assets,keep_sources=roots)
        archive_profiles(profiles,places.data)
        getattr(backend,'committed_data',lambda _target:None)(places.data)
        for root in roots:
            if owner_stop.stopped() or session_exit.requested(program):raise InterruptedError('Owner stopped programme retirement')
            if retirement_update.cancelled(value):raise InterruptedError('Owner cancelled this update; remaining former programmes retained')
            captured=next(row for row in value['targets'] if row['root']==str(root))
            monitored_validation(program,window,lambda:_validate_target(captured,value,places),stopped=stopped)
            backend.assert_quiet([root])
            if stopped():raise InterruptedError('Owner stopped before programme retirement')
            value['deleting']=str(root);_write(path(program),value,private=True)
            backend.retire(root)
            if root.exists():
                monitored_validation(program,window,lambda:_validate_target(captured,value,places),stopped=stopped)
                if owner_stop.stopped() or session_exit.requested(program) or retirement_update.cancelled(value):
                    raise InterruptedError('Owner stopped before programme deletion')
                backend.remove(root)
            if root.exists():raise RuntimeError('Former programme remains')
            value['removed'].append(str(root));value.pop('deleting',None);_write(path(program),value,private=True)
        if stopped():raise InterruptedError('Owner stopped before final retirement shortcuts')
        backend.shortcuts(roots,program)
        value.update(state='completed',completed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        _write(path(program),value,private=True)
    except BaseException as error:
        value.update(state='partial',error=str(error));_write(path(program),value,private=True)
        raise
    return value


@contextmanager
def lease(program,identity,*,timeout_ms=20000):
    """Independent helpers cannot both own one captured retirement batch."""
    from .windows_first_start import kernel
    from ctypes import WinError,get_last_error
    api=kernel();key=hashlib.sha256((str(Path(program).resolve())+identity).encode()).hexdigest()
    handle=api.CreateMutexW(None,False,'Local\\HeleneRetirement-'+key)
    if not handle:raise WinError(get_last_error())
    acquired=False
    try:
        acquired=api.WaitForSingleObject(handle,timeout_ms) in (0,0x80)
        yield acquired
    finally:
        if acquired:api.ReleaseMutex(handle)
        api.CloseHandle(handle)


def start(program,host=None,*,owner=None):
    """The authenticated session owns this helper's Job, including on Exit."""
    program=Path(program);value=read(path(program))
    if not value or value.get('mode') not in ('archive','update'):return {'started':False}
    if value.get('state')=='completed' and (value['mode']=='archive' or value.get('finalized_at')):return {'started':False,'completed':True}
    if owner is None:
        if host is not None and callable(getattr(host,'retirement',None)):return host.retirement()
        return {'started':False,'session_required':True}
    from .update_process import interpreter_for,permanent_environment
    from .program_state import directory
    with owner.lock:
        if owner.exiting or owner.stop.is_set():return {'started':False,'exiting':True}
        previous=getattr(owner,'retirement_helper',None)
        if previous is not None and previous.handle is not None and previous.poll() is None:
            return {'started':False,'running':True,'pid':previous.pid}
        command=[str(interpreter_for(program)),'-B','-s',str(program/'app/localharness/portable_retirement.py'),
             '--program',str(program),'--identity',value['id'],'--sha256',authority(value)]
        owner.retirement_helper=owner.background(command,directory(program,'logs')/'retirement-worker.log',permanent_environment())
        return {'started':True,'pid':owner.retirement_helper.pid}


def run(program,identity,expected):
    from .first_start import Places,_write
    from .portable_switch import HealthWindow,health,trial_paused,TrialPaused,TrialRestart,TRIAL_SECONDS
    from . import owner_stop,session_exit
    from .update_progress import Progress
    from .program_state import progress as progress_path
    import psutil
    places=Places.current();value=read(path(program))
    if value.get('id')!=identity or authority(value)!=expected:raise ValueError('Retirement helper admission differs')
    # One watcher for the programme, even with multiple attached windows.
    with lease(program,'watcher-'+identity,timeout_ms=0) as watching:
        if not watching:return
        while True:
            if owner_stop.stopped() or session_exit.requested(program):raise InterruptedError('Owner stopped the trial')
            with lease(program,identity,timeout_ms=250) as acquired:
                if not acquired:continue
                value=read(path(program))
                if value.get('id')!=identity or authority(value)!=expected:raise ValueError('Retirement authority changed while waiting')
                validate(program,value,places)
                if value['state']=='completed' and (value['mode']=='archive' or value.get('finalized_at')):return
                if value['mode']=='update':
                    from . import retirement_update,session_host
                    context,_prepared=retirement_update._context(program,value)
                    if value['state']!='completed' and retirement_update.cancelled(value):
                        raise InterruptedError('Owner cancelled this update')
                    # An available/abandoned mutex proves no lease owner, not
                    # the death of the original producer or its rollback path.
                    deadline=time.monotonic()+15
                    while session_host.process_state(context['producer']) not in ('absent','changed'):
                        if owner_stop.stopped() or session_exit.requested(program):raise InterruptedError('Owner stopped recovery')
                        if time.monotonic()>deadline:raise RuntimeError('Previous updater exit remains unconfirmed')
                        time.sleep(.2)
                operation=(value.get('completion') or {}).get('prepared',{}).get('note',{}).get('id',identity)
                reporter=Progress(progress_path(program,operation),operation,program)
                window=HealthWindow()
                value['owner']={'pid':os.getpid(),'birth':psutil.Process().create_time()}
                _write(path(program),value,private=True)
                try:
                    if value['state']!='completed':
                        value['state']='probation';_write(path(program),value,private=True)
                        while True:
                            if owner_stop.stopped() or session_exit.requested(program):raise InterruptedError('Owner stopped the trial')
                            if value['mode']=='update' and retirement_update.cancelled(value):raise InterruptedError('Owner cancelled this update')
                            if trial_paused(program):
                                window.observe(None,time.monotonic())
                                if value['state']!='paused':
                                    value['state']='paused';_write(path(program),value,private=True)
                                reporter.phase('paused',label='Проверка приостановлена',old_programmes_retained=True)
                                time.sleep(.2)
                                continue
                            if value['state']=='paused':
                                value['state']='probation';_write(path(program),value,private=True)
                            try:observed=health(program)
                            except TrialPaused:
                                window.observe(None,time.monotonic())
                                continue
                            except (OSError,RuntimeError,ValueError,KeyError):
                                window.observe(None,time.monotonic())
                                if trial_paused(program):continue
                                raise
                            if window.observe(observed,time.monotonic()):
                                reporter.phase('probation',label='Проверяем файлы новой версии',seconds=None,total=None)
                                try:commit(program,value,window,places=places)
                                except (TrialPaused,TrialRestart):
                                    if value['state'] in ('commit-started','partial','completed'):raise
                                    window.observe(None,time.monotonic())
                                    continue
                                break
                            reporter.phase('probation',label='Проверяем работу новой версии',
                                seconds=int(time.monotonic()-(window.since or time.monotonic())),total=TRIAL_SECONDS)
                            # Stop remains responsive between health observations.
                            until=time.monotonic()+5
                            while time.monotonic()<until:
                                if owner_stop.stopped() or session_exit.requested(program):raise InterruptedError('Owner stopped the trial')
                                time.sleep(.2)
                    if value['mode']=='update':
                        retirement_update.finalize(program,value)
                    reporter.phase('completed',installed_root=str(program),healthy_seconds=TRIAL_SECONDS)
                    return
                except BaseException as error:
                    if value['state'] not in ('partial','completed'):
                        value.update(state='prepared',error=str(error));_write(path(program),value,private=True)
                    reporter.phase('failed',error=str(error),old_programmes_retained=value['state']!='completed')
                    raise


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--program',type=Path,required=True)
    parser.add_argument('--identity',required=True);parser.add_argument('--sha256',required=True)
    args=parser.parse_args();run(args.program,args.identity,args.sha256)
