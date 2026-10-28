"""In-place updates retain profile paths; only executable code gets a candidate."""
from pathlib import Path
import shutil
import uuid
import hashlib
import os
import zipfile

from . import app_migration
from .essential_backup import configs
from .package_notice import write


def settings_snapshot(old, operation, progress=None):
    """Only settings changed by this update; manual/periodic backups stay full.

    Authored code and Git get an independent candidate, original source stays
    selected until switching, and program preimages live in the recovery map.
    No memory/runs/media/session/model inventory is walked by this backup.
    """
    folder = old.parent / 'Helene-update-backups'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (operation + '-settings.zip')
    staged = path.with_suffix('.tmp')
    total = 0
    try:
        with zipfile.ZipFile(staged, 'x', zipfile.ZIP_DEFLATED) as output:
            for source in [*configs(old), *(old / n for n in ('agent-default.json', 'window-preferences.json', 'owner-stop.json'))]:
                if source.is_file():
                    content = source.read_bytes()
                    output.writestr(source.relative_to(old).as_posix(), content)
                    total += len(content)
                    from .update_io import count
                    count(progress, 'settings_backup', len(content))
        staged.replace(path)
    finally:staged.unlink(missing_ok=True)
    return {'path': str(path), 'bytes': total, 'archive_bytes': path.stat().st_size,
            'scope': 'update-settings', 'includes_memory': False, 'includes_models': False}


def selection(old):
    """Bind settings and executable/Git metadata, not the untouched profile.

    code_transition.validate independently hashes the selected executable source
    at the final quiescent boundary. Git metadata is included because index and
    history can change without changing working source bytes.
    """
    from .source_merge import allowed
    from .update_io import stamp
    old = Path(old).resolve()
    found = app_migration.discover(old)
    result = {'settings': {}, 'sources': {}, 'git': {}}
    for config in [*configs(old), *(old / n for n in ('agent-default.json', 'window-preferences.json', 'owner-stop.json'))]:
        if config.is_file():
            result['settings'][config.relative_to(old).as_posix()] = hashlib.sha256(config.read_bytes()).hexdigest()
    for code in dict.fromkeys(str(Path(row['code']).resolve()) for row in found['agents'] if row['value'].get('mode') != 'remote'):
        root = Path(code)
        files = {}
        for directory, dirs, names in os.walk(root, followlinks=False):
            here = Path(directory)
            dirs[:] = sorted(n for n in dirs if allowed((here / n).relative_to(root).as_posix()))
            for name in sorted(names):
                path = here / name
                relative = path.relative_to(root).as_posix()
                if allowed(relative):files[relative] = stamp(path)
        result['sources'][code] = files
        git = root / '.git'
        roots = []
        if git.is_file():
            content = git.read_text(encoding='utf-8').strip()
            if not content.startswith('gitdir: '):raise ValueError('Unrecognized Git pointer')
            roots.append((root / content[8:]).resolve())
            result['git'][str(git)] = stamp(git)
            common = roots[0] / 'commondir'
            if common.is_file():roots.append((roots[0] / common.read_text(encoding='utf-8').strip()).resolve())
        elif git.is_dir():roots.append(git)
        for repo in roots:
            for path in sorted(repo.rglob('*')):
                if path.is_file():result['git'][str(path)] = stamp(path)
    return result


def prepare(old, new, progress=None, *, attached_data=None):
    old, new = Path(old).resolve(), Path(new).resolve()
    found = app_migration.discover(old)
    operation = uuid.uuid4().hex
    backup = settings_snapshot(old, operation, progress)
    sources, trees = [], []
    for row in found['agents']:
        value = dict(row['value'])
        value['tree'] = str(Path(attached_data).resolve() if attached_data and row['relative']=='helene.json' else Path(row['tree']).resolve())
        if attached_data and value.get('mode')!='remote' and not Path(value['tree']).is_dir():
            raise ValueError('Selected agent data folder does not exist')
        value['code'] = str(Path(row['code']).resolve())
        trees.append(value['tree'])
        if value.get('mode') != 'remote':
            sources.append({'old': value['code'], 'selected': value['code']})
        prior_python=Path(str(value.get('python') or ''))
        if not (prior_python.is_absolute() and not prior_python.is_relative_to(old)):
            value['python'] = str(new / ('runtime/python.exe' if app_migration.os.name == 'nt' else 'runtime/bin/python3'))
        for key, default, names in (('app', 'app/deskapp.py', ('deskapp.py', 'app/deskapp.py')),
                                     ('runner', 'app/localharness/runner.py', ('localharness/runner.py', 'app/localharness/runner.py'))):
            prior = value.get(key)
            if not prior:
                if key == 'app':
                    value[key] = str(new / default)
                continue
            path = Path(str(prior))
            absolute = (path if path.is_absolute() else Path(row['config']).parent / path).resolve()
            value[key] = str(new / default) if any(absolute == (old / name).resolve() for name in names) else str(absolute)
        # Relative backup paths previously used the installed config directory.
        if isinstance(value.get('backup'), dict) and value['backup'].get('dir'):
            folder = Path(value['backup']['dir'])
            if not folder.is_absolute():
                value['backup'] = dict(value['backup'], dir=str((Path(row['config']).parent / folder).resolve()))
        write(new / row['relative'], value)
    for name in ('agent-default.json', 'window-preferences.json', 'owner-stop.json'):
        if (old / name).is_file():
            shutil.copy2(old / name, new / name)
    note = {'format': 'helene-python-migration.v1', 'id': operation, 'state': 'prepared',
            'old_root': str(old), 'new_root': str(new), 'backup': backup['path'],
            'old_data_paths': [r['tree'] for r in found['agents']], 'data_paths': trees, 'source_paths': sources,
            'copied_data': [], 'preserved_roots': {}, 'data_home': str(old / 'profile'),
            'in_place_profile': True, 'backup_scope': backup['scope'],
            'backup_bytes': backup['bytes'], 'backup_archive_bytes': backup['archive_bytes']}
    app_migration.write_note(new, note)
    return note
