"""Exact installable update composition: immutable release, editable core/app.

A/L/B stay frozen. App/resource repairs have a separate recorded M surface and
are installed through the existing preimage overlay, without overwriting the
incoming verifier/helper while it is running. Runtime bytes remain bound to the
incoming package: source-only repair cannot silently stand in for a rebuild.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import time
from . import source_merge as merge
from .package_notice import read, write

FORMAT = 'helene-update-composition.v1'
MANIFESTS = ('python-app.json', 'source-inputs.json', 'python-package-files.json',
             'code-upstream.json', 'component-downloads.json')


def _hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def app_inventory(root):
    """Shipped app resources/soul are program, not a live agent's private soul."""
    root=Path(root).resolve()
    files={}
    for here,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name!='__pycache__']
        for name in [*dirs,*names]:
            item=Path(here)/name
            relative=item.relative_to(root).as_posix();merge.relative(relative)
            if item.is_symlink() or getattr(item,'is_junction',lambda:False)():
                raise ValueError('Linked application composition path')
            if item.is_file() and not name.endswith('.pyc'):
                files[relative]={'sha256':_hash(item),'mode':'100755' if os.name!='nt' and item.stat().st_mode&0o111 else '100644'}
    return files


def _operation(directory, operation=None):
    return operation or read(Path(directory) / 'pipeline.json')


def prepare(directory, operation=None):
    directory = Path(directory).resolve()
    operation = _operation(directory, operation)
    binding = operation.get('coordinator') or {}
    if binding.get('kind') != 'incoming-update-runtime.v1':
        raise ValueError('Full verification needs the verified incoming application/runtime')
    program = Path(binding['program']).resolve()
    state = read(directory / 'composition.json')
    if state:
        if state.get('program') != str(program) or state.get('input_sha256') != operation['input_sha256']:
            raise ValueError('Composition belongs to another update')
        _frozen(directory, state)
        return state
    app_files = app_inventory(program / 'app')
    if not app_files:
        raise ValueError('Incoming application composition is empty')
    base = directory / 'compound/base-app'
    result = directory / 'compound/app'
    if base.exists() or result.exists():
        raise ValueError('Interrupted composition snapshot retained; inspect it before retry')
    for name, row in app_files.items():
        source = program / 'app' / name
        for root in (base, result):
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    manifests = {name: _hash(program / name) for name in MANIFESTS if (program / name).is_file()}
    package = read(program / 'python-package-files.json').get('files')
    if not isinstance(package, dict) or not package:
        raise ValueError('Incoming composition omits the full program inventory')
    state = {'format': FORMAT, 'program': str(program), 'input_sha256': operation['input_sha256'],
             'base_app': app_files, 'manifests': manifests, 'package_files': package, 'repairs': []}
    write(directory / 'composition.json', state)
    _frozen(directory, state)
    return state


def _frozen(directory, state):
    program = Path(state['program'])
    if app_inventory(directory / 'compound/base-app') != state['base_app']:
        raise ValueError('Frozen incoming app snapshot changed')
    if app_inventory(program / 'app') != state['base_app']:
        raise ValueError('Verified incoming app changed; compound repairs must use its declared M surface')
    for name, digest in state['manifests'].items():
        if _hash(program / name) != digest:
            raise ValueError('Incoming manifest changed: ' + name)


def describe(directory, operation=None):
    directory = Path(directory).resolve()
    state = read(directory / 'composition.json')
    if not state:
        state = prepare(directory, operation)
    _frozen(directory, state)
    plan = merge.validate_inputs(directory)
    app = app_inventory(directory / 'compound/app')
    runtime = {}
    program = Path(state['program'])
    for name, row in state['package_files'].items():
        merge.relative(name)
        if not name.startswith('runtime/'):
            continue
        if '__pycache__' in Path(name).parts or name.endswith('.pyc'):
            continue
        expected = row.get('sha256') if isinstance(row, dict) else row
        candidate = program / name
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(program):
            raise ValueError('Linked runtime input')
        digest = _hash(candidate)
        if digest != expected:
            raise ValueError('Incoming runtime changed: ' + name)
        runtime[name.removeprefix('runtime/')] = {'sha256': digest,
            'mode': '100755' if os.name != 'nt' and candidate.stat().st_mode & 0o111 else '100644'}
    if not runtime:
        raise ValueError('Incoming runtime inventory is empty')
    # Additional executable/package bytes are not hidden behind the old manifest.
    actual_runtime = set()
    for here, dirs, names in os.walk(program / 'runtime', followlinks=False):
        dirs[:] = [name for name in dirs if name != '__pycache__']
        for name in [*dirs, *names]:
            item = Path(here) / name
            if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
                raise ValueError('Linked runtime path')
            if item.is_file() and not name.endswith('.pyc'):
                actual_runtime.add(item.relative_to(program / 'runtime').as_posix())
    if actual_runtime != set(runtime):
        raise ValueError('Runtime has undeclared or changed installable files')
    payload = {'format': FORMAT, 'input_sha256': state['input_sha256'],
               'core': merge.inventory(directory / 'result'), 'app': app,
               'runtime': runtime, 'manifests': state['manifests']}
    return {'sha256': merge.signature(payload),
            'core': {'root': str(directory / 'result'), 'files': payload['core']},
            'app': {'root': str(directory / 'compound/app'), 'files': app},
            'runtime': {'root': str(program / 'runtime'), 'files': runtime},
            'manifests': dict(state['manifests'])}


