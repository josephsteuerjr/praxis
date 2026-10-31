"""Version-bound, source-only verification material; no installer or network effects.

The publisher supplies this directory beside the program, and binds manifest.json
in the incoming package inventory. The candidate cannot choose a smaller recipe.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import site
from functools import lru_cache

FORMAT = 'helene-verification-bundle.v1'
EXCLUDED = {'.git', '.ouroboros', '__pycache__', '.pytest_cache', '.venv', 'venv',
            'node_modules', 'target', '.cache', '_state'}
PRIVATE = {'.env', 'helene.json', 'auth.json', 'credentials.json', 'local-build.json'}
TREES = ('praxis', 'helene/core', 'desk')
CONTEXT_TREES = ('remote', 'arete-contract', 'swarm-base', '.github')
CONTEXT_FILES = ('LICENSE', 'NOTICE', 'README.md', 'CORE-SOURCE.json', 'HELENE-SOURCE.json',
                 'RELAY-SOURCE.json', 'SOURCE-SNAPSHOTS.md', 'helene/EDITION.md', 'helene/PLATFORM-SOURCE.json')
RUNNERS = ('source_verification_runner.py',)
# Current Python application has no legacy shell/svc/setup Rust prerequisite.
# Native source repairs request the actual affected component, not every old crate.
NATIVE = ('live/body', 'live/relay', 'live/hands')
# app imports desk/remote/src/settings-server, which resolves its own qrcode.
# These are the existing locked projects, not a general workspace installer.
NPM_PROJECTS = ('desk/app', 'desk/mobile', 'desk/miniapp', 'desk/remote')
SHARED_NATIVE_INPUTS=frozenset({'HELENE_BODY_DIR','HELENE_BODY_BIN','DISPLAY',
                              'DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR'})


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf8')


def digest(path):
    from .validation_read import checkpoint,digest as measured
    checkpoint()
    with Path(path).open('rb') as stream:
        return measured(stream)


def relative(name):
    from .source_merge import relative as checked
    return checked(name)


def python_framework(path):
    """Select pytest only for actual top-level test functions (including fixtures)."""
    path=Path(path)
    if path.suffix!='.py' or not path.name.startswith(('test','t_')):return 'unittest'
    return _python_framework_content(path.read_bytes())


def python_native_inputs(path):
    path=Path(path)
    return _native_input_content(path.read_bytes()) if path.suffix=='.py' else None


@lru_cache(maxsize=2048)
def _native_input_content(content):
    """A test must expressly request the shared desktop capability it uses."""
    try:module=ast.parse(content.decode('utf-8-sig'))
    except (SyntaxError,UnicodeError):return None  # Its canonical import lane fails.
    found=[]
    for node in module.body:
        targets=node.targets if isinstance(node,ast.Assign) else [node.target] if isinstance(node,ast.AnnAssign) else []
        if any(isinstance(target,ast.Name) and target.id=='HELENE_TEST_NATIVE_INPUTS' for target in targets):
            try:value=ast.literal_eval(node.value)
            except (ValueError,TypeError):raise ValueError('Test native inputs must be a literal tuple')
            if (not isinstance(value,tuple) or any(not isinstance(key,str) or key not in SHARED_NATIVE_INPUTS for key in value)
                    or len(value)!=len(set(value))):
                raise ValueError('Invalid test native input declaration')
            if 'HELENE_BODY_BIN' in value and 'HELENE_BODY_DIR' not in value:
                raise ValueError('Native body capability requires its declared directory')
            found.append(sorted(value))
    if len(found)>1:raise ValueError('Duplicate test native input declaration')
    return found[0] if found else None


def native_lane_inputs(files,names):
    result=set()
    for name in names:
        value=files.get(name,{}).get('native_inputs',[])
        if (not isinstance(value,list) or any(not isinstance(key,str) or key not in SHARED_NATIVE_INPUTS for key in value)
                or value!=sorted(set(value))):
            raise ValueError('Invalid frozen native input metadata: '+name)
        result.update(value)
    return sorted(result)


@lru_cache(maxsize=2048)
def _python_framework_content(content):
    # Classification depends on exact bytes. Twelve private, hash-verified
    # copies need not parse the same test AST twelve more times under the GIL.
    try:
        module=ast.parse(content.decode('utf-8-sig'))
    except (SyntaxError,UnicodeError):
        # The canonical import lane reports the real error, never a helper skip.
        return 'unittest'
    return 'pytest' if any(isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test')
                           for node in module.body) else 'unittest'


def inventory(root):
    """Exact regular files including lockfiles; unlike source_merge, no .lock filter."""
    from .validation_read import checkpoint
    checkpoint()
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Verification directory unavailable: ' + str(root))
    rows = {}
    for directory, dirs, names in os.walk(root):
        checkpoint()
        here = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in [*dirs, *sorted(names)]:
            checkpoint()
            p = here / name
            rel = p.relative_to(root).as_posix()
            relative(rel)
            mode = p.lstat()
            if stat.S_ISLNK(mode.st_mode) or getattr(mode, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked verification input: ' + rel)
            if p.is_file():
                rows[rel] = {'sha256': digest(p), 'size': mode.st_size,
                             'mode': '100755' if mode.st_mode & 0o111 and os.name != 'nt' else '100644'}
                if python_framework(p)=='pytest':rows[rel]['framework']='pytest'
                native=python_native_inputs(p)
                if native is not None:rows[rel]['native_inputs']=native
    return rows


def copy_files(source, files, destination):
    source, destination = Path(source).resolve(), Path(destination)
    for name, row in files.items():
        relative(name)
        p = source / name
        if p.is_symlink() or not p.resolve().is_relative_to(source) or digest(p) != row['sha256']:
            raise ValueError('Verification input changed: ' + name)
        q = destination / name
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, q)
        if os.name != 'nt':
            q.chmod(0o755 if row.get('mode') == '100755' else 0o644)


def recipe(files, native=(), rebuild=(), scripts=None):
    """Re-derived from ALL source suites, not a hand-picked affected subset."""
    names = set(files)
    core = sorted(n for n in names if n.startswith('live/') and Path(n).name.startswith('test') and n.endswith('.py'))
    py = sorted(n for n in names if n.startswith('desk/tests/') and n.count('/') == 2
                and (Path(n).name.startswith('t_') or Path(n).name.startswith('test')) and n.endswith('.py'))
    node = sorted(n for n in names if n.startswith('desk/app/test/') and n.count('/') == 3 and n.endswith('.mjs'))
    if not core or not py or not node or 'live/praxis_test.py' not in names:
        raise ValueError('Full verification sources missing (core, desk Python, Node or praxis_test)')
    lanes = []
    for root in NPM_PROJECTS:
        inputs = [root + '/package.json', root + '/package-lock.json']
        if not any(name in names for name in inputs):
            continue
        if not all(name in names and files[name].get('sha256') for name in inputs):
            raise ValueError('Incomplete locked npm project (package.json/package-lock.json): ' + root)
        lanes.append({'id': root + '/prepare', 'kind': 'npm-prepare', 'root': root,
                      'sources': inputs, 'input_sha256': {name: files[name]['sha256'] for name in inputs}})
    lanes.append({'id': 'core', 'kind': 'unittest', 'root': 'live', 'pattern': 'test*.py', 'sources': core})
    for name in core:
        if files[name].get('framework')=='pytest':
            lanes.append({'id':'pytest:'+name,'kind':'pytest-file','root':'live','source':name,'sources':[name]})
    scripts = scripts or {}
    if set(scripts) - set(py):
        raise ValueError('Script contract names a missing/noncanonical desk suite')
    for name in py:
        contract = scripts.get(name, {'kind': 'script'} if name == 'desk/tests/t_owner_stop_seed.py' else
                               {'kind':'pytest-file' if files[name].get('framework')=='pytest' else 'unittest-file'})
        if not isinstance(contract, dict) or set(contract) - {'kind', 'argv', 'reason', 'platform_skip'}:
            raise ValueError('Script contract may not replace its source/coverage identity')
        kind = contract.get('kind')
        if kind not in {'script', 'unittest-file', 'pytest-file', 'helper'}:
            raise ValueError('Unsupported declared Python suite entry: ' + name)
        if kind == 'helper' and not str(contract.get('reason', '')).strip():
            raise ValueError('A support-only Python module needs its declared collection role')
        if (not isinstance(contract.get('argv', []), list)
                or any(not isinstance(arg, str) or '\x00' in arg for arg in contract.get('argv', []))):
            raise ValueError('Script argv must be a literal argument vector')
        skips = contract.get('platform_skip', {})
        if (not isinstance(skips, dict) or set(skips) - {'win32', 'linux', 'darwin'}
                or any(not isinstance(reason, str) or not reason.strip() for reason in skips.values())):
            raise ValueError('Platform skips require exact declared applicability reasons')
        lanes.append({'id': name, 'kind': kind, 'source': name, 'sources': [name], **contract})
    lanes.append({'id': 'desk-node', 'kind': 'node', 'sources': node})
    # Only explicit current-release or changed-native obligations. Unchanged
    # supplied binaries retain separate release qualification; no legacy rebuild.
    for root in native:
        if root not in NATIVE:
            raise ValueError('No declared native verification executor for: ' + root)
        lane = {'id': root, 'kind': 'cargo', 'root': root, 'sources': sorted(
            n for n in names if n.startswith(root + '/') and (n.endswith('.rs') or Path(n).name in {'Cargo.toml', 'Cargo.lock'}))}
        lanes.append(lane)
    for root in rebuild:
        if root not in ('desk/app', 'desk/mobile', 'desk/miniapp'):
            raise ValueError('No declared generated UI verification source: ' + root)
        if root + '/prepare' not in {lane['id'] for lane in lanes}:
            raise ValueError('Build requires its locked npm project: ' + root)
        lanes.append({'id': root + '/build', 'kind': 'npm-build', 'root': root,
                      'input_sha256': {name: files[name]['sha256'] for name in
                                       (root + '/package.json', root + '/package-lock.json')},
                      'sources': sorted(n for n in names if n.startswith(root + '/')
                                        and '/dist/' not in n and '/test/' not in n)})
    for lane in lanes:
        native=native_lane_inputs(files,lane['sources'])
        if native:lane['native_inputs']=native
    return lanes


def executor_lock(tools=None, packages=None):
    """Capture an explicitly provisioned native executor; never install packages."""
    supplied = tools or {}
    paths = {name: supplied.get(name) or (sys.executable if name == 'python' else shutil.which(name))
             for name in ('python', 'node', 'cargo', 'npm')}
    return {'platform': sys.platform, 'python_version': sys.version,
            'tools': {name: {'sha256': digest(p)} for name, p in paths.items() if p and Path(p).is_file()},
            'python_packages': dict(sorted((d.metadata['Name'].lower().replace('_', '-'), d.version)
                for d in importlib.metadata.distributions(path=site.getsitepackages())
                if d.metadata['Name'] and (packages is None or d.metadata['Name'].lower().replace('_', '-') in packages)))}


def build(source, destination, version, locks, *, allowed_skips=None, native=(), rebuild=(),
          wheelhouses=None, scripts=None, max_bytes=256 * 1024 * 1024):
    """Build a small source bundle. Locks are per-OS native executor facts.

    Code is composed from the publisher's praxis + helene/core, including tests.
    Never pass an installed profile as source. Output must be a new directory.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or source == destination or destination.is_relative_to(source):
        raise ValueError('Bundle output must be new and outside its source')
    if not str(version).strip() or not locks or any(k not in {'win32', 'linux', 'darwin'} for k in locks):
        raise ValueError('Version and explicit native executor locks are required')
    selected = {}
    for tree in (*TREES, *(n for n in CONTEXT_TREES if (source / n).is_dir())):
        base = source / tree
        for name, row in inventory(base).items():
            parts = Path(name).parts
            if (parts[0] in {'runtime', 'models'} or Path(name).name in PRIVATE or Path(name).name.endswith(('.pyc', '.session', '.log'))
                    or (tree != 'desk' and any(p in {'memory', 'soul', 'workspace', 'data', 'AGENTS.md'} for p in parts))):
                continue
            selected[tree + '/' + name] = row
    for name in CONTEXT_FILES:
        if (source / name).is_file():
            selected[name] = {'sha256': digest(source / name), 'size': (source / name).stat().st_size, 'mode': '100644'}
    total = sum(r['size'] for r in selected.values())
    if total > max_bytes:
        raise ValueError('Verification sources exceed declared small-bundle byte budget: ' + str(total))
    destination.mkdir(parents=True)
    repo = destination / 'repository'
    copy_files(source, selected, repo)
    live = repo / 'live'
    core = {}
    for tree in ('praxis', 'helene/core'):
        for name, row in inventory(repo / tree).items():
            # Edition overlay wins, matching the product's composition rule.
            core[name] = (repo / tree, row)
    for name, (base, row) in core.items():
        copy_files(base, {name: row}, live)
    helpers = destination / 'runner'
    helpers.mkdir()
    for name in RUNNERS:
        shutil.copyfile(Path(__file__).with_name(name), helpers / name)
    dependencies = {}
    for platform, wheelhouse in (wheelhouses or {}).items():
        if platform not in locks:
            raise ValueError('Wheelhouse has no matching provisioned native lock')
        wheelhouse = Path(wheelhouse)
        metadata = json.loads((wheelhouse / 'wheels.json').read_text(encoding='utf8'))
        packages = metadata.get('packages', {})
        if not packages:
            raise ValueError('Verification wheelhouse has no pinned dependency closure')
        requirements = []
        wheels = {}
        for package, row in sorted(packages.items()):
            name = row['filename']
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', package)
                    or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', row['version'])
                    or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])
                    or Path(name).name != name or not name.endswith('.whl') or digest(wheelhouse / name) != row['sha256']):
                raise ValueError('Verification dependency wheel changed: ' + name)
            if locks[platform]['python_packages'].get(package.lower().replace('_', '-')) != row['version']:
                raise ValueError('Wheel version differs from the executor dependency lock: ' + package)
            requirements.append(package + '==' + row['version'] + ' --hash=sha256:' + row['sha256'])
            wheels[name] = {'sha256': row['sha256'], 'size': (wheelhouse / name).stat().st_size, 'mode': '100644'}
        if total + sum(r['size'] for r in wheels.values()) > max_bytes:
            raise ValueError('Verification material plus wheels exceeds its byte budget; supply an explicit executor instead')
        total += sum(r['size'] for r in wheels.values())
        target = destination / 'dependencies' / platform
        copy_files(wheelhouse, wheels, target)
        (target / 'requirements.txt').write_text('\n'.join(requirements) + '\n', encoding='utf8')
        dependencies[platform] = {'files': inventory(target), 'packages': packages}
    files = inventory(repo)
    total = sum(row['size'] for row in files.values()) + sum(
        row['size'] for dep in dependencies.values() for row in dep['files'].values())
    if total > max_bytes:
        raise ValueError('Complete composed verification material exceeds its byte budget')
    manifest = {'format': FORMAT, 'version': str(version), 'files': files,
                'runners': inventory(helpers), 'lanes': recipe(files, native, rebuild, scripts),
                'native_lanes': list(native), 'build_lanes': list(rebuild),
                'script_contracts': scripts or {},
                'dependencies': dependencies,
                'executor_locks': locks, 'allowed_test_skips': allowed_skips or {},
                'reuse': 'only-identical-whole-composition; no lane reuse'}
    (destination / 'manifest.json').write_bytes(encoded(manifest))
    return {'root': str(destination), 'sha256': digest(destination / 'manifest.json'),
            'version': str(version), 'source_bytes': total, 'lanes': len(manifest['lanes'])}


