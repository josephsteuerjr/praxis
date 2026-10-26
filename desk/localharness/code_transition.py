"""Prepare source merges before shutdown and apply them to independent code."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import uuid
import zipfile

from . import source_merge as merge
from .package_notice import read, write


def seal(program):
    """Keep actual release contents, not just hashes, as the next merge base."""
    program = Path(program)
    files = merge.inventory(program / 'tree')
    archive = program / 'code-upstream.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for name in sorted(files):
            output.write(program / 'tree' / name, name)
    write(program / 'code-upstream.json', {
        'format': 'helene-code-upstream.v1', 'files': files,
        'version': read(program / 'python-app.json').get('version', ''),
        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})


def baseline(program, destination, sources=()):
    program, destination = Path(program), Path(destination)
    archive = program / 'code-upstream.zip'
    if archive.is_file():
        record = read(program / 'code-upstream.json')
        if record.get('sha256') != hashlib.sha256(archive.read_bytes()).hexdigest():
            raise ValueError('Stored upstream source archive changed')
        with zipfile.ZipFile(archive) as reader:
            names = reader.namelist()
            if len(names) != len(set(names)) or set(names) != set(record['files']):
                raise ValueError('Stored upstream source inventory differs')
            for name in names:
                if not merge.allowed(name):
                    raise ValueError('Non-code path in upstream snapshot')
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                content = reader.read(name)
                if hashlib.sha256(content).hexdigest() != record['files'][name]['sha256']:
                    raise ValueError('Stored upstream source contents differ')
                path.write_bytes(content)
                if merge.os.name != 'nt':
                    path.chmod(0o755 if record['files'][name]['mode'] == '100755' else 0o644)
        return destination
    # Legacy Python editions retain a separate shipped tree. Old donor hashes
    # in edition-inventory/snapshot_files do not describe these release bytes.
    stock = (program / 'tree').resolve()
    if any(Path(source).resolve() == stock or stock.is_relative_to(Path(source).resolve()) for source in sources):
        raise ValueError('This legacy installation edits its stock tree; an exact original source snapshot is required')
    if not stock.is_dir():
        raise ValueError('Exact previous release source is missing')
    return stock


def preflight(old, new, *, notify=True):
    from .app_migration import discover
    from . import source_update
    old, new = Path(old).resolve(), Path(new).resolve()
    found = discover(old)
    sources = list(dict.fromkeys(str(Path(row['code']).resolve()) for row in found['agents']
                                if row['value'].get('mode') != 'remote'))
    incoming = merge.inventory(new / 'tree')
    exe = merge.executable(new, old)
    root = old / '.source-updates'
    root.mkdir(parents=True, exist_ok=True)
    plans = []
    if not sources:
        return {'state': 'ready', 'plans': []}
    with tempfile.TemporaryDirectory(prefix='baseline-', dir=root) as temporary:
        base = baseline(old, Path(temporary), sources)
        base_files = merge.inventory(base)
        for source in sources:
            local_files = merge.inventory(source)
            identity = merge.signature({'base': base_files, 'local': local_files,
                                        'incoming': incoming, 'source': source})
            directory = root / identity
            plan = read(directory / 'plan.json')
            if not plan:
                if directory.exists():
                    directory.rename(root / (identity + '-incomplete-' + uuid.uuid4().hex))
                plan = merge.create(base, source, new / 'tree', directory, exe=exe)
            if (plan['files'] != {'base': base_files, 'local': local_files, 'incoming': incoming}
                    or Path(plan['local_path']).resolve() != Path(source).resolve()):
                raise ValueError('Source merge inputs changed')
            if merge.inventory(source) != local_files:
                raise ValueError('Local code changed during preparation; retry the update')
            state = plan['state']
            detail = {}
            if plan['conflicts']:
                agent = next(row for row in found['agents'] if str(Path(row['code']).resolve()) == source)
                if notify:
                    source_update.ensure(agent['tree'], old, source, directory,
                                         read(new / 'python-app.json').get('version', ''))
                try:
                    source_update.validate_ready(directory)
                    state = 'ready'
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    detail = read(directory / 'pipeline.json')
                    state = detail.get('state', 'queued')
                    if state == 'ready_to_apply':
                        state = 'needs_attention'
                        detail = {**detail, 'error': str(exc)}
            plans.append({'source': source, 'directory': str(directory), 'state': state,
                          'error': str(detail.get('error') or detail.get('reason') or '')})
    write(new / 'code-transition-plan.json', {'format': 'helene-code-plan.v1', 'old': str(old),
          'sources': sources, 'incoming': incoming, 'plans': plans})
    pending = [row for row in plans if row['state'] != 'ready']
    blocked = [row for row in pending if row['state'] in {'needs_attention', 'cancelled'}]
    message = ('Панель обновления требует внимания; старая версия сохранена. ' +
               '; '.join(row['error'] or row['state'] for row in blocked) if blocked else
               'Изолированный исполнитель и независимая панель разбирают пересечения кода. '
               'Старая версия остаётся открытой. После проверки обновление продолжится автоматически.')
    return {'state': 'needs_resolution' if pending else 'ready', 'plans': plans,
            'message': message if pending else ''}


def validate(new):
    from .app_migration import discover
    from . import source_update
    record = read(Path(new) / 'code-transition-plan.json')
    if not record:
        return
    found = discover(Path(record['old']))
    sources = list(dict.fromkeys(str(Path(row['code']).resolve()) for row in found['agents']
                                if row['value'].get('mode') != 'remote'))
    if sources != record.get('sources'):
        raise ValueError('Selected code paths changed before applying the update; retry')
    if merge.inventory(Path(new) / 'tree') != record['incoming']:
        raise ValueError('Incoming upstream code changed before applying the update; retry')
    for row in record.get('plans', []):
        plan = merge.validate_inputs(row['directory'])
        if merge.inventory(row['source']) != plan['files']['local']:
            raise ValueError('Local code changed before applying the update; retry')
        if plan['conflicts']:
            source_update.validate_ready(row['directory'])


def prepare(old, new, note, *, progress=None):
    old, new = Path(old).resolve(), Path(new).resolve()
    if not (new / 'tree').is_dir():
        return
    if not note['source_paths']:
        seal(new)
        return
    result = preflight(old, new, notify=False)
    if result['state'] != 'ready':
        from .source_update_host import review
        review(old, new, result['plans'], progress)
        validate(new)
        result = preflight(old, new, notify=False)
    if result['state'] != 'ready':
        raise RuntimeError(result['message'])
    from .app_migration import preserve_source
    from .essential_backup import configs, load
    selected = {}
    receipts = []
    for row in note['source_paths']:
        original, target = Path(row['old']).resolve(), Path(row['selected']).resolve()
        if original in selected:
            row['selected'] = str(selected[original])
            continue
        if original == target:
            target = new / 'code' / note['id'] / str(len(selected))
            preserve_source(original, target, source_only=True)
            row['selected'] = str(target)
        plan_row = next(p for p in result['plans'] if Path(p['source']).resolve() == original)
        directory = Path(plan_row['directory'])
        merge.apply(directory, target)
        selected[original] = target
        plan = read(directory / 'plan.json')
        receipts.append({'source': str(original), 'directory': str(directory),
                         'commits': plan['commits'], 'merged_commit': plan['merged_commit']})
    for config in configs(new):
        if not config.is_file():
            continue
        value = load(config)
        prior = Path(value.get('code', '')).resolve()
        if prior in selected:
            value['code'] = str(selected[prior])
        write(config, value)
    seal(new)
    write(new / 'code-transition.json', {'format': 'helene-code-transition.v2', 'merges': receipts})
    note['code_transition'] = str(old / 'code-transition.json')
