"""Setup: pristine program, attached data, explicit legacy retirement.

The same wizard is used by first launch and an extracted Update. Old code is archived, combined with incoming code and reviewed by the new panel.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import time
import uuid
import zipfile

from . import app_migration, migration_source, migration_lifecycle as migration_windows
from .essential_backup import configs, load, tree_path
from .package_notice import read, write

ARCHIVE_BEFORE = (2, 2, 1)
POLICY = ('Сохраним прежний код в архиве и автоматически совместим его с новым. '
          'Агент разберёт нестыковки, затем независимые ревьюеры проверят результат. '
          'Можно также установить чистый код, сохранив данные агента.')


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


def archived_version(text):
    import re
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?', text)
    return bool(match and tuple(map(int, match.groups())) < ARCHIVE_BEFORE)


def default_data(old):
    if not old or not (Path(old) / 'helene.json').is_file(): return ''
    path = Path(old) / 'helene.json'
    return str(tree_path(path, load(path)))


def blank_config(program, data):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    return {'mode': 'local', 'tree': str(data), 'code': str(program / 'tree'),
            'port': port, 'python': str(program / (('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3') if os.name=='nt' else 'runtime/bin/python3')),
            'app': str(program / 'app/deskapp.py'),
            'runner': str(program / 'app/localharness/runner.py'),
            'setup_complete': False, 'installed': {'service': False},
            'model': {'framework': 'openai', 'base_url': '', 'model': '', 'key': ''},
            'relay': {'enabled': False}, 'voice': {'enabled': False}}


def archive_sources(old, new, operation, progress=None):
    """Small source/Git archive, not another copy of memory, models or runtime."""
    from .app_migration import discover, preserve_source
    from .source_merge import allowed
    old, new = Path(old).resolve(), Path(new).resolve()
    rows = discover(old)['agents']; seen = set(); records = []
    destination = new / 'archives' / operation
    for row in rows:
        source = Path(row['code']).resolve()
        if row['value'].get('mode') == 'remote' or source in seen: continue
        seen.add(source)
        if not source.is_dir():
            records.append({'source': str(source), 'absent': True}); continue
        destination.mkdir(parents=True, exist_ok=True)
        archive = destination / ('previous-source-' + str(len(records)) + '.zip')
        # preserve_source also detaches linked Git worktrees. The temporary
        # source copy is removed after its verified ZIP has landed.
        with tempfile.TemporaryDirectory(prefix='archive-source-', dir=destination) as tmp:
            saved = Path(tmp) / 'source'
            preserve_source(source, saved, progress, source_only=True)
            expected = {}
            with zipfile.ZipFile(archive.with_suffix('.part'), 'x', zipfile.ZIP_DEFLATED) as output:
                for path in saved.rglob('*'):
                    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                        raise ValueError('Archive source contains a link: ' + str(path))
                    if not path.is_file(): continue
                    name = path.relative_to(saved).as_posix()
                    if '.git' not in Path(name).parts and not allowed(name): continue
                    content = path.read_bytes()
                    expected[name] = hashlib.sha256(content).hexdigest()
                    output.writestr(name, content)
            with zipfile.ZipFile(archive.with_suffix('.part')) as proof:
                if set(proof.namelist()) != set(expected) or any(
                        hashlib.sha256(proof.read(name)).hexdigest() != digest
                        for name, digest in expected.items()):
                    raise IOError('Previous source archive verification failed')
            archive.with_suffix('.part').replace(archive)
        records.append({'source': str(source), 'path': str(archive),
                        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
    write(destination / 'archive.json', {'version': version(old), 'policy': POLICY,
          'archived_legacy_version': archived_version(version(old)), 'sources': records})
    return records


def prepare_attached(old, new, data, operation, *, previous_sources=None,source_mode='clean',progress=None):
    """Connect existing directories; no recursive data copy or schema conversion."""
    old = Path(old).resolve() if old else None
    new, data = Path(new).resolve(), Path(data).resolve()
    if not data.is_dir(): raise ValueError('Выбери существующую папку данных агента')
    if data == new or new.is_relative_to(data):
        raise ValueError('Папка данных не должна содержать новую программу')
    rows = app_migration.discover(old)['agents'] if old else []
    if not rows:
        rows = [{'relative': 'helene.json', 'tree': str(data), 'code': new / 'tree',
                 'value': blank_config(new, data)}]
    sources = []; paths = []
    for row in rows:
        value = copy.deepcopy(row['value'])
        selected_data = data if row['relative'] == 'helene.json' else Path(row['tree']).resolve()
        if value.get('mode') != 'remote' and not selected_data.is_dir():
            raise ValueError('Не найдена папка данных: ' + str(selected_data))
        if value.get('mode') != 'remote' and not migration_windows.owner_lease_available(selected_data / '.desktop-owner.lock'):
            raise RuntimeError('Папку данных ещё использует другой процесс Hélène')
        value.update(tree=str(selected_data), code=str(row['code']),
                     python=str(new / (('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3') if os.name=='nt' else 'runtime/bin/python3')), app=str(new / 'app/deskapp.py'),
                     runner=str(new / 'app/localharness/runner.py'))
        value['installed'] = dict(value.get('installed') or {}, service=False)
        backup = value.get('backup')
        if old and isinstance(backup, dict) and backup.get('dir'):
            path = Path(backup['dir'])
            if not path.is_absolute():
                value['backup'] = dict(backup, dir=str((old / Path(row['relative']).parent / path).resolve()))
        write(new / row['relative'], value)
        paths.append(str(selected_data))
        if value.get('mode') != 'remote':
            sources.append({'old': str(row['code']), 'selected': str(row['code'])})
    for name in ('agent-default.json', 'window-preferences.json', 'owner-stop.json'):
        if old and (old / name).is_file(): shutil.copy2(old / name, new / name)
    if old:
        from .component_downloads import preserve_installed
        preserve_installed(old,new)  # the existing optional-runtime migration, no download/cache snapshot
    note = {'format': 'helene-python-migration.v1', 'id': operation, 'state': 'preserving',
            'old_root': str(old) if old else '', 'new_root': str(new),
            'data_paths': paths, 'old_data_paths': [r['tree'] for r in rows],
            'source_paths': sources, 'source_policy': source_mode, 'attached_data': True,
            'copied_data': [], 'data_home': str(data), 'preserved_roots': {},
            'archives': previous_sources or []}
    app_migration.write_note(new, note)
    # The source archives already contain old working bytes and Git. Avoid
    # another permanent source backup inside migration_source.prepare().
    if source_mode=='preserve':
        from .code_transition import prepare,validate
        prepare(old,new,note,progress=WizardProgress(progress or (lambda _:None)))
        validate(new)
        note['state']='prepared';app_migration.write_note(new,note)
    else:
        note = migration_source.prepare(old or new, new, note, preserve_previous=False)
    return note


def finish(new, note, *, remove_old, progress):
    """Only a confirmed new owner may remove the old program/integrations."""
    if note.get('state') != 'new_app_started' or not note.get('startup_proof'):
        raise ValueError('Новое приложение ещё не подтвердило запуск')
    old = Path(note['old_root']).resolve() if note.get('old_root') else None
    progress('retiring_old')
    errors = []
    if old:
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
    try: note['shortcuts'] = migration_windows.install_shortcuts(new, desktop_only=True)
    except Exception as exc: errors.append(str(exc))
    note.update(state='retirement_incomplete' if errors else 'completed', cleanup_errors=errors)
    app_migration.write_note(new, note); progress(note['state'])
    return note


def run(package, target, *, old=None, data=None, fresh=False, remove_old=False,
        launch=True, progress=None, source_mode=None, cancelled=None,force_extensions=False):
    """Connect the existing migration/fresh/update lifecycles, not another one."""
    import python_migrate
    package,target=Path(package).resolve(),Path(target).resolve()
    old=Path(old).resolve() if old and not fresh else None
    progress=progress or (lambda _:None)
    mode=source_mode or ('preserve' if old else 'clean')
    if mode not in ('preserve','clean'):raise ValueError('Choose preserve or clean source')
    selected_data=Path(data).resolve() if data else Path(default_data(old)) if old else target/'profile/main'
    if old and target==old:
        return in_place(package,old,data=selected_data,progress=progress,source_mode=mode,cancelled=cancelled,force_extensions=force_extensions)
    with python_migrate.installation_lock(target):
        if old:
            return python_migrate.migrate(old,package,target,target/'profile',stop_service=True,
                prepared=target==package,portable=True,source_mode=mode,launch=launch,progress=progress,
                attached_data=selected_data,remove_old=remove_old,review_cancelled=cancelled,force_extensions=force_extensions)
        return python_migrate.fresh(package,target,selected_data,launch=launch,progress=progress,
            prepared=target==package,data_exact=True,connect_data=not fresh,desktop_shortcut=True)


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
    with installation_lock(old):
        migration_source.verify(package)
        from .python_updates import prepare_code
        prepare_code(old,package,WizardProgress(progress),source_mode,cancelled=cancelled,
                     require_review=source_mode=='preserve',resolution_owner='incoming',forceExtensions=force_extensions)
        progress('stopping_old')
        before = migration_windows.service(old)
        migration_windows.stop(old); migration_windows.quiescent(old)
        committed = False
        try:
            receipt = Progress(staging.with_name(staging.name + '.progress.json'), staging.name.removeprefix('.helene-update-'), old)
            archives = archive_sources(old, old, uuid.uuid4().hex, progress)
            updater.prepare_install(old, package, progress=receipt, attached_data=data)
            ready = threading.Event(); ready.set()
            progress('starting_new')
            updater.perform(SimpleNamespace(old=old, new=package, pid=0, staging=staging,
                archive=[], cleanup=False, browser=False, observer_ready=ready))
            committed = True
            note = read(old / 'python-migration.json')
            note.update(state='new_app_started', archives=archives)
            return finish(old, note, remove_old=False, progress=progress)
        except BaseException:
            if not committed: migration_windows.restore_service(old, before)
            raise
