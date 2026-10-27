"""Run the bounded integration battery before a frozen update panel.

Test outcomes are evidence for reviewers, never a replacement for their verdict.
Only copies of candidate code and incoming application code enter the test home.
"""
from __future__ import annotations

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

from . import source_merge as merge
from .package_notice import read, write

# Each seam has a stable battery. Select affected seams, not every historical test.
SEAMS = (
    ("model route and provider response", ("llm", "brain", "agent"),
     ("test_llm", "test_brain_endpoint_2710", "test_responses_protocol_2710")),
    ("worker lifecycle and continuation", ("forge", "command_process"),
     ("test_forge_worker_durability_2610", "test_forge_admission_races_2610",
      "test_forge_coordination_2610", "test_forge_remote_admission_2710", "test_command_process_2810")),
    ("model tool contract and execution receipts", ("agent", "tool_text", "tools", "body_client", "forge"),
     ("test_tool_trace_2710", "test_tool_pointers", "test_body_coordinates_2710")),
)
VERSION = 1
TIMEOUT = 240
MAX_OUTPUT = 2 * 1024 * 1024


class CustodyUnconfirmed(RuntimeError):
    pass


def selected(plan):
    local, result = plan['files']['local'], plan['result']
    changed = {n for n in set(local) | set(result) if local.get(n) != result.get(n)}
    tests = {}
    for seam, prefixes, modules in SEAMS:
        if any(Path(n).stem.startswith(prefixes) for n in changed):
            for module in modules:
                tests.setdefault(module, []).append(seam)
    # Include directly changed test modules even when they cover an unlisted seam.
    for name in changed:
        if '/' not in name and name.startswith('test_') and name.endswith('.py'):
            tests.setdefault(name[:-3], []).append('changed test')
    if not tests:
        tests['test_run_integration'] = ['baseline model-to-tool loop']
    return dict(sorted(tests.items()))


def _copy(root, inventory, target):
    target.mkdir(parents=True, exist_ok=True)
    for name, row in inventory.items():
        merge.relative(name)
        source = Path(root) / name
        if source.is_symlink() or not source.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('Battery source escapes its bound root')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('Battery input changed: ' + name)
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if os.name != 'nt':
            dest.chmod(0o755 if row['mode'] == '100755' else 0o644)


def _environment(home, core, app):
    # Do not inherit the owner profile, API keys, proxy settings or PRAXIS_BASE.
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
                             'SYSTEMDRIVE', 'LANG', 'LC_ALL'}}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / 'appdata'),
               LOCALAPPDATA=str(home / 'local'), TEMP=str(home / 'tmp'), TMP=str(home / 'tmp'),
               TMPDIR=str(home / 'tmp'), PRAXIS_TEST='1', PYTHONNOUSERSITE='1',
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               PYTHONPATH=os.pathsep.join((str(core), str(app))), HF_HUB_OFFLINE='1')
    for name in ('appdata', 'local', 'tmp'):
        (home / name).mkdir(parents=True, exist_ok=True)
    return env


def _execute(command, cwd, env, output, stopped):
    # Imported from the verified incoming runtime, not the candidate under test.
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