def signature(directory, operation=None):
    return describe(directory, operation)['sha256']


def harness(directory, operation=None):
    subject = describe(directory, operation)
    manifests = read(Path(directory) / 'composition.json')['manifests']
    return {'harness': {'root': subject['app']['root'], 'merge_files': subject['app']['files'],
        'inventory_scope': 'full-composed-app-v1', 'published_files': {
            name: [row['sha256']] for name, row in read(Path(directory) / 'composition.json')['base_app'].items()},
        'release_evidence': {'format':'helene-distribution-evidence.v1','composition_sha256':subject['sha256'],
            'package_manifest_sha256':manifests['python-package-files.json'],
            'source_inputs_sha256':manifests['source-inputs.json'],
            'package_manifest_ref':'distribution/python-package-files.json',
            'source_inputs_ref':'distribution/source-inputs.json'}},
        'distribution': {'root':read(Path(directory)/'composition.json')['program'],
            'files':{name:name for name in ('python-package-files.json','source-inputs.json')},
            'published_files':{name:[manifests[name]] for name in ('python-package-files.json','source-inputs.json')}}}


def repair(directory, name, content=None, *, delete=False, expected='', reason=''):
    directory = Path(directory).resolve()
    merge.relative(name)
    if not name.startswith('app/'):
        raise ValueError('Compound repair supports declared app/resources only; runtime/native rebuild needs an explicit executor')
    state = read(directory / 'composition.json')
    if not state or not expected or signature(directory) != expected or not str(reason).strip():
        raise ValueError('Compound repair needs a reason and exact current composition SHA256')
    local = name.removeprefix('app/')
    declared = set(state['base_app']) | set(app_inventory(directory / 'compound/app'))
    # Additions must be in the same declared application surface, never a path
    # into private state or the frozen verifier/runtime.
    if local not in declared and not local.startswith(('localharness/', 'deskd/', 'resources/', 'static/')):
        raise ValueError('New compound file is outside the declared application surface')
    target = directory / 'compound/app' / local
    before = _hash(target) if target.is_file() else None
    if delete:
        target.unlink(missing_ok=True)
    else:
        if not isinstance(content, str):
            raise ValueError('A complete UTF8 compound repair is required')
        if local.endswith('.py'):
            compile(content, local, 'exec')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8', newline='')
    state['repairs'].append({'path': name, 'reason': reason, 'before': before,
        'after': _hash(target) if target.is_file() else None, 'composition_before_sha256': expected, 'at': time.time()})
    write(directory / 'composition.json', state)


def overlay(new, plans):
    """Publish source overrides for existing transactional preimage machinery."""
    new = Path(new).resolve()
    mapping = {}
    identities = []
    for row in plans:
        directory = Path(row['directory']).resolve()
        state = read(directory / 'composition.json')
        if not state:
            continue
        subject = describe(directory)
        identities.append({'directory': str(directory), 'sha256': subject['sha256']})
        base, app = state['base_app'], subject['app']['files']
        for name in set(base) | set(app):
            if base.get(name) == app.get(name):
                continue
            key = 'app/' + name
            value = {'source': str(directory / 'compound/app' / name), 'sha256': app[name]['sha256']} if name in app else {'delete': True}
            if key in mapping and (mapping[key].get('sha256'),mapping[key].get('delete')) != (value.get('sha256'),value.get('delete')):
                raise ValueError('Agents propose different shared application repairs; reconcile before apply')
            mapping.setdefault(key,value)
    value = {'format': FORMAT, 'identities': identities, 'files': mapping}
    target = new.parent / 'compound-overlay.json'
    write(target, value)
    return value


def selected_files(new):
    """Recheck map against canonical plans, not an editable self-approval flag."""
    new = Path(new).resolve()
    value = read(new.parent / 'compound-overlay.json')
    if not value:
        return {}
    if value.get('format') != FORMAT:
        raise ValueError('Unknown compound overlay format')
    transition = read(new / 'code-transition-plan.json')
    expected_identities = [{'directory': str(Path(row['directory']).resolve()),
        'sha256': signature(row['directory'])} for row in transition.get('plans', [])
        if read(Path(row['directory']) / 'composition.json')]
    if value['identities'] != expected_identities:
        raise ValueError('Compound overlay is not the exact current code transition')
    expected_files = {}
    for row in value['identities']:
        if signature(row['directory']) != row['sha256']:
            raise ValueError('Compound candidate changed after preparation')
        directory = Path(row['directory'])
        state = read(directory / 'composition.json')
        app = app_inventory(directory / 'compound/app')
        for name in set(state['base_app']) | set(app):
            if state['base_app'].get(name) == app.get(name):
                continue
            target = 'app/' + name
            item = {'source': str(directory / 'compound/app' / name), 'sha256': app[name]['sha256']} if name in app else {'delete': True}
            if target in expected_files and expected_files[target].get('sha256') != item.get('sha256'):
                raise ValueError('Shared application repairs disagree')
            expected_files.setdefault(target, item)
    if value['files'] != expected_files:
        raise ValueError('Compound overlay differs from declared candidate repairs')
    for name, row in value['files'].items():
        merge.relative(name)
        if not name.startswith('app/'):
            raise ValueError('Compound overlay leaves application surface')
        if not row.get('delete') and _hash(row['source']) != row['sha256']:
            raise ValueError('Compound overlay bytes changed')
    return value['files']
