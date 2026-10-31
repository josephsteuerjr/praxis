"""Prepare a portable Python update in the newly extracted programme folder.

The incoming pristine-source validation and migration startup receipt do the work.
The old programme remains intact until the new selected code confirms startup.
"""
from pathlib import Path
import copy
from .package_notice import read,write


def is_python_program(root):
    if not root:return False
    root=Path(root)
    return (root/'python-app.json').is_file() and (root/'app/python_app.py').is_file()


def require_idle_data(data):
    """A separate bootstrap must never hide an existing selected-data job."""
    from . import desktop_update_request as jobs
    previous=jobs.status(Path(data))
    if previous.get('state') in jobs.ACTIVE:
        raise RuntimeError('Finish or recover the existing update before a new portable update')


def declared_runtime_file(old,name,inventory):
    """A bundled Python package named models is code; extra weights stay data."""
    import hashlib,re
    from .program_state import _plain
    relative=Path(name)
    if relative.is_absolute() or '..' in relative.parts or not name.startswith('runtime/'):
        return False
    if any(part.lower() in ('memory','.secrets') for part in relative.parts):return False
    expected=inventory.get(name)
    if isinstance(expected,dict):expected=expected.get('sha256')
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):return False
    path=_plain(Path(old)/relative)
    if not path.is_file():return False
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()==expected