def load(root, expected, version):
    root = Path(root).resolve()
    if root.is_symlink() or digest(root / 'manifest.json') != expected:
        raise ValueError('Verification bundle manifest identity changed')
    value = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
    if value.get('format') != FORMAT or value.get('version') != str(version):
        raise ValueError('Verification bundle belongs to another version')
    if inventory(root / 'repository') != value.get('files') or inventory(root / 'runner') != value.get('runners'):
        raise ValueError('Verification bundle source/runner changed')
    if recipe(value['files'], value.get('native_lanes', []), value.get('build_lanes', []), value.get('script_contracts',{})) != value.get('lanes'):
        raise ValueError('Verification bundle omits a full discovery lane')
    for platform, row in value.get('dependencies', {}).items():
        if platform not in {'win32', 'linux', 'darwin'} or inventory(root / 'dependencies' / platform) != row['files']:
            raise ValueError('Verification dependency wheel material changed')
    return value


def provision(root, expected, version, python, target, stopped=lambda: False):
    """Explicit offline acquisition into a new private target, not global Python.

    Publisher may include hash-pinned wheels (including pip when the embedded
    runtime lacks it). No network, model, global pip or copied runtime is used.
    A failed/cancelled install remains as evidence; this never admits apply.
    """
    from . import source_update_battery
    value = load(root, expected, version)
    declared = value.get('dependencies', {}).get(sys.platform)
    if not declared:
        raise ValueError('No offline verification dependency closure for this native platform; select a provisioned executor')
    if digest(python) != value['executor_locks'][sys.platform]['tools'].get('python', {}).get('sha256'):
        raise ValueError('Dependency installer Python differs from the provisioned lock')
    target = Path(target).resolve()
    if target.exists():
        raise ValueError('Dependency target must be new; keep previous attempts separately')
    target.mkdir(parents=True)
    home = target / '.installer-home'
    env = source_update_battery._environment(home, Path(root) / 'repository/live', Path(root) / 'repository/desk')
    wheelhouse = Path(root).resolve() / 'dependencies' / sys.platform
    pip = [str(wheelhouse / n) for n in declared['files'] if n.startswith('pip-') and n.endswith('.whl')]
    env['PYTHONPATH'] = os.pathsep.join(pip)
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    command = [str(python), '-B', '-s', '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-compile',
               '--require-hashes', '--find-links', str(wheelhouse), '--target', str(target),
               '-r', str(wheelhouse / 'requirements.txt')]
    log = target.with_name(target.name + '.install.log')
    result = source_update_battery._execute(command, wheelhouse, env, log, stopped)
    receipt = {'format': 'helene-verification-dependencies.v1', 'bundle_sha256': expected,
               'platform': sys.platform, 'python_sha256': digest(python), 'result': result,
               'log_sha256': digest(log), 'files': inventory(target)}
    receipt_path = target.with_name(target.name + '.receipt.json')
    receipt_path.write_bytes(encoded(receipt))
    if result['status'] != 'passed':
        raise ValueError('Verification dependency provisioning failed; retained log: ' + str(log))
    return {'python_dependencies': str(target), 'dependency_receipt': str(receipt_path),
            'dependency_receipt_sha256': digest(receipt_path)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    capture = sub.add_parser('capture-executor')
    capture.add_argument('--output', type=Path, required=True)
    capture.add_argument('--packages', type=Path, help='JSON list of provisioned verification dependency names')
    create = sub.add_parser('build')
    for key in ('source', 'output', 'locks'):
        create.add_argument('--' + key, type=Path, required=True)
    create.add_argument('--version', required=True)
    create.add_argument('--allowed-skips', type=Path)
    create.add_argument('--native-lanes', type=Path, help='JSON list of current-release native consumer source roots')
    create.add_argument('--build-lanes', type=Path, help='JSON list of current-release generated UI source roots')
    create.add_argument('--wheelhouses', type=Path, help='JSON platform -> hash-pinned offline wheelhouse path')
    create.add_argument('--script-contracts', type=Path, help='JSON exact suite path -> supported script argv/helper collection role')
    install = sub.add_parser('provision')
    for key in ('bundle', 'python', 'output'):
        install.add_argument('--' + key, type=Path, required=True)
    install.add_argument('--sha256', required=True)
    install.add_argument('--version', required=True)
    a = p.parse_args()
    if a.command == 'capture-executor':
        packages = json.loads(a.packages.read_text(encoding='utf8')) if a.packages else None
        a.output.write_bytes(encoded(executor_lock(packages=packages)))
        print('EXECUTOR_LOCK_WRITTEN')
    elif a.command == 'build':
        locks = json.loads(a.locks.read_text(encoding='utf8'))
        skips = json.loads(a.allowed_skips.read_text(encoding='utf8')) if a.allowed_skips else None
        native = json.loads(a.native_lanes.read_text(encoding='utf8')) if a.native_lanes else []
        rebuild = json.loads(a.build_lanes.read_text(encoding='utf8')) if a.build_lanes else []
        wheels = json.loads(a.wheelhouses.read_text(encoding='utf8')) if a.wheelhouses else None
        scripts = json.loads(a.script_contracts.read_text(encoding='utf8')) if a.script_contracts else None
        print(json.dumps(build(a.source, a.output, a.version, locks, allowed_skips=skips, native=native, rebuild=rebuild, wheelhouses=wheels, scripts=scripts)))
    else:
        print(json.dumps(provision(a.bundle, a.sha256, a.version, a.python, a.output)))


if __name__ == '__main__':
    main()
