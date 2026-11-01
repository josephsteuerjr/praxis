"""Exact installable update composition: immutable release, editable core/app.

A/L/B stay frozen. App/resource repairs have a separate recorded M surface and
are installed through the existing preimage overlay, without overwriting the
incoming verifier/helper while it is running. Runtime bytes remain bound to the
incoming package: source-only repair cannot silently stand in for a rebuild.
"""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import time
from . import source_merge as merge
from .package_notice import read, write

FORMAT = 'helene-update-composition.v1'
MANIFESTS = ('python-app.json', 'source-inputs.json', 'python-package-files.json',
             'code-upstream.json', 'app-upstream.json','source-update-baseline.json','component-downloads.json')

@dataclass(frozen=True)
class RuntimeAdmission:
    program: str
    manifests: tuple
    package_signature: str
    files: tuple

_EDIT_RUNTIME = ContextVar('source_edit_runtime_admission', default=None)


def _runtime_inventory(program,state):
    """Actual runtime verification, retained for every execution/apply gate."""
    from .validation_read import current,parallel,scope,checkpoint
    check=current()
    if read(program/'python-package-files.json').get('files')!=state['package_files']:
        raise ValueError('Composition runtime declaration differs from the immutable passport')
    def measured(item):
        name,row=item;merge.relative(name);candidate=program/name
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(program):raise ValueError('Linked runtime input')
        expected=row.get('sha256') if isinstance(row,dict) else row
        digest=_hash(candidate)
        if digest!=expected:raise ValueError('Incoming runtime changed: '+name)
        return name.removeprefix('runtime/'),{'sha256':digest,
            'mode':'100755' if os.name!='nt' and candidate.stat().st_mode&0o111 else '100644'}
    selected=[(name,row) for name,row in state['package_files'].items() if name.startswith('runtime/')
              and '__pycache__' not in Path(name).parts and not name.endswith('.pyc')]
    def scoped(item):
        with scope(check):return measured(item)
    runtime=dict(parallel(scoped,selected,workers=os.cpu_count() or 1))
    if not runtime:raise ValueError('Incoming runtime inventory is empty')
    actual=set()
    for here,dirs,names in os.walk(program/'runtime',followlinks=False):
        checkpoint(check)
        dirs[:]=[name for name in dirs if name!='__pycache__']
        for name in [*dirs,*names]:
            checkpoint(check)
            item=Path(here)/name
            if item.is_symlink() or getattr(item,'is_junction',lambda:False)():raise ValueError('Linked runtime path')
            if item.is_file() and not name.endswith('.pyc'):actual.add(item.relative_to(program/'runtime').as_posix())
    if actual!=set(runtime):raise ValueError('Runtime has undeclared or changed installable files')
    return runtime


def admit_runtime(directory,operation=None):
    """One actual admission owned in memory by the existing coordinator."""
    directory=Path(directory).resolve();state=read(directory/'composition.json')
    if not state:state=prepare(directory,operation)
    _frozen(directory,state)
    runtime=_runtime_inventory(Path(state['program']),state)
    return RuntimeAdmission(str(Path(state['program']).resolve()),tuple(sorted(state['manifests'].items())),
        merge.signature(state['package_files']),tuple((name,row['sha256'],row['mode']) for name,row in sorted(runtime.items())))


@contextmanager
def editing_runtime(admission):
    """Candidate editing only; never exported as verification or apply evidence."""
    if not isinstance(admission,RuntimeAdmission):raise ValueError('A coordinator-owned runtime admission is required')
    token=_EDIT_RUNTIME.set(admission)
    try:yield
    finally:_EDIT_RUNTIME.reset(token)


def _hash(path):
    from .validation_read import checkpoint,digest
    checkpoint()
    with Path(path).open('rb') as stream:
        return digest(stream)


