"""Incoming application's isolated update coordinator, independent of authored Forge.

This process imports only the verified distribution's core. It admits the
existing conflict-only merger and frozen-source reviewers, never an agent loop.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from localharness import source_merge as merge, source_update, self_review
from localharness.agent_source_update import AgentResolutionPending
from localharness.package_notice import read, write


def verified_runtime(new):
    """Recheck executable source before importing it; never run merge material."""
    new = Path(new).resolve()
    from localharness.source_admission import activate
    activate(new)
    core = read(new / 'code-upstream.json')
    app = read(new / 'source-inputs.json')
    if not {'forge.py', 'forge_worker.py', 'llm.py'} <= set(core.get('files', {})):
        raise ValueError('Incoming distribution has no complete update runtime')
    for name, row in core['files'].items():
        merge.relative(name)
        path = new / 'tree' / name
        if path.is_symlink() or not path.resolve().is_relative_to(new / 'tree'):
            raise ValueError('Incoming runtime source escapes its distribution')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Incoming runtime source changed: ' + name)
    required = {'localharness/source_update_host.py', 'localharness/source_update.py',
                'localharness/source_update_worker.py', 'localharness/source_update_battery.py', 'localharness/self_review.py',
                'localharness/self_review_worker.py', 'localharness/source_update_transport.py',
                'localharness/agent_source_update.py', 'localharness/update_source.py',
                'localharness/desktop_supervisor.py', 'localharness/python_relay.py'}
    if not required <= set(app.get('files', {})):
        raise ValueError('Incoming distribution has no complete coordinator inventory')
    for name, digest in app['files'].items():
        merge.relative(name)
        relative = 'static/' + name[len('app/dist/'):] if name.startswith('app/dist/') else name
        path = new / 'app' / relative
        if path.is_symlink() or not path.resolve().is_relative_to(new / 'app'):
            raise ValueError('Incoming coordinator source escapes its distribution')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Incoming application source changed: ' + name)
    return {'kind': 'incoming-update-runtime.v1', 'program': str(new), 'review_store': 'isolated-v1',
            'test_battery': 1,
            'core_sha256': merge.signature(core['files']),
            'app_sha256': merge.signature(app['files'])}


def _settle(tree, forge, service, directory, reason):
    """Cancel only this update's named producers and require their receipts."""
    deadline = time.monotonic() + 45
    while True:
        operation = source_update.cancel(tree, forge, service, reason, directory=directory)
        if operation.get('stop_confirmed'):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Update review cancellation is not yet confirmed; existing work retained')
        time.sleep(.2)


