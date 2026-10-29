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
    from localharness.source_verification_bundle import load as load_bundle
    bundle_digest = read(new / 'python-package-files.json').get('files', {}).get('verification/manifest.json')
    if isinstance(bundle_digest, dict):
        bundle_digest = bundle_digest.get('sha256')
    if not bundle_digest:
        raise ValueError('Incoming package has no bound full verification bundle')
    load_bundle(new / 'verification', bundle_digest, read(new / 'python-app.json').get('version'))
    return {'kind': 'incoming-update-runtime.v1', 'program': str(new), 'review_store': 'isolated-v1',
            'test_battery': 1,
            'verification_bundle_sha256': bundle_digest,
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


class CoordinatorProcess:
    """Rejoin a named local producer after UI/host crash; never retry its model."""
    def __init__(self, directory, descriptor, process=None):
        self.directory=Path(directory)
        self.descriptor=descriptor
        self.process=process
        self.returncode=None
    def poll(self):
        from localharness.session_host import process_state
        if self.process is not None:
            self.returncode=self.process.poll()
            return self.returncode
        observed=process_state(self.descriptor)
        if observed=='alive':return None
        if observed=='unknown':raise RuntimeError('Incoming coordinator process observation is unknown; work retained')
        result=read(self.directory/'coordinator-result.json')
        if (result.get('invocation')!=self.descriptor.get('invocation')
                or result.get('pid')!=self.descriptor.get('pid') or result.get('birth')!=self.descriptor.get('birth')):
            raise RuntimeError('Incoming coordinator exited without a bound terminal receipt; inspect retained producer before resume')
        self.returncode=result['returncode']
        return self.returncode


def _producer(command, new, directory, cancel, log):
    from localharness.session_host import process_state
    prior=read(directory/'coordinator-process.json')
    if prior:
        state=process_state(prior)
        if state=='alive':
            return CoordinatorProcess(directory,prior),Path(prior['cancel']),Path(prior['log'])
        if state=='unknown':
            raise RuntimeError('Earlier coordinator admission is unknown; do not start a replacement')
    invocation=__import__('uuid').uuid4().hex
    intent={'invocation':invocation,'state':'starting','program':str(new),'directory':str(directory),
        'cancel':str(cancel),'log':str(log),'command_sha256':hashlib.sha256(json.dumps(command).encode()).hexdigest()}
    write(directory/'coordinator-process.json',intent)
    command=[*command,'--invocation',invocation,'--producer-log',str(log)]
    with log.open('ab') as output:
        process=subprocess.Popen(command,cwd=new,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),start_new_session=os.name!='nt')
    import psutil
    descriptor={**intent,'pid':process.pid,'birth':psutil.Process(process.pid).create_time(),'state':'running'}
    current=read(directory/'coordinator-process.json')
    if current.get('pid')!=process.pid:write(directory/'coordinator-process.json',descriptor)
    return CoordinatorProcess(directory,descriptor,process),cancel,log


def coordinate(old, new, directory, cancel_file):
    from localharness import app_migration, boot
    from python_migrate import installation_lock
    old, new, directory = map(lambda p: Path(p).resolve(), (old, new, directory))
    binding = verified_runtime(new)
    plan = merge.validate_inputs(directory)
    if merge.inventory(new / 'tree') != plan['files']['incoming']:
        raise ValueError('Incoming review runtime differs from the bound release')
    agent = next((a for a in app_migration.discover(old)['agents']
                  if Path(a['code']).resolve() == Path(plan['local_path']).resolve()
                  and a['value'].get('mode') != 'remote'), None)
    if agent is None:
        raise ValueError('Selected authored code changed before review')
    tree = Path(agent['tree']).resolve()
    if directory.parent not in (old / '.source-updates', tree / 'memory/.source-updates'):
        raise ValueError('Review plan is outside this installation/data identity')
    from localharness import owner_stop
    owner_stop.configure(tree)
    if owner_stop.stopped():
        raise RuntimeError('Owner Stop forbids update review; previous application retained')
    binding['plan_id'] = directory.name
    from localharness import desktop_update_request as jobs
    job_ref=read(jobs.incoming_reference(new))
    if job_ref:
        job=read(job_ref['path'])
        if (job_ref.get('program')!=str(old) or job_ref.get('incoming')!=str(new)
                or job.get('id')!=job_ref.get('id') or jobs.path(job['tree']).resolve()!=Path(job_ref['path']).resolve()):
            raise ValueError('Incoming work is bound to another durable update job')
        binding['job_control']={'id':job['id'],'path':job_ref['path'],'tree':job['tree']}
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
        from localharness import update_composition
        update_composition.prepare(directory, operation)
        if operation['state'] == 'ready_to_apply':
            source_update.validate_ready(directory)
            return
        if operation.get('coordinator') != binding or operation['state'] not in source_update.ACTIVE:
            raise RuntimeError(operation.get('error') or 'Existing update review requires attention')
        try:
            from localharness import owner_stop
            def job_state():
                control=binding.get('job_control')
                if not control:return {}
                value=read(control['path'])
                if value.get('id')!=control['id']:raise ValueError('Durable update job changed')
                return value
            stopped = lambda: Path(cancel_file).exists() or owner_stop.stopped() or job_state().get('cancel_requested') is True
            paused = lambda: owner_stop.paused() or job_state().get('state')=='paused'
            service.battery_cancelled = lambda:stopped() or paused()
            with review_transport(agent, old, directory, llm._config(), stopped) as transport:
                while True:
                    if stopped():
                        raise RuntimeError('Owner cancelled or stopped update review')
                    if paused():
                        operation['paused'] = True
                        source_update._persist(directory, operation)
                        time.sleep(.25)
                        continue
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
            if stopped():
                try:
                    _settle(tree, forge, service, directory, 'Owner stopped the update')
                except Exception as cleanup:
                    raise RuntimeError(f'{type(primary).__name__}: {primary}; update cancellation remains unconfirmed: {cleanup}') from primary
            else:
                # Infrastructure/verification failure retains the exact job,
                # revisions and any producer evidence. It is not owner cancel.
                operation = read(directory / 'pipeline.json')
                operation.update(state='needs_attention', error=f'{type(primary).__name__}: {primary}')
                source_update._persist(directory, operation)
            raise


