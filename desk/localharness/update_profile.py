"""In-place updates retain profile paths; only executable code gets a candidate."""
from pathlib import Path
import shutil
import uuid

from . import app_migration
from .essential_backup import snapshot, load
from .package_notice import write


def prepare(old, new):
    old, new = Path(old).resolve(), Path(new).resolve()
    found = app_migration.discover(old)
    operation = uuid.uuid4().hex
    backup = snapshot(old, old.parent / 'Helene-update-backups', 'before-source-update')
    sources, trees = [], []
    for row in found['agents']:
        value = dict(row['value'])
        value['tree'] = str(Path(row['tree']).resolve())
        value['code'] = str(Path(row['code']).resolve())
        trees.append(value['tree'])
        if value.get('mode') != 'remote':
            sources.append({'old': value['code'], 'selected': value['code']})
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
            'old_data_paths': trees, 'data_paths': trees, 'source_paths': sources,
            'copied_data': [], 'preserved_roots': {}, 'data_home': str(old / 'profile'),
            'in_place_profile': True}
    app_migration.write_note(new, note)
    return note
