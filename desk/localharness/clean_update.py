"""Owner-selected clean folder switch: extraction is the programme admission.

There is deliberately no old/new source, runtime or file inventory and no trial.
Only selected data pointers and process/startup custody constrain retirement.
"""
from __future__ import annotations
import copy
import datetime as dt
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
from .package_notice import read, write

FORMAT='helene-clean-switch.v1'
_RETIREMENTS={}
_RETIREMENT_LOCK=threading.Lock()


def plain(path):
    path=Path(os.path.abspath(path))
    for item in (path,*path.parents):
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)():
            raise ValueError('Clean update cannot own a linked programme path')
    return path


def roots(old,new):
    old,new=plain(old),plain(new)
    if old==new or old.is_relative_to(new) or new.is_relative_to(old):
        raise ValueError('Clean update requires independent programme folders')
    if old==Path(old.anchor) or old==Path.home().resolve():
        raise ValueError('A drive or owner home cannot be retired as a programme')
    return old,new


def directory_identity(path):
    value=plain(path).stat()
    return [value.st_dev,value.st_ino]


def ready(program):
    """A completed extraction with connected data needs no first-start sweep."""
    program=plain(program);note=read(program/'python-migration.json')
    if (note.get('clean_switch')!=FORMAT or note.get('new_root')!=str(program)
            or note.get('state') not in ('prepared','new_app_started','retiring_old','completed','retirement_incomplete')):return False
    old=plain(note.get('old_root') or program)
    try:return bool(note.get('data_paths')) and all(_outside(p,old,program).is_dir() for p in note['data_paths'])
    except (OSError,ValueError):return False


def selected(old,new):
    from .update_source import choice_path
    choice=read(choice_path(new))
    return (choice.get('mode')=='clean' and choice.get('old')==str(Path(old).resolve())
            and choice.get('new')==str(Path(new).resolve()))


def choose(old,new):
    old,new=roots(old,new)
    from .update_source import choice_path
    previous=read(choice_path(new))
    if previous and (previous.get('mode')!='clean' or previous.get('old')!=str(old) or previous.get('new')!=str(new)):
        raise ValueError('Another source policy is already selected for this update')
    if not previous:
        write(choice_path(new),{'format':'helene-source-choice.v1','old':str(old),'new':str(new),
            'mode':'clean','by':'window','at':time.time(),'clean_switch':FORMAT})
    return previous


def adopt_legacy(old,new):
    """A 2.4 host may have prepared an in-place clean overlay before handoff.

    Its incoming 2.5 helper switches folders directly. Previous preparation
    metadata stays available; no old/new programme is revalidated here.
    """
    from .python_source_updater import prepared_install_path
    old,new=Path(old).resolve(),Path(new).resolve()
    path=prepared_install_path(new);previous=read(path)
    if previous.get('folder_switch') is False:
        return previous  # Deliberate mechanical-overlay API preparation.
    if (not previous or previous.get('format')==FORMAT or not selected(old,new)):
        return previous
    from .legacy_clean_bridge import required
    if required(old,new,previous):
        return previous  # Admit the original sender before copying externally.
    old,new=roots(old,new)
    if (previous.get('old')!=str(old) or previous.get('new')!=str(new)
            or previous.get('note',{}).get('source_policy')!='clean'):
        raise ValueError('Legacy preparation is not the selected clean update')
    write(path.with_name('legacy-'+path.name),previous)
    path.unlink()
    return prepare(old,new)


def _outside(path,old,new):
    path=plain(path)
    if path==old or path.is_relative_to(old) or path==new or path.is_relative_to(new):
        raise ValueError('Selected data must remain independent of both programme folders')
    if old.is_relative_to(path) or new.is_relative_to(path):
        raise ValueError('A programme parent cannot be selected as agent data')
    return path


