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
from contextlib import ExitStack, nullcontext

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
                'localharness/source_update_worker.py', 'localharness/source_update_battery.py',
                'localharness/source_update_parallel.py', 'localharness/self_review.py',
                'localharness/source_update_hand.py',
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


def _handle_parent_stop(old, tree, forge, service, directory, binding, job):
    """A parent's teardown signal is not itself durable owner cancellation."""
    from localharness import owner_stop, session_exit
    control = binding.get('job_control')
    if control and job.get('id') != control['id']:
        raise ValueError('Durable update job changed during coordinator shutdown')
    with source_update._LOCK:
        operation = read(Path(directory)/'pipeline.json')
        cancelled = (not control or job.get('cancel_requested') is True
                     or owner_stop.stopped() or session_exit.requested(old)
                     or operation.get('cancel_requested') is True
                     or source_update._cancelled(directory))
        if not cancelled:
            # Keep the exact candidate, failures and producer receipts. The next
            # host must reconcile those records; no tests/reviews are blessed or
            # reset here, and an incomplete battery still refuses a blind retry.
            operation['coordinator_interruption'] = {
                'at': time.time(), 'reason': 'Parent process ended without owner cancellation'}
            source_update._persist(directory, operation)
            return
    _settle(tree, forge, service, directory, 'Owner stopped the update')


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
    review_config = {'max_parallel': 4, 'max_iters': 0, **agent['value'].get('self_review', {})}
    service = self_review.Service(tree, agent['code'], old, forge,
                                  review_config, coordinator=binding)
    # A distinct plan lease does not contend with the parent's install lease.
    with installation_lock(directory, timeout_sec=1):
        operation = source_update.ensure(tree, old, agent['code'], directory,
                    read(new / 'python-app.json').get('version', ''), coordinator=binding)
        from localharness import update_composition
        update_composition.prepare(directory, operation)
        if operation.get('coordinator') != binding or operation['state'] not in source_update.ACTIVE | {'ready_to_apply'}:
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
            from localharness.source_update_hand import serve
            hand=(serve(directory,tree,new,binding)
                  if operation.get('continuation')==source_update.AUTOMATIC else nullcontext())
            with hand, ExitStack() as transport_scope:
                transport = None
                transport_open = False
                shared_observation = {}
                while True:
                    if stopped():
                        raise RuntimeError('Owner cancelled or stopped update review')
                    if paused():
                        with source_update._LOCK:
                            operation=read(directory/'pipeline.json')
                            operation['paused'] = True
                            source_update._persist(directory, operation)
                        time.sleep(.25)
                        continue
                    operation = read(directory / 'pipeline.json')
                    needs_transport = operation['state'] not in ('waiting_agent', 'ready_to_apply')
                    if needs_transport and not transport_open:
                        transport = transport_scope.enter_context(review_transport(agent, old, directory, llm._config(), stopped))
                        transport_open = True
                    if transport is not None:
                        transport.check()
                    # A hand RPC can publish review_requested after the read
                    # above. Recheck under the same mutex as tick, never admit
                    # model work from a waiting phase without its relay turn.
                    with source_update._LOCK:
                        current = read(directory / 'pipeline.json')
                        deferred = current['state'] not in ('waiting_agent', 'ready_to_apply') and not transport_open
                        if not deferred:
                            source_update.tick(tree, forge, service, coordinator=binding)
                            operation = read(directory / 'pipeline.json')
                            if operation.get('review_id') and transport_open:
                                service.tick(review_ids={operation['review_id']})
                    if deferred:
                        time.sleep(.25)
                        continue
                    if operation['state'] in ('waiting_agent', 'ready_to_apply') and transport_open:
                        transport_scope.close()
                        transport = None
                        transport_open = False
                    if operation['state'] == 'ready_to_apply':
                        if _shared_batch_ready(directory, operation, shared_observation):
                            source_update.validate_ready(directory)
                            return
                        operation = read(directory / 'pipeline.json')
                    if operation['state']=='waiting_agent':
                        if operation.get('continuation')!=source_update.AUTOMATIC:
                            raise AgentResolutionPending('Кандидат ожидает разрешений или исправлений от выбранного агента')
                    if operation['state'] not in source_update.ACTIVE | {'ready_to_apply'}:
                        raise RuntimeError(operation.get('error') or operation.get('reason') or 'Update review failed')
                    time.sleep(.25)
        except AgentResolutionPending:
            raise  # no producer admitted; preserve the selected-agent request
        except BaseException as primary:
            if stopped():
                try:
                    _handle_parent_stop(old, tree, forge, service, directory, binding, job_state())
                except Exception as cleanup:
                    raise RuntimeError(f'{type(primary).__name__}: {primary}; update cancellation remains unconfirmed: {cleanup}') from primary
            else:
                # Infrastructure/verification failure retains the exact job,
                # revisions and any producer evidence. It is not owner cancel.
                operation = read(directory / 'pipeline.json')
                operation.update(state='needs_attention', error=f'{type(primary).__name__}: {primary}')
                source_update._persist(directory, operation)
            raise


