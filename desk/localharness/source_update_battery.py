"""Full version-bound update verification, separate from independent review.

prepare records attempted lanes (including red/unavailable) as frozen evidence.
validate alone certifies exact full coverage for apply; complete is NOT pass.
No prior lane is reused after any whole-composition or bundle change.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from . import source_merge as merge, source_verification_bundle as bundle
from .package_notice import read, write

VERSION = 2
TIMEOUT = 3600  # per physical command, never the age of an update job
MAX_OUTPUT = 32 * 1024 * 1024


class CustodyUnconfirmed(RuntimeError):
    pass


def composition(operation, directory, harness=None):
    """One authority for code + app/resources + runtime/manifests (owned by A)."""
    from . import update_composition
    value = update_composition.describe(Path(directory), operation)
    if not update_composition.for_plan(directory, operation)['consistent']:
        raise ValueError('Final shared application differs; reconcile before the full battery')
    if not value.get('sha256') or not all(value.get(k, {}).get('files') for k in ('core', 'app', 'runtime')):
        raise ValueError('Full update composition is unavailable')
    return value


def _bundle(operation):
    coordinator = operation.get('coordinator') or {}
    binding = operation.get('verification_bundle') or {
        'root': str(Path(coordinator.get('program', '')) / 'verification'),
        'sha256': coordinator.get('verification_bundle_sha256')}
    if not binding.get('sha256'):
        raise ValueError('Incoming package has no version-bound verification bundle identity')
    root = Path(binding['root']).resolve()
    value = bundle.load(root, binding['sha256'], operation['version'])
    return root, binding['sha256'], value


def _obligations(directory, value, manifest):
    """Repairs add required build/native lanes; untouched legacy crates do not."""
    plan = merge.validate_inputs(directory)
    incoming = plan.get('files', {}).get('incoming', {})
    native = set(manifest.get('native_lanes', []))
    for name in set(incoming) | set(value['core']['files']):
        before, after = incoming.get(name), value['core']['files'].get(name)
        if before == after or not (name.endswith('.rs') or Path(name).name in {'Cargo.toml', 'Cargo.lock'}):
            continue
        component = name.split('/', 1)[0]
        root = 'live/' + component
        if root not in bundle.NATIVE:
            raise ValueError('Changed native source has no declared verification executor: ' + name)
        native.add(root)
    rebuild = set(manifest.get('build_lanes', []))
    state = read(Path(directory) / 'composition.json')
    base_app = state.get('base_app', {})
    for name in set(base_app) | set(value['app']['files']):
        if base_app.get(name) == value['app']['files'].get(name):
            continue
        if name.startswith('static/'):
            rebuild.add('desk/app')
        elif name.startswith('mobile/'):
            rebuild.add('desk/mobile')
        elif name.startswith('miniapp/'):
            rebuild.add('desk/miniapp')
        if name.endswith(('.rs', '.c', '.cpp')):
            raise ValueError('Changed app native source needs a declared rebuild executor: ' + name)
    return sorted(native), sorted(rebuild)


def _verification_files(value, manifest, base_app=None):
    """The same full source composition, without copying to validate a receipt."""
    files = dict(manifest['files'])
    core = dict(value['core']['files'])
    for name,row in list(core.items()):
        if bundle.python_framework(Path(value['core']['root'])/name)=='pytest':
            core[name]={**row,'framework':'pytest'}
    for name, row in manifest['files'].items():
        if name.startswith('live/') and ((Path(name).name.startswith('test') and name.endswith('.py')) or Path(name).name == 'Cargo.lock' or name[5:] in {'.env.example', 'docker-compose.deploy.yml'}):
            core.setdefault(name[5:], row)
    for prefix in ('live/', 'praxis/', 'helene/core/'):
        files = {n: r for n, r in files.items() if not n.startswith(prefix)}
        files.update({prefix + n: r for n, r in core.items()})
    for name in base_app or {}:
        mapped = ('app/dist/' + name[7:] if name.startswith('static/') else
                  'mobile/dist/' + name[7:] if name.startswith('mobile/') else
                  'miniapp/dist/' + name[8:] if name.startswith('miniapp/') else name)
        files.pop('desk/' + mapped, None)
    for name, row in value['app']['files'].items():
        mapped = ('app/dist/' + name[7:] if name.startswith('static/') else
                  'mobile/dist/' + name[7:] if name.startswith('mobile/') else
                  'miniapp/dist/' + name[8:] if name.startswith('miniapp/') else name)
        files['desk/' + mapped] = row
    return files


def _lane_tool(lane):
    kind = lane['kind']
    return ('python' if kind in {'unittest', 'unittest-file', 'pytest-file', 'script', 'helper'} else
            'node' if kind == 'node' else 'npm' if kind in {'npm-prepare', 'npm-build'} else 'cargo')


def _required_tools(lanes):
    selected = [lane for lane in lanes if sys.platform not in lane.get('platform_skip', {})]
    return ({_lane_tool(lane) for lane in selected}
            | ({'node'} if any(lane['kind'] in {'npm-prepare', 'npm-build'} for lane in selected) else set()))


def _native_inputs(executor):
    """Explicit stand capability, not ambient desktop or native build proof."""
    env = executor.get('native_env', {})
    files = executor.get('native_files', {})
    allowed = {'HELENE_BODY_DIR', 'HELENE_BODY_BIN', 'DISPLAY',
               'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR'}
    if (not isinstance(env, dict) or not isinstance(files, dict)
            or set(env) - allowed or any(not isinstance(v, str) or not v.strip() or '\x00' in v for v in env.values())):
        raise ValueError('Invalid explicit native_env/native_files declaration')
    env = dict(env)
    for key in ('HELENE_BODY_DIR', 'HELENE_BODY_BIN', 'XDG_RUNTIME_DIR'):
        if key in env:
            path = Path(env[key])
            if not path.is_absolute() or path.is_symlink():
                raise ValueError('Native input requires an absolute non-symlink path: ' + key)
            env[key] = str(path.resolve())
            if key != 'HELENE_BODY_BIN' and not path.is_dir():
                raise ValueError('Native input directory unavailable: ' + key)
    required = set()
    if 'HELENE_BODY_DIR' in env:
        base = Path(env['HELENE_BODY_DIR'])
        suffix = '.exe' if os.name == 'nt' else ''
        pairs = [(base / (prefix + '-bridge' + suffix), base / (prefix + '-body' + suffix))
                 for prefix in ('helene', 'praxis')]
        if not any(all(p.is_file() for p in pair) for pair in pairs):
            raise ValueError('Native body/bridge pair unavailable in HELENE_BODY_DIR')
        # Pin every recognized candidate, so consumer-specific pair precedence
        # cannot select another unbound binary in the same directory.
        required.update(str(p.resolve()) for pair in pairs for p in pair if p.exists())
        if 'HELENE_BODY_BIN' in env and env['HELENE_BODY_BIN'] not in {str(pair[1].resolve()) for pair in pairs}:
            raise ValueError('HELENE_BODY_BIN is not the declared native body')
    elif 'HELENE_BODY_BIN' in env:
        raise ValueError('Native body binary requires its body/bridge directory')
    pinned = {}
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid native file path/SHA256')
        path = Path(name)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError('Native file unavailable or not a regular absolute input: ' + name)
        canonical = str(path.resolve())
        if canonical in pinned or bundle.digest(path) != digest:
            raise ValueError('Native file changed or does not match its SHA256: ' + name)
        pinned[canonical] = digest
    if set(pinned) != required:
        raise ValueError('Native files must pin exactly the selected body/bridge inputs')
    return {'native_env': env, 'native_files': pinned}


def _tools(operation, manifest):
    supplied = operation.get('verification_executor') or {}
    program = (operation.get('coordinator') or {}).get('program')
    runtime_python = (Path(program) / 'runtime' / ('python.exe' if os.name == 'nt' else 'bin/python3')) if program else None
    default_python = str(runtime_python) if runtime_python and runtime_python.is_file() else sys.executable
    tools = {name: supplied.get(name) or (default_python if name == 'python' else shutil.which(name))
             for name in ('python', 'node', 'cargo', 'npm')}
    expected = manifest.get('executor_locks', {}).get(sys.platform)
    observation = {'platform': sys.platform, 'tools': {}}
    if 'native_env' in supplied or 'native_files' in supplied:
        observation['native_inputs'] = _native_inputs(supplied)
    errors = {}
    npm_cache = supplied.get('npm_config_cache')
    if (not isinstance(npm_cache, str) or not Path(npm_cache).is_absolute()
            or Path(npm_cache).is_symlink() or not Path(npm_cache).is_dir()):
        errors['npm'] = 'Explicit owned offline npm_config_cache directory unavailable'
    else:
        # npm verifies cached content against the source lock's integrity. The
        # mutable cache is an explicit capability, never a credential/home copy.
        observation['npm_cache'] = str(Path(npm_cache).resolve())
    for name, path in tools.items():
        if not path or not Path(path).is_file():
            errors[name] = 'Explicit native executor tool unavailable: ' + name
            continue
        observation['tools'][name] = {'sha256': bundle.digest(path)}
        if not expected or observation['tools'][name] != expected.get('tools', {}).get(name):
            errors[name] = 'Native executor tool does not match the version-bound lock: ' + name
    if supplied.get('python_dependencies'):
        receipt_path = Path(supplied.get('dependency_receipt', ''))
        target = Path(supplied['python_dependencies'])
        try:
            if bundle.digest(receipt_path) != supplied.get('dependency_receipt_sha256'):
                raise ValueError('Dependency provisioning receipt changed')
            receipt = read(receipt_path)
            binding = operation.get('verification_bundle') or {}
            expected_bundle = binding.get('sha256') or (operation.get('coordinator') or {}).get('verification_bundle_sha256')
            if (receipt.get('bundle_sha256') != expected_bundle or receipt.get('platform') != sys.platform
                    or receipt.get('result', {}).get('status') != 'passed'
                    or receipt.get('python_sha256') != observation['tools'].get('python', {}).get('sha256')
                    or bundle.inventory(target) != receipt.get('files')):
                raise ValueError('Provisioned dependency input differs from the current native bundle')
            observation['dependencies_sha256'] = supplied['dependency_receipt_sha256']
        except (OSError, ValueError) as exc:
            errors['python'] = str(exc)
    return tools, observation, errors


def _environment(home, core, app, operation=None):
    # No owner's API keys, proxy, Telegram session, PRAXIS_BASE or model settings.
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
                             'SYSTEMDRIVE', 'LANG', 'LC_ALL'}}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / 'appdata'),
               LOCALAPPDATA=str(home / 'local'), TEMP=str(home / 'tmp'), TMP=str(home / 'tmp'),
               TMPDIR=str(home / 'tmp'), PRAXIS_TEST='1', PYTHONNOUSERSITE='1',
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               PYTHONPATH=os.pathsep.join((str(core), str(app), str(app / 'localharness'))),
               HELENE_TREE_SRC=str(core), HF_HUB_OFFLINE='1', CARGO_NET_OFFLINE='true',
               HELENE_TEST_SOURCE_ROOT=str(app.parent),
               npm_config_offline='true', npm_config_audit='false', npm_config_fund='false')
    env['npm_config_userconfig'] = str(home / 'npm-user.rc')
    env['npm_config_globalconfig'] = str(home / 'npm-global.rc')
    for name in ('appdata', 'local', 'tmp'):
        (home / name).mkdir(parents=True, exist_ok=True)
    # Only expressly supplied compiler/package caches; never inherit credential config.
    executor = (operation or {}).get('verification_executor') or {}
    if executor.get('python_dependencies'):
        env['PYTHONPATH'] += os.pathsep + str(Path(executor['python_dependencies']).resolve())
    for key in ('CARGO_HOME', 'RUSTUP_HOME', 'npm_config_cache'):
        env[key] = str(Path(executor[key]).resolve()) if executor.get(key) else str(home / key.lower())
    env.update(_native_inputs(executor)['native_env'])
    return env


def _execute(command, cwd, env, output, stopped, native_inputs=None):
    if native_inputs is not None and _native_inputs(native_inputs) != native_inputs:
        raise ValueError('Native executor inputs changed before command launch')
    # Imported from the verified incoming runtime, not candidate M.
    from command_process import CommandProcess
    with output.open('wb') as stream:
        proc = CommandProcess(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=stream, stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + TIMEOUT
        try:
            while proc.poll() is None:
                if stopped():
                    raise InterruptedError('Owner stopped update battery')
                limited = output.stat().st_size > MAX_OUTPUT
                if time.monotonic() >= deadline or limited:
                    settled, reason = proc.stop_tree()
                    if not settled:
                        raise CustodyUnconfirmed('Battery command custody unresolved: ' + reason)
                    return {'status': 'output-limit' if limited else 'timeout', 'exit': proc.returncode}
                time.sleep(.1)
            return {'status': ('output-limit' if output.stat().st_size > MAX_OUTPUT else
                               'passed' if proc.returncode == 0 else 'failed'), 'exit': proc.returncode}
        except BaseException:
            settled, reason = proc.stop_tree()
            if not settled:
                raise CustodyUnconfirmed('Battery command custody unresolved: ' + reason)
            raise


def _copy_composition(value, repository, manifest, base_app=None):
    """No runtime copy: only full source/tests, then actual composed candidate/app."""
    core = repository / 'live'
    required_tests = {n[len('live/'):]: row for n, row in manifest['files'].items()
                      if n.startswith('live/') and ((Path(n).name.startswith('test') and n.endswith('.py'))
                                                    or Path(n).name == 'Cargo.lock' or n[5:] in {'.env.example', 'docker-compose.deploy.yml'})}
    # The two public deployment examples support unchanged source-contract tests;
    # they are frozen/hash-bound verification inputs, never a live .env/profile
    # or an addition to the installable candidate inventory.
    # Replace runtime source, not overlay an absent candidate file with old code.
    shutil.rmtree(core)
    bundle.copy_files(value['core']['root'], value['core']['files'], core)
    for name, row in required_tests.items():
        if name not in value['core']['files']:
            src = repository / 'praxis' / name
            layer = repository / 'helene/core' / name
            if layer.is_file():
                src = layer
            if not src.is_file() or bundle.digest(src) != row['sha256']:
                raise ValueError('Required full test source unavailable: ' + name)
            target = core / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    # Edition/direct-source tests must import M, not the old overlay or mirror.
    for base in (repository / 'praxis', repository / 'helene/core'):
        shutil.rmtree(base)
        bundle.copy_files(core, bundle.inventory(core), base)
    desk = repository / 'desk'
    app = value['app']
    for name in base_app or {}:
        mapped = ('app/dist/' + name[7:] if name.startswith('static/') else
                  'mobile/dist/' + name[7:] if name.startswith('mobile/') else
                  'miniapp/dist/' + name[8:] if name.startswith('miniapp/') else name)
        bundle.relative(mapped)
        (desk / mapped).unlink(missing_ok=True)
    # Packaged generated UI maps back to its canonical source-tree location.
    for name, row in app['files'].items():
        mapped = ('app/dist/' + name[7:] if name.startswith('static/') else
                  'mobile/dist/' + name[7:] if name.startswith('mobile/') else
                  'miniapp/dist/' + name[8:] if name.startswith('miniapp/') else name)
        bundle.relative(mapped)
        bundle.copy_files(app['root'], {name: row}, desk / '.incoming-app')
        source = desk / '.incoming-app' / name
        target = desk / mapped
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.rmtree(desk / '.incoming-app')
    return core, desk


def _text(path):
    return path.read_text(encoding='utf8', errors='replace')


def _tap(text):
    outcomes = []
    for line in text.splitlines():
        m = re.match(r'^\s*(ok|not ok) \d+ - (.*)$', line)
        if m:
            name = m[2]
            reason = re.search(r'\s+# SKIP(?:\s+(.*))?$', name, re.I)
            outcomes.append({'id': name.split(' # ')[0], 'status': 'skipped' if reason else
                             'todo' if re.search(r'\s+# TODO\b', name, re.I) else
                             'passed' if m[1] == 'ok' else 'failed',
                             **({'reason': reason[1] or ''} if reason else {})})
    total = re.findall(r'^# tests (\d+)\s*$', text, re.M)
    return {'collected': [r['id'] for r in outcomes], 'executed': [r['id'] for r in outcomes],
            'outcomes': outcomes, 'tests_run': int(total[-1]) if total else 0,
            'complete': bool(total), 'coverage_complete': bool(total) and bool(outcomes)}


def _cargo(listing, text):
    collected = re.findall(r'^(.+): test\s*$', listing, re.M)
    outcomes = []
    for name, status, reason in re.findall(r'^test (.+?) \.\.\. (ok|FAILED|ignored)([^\r\n]*)$', text, re.M):
        outcomes.append({'id': name, 'status': {'ok': 'passed', 'FAILED': 'failed', 'ignored': 'skipped'}[status],
                         **({'reason': reason.lstrip(', ')} if status == 'ignored' else {})})
    return {'collected': collected, 'executed': [r['id'] for r in outcomes], 'outcomes': outcomes,
            'tests_run': len(outcomes), 'complete': True,
            'coverage_complete': bool(collected) and Counter(collected) == Counter(r['id'] for r in outcomes)}


def _run_lane(lane, repository, runner, tools, env, cache, stopped, native_inputs=None):
    identity = hashlib.sha256(lane['id'].encode()).hexdigest()[:20]
    output = cache / (identity + '.output')
    observation = cache / (identity + '.cases.json')
    kind = lane['kind']
    root = repository / lane.get('root', 'desk')
    commands = []
    if kind in {'unittest', 'unittest-file', 'pytest-file'}:
        command = [tools['python'], '-B', '-s', str(runner), '--root', str(root), '--report', str(observation),
                   '--lock', str(cache / 'executor-lock.json'), '--sandbox', str(repository.parent)]
        if env.get('HELENE_VERIFICATION_DEPENDENCIES'):
            command += ['--dependencies', env['HELENE_VERIFICATION_DEPENDENCIES']]
        if kind in {'unittest-file','pytest-file'}:
            command += ['--source', str(repository / lane['source'])]
        if kind=='pytest-file':command+=['--framework','pytest']
        commands.append(command)
    elif kind == 'script':
        argv = [arg.replace('{repository}', str(repository)).replace('{work}', str(repository.parent))
                for arg in lane.get('argv', [])]
        commands.append([tools['python'], '-B', '-s', str(repository / lane['source']), *argv])
    elif kind == 'node':
        commands.append([tools['node'], '--test', '--test-concurrency=1', '--test-reporter=tap', *[str(repository / n) for n in lane['sources']]])
        root = repository / 'desk/app'
    elif kind in {'cargo', 'cargo-build'}:
        command = [tools['cargo'], 'check' if kind == 'cargo-build' else 'test', '--offline', '--locked']
        if lane['root'] == 'desk/shell' and sys.platform == 'linux':
            command += ['--no-default-features', '--features', 'host', '--bin', 'helene-host']
        if lane['root'] == 'live/body':
            command += ['--workspace']
        if kind == 'cargo':
            commands.append(command + ['--', '--list'])
            commands.append(command + ['--', '--test-threads=1'])
        else:
            commands.append(command)
    elif kind in {'npm-prepare', 'npm-build'}:
        inputs = lane.get('input_sha256', {})
        if set(inputs) != {lane['root'] + '/package.json', lane['root'] + '/package-lock.json'}:
            raise ValueError('NPM lane has no exact package/lock input identity')
        for name, digest in inputs.items():
            bundle.relative(name)
            if bundle.digest(repository / name) != digest:
                raise ValueError('NPM verification input changed: ' + name)
        # Prefer the explicitly selected Node/npm over ambient PATH. npm scripts
        # still use the normal local node_modules/.bin resolution.
        env = dict(env, PATH=os.pathsep.join((str(Path(tools['node']).parent),
                    str(Path(tools['npm']).parent), env.get('PATH', ''))))
        command = ([tools['npm'], 'ci', '--ignore-scripts'] if kind == 'npm-prepare'
                   else [tools['npm'], 'run', 'build'])
        commands.append(command + ['--offline', '--cache', env['npm_config_cache'], '--audit=false', '--fund=false'])
    else:
        raise ValueError('Unknown trusted verification lane kind: ' + str(kind))
    rows = []
    logs = []
    for index, command in enumerate(commands):
        if stopped():
            raise InterruptedError('Owner stopped update battery')
        target = output if index == len(commands) - 1 else cache / (identity + '-' + str(index) + '.output')
        row = _execute(command, root, env, target, stopped, native_inputs)
        if kind in {'npm-prepare', 'npm-build'}:
            row.update(argv=command, cwd=str(root), input_sha256=inputs,
                       npm_cache=env['npm_config_cache'],
                       tools={name: {'path': str(tools[name]), 'sha256': bundle.digest(tools[name])}
                              for name in ('node', 'npm')})
            for name, digest in inputs.items():
                if bundle.digest(repository / name) != digest:
                    row.update(status='input-changed', reason='NPM verification input changed: ' + name)
        logs.append(target)
        rows.append(row)
        if row['status'] != 'passed':
            break
    result = {'id': lane['id'], 'kind': kind, 'commands': commands[:len(rows)],
              'status': rows[-1]['status'], 'exit': rows[-1].get('exit')}
    if kind in {'npm-prepare', 'npm-build'}:
        result['command_results'] = rows
    if kind in {'unittest', 'unittest-file', 'pytest-file'}:
        result['observation'] = read(observation) if observation.is_file() else {}
    elif kind == 'node':
        result['observation'] = _tap(_text(logs[-1]))
    elif kind == 'cargo' and len(rows) == 2:
        result['observation'] = _cargo(_text(logs[0]), _text(logs[1]))
    elif kind in {'script', 'cargo-build', 'npm-prepare', 'npm-build'}:
        # Build/script obligations are command units, never invented unittest cases.
        units = [lane['id'] + ':command-' + str(i) for i in range(len(commands))]
        result['observation'] = {'collected': units, 'executed': units[:len(rows)], 'complete': len(rows) == len(commands),
            'coverage_complete': len(rows) == len(commands), 'checks_run': len(rows),
            'outcomes': [{'id': n, 'status': r['status']} for n, r in zip(units, rows)]}
    result['logs'] = {p.name: bundle.digest(p) for p in logs}
    if observation.is_file():
        result['observation_sha256'] = bundle.digest(observation)
        result['observation_file'] = observation.name
    return result


def _check_results(report, manifest):
    expected = [lane['id'] for lane in report['lanes']]
    rows = report.get('results') or []
    if not expected or [r.get('id') for r in rows] != expected:
        raise ValueError('Full battery lane coverage is missing, duplicated or reordered')
    permitted = manifest.get('allowed_test_skips', {}).get(sys.platform, {})
    total = 0
    for lane, row in zip(report['lanes'], rows):
        if lane['kind'] == 'helper':
            if row.get('status') != 'support-only' or row.get('reason') != lane.get('reason'):
                raise ValueError('Unsupported helper collection declaration: ' + lane['id'])
            continue
        if row.get('status') == 'platform-skip':
            if row.get('reason') != lane.get('platform_skip', {}).get(sys.platform):
                raise ValueError('Undeclared platform lane skip: ' + lane['id'])
            continue
        if row.get('status') != 'passed' or row.get('exit') != 0:
            raise ValueError('Full battery lane did not pass: ' + lane['id'] + ': ' + str(row.get('reason') or row.get('status')))
        observed = row.get('observation') or {}
        collected, executed = observed.get('collected') or [], observed.get('executed') or []
        not_started = observed.get('skipped_before_start') or []
        if (not observed.get('complete') or not observed.get('coverage_complete') or not collected
                or Counter(collected) != Counter(executed + not_started)):
            raise ValueError('Nonzero exact collected/executed coverage unavailable: ' + lane['id'])
        terminal = []
        for case in observed.get('outcomes', []):
            terminal.append(case['id'])
            if case.get('status') == 'skipped':
                key = lane['id'] + '::' + case['id']
                if not case.get('reason') or permitted.get(key) != case['reason']:
                    raise ValueError('Undeclared test skip: ' + key)
            elif case.get('status') != 'passed':
                raise ValueError('Full battery case is not passed: ' + lane['id'] + '::' + case['id'])
        if Counter(terminal) != Counter(collected):
            raise ValueError('Full battery has missing/duplicate terminal cases: ' + lane['id'])
        if lane['kind'] in {'node', 'cargo', 'unittest', 'unittest-file', 'pytest-file'} and not observed.get('tests_run') and not not_started:
            raise ValueError('Zero test execution: ' + lane['id'])
        total += len(executed)
    if not total:
        raise ValueError('Full battery has zero applicable execution')


def _binding(operation, directory):
    value = composition(operation, directory)
    root, manifest_sha, manifest = _bundle(operation)
    tools, observed, errors = _tools(operation, manifest)
    # A source check by an arbitrary operator Python is not proof about the
    # actual runtime selected for this composition. A provisioned test dependency
    # overlay may extend it; the interpreter itself stays the incoming program's.
    python_sha = observed['tools'].get('python', {}).get('sha256')
    python_path = Path(tools['python']).resolve() if tools.get('python') else None
    if (not python_sha or python_sha not in {row['sha256'] for row in value['runtime']['files'].values()}
            or python_path is None or not python_path.is_relative_to(Path(value['runtime']['root']).resolve())):
        errors['python'] = 'Verification Python is not a member of the exact incoming runtime composition'
    binding = {'version': VERSION, 'candidate_sha256': operation['candidate_sha256'],
               'composition_sha256': value['sha256'], 'bundle_sha256': manifest_sha,
               'input_sha256': operation['input_sha256'], 'target_version': operation['version'],
               'platform': sys.platform, 'executor': observed,
               'host_runner_sha256': bundle.digest(__file__)}
    if merge.candidate_signature(directory) != operation['candidate_sha256']:
        raise ValueError('Battery core candidate changed')
    return binding, value, root, manifest, tools, errors


def _sources(cache, receipt):
    evidence = cache / 'evidence'
    for name, digest in receipt['files'].items():
        bundle.relative(name)
        if bundle.digest(evidence / name) != digest:
            raise ValueError('Stored battery evidence changed')
    for name, digest in receipt.get('raw_files', {}).items():
        bundle.relative(name)
        if bundle.digest(cache / name) != digest:
            raise ValueError('Stored battery raw evidence changed')
    return {'battery': {'root': str(evidence), 'files': {n: n for n in receipt['files']}}}


def _test_copy_changes(repository, before, lanes):
    """Separate observed build output from mutation of the private test inputs.

    This NEVER changes/blesses installed M; _binding rechecks its exact bytes.
    Only trusted declared frontend build roots can generate their dist subtree.
    """
    prefixes = sorted({lane['root'] + '/dist/' for lane in lanes
        if lane.get('kind') == 'npm-build' and lane.get('root') in
        {'desk/app', 'desk/mobile', 'desk/miniapp'} and
        lane.get('id') == lane['root'] + '/build'})
    after = bundle.inventory(repository)
    changed = sorted(name for name in set(before) | set(after)
                     if before.get(name) != after.get(name))
    authored = [name for name in changed if not any(name.startswith(p) for p in prefixes)]
    outputs = [{'root': prefix.rstrip('/'),
                'before': {n: r for n, r in before.items() if n.startswith(prefix)},
                'after': {n: r for n, r in after.items() if n.startswith(prefix)},
                'changed_paths': [n for n in changed if n.startswith(prefix)]}
               for prefix in prefixes]
    return authored, outputs


def prepare(operation, directory, harness, stopped):
    """Full attempts, frozen for review. Caller must persist mutated operation."""
    directory = Path(directory)
    if stopped():
        raise InterruptedError('Owner stopped update battery')
    merge.validate_inputs(directory, operation['input_sha256'])
    binding, value, bundle_root, manifest, tools, errors = _binding(operation, directory)
    key = merge.signature(binding)
    cache = directory / 'test-battery' / key
    receipt = read(cache / 'receipt.json')
    if receipt.get('binding') == binding and receipt.get('complete') is True:
        sources = _sources(cache, receipt)
        report = read(cache / 'report.json')
        operation['battery'] = {'key': key, 'receipt_sha256': bundle.digest(cache / 'receipt.json'),
                                'full_pass': report.get('full_pass') is True, 'gate_error': report.get('gate_error', '')}
        return sources
    if (cache / 'attempt.json').exists() or (cache / 'custody-unconfirmed.json').exists():
        raise ValueError('Previous verification attempt has no complete receipt; reconcile its retained custody/results before retry: ' + str(cache))
    cache.mkdir(parents=True, exist_ok=True)
    evidence = cache / 'evidence'
    evidence.mkdir(exist_ok=True)
    rows = []
    work = Path(tempfile.mkdtemp(prefix='helene-update-tests-')).resolve()
    write(cache / 'attempt.json', {'state': 'started', 'binding': binding, 'work': str(work), 'at': time.time()})
    safe_to_remove = True
    try:
        repository = work / 'repository'
        bundle.copy_files(bundle_root / 'repository', manifest['files'], repository)
        base_app = read(directory / 'composition.json').get('base_app', {})
        core, desk = _copy_composition(value, repository, manifest, base_app)
        # Added/modified tests are part of the actual full collection, never lost
        # behind the release module list. Required release modules remain present.
        actual_files = bundle.inventory(repository)
        native, rebuild = _obligations(directory, value, manifest)
        lanes = bundle.recipe(actual_files, native, rebuild, manifest.get('script_contracts', {}))
        env = _environment(work / 'home', core, desk, operation)
        dependencies = (operation.get('verification_executor') or {}).get('python_dependencies')
        if dependencies:
            env['HELENE_VERIFICATION_DEPENDENCIES'] = str(Path(dependencies).resolve())
        write(cache / 'executor-lock.json', manifest.get('executor_locks', {}).get(sys.platform, {}))
        runner = bundle_root / 'runner/source_verification_runner.py'
        for lane in lanes:
            if stopped():
                raise InterruptedError('Owner stopped update battery')
            if lane['kind'] == 'helper':
                rows.append({'id': lane['id'], 'status': 'support-only', 'reason': lane['reason']})
                continue
            reason = lane.get('platform_skip', {}).get(sys.platform)
            if reason:
                rows.append({'id': lane['id'], 'status': 'platform-skip', 'reason': reason})
                continue
            tool = _lane_tool(lane)
            missing = errors.get(tool)
            if lane['kind'] in {'npm-prepare', 'npm-build'}:
                missing = missing or errors.get('node')
            if not lane['sources']:
                missing = 'Full lane sources unavailable: ' + lane['id']
            if missing:
                rows.append({'id': lane['id'], 'status': 'unavailable', 'reason': missing})
                continue
            try:
                rows.append(_run_lane(lane, repository, runner, tools, env, cache, stopped,
                                      binding['executor'].get('native_inputs')))
            except OSError as exc:
                rows.append({'id': lane['id'], 'status': 'unavailable', 'reason': type(exc).__name__ + ': ' + str(exc)})
        # This includes supplied test sources and direct-import edition paths,
        # not merely files which happened to be included in packaged M.
        candidate_changes, build_outputs = _test_copy_changes(repository, actual_files, lanes)
    except CustodyUnconfirmed:
        safe_to_remove = False
        write(cache / 'custody-unconfirmed.json', {'work': str(work), 'complete': False})
        raise
    finally:
        if safe_to_remove:
            shutil.rmtree(work)
    if stopped():
        raise InterruptedError('Owner stopped update battery')
    if _binding(operation, directory)[0] != binding:
        raise ValueError('Full composition or executor changed during battery')
    report = {'binding': binding, 'complete': True, 'scope': 'full version-bound applicable composition; no lane reuse',
              'lanes': lanes, 'results': rows, 'test_copy_modified_paths': candidate_changes,
              'test_copy_build_outputs': build_outputs,
              'interpretation': 'Complete means attempted, not pass. Independent review and installed acceptance are separate.'}
    try:
        if candidate_changes:
            raise ValueError('Tests changed composed source: ' + ', '.join(candidate_changes))
        _check_results(report, manifest)
        report['full_pass'] = True
    except ValueError as exc:
        report.update(full_pass=False, gate_error=str(exc))
    write(cache / 'report.json', report)
    # Raw logs/observations stay exact and private. Review sees hashes + redacted
    # excerpts, not a diagnostic instruction or credential-shaped text.
    from .self_review import SECRET
    for p in cache.glob('*.output'):
        raw = p.read_bytes()
        text = raw.decode('utf8', 'replace')
        text = '\n'.join('[credential-shaped diagnostic withheld]' if SECRET.search(line) else line for line in text.splitlines())
        (evidence / (p.stem + '.md')).write_text(text, encoding='utf8')
    (evidence / 'report.md').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    (evidence / 'composition.md').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    files = {p.name: bundle.digest(p) for p in evidence.iterdir() if p.is_file()}
    raw_files = {p.name: bundle.digest(p) for p in cache.iterdir() if p.is_file() and p.name != 'receipt.json'}
    receipt = {'binding': binding, 'complete': True, 'files': files, 'raw_files': raw_files}
    write(cache / 'receipt.json', receipt)
    operation['battery'] = {'key': key, 'receipt_sha256': bundle.digest(cache / 'receipt.json'),
                            'full_pass': report.get('full_pass') is True, 'gate_error': report.get('gate_error', '')}
    return _sources(cache, receipt)


def validate(operation, directory):
    """Apply gate: exact current whole composition, immutable full observations."""
    directory = Path(directory)
    if operation.get('cancel_requested') or operation.get('paused') or (directory / 'cancel.json').exists():
        raise ValueError('Stopped/paused update has no applicable full battery')
    binding, value, _, manifest, _, errors = _binding(operation, directory)
    native, rebuild = _obligations(directory, value, manifest)
    base_app = read(directory / 'composition.json').get('base_app', {})
    required_lanes = bundle.recipe(_verification_files(value, manifest, base_app), native, rebuild, manifest.get('script_contracts', {}))
    missing_tools = [errors[name] for name in _required_tools(required_lanes) if name in errors]
    if missing_tools:
        raise ValueError('Full native executor is unavailable: ' + '; '.join(missing_tools))
    key = merge.signature(binding)
    pointer = operation.get('battery') or {}
    cache = directory / 'test-battery' / key
    if pointer.get('key') != key or not (cache / 'receipt.json').is_file():
        raise ValueError('No full battery for the exact current composition')
    if bundle.digest(cache / 'receipt.json') != pointer.get('receipt_sha256'):
        raise ValueError('Full battery canonical receipt changed')
    receipt = read(cache / 'receipt.json')
    if receipt.get('binding') != binding or receipt.get('complete') is not True:
        raise ValueError('Full battery receipt is incomplete or stale')
    _sources(cache, receipt)
    report = read(cache / 'report.json')
    if report.get('binding') != binding or report.get('complete') is not True or report.get('test_copy_modified_paths'):
        raise ValueError('Full battery report is incomplete, stale or its test copy changed')
    # Derive required release lanes again. Candidate-added lanes may extend this
    # list, but cannot remove or replace a required source module.
    if report.get('lanes') != required_lanes:
        raise ValueError('Full battery omitted current release/repair lane sources')
    _check_results(report, manifest)
    return {'binding': binding, 'key': key, 'full_pass': True, 'receipt_sha256': pointer['receipt_sha256']}


def main():
    """Explicit native executor adapter. It never overwrites the job pipeline."""
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    operation = read(a.directory / 'pipeline.json')
    result = {'format': 'helene-verification-execution.v1', 'directory': str(a.directory.resolve())}
    try:
        from . import source_update
        stopped = lambda: ((a.directory / 'cancel.json').exists()
                           or bool(operation.get('tree') and source_update._owner_blocked(operation['tree'])))
        result['sources'] = prepare(operation, a.directory, {}, stopped)
        result['battery'] = operation.get('battery')
        result['validation'] = validate(operation, a.directory)
        result['status'] = 'passed'
    except (OSError, ValueError, RuntimeError, InterruptedError) as exc:
        result.update(status='blocked', battery=operation.get('battery'), error=type(exc).__name__ + ': ' + str(exc))
    write(a.output, result)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
