"""Safe Windows distribution consumers; Forge/core suites belong to Linux.

Run from desk after building the selected edition body/bridge and desk package:
python windows_safe_gate.py --edition PATH --package PATH --relay PATH --logs PATH
No installer, service, scheduler or live agent is invoked. Each child keeps its
complete output; a missing/skipped-only consumer never counts as a pass.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PYTHON_TESTS = (
    't_desktop_supervisor_1710', 't_local_config_writer_1710',
    't_paper_files_host', 't_paper_files_package',
    't_cu_python_read_1810', 't_cu_python_fs_1810',
    't_vnext_dist_1810', 't_vnext_skill_model_send',
    't_external_test_receipt',
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('edition', 'package', 'relay', 'logs'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('this consumer gate requires Windows')
    desk = Path(__file__).resolve().parent
    edition, package, relay, logs = (getattr(args, n).resolve()
                                     for n in ('edition', 'package', 'relay', 'logs'))
    logs.mkdir(parents=True, exist_ok=True)
    body = edition / 'body' / 'target' / 'debug'
    required = [body / 'praxis-body.exe', body / 'praxis-bridge.exe',
                package / 'localharness' / 'cu_read_cli.py',
                package / 'localharness' / 'cu_fs_cli.py']
    if any(not p.is_file() for p in required):
        parser.error('build selected body/bridge and complete desk package first')
    env = dict(os.environ)
    # Keep the compiler environment, never ambient product credentials.
    for key in list(env):
        if any(word in key.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY',
               'TELEGRAM', 'PRAXIS_', 'HELENE_', 'RELAY_', 'OPENAI_', 'ANTHROPIC_')):
            env.pop(key, None)
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               CARGO_BUILD_JOBS='3', NO_PROXY='127.0.0.1,localhost,::1',
               RELAY_INSTRUCTIONS_FILE=str(logs / 'absent-instructions'),
               HELENE_TREE_SRC=str(edition), HELENE_BODY_DIR=str(body),
               HELENE_CU_TEST_EDITION=str(edition),
               HELENE_CU_TEST_BODY_EXE=str(required[0]),
               HELENE_CU_TEST_BRIDGE_EXE=str(required[1]),
               HELENE_CU_TEST_STAGED_CLI=str(required[2]),
               HELENE_CU_TEST_STAGED_FS_CLI=str(required[3]),
               HELENE_RUST_PAYLOAD_CONSUMER='1')
    sys.path.insert(0, str(desk / 'installer'))
    from test_receipt import log_count
    jobs = [(name, [sys.executable, '-X', 'utf8', str(desk / 'tests' / (name + '.py'))], desk)
            for name in PYTHON_TESTS]
    cargo, node = shutil.which('cargo'), shutil.which('node')
    if not cargo or not node:
        parser.error('cargo and node are required')
    jobs.extend(('cargo-' + part, [cargo, 'test', '--offline', '--locked'], desk / part)
                for part in ('shell', 'svc', 'setup'))
    jobs.extend([
        ('body', [cargo, 'test', '--offline', '--locked', '--workspace'], edition / 'body'),
        ('relay', [cargo, 'test', '--offline', '--locked', '--bin', 'codex-proxy-server',
                   '--', '--test-threads=1'], relay),
        ('node-window', [node, '--test', '--test-reporter=tap', *map(str, sorted((desk / 'app' / 'test').glob('*.mjs')))], desk / 'app'),
    ])
    (logs / 'invocation.json').write_text(json.dumps({
        'command': ['python', 'windows_safe_gate.py', '--edition', str(edition),
                    '--package', str(package), '--relay', str(relay), '--logs', str(logs)],
        'selected_paths': dict(edition=str(edition), package=str(package), relay=str(relay), logs=str(logs)),
    }, indent=2), encoding='utf-8')
    rows, total = [], 0
    for name, command, cwd in jobs:
        started = time.time()
        path = logs / (name + '.log')
        with path.open('wb') as output:
            try:
                result = subprocess.run(command, cwd=cwd, env=env, stdout=output,
                                        stderr=subprocess.STDOUT, timeout=3600,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        text = path.read_text(encoding='utf-8', errors='replace')
        if name.startswith('cargo-') or name in ('body', 'relay'):
            count = log_count('body', text)
        elif name == 'node-window':
            import re
            match = re.search(r'(?m)^# pass (\d+)$', text)
            count = int(match.group(1)) if match else 0
        else:
            count = log_count('core-1', text)
        rows.append(dict(name=name, command=command, cwd=str(cwd), exit_code=code,
                         tests_executed=count, seconds=time.time() - started,
                         log=path.name))
        (logs / 'results.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        print(f'{name}: exit={code}, executed={count}', flush=True)
        if code or not count:
            print('WINDOWS_SAFE_FAILED=' + name, flush=True)
            return 1
        total += count
    print('WINDOWS_SAFE_PASSED=' + str(total), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