def app_inventory(root):
    """Shipped app resources/soul are program, not a live agent's private soul."""
    from .validation_read import checkpoint
    checkpoint();root=Path(root).resolve()
    files={}
    for here,dirs,names in os.walk(root,followlinks=False):
        checkpoint()
        dirs[:]=[name for name in dirs if name!='__pycache__']
        for name in [*dirs,*names]:
            checkpoint()
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
    from .app_source_update import seed
    seed(directory,operation,state)
    write(directory / 'composition.json', state)
    _frozen(directory, state)
    return state


def _frozen(directory, state):
    program = Path(state['program'])
    if app_inventory(directory / 'compound/base-app') != state['base_app']:
        raise ValueError('Frozen incoming app snapshot changed')
    actual=app_inventory(program/'app')
    if actual!=state['base_app']:
        overlay=read(program.parent/'compound-overlay.json')
        admitted=overlay.get('format')==FORMAT and any(Path(row['directory']).resolve()==directory.resolve()
            for row in overlay.get('identities',[]))
        if not admitted or actual!=app_inventory(directory/'compound/app'):
            raise ValueError('Verified incoming app changed; compound repairs must use its declared M surface')
    from .app_source_update import validate_inputs
    validate_inputs(directory,state)
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
    program = Path(state['program'])
    admission=_EDIT_RUNTIME.get()
    if admission is None:runtime=_runtime_inventory(program,state)
    else:
        if (admission.program!=str(program.resolve()) or admission.manifests!=tuple(sorted(state['manifests'].items()))
                or admission.package_signature!=merge.signature(state['package_files'])):
            raise ValueError('Runtime editing admission belongs to another immutable input')
        runtime={name:{'sha256':digest,'mode':mode} for name,digest,mode in admission.files}
    payload = {'format': FORMAT, 'input_sha256': state['input_sha256'],
               'core': merge.inventory(directory / 'result'), 'app': app,
               'runtime': runtime, 'manifests': state['manifests']}
    if state.get('app_source'):payload['app_source']=merge.signature(state['app_source'])
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
    from .app_source_update import record_resolution
    record_resolution(directory,local,reason=reason)


def shared(new, plans=None):
    """Compare complete app candidates, including unchanged entries/deletions.

    This is reconciliation evidence, never test/review approval. A plan without
    a compound snapshot still selects the incoming app, not a peer's repairs.
    """
    new = Path(new).resolve()
    if plans is None:
        plans = read(new / 'code-transition-plan.json').get('plans', [])
    if not any(read(Path(row['directory']) / 'composition.json') for row in plans):
        return {'consistent': True, 'changed': False, 'candidates': [], 'different_paths': []}
    base = app_inventory(new / 'app')
    candidates = []
    for row in plans:
        directory = Path(row['directory']).resolve()
        state = read(directory / 'composition.json')
        if state:
            if Path(state['program']).resolve() != new:
                raise ValueError('Shared application candidate belongs to another incoming program')
            app = describe(directory)['app']
        else:
            app = {'root': str(new / 'app'), 'files': base}
        candidates.append({'directory': str(directory), 'app': app})
    names = set().union(*(set(row['app']['files']) for row in candidates))
    different = sorted(name for name in names if any(
        row['app']['files'].get(name) != candidates[0]['app']['files'].get(name) for row in candidates[1:]))
    return {'consistent': not different, 'changed': any(row['app']['files'] != base for row in candidates),
            'candidates': candidates, 'different_paths': different}


def require_shared(new, plans=None):
    value = shared(new, plans)
    if not value['consistent']:
        raise ValueError('Final shared application differs across selected code roots; reconcile each candidate with '
                         'source_update repair, finish, full verify and independent review before apply: '
                         + ', '.join(value['different_paths']))
    return value


def for_plan(directory, operation=None):
    operation = _operation(directory, operation)
    binding = operation.get('coordinator') or {}
    if binding.get('kind') != 'incoming-update-runtime.v1':
        return {'consistent': True, 'changed': False, 'candidates': [], 'different_paths': []}
    return shared(binding['program'])


def overlay(new, plans):
    """Publish source overrides for existing transactional preimage machinery."""
    new = Path(new).resolve()
    require_shared(new, plans)
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
    require_shared(new, transition.get('plans', []))
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