def prepare(old,new,progress=None,*,attached_data=None,data_destination=None):
    """Bind configurations and permanent dependencies without reading programme bytes."""
    from .app_migration import discover
    from .essential_backup import configs
    from .portable_profile import default_data
    from .component_store import PINS, export_references
    from .python_source_updater import prepared_install_path
    old,new=roots(old,new);choose(old,new)
    previous=read(prepared_install_path(new))
    if previous.get('format')==FORMAT:return identity(old,new,previous)
    if previous:
        if previous.get('folder_switch') is False:
            raise ValueError('Prepared in-place update already owns the candidate')
        if previous.get('note',{}).get('source_policy')=='clean':return adopt_legacy(old,new)
        raise ValueError('Another prepared update already owns the candidate')
    found=discover(old);operation=uuid.uuid4().hex
    home=plain(Path(data_destination) if data_destination else default_data().parent/('retained-'+operation))
    _outside(home,old,new)
    moves={}
    def retain(path,relative=None):
        path=plain(path)
        if path==old:raise ValueError('Separate the agent data from the programme root before updating')
        if not path.is_relative_to(old):return _outside(path,old,new)
        if path.is_relative_to(new):raise ValueError('Incoming programme cannot be old agent data')
        target=plain(home/(relative or path.relative_to(old)))
        _outside(target,old,new)
        moves[str(path)]=str(target)
        return target
    rows=[];data_paths=[];source_paths=[];data_owners=[];empty_data=[]
    # Read only the selected settings. Code is selected directly from the
    # completed extraction; authored source is never scanned or compared.
    for row in found['agents']:
        value=copy.deepcopy(row['value']);remote=value.get('mode')=='remote'
        tree=Path(attached_data).resolve() if attached_data and row['relative']=='helene.json' else Path(row['tree']).resolve()
        if tree.is_dir():
            previous_tree=tree;tree=retain(tree);data_paths.append(str(tree));data_owners.append((previous_tree,tree))
            value['tree']=str(tree)
        elif not remote:raise ValueError('Selected agent data is unavailable')
        else:
            tree=_outside(home/'remote-cache'/str(len(rows)),old,new)
            data_paths.append(str(tree));empty_data.append(str(tree));value['tree']=str(tree)
        if not remote:
            source_paths.append({'old':str(row['code']),'selected':str(new/'tree')})
        value['code']=str(new/'tree')
        value.update(python=str(new/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')),
            app=str(new/'app/deskapp.py'),runner=str(new/'app/localharness/runner.py'),read_dotenv=False)
        value['installed']=dict(value.get('installed') or {},service=False)
        # Known data dependencies may have programme-relative paths in legacy
        # profiles. Move them only after the old owner exits, never copy models.
        for block,key in ((value.get('backup'),'dir'),(value.get('voice'),'tts_reference')):
            if isinstance(block,dict) and block.get(key):
                path=Path(block[key]).expanduser()
                path=path if path.is_absolute() else Path(row['config']).parent/path
                if path.exists():block[key]=str(retain(path))
        relay=value.get('relay')
        if isinstance(relay,dict) and relay.get('executable'):
            path=Path(relay['executable']).expanduser()
            path=(path if path.is_absolute() else old/path).resolve()
            if path.is_relative_to(old):
                from .standalone_relay import executable
                bundled = executable(new)
                if bundled.is_file() and bundled.parent == new/'relay-product':
                    # Internal legacy paths follow the new bundled transport.
                    # Independently selected external relays keep their address.
                    relay['executable'] = str(bundled)
                elif path.exists():
                    incoming=new/path.relative_to(old)
                    relay['executable']=str(incoming if incoming.is_file() else retain(path))
            else:
                relay['executable'] = str(path)
        def dependencies(item):
            if isinstance(item,dict):return {k:dependencies(v) for k,v in item.items()}
            if isinstance(item,list):return [dependencies(v) for v in item]
            if isinstance(item,str):
                path=Path(item)
                if path.is_absolute() and path.is_relative_to(old) and path.exists():return str(retain(path))
            return item
        value=dependencies(value)
        rows.append((new/row['relative'],value))
    if not data_paths:raise ValueError('Clean update requires an existing selected data folder')
    # Validate small pins/receipts only. Pinned payloads stay in their permanent
    # store: no hashing, import or copy of their many gigabytes.
    old_pins=export_references(old);incoming_pins=export_references(new)
    def independent_pins(value):
        value=copy.deepcopy(value)
        from .component_store import _reference
        for ref in value['pins'].values():
            payload=_reference(ref);store=plain(ref['store'])
            if payload.is_relative_to(old):
                if not store.is_relative_to(old):
                    raise ValueError('A selected component store cannot be separated from the former programme')
                ref['store']=str(retain(store))
            elif payload.is_relative_to(new):
                raise ValueError('Move selected model dependencies outside the incoming programme before updating')
        return value
    old_pins=independent_pins(old_pins);incoming_pins=independent_pins(incoming_pins)
    data_pin_updates=[]
    for source,target in data_owners:
        previous=export_references(source);revised=independent_pins(previous)
        if revised!=previous:
            data_pin_updates.append({'path':str(target/PINS),'source_path':str(source/PINS),'before':previous,'after':revised})
    pins={'format':old_pins['format'],'pins':{name:ref for name,ref in old_pins['pins'].items() if not (new/name).exists()}}
    pins['pins'].update(incoming_pins['pins'])
    if pins['pins']:write(new/PINS,pins)
    from .component_store import ROOTS
    legacy_components=[]
    for name in ROOTS:
        if name not in pins['pins'] and (old/name).is_dir() and not (new/name).exists():
            legacy_components.append({'source':str(plain(old/name)),'target':str(plain(new/name))})
    for name in ('.helene/webview','.webview','.helene/settings-backups','backups'):
        if (old/name).is_dir():retain(old/name)
    # Remove only shipped configuration stubs, then install the old selected
    # settings with clean distribution entry points. Personal state is external.
    for path in configs(new):
        if path.is_file():path.unlink()
    for path,value in rows:write(path,value)
    for name in ('agent-default.json','window-preferences.json','owner-stop.json'):
        if (old/name).is_file():shutil.copy2(old/name,new/name)
    # Reduce overlapping data moves to their outermost selected root.
    reduced=[]
    for source,target in sorted(moves.items(),key=lambda pair:len(Path(pair[0]).parts)):
        parent=next((r for r in reduced if Path(source).is_relative_to(Path(r['source']))),None)
        if parent:
            expected=Path(parent['target'])/Path(source).relative_to(parent['source'])
            if expected!=Path(target):raise ValueError('Overlapping retained data has conflicting destinations')
        else:reduced.append({'source':source,'target':target})
    note={'format':'helene-python-migration.v1','id':operation,'state':'prepared','old_root':str(old),
        'new_root':str(new),'data_home':data_paths[0],'data_paths':data_paths,
        'old_data_paths':list(dict.fromkeys([*[r['tree'] for r in found['agents']],*[str(source) for source,_ in data_owners]])),
        'source_paths':source_paths,'copied_data':[],'preserved_roots':{},'backup':'',
        'normal_python_update':True,'source_policy':'clean','source_layout':'shipped-tree',
        'installation_flow':'clean-folder-switch','clean_switch':FORMAT,'remove_old':True,
        'old_directory_identity':directory_identity(old)}
    write(new/'python-migration.json',note)
    value={'format':FORMAT,'old':str(old),'new':str(new),'note':note,'moves':reduced,
        'legacy_components':legacy_components,'data_pin_updates':data_pin_updates,'empty_data':empty_data,
        'progress':str(getattr(progress,'path','')) if progress else ''}
    write(prepared_install_path(new),value)
    if progress:progress.phase('prepared',preparation_completed=True,clean_switch=True)
    return value


def identity(old,new,value):
    old,new=roots(old,new)
    if value.get('format')!=FORMAT or value.get('old')!=str(old) or value.get('new')!=str(new):
        raise ValueError('Prepared clean update belongs to another programme')
    note=value['note']
    if note.get('clean_switch')!=FORMAT or note.get('source_policy')!='clean':raise ValueError('Clean source consent is missing')
    if note.get('old_root')!=str(old) or note.get('new_root')!=str(new):raise ValueError('Clean update data binding differs')
    if old.exists() and directory_identity(old)!=note.get('old_directory_identity'):
        raise ValueError('The selected former programme directory was replaced')
    for path in note['data_paths']:_outside(path,old,new)
    for row in value.get('moves',[]):
        source,target=plain(row['source']),plain(row['target'])
        if source==old or not source.is_relative_to(old):raise ValueError('Retained data source escaped former programme')
        _outside(target,old,new)
    for row in value.get('legacy_components',[]):
        from .component_store import ROOTS
        if not any(plain(old/name)==plain(row['source']) and plain(new/name)==plain(row['target']) for name in ROOTS):
            raise ValueError('Legacy component move escaped the selected programme')
    from .component_store import PINS
    for row in value.get('data_pin_updates',[]):
        if plain(row['path']) not in [plain(data)/PINS for data in note['data_paths']]:
            raise ValueError('Data pin update escaped the selected profiles')
        if plain(row['source_path']) not in [plain(data)/PINS for data in note['old_data_paths']]:
            raise ValueError('Previous data pin update escaped the selected profiles')
    if any(str(path) not in note['data_paths'] for path in value.get('empty_data',[])):
        raise ValueError('Remote cache creation escaped the selected profiles')
    return value


def relocate(old,new,value):
    """The old process has exited; rename internal state into permanent storage."""
    identity(old,new,value)
    from . import migration_lifecycle
    for data in dict.fromkeys([*value['note']['old_data_paths'],*value['note']['data_paths']]):
        for relative in ('.desktop-owner.lock','memory/.control/desktop-session.lock'):
            if not migration_lifecycle.owner_lease_available(Path(data)/relative):
                raise RuntimeError('Selected data still has a writer; previous programme retained')
    value['_relocation_started']=True
    for path in value.get('empty_data',[]):plain(path).mkdir(parents=True,exist_ok=True)
    for row in [*value.get('moves',[]),*value.get('legacy_components',[])]:
        source,target=plain(row['source']),plain(row['target'])
        if source.exists() and target.exists():raise ValueError('Retained data destination already exists')
        if source.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(source),str(target))
        elif not target.exists():raise ValueError('Retained data is missing from both locations')
    for row in value.get('data_pin_updates',[]):write(row['path'],row['after'])
    from .first_start import FORMAT as FIRST_FORMAT,receipt
    write(new/'python-migration.json',value['note'])
    write(receipt(new),{'format':FIRST_FORMAT,'state':'completed','completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'data':value['note']['data_paths'][0]})


def restore(value):
    """Before retirement, a failed launch returns renamed legacy state intact."""
    identity(value['old'],value['new'],value)
    for row in reversed([*value.get('moves',[]),*value.get('legacy_components',[])]):
        source,target=plain(row['source']),plain(row['target'])
        if target.exists() and not source.exists():
            source.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(target),str(source))
    for row in value.get('data_pin_updates',[]):write(row['source_path'],row['before'])


def rebind_job(args,value,*,reverse=False):
    """An embedded profile's one durable job follows its renamed data root."""
    job=getattr(args,'job',None)
    if not job:return
    def remap(path):
        path=Path(path)
        for row in value.get('moves',[]):
            source=Path(row['target'] if reverse else row['source'])
            target=Path(row['source'] if reverse else row['target'])
            if path.is_relative_to(source):return target/path.relative_to(source)
        return path
    target=remap(job)
    if target==Path(job):return
    current=read(target)
    if current.get('id')!=args.job_id:raise ValueError('Relocated update job identity differs')
    current['tree']=str(remap(current['tree']));write(target,current);args.job=target
    from .desktop_update_request import incoming_reference
    reference=incoming_reference(Path(value['new']));bound=read(reference)
    if bound and bound.get('id')==args.job_id:
        bound['path']=str(target);write(reference,bound)


def finish(old,new,note,*,stopped=lambda:False):
    """Retire exactly the explicitly replaced programme after one startup ack."""
    old,new=roots(old,new)
    if (note.get('clean_switch')!=FORMAT or note.get('source_policy')!='clean' or not note.get('startup_proof')
            or note.get('id')!=read(new/'python-migration.json').get('id')
            or note.get('old_root')!=str(old) or note.get('new_root')!=str(new)):
        raise ValueError('Clean retirement lacks its selected new application startup')
    for data in note['data_paths']:
        if not _outside(data,old,new).is_dir():raise ValueError('Selected data is unavailable')
    from .component_store import export_references,_reference
    for owner in [new,*map(Path,note['data_paths'])]:
        for ref in export_references(owner)['pins'].values():
            if _reference(ref).is_relative_to(old):
                raise ValueError('Selected model dependency still belongs to the former programme')
    if stopped():raise InterruptedError('Owner stopped before old programme retirement')
    if old.exists() and directory_identity(old)!=note.get('old_directory_identity'):
        raise ValueError('The selected former programme directory was replaced')
    from . import migration_lifecycle
    migration_lifecycle.quiescent(old)
    note['state']='retiring_old';write(new/'python-migration.json',note)
    note['integrations']=migration_lifecycle.cleanup_installation(old,new)
    # rmtree never follows directory symlinks/junctions. Only the resolved,
    # independent programme chosen in the update request can be removed.
    def readonly(function,path,error):
        if function is not os.unlink or not isinstance(error[1],PermissionError):raise error[1]
        from .update_io import remove_created_file
        remove_created_file(path)
    if old.exists():shutil.rmtree(old,onerror=readonly)
    note.update(state='completed',retirement={'ok':True,'old_folder_removed':not old.exists(),'clean_switch':True},cleanup_errors=[])
    try:note['shortcuts']=migration_lifecycle.install_shortcuts(new,desktop_only=True)
    except (OSError,RuntimeError) as exc:note['cleanup_errors'].append(str(exc))
    write(new/'python-migration.json',note)
    return note


def run(args,value,report,stopped):
    """Already-admitted helper: old owner exit -> data move -> launch -> retire."""
    old,new=args.old.resolve(),args.new.resolve();identity(old,new,value)
    from . import migration_lifecycle
    from .python_platform import desktop_argv
    from .update_process import permanent_environment,background_flags
    from .python_source_updater import _job_report
    from .update_progress import Progress
    from .program_state import progress as progress_path
    from .python_source_updater import prepared_install_path
    value.pop('_relocation_started',None)
    progress=Progress(progress_path(new,value['note']['id']),value['note']['id'],new)
    value['progress']=str(progress.path);write(prepared_install_path(new),value)
    report=progress.phase
    current=read(new/'python-migration.json')
    if current.get('id')!=value['note']['id']:raise ValueError('Clean candidate startup identity changed')
    def completed(note):
        value['note']=note;value.pop('_relocation_started',None);write(prepared_install_path(new),value)
        report('completed',installed_root=str(new),new_ready_observed_at=time.time())
        _job_report(args,'completed',result={'installed_root':str(new),'effect':'committed','clean_switch':True})
        return 0
    # A retried helper must never stop a committed NEW owner or move its data
    # back into a partially/already retired OLD folder. Resume cleanup only.
    if current.get('startup_proof') and current.get('state') in ('new_app_started','retiring_old','retirement_incomplete','completed'):
        if current['state']!='completed':current=finish(old,new,current,stopped=stopped)
        return completed(current)
    if current.get('state') in ('retiring_old','completed'):
        raise ValueError('Committed clean update is missing its startup acknowledgement')
    if not old.is_dir():raise ValueError('Former programme disappeared before clean handoff')
    _job_report(args,'handed_off',progress=str(progress.path))
    if stopped():raise InterruptedError('Owner stopped before clean switch')
    migration_lifecycle.stop(old);migration_lifecycle.quiescent(old)
    launched=False
    try:
        relocate(old,new,value)
        rebind_job(args,value)
        if stopped():raise InterruptedError('Owner stopped before clean application startup')
        report('starting',label='Открываем новую версию')
        argv=desktop_argv(new,['--root',str(new),'--migration',value['note']['id'],*(['--browser'] if args.browser else [])])
        process=subprocess.Popen(argv,cwd=new,env=permanent_environment(),stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=background_flags(),start_new_session=os.name!='nt')
        launched=True
        deadline=time.monotonic()+120
        while True:
            if stopped():raise InterruptedError('Owner stopped clean application startup; previous programme retained')
            note=read(new/'python-migration.json')
            if note.get('id')==value['note']['id'] and note.get('state')=='new_app_started':break
            if process.poll() not in (None,0):raise RuntimeError('New application failed; previous programme retained')
            if time.monotonic()>deadline:raise TimeoutError('New application did not open; previous programme retained')
            time.sleep(.1)
    except BaseException:
        if launched:migration_lifecycle.stop(new);migration_lifecycle.quiescent(new)
        if value.pop('_relocation_started',False):
            restore(value)
            rebind_job(args,value,reverse=True)
            failed=dict(value['note'],state='failed_before_retirement');write(new/'python-migration.json',failed)
        raise
    report('retiring_old',label='Удаляем прежнюю программу')
    return completed(finish(old,new,note,stopped=stopped))


def start_retirement(program,*,owner=None):
    """A manually opened clean archive retires its known old owner after UI ack."""
    program=plain(program);note=read(program/'python-migration.json')
    if note.get('clean_switch')!=FORMAT or not note.get('app_retirement'):return None
    if note.get('state')=='completed':return {'started':False,'completed':True,'clean_switch':True}
    with _RETIREMENT_LOCK:
        existing=_RETIREMENTS.get(str(program))
        if existing is not None and existing.is_alive():return {'started':False,'running':True,'clean_switch':True}
        def work():
            from . import owner_stop,session_exit
            from python_migrate import installation_lock
            def stopped():
                return (owner_stop.stopped() or session_exit.requested(program)
                    or (owner is not None and (getattr(owner,'exiting',False) or owner.stop.is_set())))
            try:
                # Keep the lease in NEW: holding a file handle beneath OLD
                # would itself prevent whole-folder retirement on Windows.
                with installation_lock(program):
                    deadline=time.monotonic()+120
                    while True:
                        if stopped():return
                        current=read(program/'python-migration.json')
                        if current.get('id')!=note['id']:raise ValueError('Clean startup identity changed')
                        if current.get('state')=='completed':return
                        if current.get('startup_proof') and current.get('state') in ('new_app_started','retiring_old','retirement_incomplete'):break
                        if time.monotonic()>deadline:raise TimeoutError('Clean archive did not acknowledge startup')
                        time.sleep(.1)
                    finish(note['old_root'],program,current,stopped=stopped)
            except Exception as exc:
                current=read(program/'python-migration.json')
                if current.get('id')==note['id']:
                    current.update(state='retirement_incomplete',cleanup_errors=[type(exc).__name__+': '+str(exc)])
                    write(program/'python-migration.json',current)
        thread=threading.Thread(target=work,name='clean-update-retirement',daemon=True)
        _RETIREMENTS[str(program)]=thread;thread.start()
        return {'started':True,'clean_switch':True}