def prepare(package,target,old,data,*,progress,source_mode='clean',cancelled=None,data_destination=None):
    from . import code_transition,update_profile,update_source,migration_source,migration_lifecycle
    from .windows_installation import WizardProgress
    from .python_updates import prepare_code
    from .source_update_host import verified_runtime
    from python_migrate import installation_lock,copy_program
    package,target,old,data=(Path(p).resolve() for p in (package,target,old,data))
    if not is_python_program(old):raise ValueError('Portable Python update needs the previous Python programme')
    if source_mode!='clean':raise ValueError('Запуск из архива заменяет код чистой версией; совмещение правок доступно из приложения')
    if not data.is_dir():raise ValueError('Select the existing agent data folder')
    require_idle_data(data)
    from .python_source_updater import active_update
    if active_update(old).is_file():raise RuntimeError('Recover the interrupted previous update before a new portable update')
    if old==target or target.is_relative_to(old) or old.is_relative_to(target):
        raise ValueError('Choose a separate new portable programme folder')
    if package==old or package.is_relative_to(old):raise ValueError('Incoming programme must be independent of the old one')
    if data==target or target.is_relative_to(data) or data.is_relative_to(target):
        raise ValueError('Keep the connected data independent of the new programme')
    from .app_migration import discover
    data_inside_old=data.is_relative_to(old) or any(Path(row['tree']).resolve().is_relative_to(old)
        for row in discover(old)['agents'] if row['value'].get('mode')!='remote')
    if data_inside_old and not data_destination:
        raise ValueError('Move the data to a permanent folder outside the old programme before retiring its folder')
    def check():
        if cancelled is not None and cancelled.is_set():raise InterruptedError('Owner cancelled the portable update')
    check();verified_runtime(package)
    with installation_lock(target):
        if target!=package:copy_program(package,target,progress)
        from .source_admission import activate
        activate(target,lease_held=True);migration_source.verify(target)
        prepare_code(old,target,WizardProgress(progress),sourceMode=source_mode,
                     cancelled=cancelled,require_review=source_mode=='preserve',resolution_owner='incoming')
        check();progress('stopping_old')
        stopped=migration_lifecycle.stop(old);migration_lifecycle.quiescent(old)
        check()
        # No data or whole-program backup: settings and the independent selected
        # code candidate use the existing ordinary update preparation.
        note=update_profile.prepare(old,target,WizardProgress(progress),attached_data=data)
        shipped=read(old/'python-package-files.json').get('files') or {}
        if not isinstance(shipped,dict):raise ValueError('Invalid previous programme inventory')
        for name in shipped:
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid previous programme path')
        note['old_programme_roots']=sorted({Path(name).parts[0] for name in shipped if len(Path(name).parts)>1})
        # Only the small exception inventory is needed after the old package
        # passport retires; no additional source/runtime backup is created.
        note['old_runtime_code_inventory']={name:value for name,value in shipped.items()
            if name.startswith('runtime/') and any(p.lower() in ('model','models') for p in Path(name).parts)}
        note['old_settings_sha256']=update_profile.selection(old)['settings']
        import hashlib
        for name in ('portable-program.json','python-migration.json','data-relocation.json',
                     'helene-bootstrap.json','old-program-retirement.json','code-transition-plan.json','code-transition.json'):
            path=old/name
            if path.is_file():note['old_settings_sha256'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
        code_transition.prepare(old,target,note,progress=WizardProgress(progress))
        code_transition.validate(target)
        from .source_materials import capture
        capture(target,note)
        transfer=None
        if data_destination:
            from . import data_relocation,app_migration
            rows=app_migration.discover(old)['agents']
            transfer=data_relocation.plan(rows,data,data_destination,target,old,target/'data-relocation.json')
            transfer=data_relocation.prepare(transfer,target/'data-relocation.json',progress,cancelled)
            from .essential_backup import configs,load
            for config in configs(target):
                if not config.is_file():continue
                value=copy.deepcopy(load(config))
                value['tree']=str(data_relocation.remap(value['tree'],transfer))
                voice=value.get('voice')
                if isinstance(voice,dict) and voice.get('tts_reference'):
                    voice['tts_reference']=str(data_relocation.remap(voice['tts_reference'],transfer))
                write(config,value)
            note['data_paths']=[str(data_relocation.remap(path,transfer)) for path in note['data_paths']]
            note['data_relocation']=transfer;write(target/'data-relocation.json',transfer)
        note.update(state='prepared',normal_python_update=True,source_policy=source_mode,
                    old_quiescent=stopped,remove_old=True,installation_flow='portable-python-update',
                    data_home=str(transfer['destination'] if transfer else data))
        from .app_migration import write_note
        write_note(target,note);progress('prepared')
        return note


def validate_retirement(old,new,note):
    """Only the confirmed independent candidate may retire its old programme."""
    from . import code_transition
    from .app_migration import discover
    old,new=Path(old).resolve(),Path(new).resolve()
    if (not note.get('normal_python_update') or not note.get('startup_proof')
            or note.get('state')!='new_app_started' or not (new/'code-transition-plan.json').is_file()):
        raise ValueError('Portable update retirement needs verified code and confirmed startup')
    code_transition.validate(new)
    for row in discover(new)['agents']:
        if row['value'].get('mode')=='remote':continue
        if any(Path(row[field]).resolve().is_relative_to(old) for field in ('tree','code')):
            raise ValueError('The new agent still depends on the old programme folder')


def retire_metadata(old,new,note):
    """Retire unchanged portable settings and empty programme parents only."""
    import hashlib
    from .program_state import _plain,directory,_identity
    old,new=Path(old).resolve(),Path(new).resolve()
    if not note.get('startup_proof') or not note.get('normal_python_update'):
        raise ValueError('Unconfirmed portable programme cleanup')
    retained=[]
    for name,expected in note.get('old_settings_sha256',{}).items():
        path=_plain(old/name)
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==expected:path.unlink()
        elif path.exists():retained.append(name)
    # Preserve the small settings receipt in the new private state. This is not
    # a whole-source/history backup and contains no agent memory or models.
    backup=Path(note['backup'])
    if backup.is_file() and backup.is_relative_to(old/'.helene/settings-backups'):
        destination=directory(new,'settings-backups')/backup.name
        if destination.exists():raise ValueError('Settings recovery receipt already exists')
        backup.replace(destination);note['backup']=str(destination)
    # Move, never clone, the former programme's private UI/recovery state. It
    # may contain drafts or failed recovery material, so treating it as cache
    # and deleting it would lose owner data. The new programme owns the receipt.
    private=_plain(old/'.helene')
    if private.is_dir():
        destination=directory(new,'previous-programmes')/_identity(note['id'])
        if destination.exists():raise ValueError('Previous programme state already exists')
        private.replace(destination)
        note['previous_programme_state']=str(destination)
    # Unknown files and old recovery material are never classified as rubbish.
    # Empty known programme directories can disappear without recursive delete.
    names=set(('app','runtime','resources','bin','tree','code','code-backup','components','voice','voice-xtts','relay-product','verification'))
    for name in note.get('old_programme_roots',[]):
        if Path(name).name!=name or name in ('.','..'):raise ValueError('Invalid previous programme root')
        if name.lower() not in ('data','profile','agents','backups','archives','memory','models'):names.add(name)
    roots=[old/name for name in names]
    for root in roots:
        if not root.is_dir():continue
        try:_plain(root)
        except ValueError:continue
        for folder in sorted((p for p in root.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
            try:_plain(folder);folder.rmdir()
            except (OSError,ValueError):pass
        try:root.rmdir()
        except OSError:pass
    try:old.rmdir()
    except OSError:pass
    return {'old_folder_removed':not old.exists(),'changed_settings_retained':retained,
            'remaining_entries':sorted(p.name for p in old.iterdir()) if old.is_dir() else []}


def mark_applied(new,note):
    """A current startup receipt, not a reminder, closes each exact source job."""
    import time
    from . import source_update
    if (Path(new)/'source-materials.json').is_file():
        from .source_materials import commit
        return commit(new,note)
    if not note.get('startup_proof') or not note.get('normal_python_update'):
        raise ValueError('Source jobs need confirmed portable startup')
    record=read(Path(new)/'code-transition-plan.json');applied=[]
    for row in record.get('plans',[]):
        directory=Path(row['directory']);operation=read(directory/'pipeline.json')
        if not operation:continue
        if operation.get('state') not in ('ready_to_apply','applied'):
            raise ValueError('Source job did not reach its accepted candidate')
        operation.update(state='applied',installed_program=str(Path(new).resolve()),applied_at=time.time(),
                         startup_receipt=note['id'])
        source_update._persist(directory,operation)
        current=read(source_update.path(operation['tree']))
        if current.get('id')==operation['id']:
            current.update(state='applied',installed_program=operation['installed_program'],
                           startup_receipt=note['id'],applied_at=operation['applied_at'])
            write(source_update.path(operation['tree']),current)
        applied.append(operation['id'])
    note['applied_source_jobs']=applied