def _shared_batch_ready(directory, operation, observation=None):
    """Keep a ready peer's hand alive until the shared acceptance barrier.

    Another agent can still repair app M while its own checks run. A finished
    peer must be reachable for explicit reconciliation, not strand old approval
    behind an exited hand. No candidate is copied and no receipt is invented.
    """
    from localharness import update_composition, agent_source_update
    new = Path(operation['coordinator']['program'])
    plans = read(new / 'code-transition-plan.json').get('plans', [])
    # Waiting is reconciliation, not approval. Compare actual editable app bytes
    # only; full frozen/runtime/receipt validation still runs at every gate.
    candidates = [Path(row['directory']) / 'compound/app' for row in plans]
    revisions = tuple((str(row['directory']), source_update._signature(Path(row['directory']) / 'composition.json'))
                      for row in plans)
    observation = observation if observation is not None else {}
    if observation.get('revisions') != revisions:
        inventories = [update_composition.app_inventory(candidate if candidate.is_dir() else new / 'app')
                       for candidate in candidates]
        observation.update(revisions=revisions, consistent=not any(value != inventories[0] for value in inventories[1:]))
    # Host source-edit writers publish composition.json on each repair. This is
    # disposable waiting evidence only; direct/out-of-band edits still fail the
    # uncached full validate_ready/require_shared gates before any execution/apply.
    if not observation['consistent']:
        if operation.get('resolution_owner') != 'agent':
            raise ValueError('Shared application needs explicit selected-agent reconciliation')
        with source_update._LOCK:
            latest = read(Path(directory) / 'pipeline.json')
            if latest.get('state') == 'ready_to_apply':
                agent_source_update._invalidate(directory, latest)
                current = read(source_update.path(latest['tree']))
                if current.get('id') == latest['id']:
                    current.update(state='pending', tries=0, retry_at=0)
                    write(source_update.path(latest['tree']), current)
        return False
    for row in plans:
        peer = read(Path(row['directory']) / 'pipeline.json')
        if peer:
            if peer.get('state') != 'ready_to_apply':
                return False
        elif row.get('state') != 'ready':
            return False
    return True


