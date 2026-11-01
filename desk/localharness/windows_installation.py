"""Setup: pristine program, attached data, explicit legacy retirement.

The same wizard is used by first launch and an extracted Update. Migration
attaches existing data to pristine code and retires the stopped old program.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import socket
import time

from . import app_migration, migration_source, migration_lifecycle as migration_windows
from .essential_backup import configs, load, tree_path
from .package_notice import read, write

POLICY = ('Установим чистый код. Найдём прежнюю программу и путь к данным, остановим её супервизор и дочерние процессы, '
          'проверим освобождение данных и удалим старые программные файлы. Затем подключим прежние данные и автоматически откроем новую Hélène.')


class WizardProgress:
    def __init__(self,report):self.report=report
    def __call__(self,value):self.report(value)
    def phase(self,stage,**values):self.report({'stage':stage,**values})


def version(root):
    for name in ('python-app.json', 'build.json', 'source-inputs.json'):
        value = read(Path(root) / name)
        text = str(value.get('version') or value.get('app_version') or '')
        if text: return text
    return ''


def default_data(old):
    if not old or not (Path(old) / 'helene.json').is_file():
        from .portable_profile import default_data as new_default
        return str(new_default())
    path = Path(old) / 'helene.json'
    return str(tree_path(path, load(path)))


def is_python_program(old):
    from .python_archive_update import is_python_program as detected
    return detected(old)


def relocation_default(data):
    import hashlib
    base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local') if os.name=='nt' else Path.home()/'.local/share'
    identity=hashlib.sha256(os.path.normcase(str(Path(data).resolve())).encode('utf-8')).hexdigest()[:16]
    return str(base/'Helene-Data'/identity)


def relocation_recommended(data):
    return os.name=='nt' and Path(data).resolve().is_relative_to(Path(os.environ.get('ProgramFiles','C:/Program Files')).resolve())


def blank_config(program, data):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    return {'mode': 'local', 'tree': str(data), 'code': 'tree',
            'port': port, 'python': 'runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3',
            'app': 'app/deskapp.py',
            'runner': 'app/localharness/runner.py',
            'setup_complete': False, 'installed': {'service': False},
            'model': {'framework': 'openai', 'base_url': '', 'model': '', 'key': ''},
            'relay': {'enabled': False}, 'voice': {'enabled': False}}


def finish(new, note, *, remove_old, progress):
    """Only a confirmed new owner may remove the old program/integrations."""
    if note.get('state') != 'new_app_started' or not note.get('startup_proof'):
        raise ValueError('Новое приложение ещё не подтвердило запуск')
    old = Path(note['old_root']).resolve() if note.get('old_root') else None
    if note.get('normal_python_update'):
        from .python_archive_update import validate_retirement
        validate_retirement(old,new,note)
    progress('retiring_old')
    errors = []
    transfer=note.get('data_relocation')
    if transfer and transfer.get('state')=='verified':
        from .data_relocation import retire_sources
        try:
            note['data_relocation']=retire_sources(transfer,startup_proof=note['startup_proof'])
            write(new/'data-relocation.json',note['data_relocation'])
            errors.extend(note['data_relocation'].get('errors',[]))
        except (OSError,ValueError,RuntimeError) as exc:
            errors.append('Данные подключены к новой программе; исходная папка сохранена: '+str(exc))
    if old and not note.get('old_retired_before_attach'):
        # A stopped service must stay stopped even when the user keeps its old
        # program files. Otherwise Windows could start a second data writer.
        try: note['integrations'] = migration_windows.cleanup_installation(old, new)
        except Exception as exc: errors.append(str(exc))
        if remove_old and old != new and not errors:
            try:
                note['retirement'] = migration_windows.remove_program(old, new, note)
                if not note['retirement'].get('ok'):errors.append('Некоторые файлы прежней программы защищены как данные и оставлены на месте.')
            except Exception as exc: errors.append(str(exc))
        else: note['retirement'] = {'ok':not errors,'old_program_retained':old != new,'purge':False}
    if old and note.get('normal_python_update') and not errors:
        from .python_archive_update import retire_metadata,mark_applied
        try:
            mark_applied(new,note)
            from .source_materials import retire
            note['source_materials_cleanup']=retire(new,note)
            if note['source_materials_cleanup']['state']=='retained-material':
                errors.append('Изменённые материалы завершённого обновления сохранены для проверки.')
            note['portable_retirement']=retire_metadata(old,new,note)
            if not note['portable_retirement']['old_folder_removed']:
                errors.append('В прежней папке остались неизвестные или изменённые файлы: '+', '.join(note['portable_retirement']['remaining_entries']))
        except (OSError,ValueError,RuntimeError) as exc:errors.append(str(exc))
    try: note['shortcuts'] = migration_windows.install_shortcuts(new, desktop_only=True)
    except Exception as exc: errors.append(str(exc))
    note.update(state='retirement_incomplete' if errors else 'completed', cleanup_errors=errors)
    app_migration.write_note(new, note); progress(note['state'])
    return note


def clean_attached(package,target,old,data,*,progress,cancelled=None,data_destination=None):
    """Retire a dead legacy owner before attaching any existing data."""
    import python_migrate
    from .source_update_host import verified_runtime
    from .migration_windows import owner_lease_available
    package,target,old,data=(Path(p).resolve() for p in (package,target,old,data))
    def check():
        if cancelled and cancelled.is_set():raise InterruptedError('Owner stopped migration')
    if target==old or target.is_relative_to(old) or old.is_relative_to(target):
        raise ValueError('Choose a new program folder separate from the old installation')
    if package==old or package.is_relative_to(old):raise ValueError('Use the separately unpacked incoming programme')
    if data==target or target.is_relative_to(data):raise ValueError('Data must remain separate from the new program')
    check()
    # Configuration discovery is read-only. No old data token, runner, lease or
    # control directory is opened before the old supervisor has been stopped.
    rows=app_migration.discover(old)['agents']
    settings={name:(old/name).read_bytes() for name in ('agent-default.json','window-preferences.json','owner-stop.json') if (old/name).is_file()}
    progress('checking_programme');check()
    runtime=verified_runtime(package)
    with python_migrate.installation_lock(target):
        prior=python_migrate.transaction(old,target,prepared=target==package)
        if target!=package:python_migrate.copy_program(package,target,progress)
        from .source_admission import activate
        activate(target,lease_held=True)
        migration_source.verify(target)
        transfer=None
        if data_destination:
            from . import data_relocation
            transfer=data_relocation.plan(rows,data,data_destination,target,old,target/'data-relocation.json')
            write(target/'data-relocation.json',transfer)
        # Required text/image transport is prepared from the current pinned
        # release BEFORE any destructive action. No old component code copies.
        from .standalone_relay import prepare_migration
        prepared=prepare_migration(old,target,[row['value'] for row in rows],progress,cancelled)
        rows=[dict(row,value=value) for row,value in zip(rows,prepared)]
        check()
        selected=[str(data if row['relative']=='helene.json' else Path(row['tree']).resolve()) for row in rows if row['value'].get('mode')!='remote']
        command=migration_windows.uninstaller_for(old) if os.name=='nt' else None
        if command:
            progress('uninstalling_old');check()
            retirement=migration_windows.uninstall_before_attach(old,command,list(dict.fromkeys(selected+[r['tree'] for r in rows if r['value'].get('mode')!='remote'])),
                progress=progress,cancelled=cancelled)
            stopped={'native_uninstaller':True};integrations={'native_uninstaller':True}
        else:
            # Extracted portable programmes have no installed uninstaller.
            progress('stopping_old');check()
            stopped=migration_windows.stop(old)
            migration_windows.quiescent(old)
            integrations=migration_windows.cleanup_installation(old,target)
            retirement=None
        migration_windows.quiescent(old)
        # This is the first contact with the existing data trees.
        if not data.is_dir():raise ValueError('Existing data folder is missing')
        if any(not owner_lease_available(Path(p)/name) for p in selected
               for name in ('.desktop-owner.lock','memory/.control/desktop-session.lock')):
            raise RuntimeError('Old data owner has not released its lease')
        check()
        note=dict(prior,source_policy='clean',attached_data=True,copied_data=[],data_home=str(data),
            data_paths=selected,old_data_paths=[r['tree'] for r in rows],
            source_paths=[{'old':str(r['code']),'selected':str(target/'tree')} for r in rows if r['value'].get('mode')!='remote'],
            old_quiescent={'old':str(old),'at':time.time(),'stop':stopped},runtime_binding=runtime,
            integrations=integrations)
        app_migration.write_note(target,note)
        # Models already in the selected home stay in place. Old optional
        # interpreters/toolchains are code too: clean migration never imports
        # them. Missing optional engines can be installed from current packs.
        note['optional_runtime_policy']='install-current-packs;existing-model-data-retained'
        if retirement is None:
            progress('removing_old')
            retirement=migration_windows.remove_program(old,target,note,before_start=True,progress=progress,cancelled=cancelled)
        note['retirement']=retirement
        if not note['retirement'].get('ok'):raise RuntimeError('Old program removal is incomplete; new owner not started')
        migration_windows.quiescent(old)
        note['old_retired_before_attach']=True;app_migration.write_note(target,note)
        if transfer:
            transfer=data_relocation.prepare(transfer,target/'data-relocation.json',progress,cancelled)
            data=Path(transfer['destination'])
            selected=[str(data_relocation.remap(p,transfer)) for p in selected]
            note.update(data_home=str(data),data_paths=selected,data_relocation=transfer,
                        copied_data=[dict(source=row['source'],destination=row['destination']) for row in transfer['roots']])
            app_migration.write_note(target,note)
        progress('connecting_data');check()
        if transfer:data_relocation.validate_prepared(transfer,cancelled)
        # Always select pristine shipped code. No merge, old-source copy, Git
        # preparation, model call or reviewer is involved in migration.
        for row in rows:
            value=copy.deepcopy(row['value'])
            selected_data=data if row['relative']=='helene.json' else (
                data_relocation.remap(row['tree'],transfer) if transfer else Path(row['tree']).resolve())
            value.update(tree=str(selected_data),code=str(target/'tree'),
                python=str(target/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')),
                app=str(target/'app/deskapp.py'),runner=str(target/'app/localharness/runner.py'))
            value['installed']=dict(value.get('installed') or {},service=False)
            backup=value.get('backup')
            if isinstance(backup,dict) and backup.get('dir') and not Path(backup['dir']).is_absolute():
                value['backup']=dict(backup,dir=str((old/Path(row['relative']).parent/backup['dir']).resolve()))
            if transfer:
                if isinstance(value.get('backup'),dict) and value['backup'].get('dir'):
                    value['backup']=dict(value['backup'],dir=str(data_relocation.remap(value['backup']['dir'],transfer)))
                voice=value.get('voice')
                if isinstance(voice,dict) and voice.get('tts_reference') and Path(voice['tts_reference']).is_absolute():
                    value['voice']=dict(voice,tts_reference=str(data_relocation.remap(voice['tts_reference'],transfer)))
            write(target/row['relative'],value)
        for name,content in settings.items():
            (target/name).write_bytes(content)
        note.update(state='prepared',installation_flow='attached',remove_old=True,
                    pristine_source_sha256=read(target/'code-upstream.json')['sha256'])
        app_migration.write_note(target,note)
        progress('prepared')
        return note


def run(package, target, *, old=None, data=None, fresh=False, remove_old=False,
        launch=True, progress=None, source_mode=None, cancelled=None,force_extensions=False,bootstrap_id='',data_destination=None):
    """Connect the existing migration/fresh/update lifecycles, not another one."""
    import python_migrate
    package,target=Path(package).resolve(),Path(target).resolve()
    old=Path(old).resolve() if old and not fresh else None
    progress=progress or (lambda _:None)
    ordinary=bool(old and is_python_program(old))
    mode=source_mode or ('preserve' if ordinary else 'clean')
    if ordinary and target!=old:
        from .python_archive_update import prepare
        return prepare(package,target,old,Path(data or default_data(old)),progress=progress,
                       source_mode=mode,cancelled=cancelled,data_destination=data_destination)
    if mode!='clean':raise ValueError('Migration installs clean code and keeps existing data; preserving code edits is unavailable')
    selected_data=Path(data).resolve() if data else Path(default_data(old))
    if old and target==old:
        if data_destination:raise ValueError('Data relocation requires migration into a separate programme')
        return in_place(package,old,data=selected_data,progress=progress,source_mode=mode,cancelled=cancelled,force_extensions=force_extensions)
    if old:
        return clean_attached(package,target,old,selected_data,progress=progress,cancelled=cancelled,data_destination=data_destination)
    if data_destination:raise ValueError('Select the previous programme before transferring its data')
    with python_migrate.installation_lock(target):
        if not fresh:
            from .migration_windows import owner_lease_available
            if any(not owner_lease_available(selected_data/name) for name in ('.desktop-owner.lock','memory/.control/desktop-session.lock')):
                raise RuntimeError('Existing data still has an owner; close its supervisor before connecting')
        return python_migrate.fresh(package,target,selected_data,launch=launch,progress=progress,
            prepared=target==package,data_exact=True,connect_data=not fresh,desktop_shortcut=True,bootstrap_id=bootstrap_id)


def in_place(package, old, *, data=None, progress,source_mode='preserve',cancelled=None,force_extensions=False):
    """Use the existing byte-preimage/rollback transaction for the current folder."""
    import threading
    from types import SimpleNamespace
    from python_migrate import installation_lock
    from . import python_source_updater as updater, update_source
    from .update_progress import Progress
    staging = package.parent
    if data is not None:
        data=Path(data).resolve()
        if not data.is_dir() or data==old or old.is_relative_to(data) or data.is_relative_to(staging):
            raise ValueError('Выбери постоянную папку данных отдельно от программы и временной папки обновления')
    if not (old/'python-app.json').is_file():
        raise ValueError('Для прежней установки выбери отдельную папку программы. Данные можно оставить в прежней папке.')
    if package != staging / 'Helene' or staging.parent != old.parent or not staging.name.startswith('.helene-update-'):
        raise ValueError('Для обновления в той же папке используй кнопку «Обновить» в Hélène')
    # The old window's startup recovery also acquires installation_lock(old).
    # Keep that lease free while its runner loads the temporary update hand.
    # A separate preparation lease serializes wizards sharing that old runner.
    preparation = old.parent / ('.helene-preparation-' + old.name)
    with installation_lock(preparation):
        migration_source.verify(package)
        from .python_updates import prepare_code
        prepare_code(old,package,WizardProgress(progress),source_mode,cancelled=cancelled,
                     require_review=source_mode=='preserve',resolution_owner='agent',forceExtensions=force_extensions)
        if cancelled and cancelled.is_set():
            raise RuntimeError('Обновление отменено; прежняя программа сохранена')
        with installation_lock(old):
            from .code_transition import validate
            validate(package)  # Rebind preparation to current code before stopping.
            progress('stopping_old')
            before = migration_windows.service(old)
            migration_windows.stop(old); migration_windows.quiescent(old)
            committed = False
            try:
                receipt = Progress(staging.with_name(staging.name + '.progress.json'), staging.name.removeprefix('.helene-update-'), old)
                updater.prepare_install(old, package, progress=receipt, attached_data=data)
                ready = threading.Event(); ready.set()
                progress('starting_new')
                updater.perform(SimpleNamespace(old=old, new=package, pid=0, staging=staging,
                    archive=[], cleanup=False, browser=False, observer_ready=ready))
                committed = True
                note = read(old / 'python-migration.json')
                note.update(state='new_app_started')
                return finish(old, note, remove_old=False, progress=progress)
            except BaseException:
                if not committed: migration_windows.restore_service(old, before)
                raise