def coordinate(old, new, directory, cancel_file):
    from localharness import app_migration, boot
    from python_migrate import installation_lock
    old, new, directory = map(lambda p: Path(p).resolve(), (old, new, directory))
    binding = verified_runtime(new)
    if directory.parent != old / '.source-updates':
        raise ValueError('Review plan is outside this installation')
    plan = merge.validate_inputs(directory)
    if merge.inventory(new / 'tree') != plan['files']['incoming']:
        raise ValueError('Incoming review runtime differs from the bound release')
    agent = next((a for a in app_migration.discover(old)['agents']
                  if Path(a['code']).resolve() == Path(plan['local_path']).resolve()
                  and a['value'].get('mode') != 'remote'), None)
    if agent is None:
        raise ValueError('Selected authored code changed before review')
    tree = Path(agent['tree']).resolve()
    if source_update._owner_blocked(tree):
        raise RuntimeError('Owner Stop/Pause forbids update review; previous application retained')
    binding['plan_id'] = directory.name
    # No dotenv or imports from the authored source. The same profile model
    # settings remain in memory/llm.json; only trusted distribution code runs.
    boot.apply_env(boot.env_knobs(agent['value']), where='isolated update review')
    os.environ['PRAXIS_BASE'] = str(tree)
    os.environ['PYTHONNOUSERSITE'] = '1'
    # Review may read an older settings schema, but must not migrate the live
    # owner's model configuration before the installation has been accepted.
    os.environ['PRAXIS_LLM_READ_ONLY_CONFIG'] = '1'
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONPATH'] = str(new / 'app')
    sys.path.insert(0, str(new / 'tree'))
    import forge
    import llm
    from localharness.source_update_transport import review_transport
    if (Path(forge.__file__).resolve() != new / 'tree/forge.py'
            or forge.SOURCE_UPDATE_API != 1 or forge.SELF_REVIEW_API != 1):
        raise ValueError('Coordinator did not load the verified incoming Forge')
    review_config = {'max_parallel': 4, 'max_iters': 60, **agent['value'].get('self_review', {})}
    service = self_review.Service(tree, agent['code'], old, forge,
                                  review_config, coordinator=binding)
    # A distinct plan lease does not contend with the parent's install lease.
    with installation_lock(directory, timeout_sec=1):
        operation = source_update.ensure(tree, old, agent['code'], directory,
                    read(new / 'python-app.json').get('version', ''), coordinator=binding)
        if operation['state'] == 'ready_to_apply':
            source_update.validate_ready(directory)
            return
        if operation.get('coordinator') != binding or operation['state'] not in source_update.ACTIVE:
            raise RuntimeError(operation.get('error') or 'Existing update review requires attention')
        try:
            stopped = lambda: Path(cancel_file).exists() or source_update._owner_blocked(tree)
            service.battery_cancelled = stopped
            with review_transport(agent, old, directory, llm._config(), stopped) as transport:
                while True:
                    if stopped():
                        raise RuntimeError('Owner cancelled, stopped or paused update review')
                    if transport is not None:
                        transport.check()
                    source_update.tick(tree, forge, service, coordinator=binding)
                    operation = read(directory / 'pipeline.json')
                    if operation.get('review_id'):
                        service.tick(review_ids={operation['review_id']})
                    if operation['state'] == 'ready_to_apply':
                        source_update.validate_ready(directory)
                        return
                    if operation['state']=='waiting_agent':
                        raise AgentResolutionPending('Кандидат ожидает разрешений или исправлений от выбранного агента')
                    if operation['state'] not in source_update.ACTIVE:
                        raise RuntimeError(operation.get('error') or operation.get('reason') or 'Update review failed')
                    time.sleep(.25)
        except AgentResolutionPending:
            raise  # no producer admitted; preserve the selected-agent request
        except BaseException as primary:
            try:
                _settle(tree, forge, service, directory, 'Incoming update coordinator stopped before acceptance')
            except Exception as cleanup:
                raise RuntimeError(f'{type(primary).__name__}: {primary}; update cancellation remains unconfirmed: {cleanup}') from primary
            raise


def review(old, new, plans, progress=None, cancelled=None):
    """Blocking preparation step, before overlay; output stays in a local log."""
    from localharness.update_process import interpreter_for
    old, new = Path(old).resolve(), Path(new).resolve()
    verified_runtime(new)
    for row in plans:
        if row['state'] == 'ready':
            continue
        directory = Path(row['directory']).resolve()
        token = __import__('uuid').uuid4().hex
        cancel = new.parent / ('review-cancel-' + token + '.json')
        log = new.parent / ('source-review-' + token + '.log')
        if progress:
            progress.phase('waiting_review', message='Проверяю изменения кода координатором из новой поставки')
        command = [str(interpreter_for(new)), '-s', str(new / 'app/localharness/source_update_host.py'),
                   '--old', str(old), '--new', str(new), '--directory', str(directory), '--cancel', str(cancel)]
        with log.open('ab') as output:
            process = subprocess.Popen(command, cwd=new, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            started = time.monotonic()
            cancel_deadline = None
            while process.poll() is None:
                if (cancelled is not None and cancelled.is_set()) or time.monotonic() - started > source_update.budget_seconds(read(directory / 'pipeline.json')) + 60:
                    if not cancel.exists():
                        write(cancel, {'reason': 'Update preparation cancelled or timed out'})
                    if cancel_deadline is None:
                        cancel_deadline = time.monotonic() + 90
                    if time.monotonic() > cancel_deadline:
                        raise RuntimeError('Update review process has not confirmed exit; no installation applied. Log: ' + str(log))
                time.sleep(.2)
        if process.returncode:
            operation = read(directory / 'pipeline.json')
            if process.returncode==75 and operation.get('state')=='waiting_agent':
                from .agent_source_update import AgentResolutionPending
                raise AgentResolutionPending('Агенту переданы замечания независимой проверки')
            raise RuntimeError((operation.get('error') or 'Изолированная проверка обновления не завершена')
                               + '. Log: ' + str(log))
        source_update.validate_ready(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('old', 'new', 'directory', 'cancel'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    from localharness.agent_source_update import AgentResolutionPending
    try:coordinate(args.old, args.new, args.directory, args.cancel)
    except AgentResolutionPending:return 75
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