def _settle_review_processes(producers):
    """Signal every admitted peer, then observe exit; never kill or respawn it.

    A parent stop file closes coordinator custody. The child distinguishes a
    durable owner Stop from a failed/interrupted host and retains that evidence.
    """
    pending = [row for row in producers if not row['done']]
    errors = []
    for row in pending:
        try:
            if not row['cancel'].exists():
                write(row['cancel'], {'reason': 'Update preparation ended; settle coordinator'})
        except Exception as exc:
            errors.append(str(row['log']) + ': ' + str(exc))
    deadline = time.monotonic() + 90
    while pending:
        live = []
        for row in pending:
            try:
                if row['process'].poll() is None:
                    live.append(row)
                else:
                    row['done'] = True
            except Exception as exc:
                errors.append(str(row['log']) + ': ' + str(exc))
        pending = live
        if not pending:
            break
        if time.monotonic() >= deadline:
            errors.extend(str(row['log']) for row in pending)
            break
        time.sleep(.2)
    if errors:
        raise RuntimeError('; '.join(errors))


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
    producers = []
    pristine_observation = None
    def admit(row):
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError('Owner cancelled update preparation')
        directory = Path(row['directory']).resolve()
        token = __import__('uuid').uuid4().hex
        cancel = new.parent / ('review-cancel-' + token + '.json')
        log = new.parent / ('source-review-' + token + '.log')
        if progress:
            progress.phase('code', message='Подготавливаю совмещение кода')
        command = [str(interpreter_for(new)), '-s', str(new / 'app/localharness/source_update_host.py'),
                   '--old', str(old), '--new', str(new), '--directory', str(directory), '--cancel', str(cancel)]
        process, cancel, log = _producer(command, new, directory, cancel, log)
        producers.append({'process': process, 'directory': directory, 'cancel': cancel,
                          'log': log, 'stage': None, 'done': False})
    try:
        # Admit/rejoin every gated channel before its shared-app barrier.
        for row in plans:
            if row['state'] != 'ready' or read(Path(row['directory']) / 'pipeline.json'):
                admit(row)
        while any(not row['done'] for row in producers):
            if cancelled is not None and cancelled.is_set():
                raise RuntimeError('Owner cancelled update preparation')
            # A root that was pristine at preflight may become affected by a
            # later app repair. Promote it through the existing selected-agent
            # request path, not by copying another M or inventing approval.
            skipped = [row for row in plans if not any(p['directory'] == Path(row['directory']).resolve() for p in producers)]
            revisions = tuple(source_update._signature(Path(row['directory']) / 'composition.json') for row in plans)
            if skipped and pristine_observation != revisions:
                pristine_observation = revisions
                if update_composition.shared(new, plans)['changed']:
                    from localharness import code_transition
                    refreshed = code_transition.preflight(old, new)['plans']
                    if {row['directory'] for row in refreshed} != {row['directory'] for row in plans}:
                        raise ValueError('Selected source roots changed during shared reconciliation')
                    plans = refreshed
                    for peer in skipped:
                        current = next(row for row in plans if row['directory'] == peer['directory'])
                        if read(Path(current['directory']) / 'pipeline.json'):
                            admit(current)
            for row in producers:
                if row['done']:
                    continue
                code = row['process'].poll()
                operation = read(row['directory'] / 'pipeline.json')
                if code is not None:
                    row['done'] = True
                    if code == 75 and operation.get('state') in {'waiting_agent','verification_requested','review_requested'}:
                        raise AgentResolutionPending('Агенту переданы замечания независимой проверки')
                    elif code:
                        raise RuntimeError((operation.get('error') or 'Изолированная проверка обновления не завершена')
                                           + '. Log: ' + str(row['log']))
                    continue
                stage = {'queued': 'code', 'merging': 'waiting_agent',
                         'waiting_agent': 'waiting_agent', 'reviewing': 'waiting_review',
                         'verification_requested': 'verifying', 'review_requested': 'waiting_review',
                         'ready_to_apply': 'code_ready'}.get(operation.get('state'))
                if progress and stage and stage != row['stage']:
                    progress.phase(stage)
                    row['stage'] = stage
            if any(not row['done'] for row in producers):
                time.sleep(.2)
    except BaseException as primary:
        try:
            _settle_review_processes(producers)
        except Exception as cleanup:
            raise RuntimeError(f'{type(primary).__name__}: {primary}; coordinator exit unconfirmed: {cleanup}') from primary
        raise
    # A peer may change while another completes. A terminal exit is not a
    # whole-transition approval: revalidate all exact subjects after the join.
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError('Owner cancelled update preparation')
    update_composition.require_shared(new, plans)
    for row in producers:
        source_update.validate_ready(row['directory'])


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