def prepare(operation, directory, harness, stopped):
    """Mandatory attempted runs; failures remain readable supplemental evidence."""
    directory = Path(directory)
    if stopped():
        raise InterruptedError('Owner stopped update battery')
    plan = merge.validate_inputs(directory)
    candidate = merge.candidate_signature(directory)
    if candidate != operation['candidate_sha256']:
        raise ValueError('Battery candidate changed')
    tests = selected(plan)
    app = harness['harness']
    package_manifest = Path(app['root']).parent / 'python-package-files.json'
    binding = {'version': VERSION, 'candidate_sha256': candidate,
               'app_sha256': operation['coordinator']['app_sha256'],
               'tests': tests, 'platform': sys.platform, 'python': sys.version,
               'interpreter_sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
               'package_manifest_sha256': (hashlib.sha256(package_manifest.read_bytes()).hexdigest()
                                          if package_manifest.is_file() else None),
               'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    key = merge.signature(binding)
    cache = directory / 'test-battery' / key
    evidence = cache / 'evidence'
    receipt = read(cache / 'receipt.json')
    if receipt.get('binding') == binding and receipt.get('complete') is True:
        files = receipt.get('files') or {}
        if files and all((evidence / n).is_file() and hashlib.sha256((evidence / n).read_bytes()).hexdigest() == h
                         for n, h in files.items() if merge.relative(n)):
            return {'battery': {'root': str(evidence), 'files': {n: n for n in files}}}
        raise ValueError('Stored battery evidence changed')
    evidence.mkdir(parents=True, exist_ok=True)
    rows = []
    work = Path(tempfile.mkdtemp(prefix='helene-update-tests-')).resolve()
    safe_to_remove = True
    try:
        core = work / 'core'
        app_copy = work / 'app'
        _copy(directory / 'result', plan['result'], core)
        _copy(app['root'], app['merge_files'], app_copy)
        env = _environment(work / 'home', core, app_copy)
        runner = core / 'praxis_test.py'
        for module, seams in tests.items():
            if stopped():
                raise InterruptedError('Owner stopped update battery')
            output = cache / (module + '.output')
            row = {'test': module, 'seams': seams,
                   'test_sha256': plan['result'].get(module + '.py', {}).get('sha256')}
            if not runner.is_file() or not (core / (module + '.py')).is_file():
                row.update(status='unavailable', reason='Candidate omits test module or hermetic entrypoint')
            else:
                started = time.monotonic()
                # A separate process per module preserves isolation between suites.
                row.update(_execute([sys.executable, '-B', '-s', str(runner), '-q', module],
                                    core, env, output, stopped))
                row['seconds'] = round(time.monotonic() - started, 3)
                size = output.stat().st_size
                with output.open('rb') as stream:
                    output_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
                    stream.seek(max(0, size - 16000))
                    text = stream.read().decode('utf8', 'replace')
                from .self_review import SECRET
                # Diagnostic text is data. Withhold credential-shaped lines.
                text = '\n'.join('[credential-shaped diagnostic withheld]' if SECRET.search(line) else line
                                 for line in text.splitlines())
                excerpt = module + '.md'
                (evidence / excerpt).write_text(text, encoding='utf8')
                row.update(output_sha256=output_sha256, output_bytes=size,
                           excerpt=excerpt, excerpt_truncated=size > 16000)
                count = re.search(r'Ran (\d+) tests?', text)
                row['tests_run'] = int(count[1]) if count else None
                skipped = re.search(r'\bskipped=(\d+)', text)
                row['tests_skipped'] = int(skipped[1]) if skipped else 0
                if row['status'] == 'passed' and not row['tests_run']:
                    row['status'] = 'no-tests'
                elif row['status'] == 'passed' and row['tests_run'] == row['tests_skipped']:
                    row['status'] = 'skipped'
            rows.append(row)
        modified = [name for name, value in plan['result'].items()
                    if not (core / name).is_file() or
                    hashlib.sha256((core / name).read_bytes()).hexdigest() != value['sha256']]
    except CustodyUnconfirmed:
        safe_to_remove = False  # keep the work directory while a child may still own it
        raise
    finally:
        if safe_to_remove:
            assert work.parent == Path(tempfile.gettempdir()).resolve()
            assert work.name.startswith('helene-update-tests-')
            shutil.rmtree(work)
    if stopped():
        raise InterruptedError('Owner stopped update battery')
    if merge.candidate_signature(directory) != candidate:
        raise ValueError('Candidate changed during battery')
    report = {'binding': binding, 'complete': True, 'results': rows,
              'test_copy_modified_paths': modified,
              'scope': 'selected integration suites; no installed UI or full platform matrix',
              'interpretation': 'Supplemental evidence only. Failures, skips, unavailable tests and timeouts direct attention; no automatic verdict. Passing tests do not prove integration correctness.',
              'uncovered': [] if tests else ['No mapped integration suites for these changed paths']}
    (evidence / 'report.md').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in evidence.iterdir() if p.is_file()}
    write(cache / 'receipt.json', {'binding': binding, 'complete': True, 'files': files})
    return {'battery': {'root': str(evidence), 'files': {n: n for n in files}}}