def review(old, new, plans, progress=None, cancelled=None):
    """Blocking preparation step, before overlay; output stays in a local log."""
    from localharness.update_process import interpreter_for
    old, new = Path(old).resolve(), Path(new).resolve()
    binding = verified_runtime(new)
    # Every selected core sees its own editable app snapshot before the first
    # agent can finish. This grants no verification/review/apply authority.
    from localharness import update_composition
    for row in plans:
        directory = Path(row['directory']).resolve()
        plan = merge.validate_inputs(directory)
        update_composition.prepare(directory, {'coordinator': binding,
            'input_sha256': merge.input_signature(plan)})
    for row in plans:
        if row['state'] == 'ready':
            continue
        directory = Path(row['directory']).resolve()
        token = __import__('uuid').uuid4().hex
        cancel = new.parent / ('review-cancel-' + token + '.json')
        log = new.parent / ('source-review-' + token + '.log')
        if progress:
            progress.phase('code', message='Подготавливаю совмещение кода')
        command = [str(interpreter_for(new)), '-s', str(new / 'app/localharness/source_update_host.py'),
                   '--old', str(old), '--new', str(new), '--directory', str(directory), '--cancel', str(cancel)]
        process,cancel,log = _producer(command,new,directory,cancel,log)
        cancel_deadline = None
        last_stage = None
        while process.poll() is None:
            operation = read(directory / 'pipeline.json')
            stage = {'queued': 'code', 'merging': 'waiting_agent',
                     'waiting_agent': 'waiting_agent', 'reviewing': 'waiting_review',
                     'verification_requested':'verifying', 'review_requested':'waiting_review',
                     'ready_to_apply': 'code_ready'}.get(operation.get('state'))
            if progress and stage and stage != last_stage:
                progress.phase(stage)
                last_stage = stage
            if cancelled is not None and cancelled.is_set():
                if not cancel.exists():
                    write(cancel, {'reason': 'Owner cancelled update preparation'})
                if cancel_deadline is None:
                    cancel_deadline = time.monotonic() + 90
                if time.monotonic() > cancel_deadline:
                    raise RuntimeError('Update review process has not confirmed exit; no installation applied. Log: ' + str(log))
            time.sleep(.2)
        if process.returncode:
            operation = read(directory / 'pipeline.json')
            if process.returncode==75 and operation.get('state') in {'waiting_agent','verification_requested','review_requested'}:
                from .agent_source_update import AgentResolutionPending
                raise AgentResolutionPending('Агенту переданы замечания независимой проверки')
            raise RuntimeError((operation.get('error') or 'Изолированная проверка обновления не завершена')
                               + '. Log: ' + str(log))
        source_update.validate_ready(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('old', 'new', 'directory', 'cancel'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--invocation',default='')
    parser.add_argument('--producer-log',default='')
    args = parser.parse_args()
    from localharness.agent_source_update import AgentResolutionPending
    import psutil
    identity={'invocation':args.invocation,'pid':os.getpid(),'birth':psutil.Process().create_time()}
    if args.invocation:
        write(args.directory/'coordinator-process.json',{**identity,'state':'running','program':str(args.new),
            'directory':str(args.directory),'cancel':str(args.cancel),'log':args.producer_log})
    code=1
    try:
        coordinate(args.old, args.new, args.directory, args.cancel)
        code=0
    except AgentResolutionPending:
        code=75
    finally:
        if args.invocation:write(args.directory/'coordinator-result.json',{**identity,'returncode':code,'at':time.time()})
    return code


if __name__ == '__main__':
    raise SystemExit(main())
