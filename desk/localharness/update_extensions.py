import json
import os
from pathlib import Path
import subprocess
import time
from .app_migration import discover


def run_check(command, staged, log):
    if os.name != 'nt':
        return subprocess.run(command, cwd=staged, capture_output=True, timeout=90)
    from .desktop_process import JobChild
    log.write_bytes(b'')
    child = JobChild.spawn(command, cwd=staged, env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                           log=log, stopped=lambda: False)
    try:
        deadline = time.monotonic() + 90
        while True:
            code = child.poll()
            if code is not None:
                return subprocess.CompletedProcess(command, code, log.read_bytes())
            if time.monotonic() >= deadline:
                raise TimeoutError('Extension compatibility check timed out')
            time.sleep(.05)
    finally:
        child.stop()


def check(previous, staged, *, force=False):
    previous, staged = Path(previous).resolve(), Path(staged).resolve()
    from .extensions import discover as extension_directories
    agents = [agent for agent in discover(previous)['agents'] if extension_directories(Path(agent['tree']))]
    if not agents:
        return []
    runtime = staged / ('runtime/python.exe' if os.name == 'nt' else 'runtime/bin/python3')
    runner = staged / 'app/localharness/runner.py'
    version = json.loads((staged / 'python-app.json').read_text(encoding='utf-8'))['version']
    reports = []
    for agent in agents:
        report_path = staged.parent / ('extensions-check-' + str(len(reports)) + '.json')
        report_path.unlink(missing_ok=True)
        result = run_check([str(runtime), '-X', 'utf8', '-s', str(runner), '--check-extensions',
            '--config', agent['config'], '--data', agent['tree'], '--host-version', version,
            '--extension-report', str(report_path)],
            staged, staged.parent / ('extensions-check-' + str(len(reports)) + '.log'))
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError) as error:
            raise RuntimeError('Extension compatibility report is unavailable') from error
        if not isinstance(report, dict) or type(report.get('ok')) is not bool or result.returncode not in (0, 2):
            raise RuntimeError('Extension compatibility check failed')
        reports.append({'config': agent['relative'], 'report': report})
    receipt = staged.parent / 'extension-compatibility.json'
    receipt.write_text(json.dumps({'forced': force, 'reports': reports}, ensure_ascii=False, indent=2), encoding='utf-8')
    if any(not row['report']['ok'] for row in reports) and not force:
        raise RuntimeError('Extensions are incompatible with this update; inspect ' + str(receipt))
    return reports
