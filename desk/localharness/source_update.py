"""Owner-requested update conflicts: isolated merger -> frozen panel -> correction.

The main agent is not sent an ordinary conflict turn. This coordinator shares
Forge's process/Stop authority and the existing self_review service. Syntax-ready
source is NOT review-ready, and neither worker prose nor an author disposition can
substitute for a completed clear panel on this exact candidate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from .package_notice import read, write
from . import source_merge as merge, self_review

_LOCK = threading.RLock()
_OBSERVERS = {}  # disposable per-tree discovery signatures/current producers, never authority
ACTIVE = {'queued', 'merging', 'reviewing', 'waiting_agent', 'verification_requested', 'review_requested'}
MAX_ATTEMPTS = 0  # no automatic correction-count ceiling for new work
MAX_ITERS = 0
AUTOMATIC = 'automatic-v1'
CONTINUATION_PROTOCOL = 2
UNAVAILABLE = ('Running application/selected Forge lacks an available source-update coordinator API v1. '
               'Old application retained; no ordinary agent conflict turn or generic worker fallback.')
TOOL = {'name': 'source_update', 'description':
    'Resolve the owner-requested update in its isolated candidate. status lists inputs; '
    'diff shows A->local and A->incoming changes; read returns one side with pagination. '
    'resolve supplies full UTF8 or a side; patch replaces one exact fragment using before/after. '
    'Repairs outside conflicts need reason and expected_candidate_sha256 from status. '
    'finish declares the semantic candidate complete. For new automatic jobs the durable updater then runs '
    'the full version-bound battery, independent review and the already-authorized mechanical switch after clear. '
    'Read status and repair real findings, then finish again. Do not poll/restart the same work or ask for new owner permission. '
    'Older jobs retain explicit verify/review/apply actions: verify runs the full battery; review requires exact PASS; apply requires clear. '
    'Acceptance is delegated to you: inspect the panel evidence, repair findings, finish again, '
    'and verify installed behavior after the authorized update. Do not ask the owner to run review '
    'or confirm it again. status supplies candidate_state, review_evidence and next_action. '
    'Run the relevant user journey yourself, including headless UI checks when available. '
    'Check offered tool/input, actual harness execution and returned model observation separately; '
    'a request is not proof of execution, and source tests are not installed acceptance. '
    'Use this update panel, not ordinary self_review on the old live Forge. '
    'A clear panel binds exact source bytes; never manufacture approval or bypass Stop.',
    'input_schema': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['status','read','diff','resolve','patch','finish','verify','review','apply']},
        'path':{'type':'string'},'content':{'type':'string'},'delete':{'type':'boolean'},
        'side':{'type':'string','enum':['base','local','incoming','result']},'offset':{'type':'integer','minimum':0},
        'before':{'type':'string'},'after':{'type':'string'},'reason':{'type':'string'},
        'expected_candidate_sha256':{'type':'string'},
        'expected_composition_sha256':{'type':'string'}}, 'required': ['action']}}


def requires_gate(plan, directory=None):
    """Modified-preserve is semantic reconciliation, even without Git markers."""
    compound = read(Path(directory) / 'composition.json') if directory is not None else {}
    return bool(plan.get('conflicts') or plan.get('repairs') or plan.get('shared_app_required') or plan.get('app_source') or compound.get('repairs')
                or plan.get('files', {}).get('local') != plan.get('files', {}).get('base'))


def path(tree):
    return Path(tree) / 'memory/.control/source-update.json'


def _requests(tree):
    return path(tree).with_name('source-update-requests')


def _cancel_marker(directory, reason):
    # A separate monotonic intent survives stale summary writers in another
    # process: GUI preflight and runner maintenance do not share _LOCK.
    write(Path(directory) / 'cancel.json', {'reason': str(reason), 'at': time.time()})


def _cancelled(directory):
    return (Path(directory) / 'cancel.json').exists()


def _persist(directory, operation):
    from .update_supersession import blocked
    if blocked(operation,protocol='source'):
        operation['state']='superseded'
        return  # Never recreate a retired programme/work directory.
    if _cancelled(directory):
        operation.update(cancel_requested=True, state='cancelled')
    target = Path(directory) / 'pipeline.json'
    if read(target) != operation:
        write(target, operation)


def _owner_blocked(tree):
    from . import owner_stop
    if owner_stop.stopped() or owner_stop.present(Path(tree) / 'memory/.control/autonomy-paused.json'):return True
    try:generation=owner_stop.pause_generation(tree)
    except RuntimeError:return True
    return generation is not None and generation['paused']


def _require_active(operation,directory):
    if _cancelled(directory) or _owner_blocked(operation['tree']) or job_blocked(operation):
        raise InterruptedError('Owner stopped or paused source-update admission')


def job_blocked(operation):
    from .update_supersession import blocked
    if blocked(operation,protocol='source'):return True
    control=(operation.get('coordinator') or {}).get('job_control')
    if not control:return False
    job=read(control['path'])
    if job.get('id')!=control['id']:raise ValueError('Durable update work identity changed')
    from .desktop_update_request import cancelled
    return (job.get('cancel_requested') is True or cancelled(control['tree'],control['id'])
            or job.get('state') in {'paused','completed','cancelled','superseded'})


def _save_request(tree, request):
    from .update_supersession import blocked
    if blocked(request,protocol='source',current_tree=tree):return
    if _cancelled(request['directory']):
        request = {**request, 'state': 'cancelled'}
    target = _requests(tree) / (request['id'] + '.json')
    if read(target) != request:
        write(target, request)
    current = read(path(tree))
    if current.get('id') in (None, request['id']) and current != request:
        write(path(tree), request)


def pending(tree):
    request = read(path(tree))
    from .update_supersession import blocked
    if blocked(request,protocol='source',current_tree=tree):return None
    return request if request.get('state') in ACTIVE or (request.get('resolution_owner')=='agent' and request.get('state')=='pending') else None


def bound(tree):
    request = read(path(tree))
    return request, *_bound(request,tree=tree)


def locate(request):
    """Scope metadata reads without rehashing frozen source on every UI tick.

    This grants no mutation/apply authority; _bound and the exact gates do that.
    """
    import re
    if not re.fullmatch('[0-9a-f]{64}',str(request.get('id') or '')):
        raise ValueError('Invalid source update identity')
    program = Path(request['program']).resolve()
    directory = Path(request['directory']).resolve()
    expected = program / '.source-updates' / request['id']
    data_directory = (Path(request['tree']).resolve() / 'memory/.source-updates' / request['id']
                      if request.get('tree') else expected)
    if directory not in (expected, data_directory) or len(request['id']) != 64:
        raise ValueError('Source update directory does not belong to this request')
    return directory


def _bound(request,*,tree=None):
    from .update_supersession import blocked
    if blocked(request,protocol='source',current_tree=tree):raise ValueError('Programme replaced; this update is superseded')
    directory = locate(request)
    plan = merge.validate_inputs(directory, request['input_sha256'])
    if Path(plan['local_path']).resolve() != Path(request['source']).resolve():
        raise ValueError('Source update request identity differs')
    return directory, plan


def ensure(tree, program, source, directory, version, *, coordinator=None):
    """Queue once before shutdown; retries never revive a stopped or failed plan."""
    directory = Path(directory).resolve()
    from .update_supersession import blocked
    if blocked({'id':directory.name,'directory':str(directory),'program':str(Path(program).resolve()),
                'tree':str(Path(tree).resolve())},protocol='source'):
        raise ValueError('Programme replaced; this update cannot be resumed')
    plan = merge.validate_inputs(directory)
    from .agent_source_update import marker
    agent_proof=read(marker(directory))
    agent_owned=bool(agent_proof)
    if agent_owned and (agent_proof.get('format')!='helene-agent-resolution.v1'
            or agent_proof.get('input_sha256')!=merge.input_signature(plan)
            or agent_proof.get('source')!=str(Path(source).resolve())):
        raise ValueError('Selected-agent source authority changed')
    request = {'id': directory.name, 'directory': str(directory), 'version': str(version),
               'source': str(Path(source).resolve()), 'program': str(Path(program).resolve()),
               'tree': str(Path(tree).resolve()),
               'input_sha256': merge.input_signature(plan), 'state': 'queued'}
    if agent_owned:request['resolution_owner']='agent'
    _bound(request,tree=tree)
    with _LOCK:
        current = read(path(tree))
        if blocked(request,protocol='source',current_tree=tree):raise ValueError('Programme replaced during update admission')
        if current and current.get('id') != request['id']:
            previous = Path(current['directory']) / 'pipeline.json'
            _cancel_marker(previous.parent, 'Superseded by a new owner-requested update')
            operation = read(previous)
            if operation:
                operation.update(cancel_requested=True, state='cancelled',
                                 reason='Superseded by a new owner-requested update')
                _persist(previous.parent, operation)
            _save_request(tree, {**current, 'state': 'cancelled'})
        operation_path = directory / 'pipeline.json'
        operation = read(operation_path)
        if not operation:
            operation = {**request, 'schema': 1, 'created': time.time(), 'attempts': [],
                         'max_attempts': MAX_ATTEMPTS, 'tree': str(Path(tree).resolve())}
            if agent_owned and agent_proof.get('continuation')==AUTOMATIC:
                operation['continuation']=AUTOMATIC
            runtime = read(Path(tree) / 'memory/.self-review/runtime.json')
            if agent_owned:
                operation.update(state='waiting_agent',coordinator={'kind':'waiting-selected-agent'})
            elif coordinator is None and (runtime.get('source_update_supported') is not True
                    or runtime.get('app_root') != str(Path(program).resolve())
                    or not 0 <= time.time() - float(runtime.get('at') or 0) < 60):
                operation.update(state='needs_attention', error=UNAVAILABLE)
            _persist(directory, operation)
        if operation['input_sha256'] != request['input_sha256']:
            raise ValueError('Update pipeline input binding changed')
        if coordinator is not None:
            # Only the verified incoming host calls this under its process lease.
            # An unavailable capability never admitted work; it is safe to retry
            # that one case. Execution failures, cancellation and Stop are final.
            unused = (operation.get('state') == 'needs_attention'
                      and operation.get('error') == UNAVAILABLE
                      and operation.get('attempts') == []
                      and not any(operation.get(k) for k in ('task_id', 'unit_id', 'review_id',
                                  'admission', 'cancel_requested', 'stop_receipt'))
                      and not _cancelled(directory))
            if unused:
                operation['capability_retry'] = {'created': operation['created'],
                                                'error': operation['error'], 'at': time.time()}
                operation.update(state='queued', created=time.time(), version=str(version))
                operation.pop('error', None)
            prior = operation.get('coordinator')
            transfer_agent=(agent_owned and (prior or {}).get('kind')=='waiting-selected-agent'
                            and operation['state'] in ('waiting_agent','verification_requested','review_requested')
                            and not any(operation.get(key) for key in ('task_id','review_id','admission','cancel_requested')))
            if prior and prior != coordinator and operation['state'] != 'ready_to_apply' and not transfer_agent:
                raise ValueError('Another incoming coordinator owns this source review; inspect its existing work')
            if (operation['state'] == 'queued' and not operation.get('attempts')) or transfer_agent:
                operation['coordinator'] = coordinator
        _persist(directory, operation)
        request['state'] = operation['state']
        write(path(tree), request)
        _save_request(tree, request)
        return operation


def applied_status(tree):
    """Read the installed receipt without resurrecting retired A/L/B payloads."""
    request=read(path(tree))
    if not request.get('installed_receipt'):return None
    directory=locate(request);operation=read(directory/'pipeline.json');receipt=operation.get('installed_receipt')
    if (receipt!=request['installed_receipt'] or not isinstance(receipt,dict)
            or operation.get('id')!=request.get('id') or receipt.get('id')!=request.get('startup_receipt')
            or receipt.get('program')!=request.get('installed_program') or receipt.get('tree')!=str(Path(tree).resolve())):
        raise ValueError('Installed source receipt identity differs')
    plan=read(directory/'plan.json')
    if (merge.input_signature(plan)!=receipt['input_sha256'] or
            merge.signature(plan['result'])!=receipt['candidate_sha256']):
        raise ValueError('Installed source receipt differs from its accepted candidate')
    return {'state':'applied','id':request['id'],'version':request.get('version'),
            'installed_program':receipt['program'],'startup_receipt':receipt['id'],
            'candidate_sha256':receipt['candidate_sha256'],'continuation_state':operation.get('state'),
            'next_action':'This update was installed and its startup confirmed. The old candidate needs no edits or reminders; continue in the installed programme. Owner Stop/Pause remains binding.'}


def command(tree, action='status', **args):
    terminal=applied_status(tree)
    if terminal:
        if action!='status':raise ValueError('This source update is already installed')
        return terminal
    if read(path(tree)).get('resolution_owner')=='agent':
        from .agent_source_update import command as selected_agent
        return selected_agent(tree,action,**args)
    if action != 'status':
        raise ValueError('Source update edits are restricted to the isolated merger/panel pipeline')
    request = read(path(tree))
    if not request:
        return {'state': 'none'}
    directory, plan = _bound(request)
    operation = read(directory / 'pipeline.json')
    return {**operation, 'version': request['version'], 'conflicts': plan['conflicts'],
            'resolved': plan['resolved']}


def install(agent, tree):
    def hand(action='status', **args):
        try:
            return json.dumps(command(tree, action, **args), ensure_ascii=False)
        except (OSError, ValueError, RuntimeError, SyntaxError, KeyError) as exc:
            return json.dumps({'state': 'needs_attention', 'error': str(exc)}, ensure_ascii=False)
    agent.TOOL_IMPL[TOOL['name']] = hand
    agent.BASE_TOOLS[:] = [tool for tool in agent.BASE_TOOLS if tool.get('name') != TOOL['name']]
    agent.BASE_TOOLS.append(TOOL)


def notify_due(tree, turn):
    from .agent_source_update import notify
    return notify(tree,turn)


def _fresh(operation, directory):
    if _cancelled(directory):
        raise ValueError('Source update was cancelled or superseded')
    plan = merge.validate_inputs(directory, operation['input_sha256'])
    if merge.inventory(operation['source']) != plan['files']['local']:
        raise ValueError('Local code changed during update preparation; retry Update')
    return plan


def _materials(directory, plan):
    files = {**plan['files'], 'result': plan['result']}
    names = set().union(*(set(side) for side in files.values()))
    published = {name: sorted({entry['sha256'] for side in ('base', 'incoming')
                              if (entry := files[side].get(name))}) for name in names}
    return {label: {'root': str(Path(directory) / side),
                    'merge_files': {name: files[side].get(name) for name in sorted(names)},
                    'published_files': published}
            for side, label in (('base', 'base'), ('local', 'mine'),
                                ('incoming', 'release'), ('result', 'resolved'))}


def _harness_material(operation):
    """Full composed app, not a Python-only context masquerading as composition."""
    if (operation.get('coordinator') or {}).get('kind') == 'incoming-update-runtime.v1':
        from . import update_composition
        directory = Path(operation['directory'])
        update_composition.prepare(directory, operation)
        return update_composition.harness(directory, operation)
    return {}


def review_evidence(record):
    """Expose the actual verdict and gaps without copying source or worker traces."""
    return {key: record.get(key) for key in ('id','subject_sha256','execution','verdict','error','gaps')} | {
        'critics': [{key: critic.get(key) for key in
                    ('unit_id','execution','verdict','summary','findings','gaps','reason','result_path')}
                    for critic in record.get('critics', [])]}


def budget_seconds(operation):
    """No master age timer for admitted semantic work; subprocess bounds remain."""
    return None


def _clear(service, record, expected_subject):
    """Re-read canonical frozen evidence, not a stale cached green summary."""
    if (record.get('subject_kind', 'source') != 'source' or record.get('typed_subject')
            or record.get('trigger') != 'contested_overlay'
            or record.get('subject_sha256') != expected_subject or record.get('execution') != 'done'
            or record.get('verdict') != 'clear' or record.get('cancel_requested')
            or record.get('gaps') or not record.get('critics')):
        reasons = [record.get('error') or ''] + [str(gap) for gap in record.get('gaps', [])]
        for critic in record.get('critics', []):
            reasons.extend(str(x) for x in critic.get('gaps', []))
            reasons.extend([critic.get('reason') or '', critic.get('summary') or ''])
        detail = '; '.join(dict.fromkeys(reason for reason in reasons if reason))[:4000]
        raise ValueError('Mandatory contested review is not completed and clear: '
                         + str(record.get('verdict') or 'insufficient')
                         + ('; ' + detail if detail else ''))
    subject = self_review.validate_snapshot(record['snapshot'], expected_subject)
    if subject.get('gaps') or service.freshness(record['id'])['stale']:
        raise ValueError('Reviewed source material changed or has gaps')
    from .self_review_merge import validate_coverage
    validate_coverage(subject, record['critics'])
    for critic in record['critics']:
        if critic.get('execution') != 'done' or critic.get('verdict') != 'clear':
            raise ValueError('Critic is incomplete or not clear')
        result_path = Path(critic['result_path'])
        if critic.get('coverage'):
            executed = read(result_path.parent / 'request.json') or {}
            if executed.get('brief') != service._brief(record, critic):
                raise ValueError('Executed critic assignment differs from frozen panel coverage')
        raw = result_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != critic.get('result_sha256'):
            raise ValueError('Canonical critic result changed')
        result = json.loads(raw)
        verdict = self_review._parse_verdict(result.get('result'), expected_subject)
        if (result.get('status') != 'done' or result.get('complete') is not True
                or result.get('snapshot_verified') is not True or result.get('tool_errors')
                or result.get('subject_sha256') != expected_subject or verdict['verdict'] != 'clear'
                or (result_path.parent / 'stop.json').exists()):
            raise ValueError('Canonical critic evidence is incomplete, stopped or not clear')
    return subject


def validate_ready(directory):
    directory = Path(directory)
    from .app_source_update import require_ready
    require_ready(directory)
    operation = read(directory / 'pipeline.json')
    if not operation:
        raise ValueError('Source update has no canonical independent review')
    plan = _fresh(operation, directory)
    from . import update_composition
    shared = update_composition.for_plan(directory, operation)
    if not shared['consistent']:
        raise ValueError('Final shared application differs across selected code roots; reconcile peer app candidates before apply')
    if _owner_blocked(operation['tree']) or job_blocked(operation):
        raise ValueError('Owner Stop/Pause forbids source-update apply; old application retained')
    if (operation.get('state') != 'ready_to_apply' or operation.get('cancel_requested')
            or operation.get('paused') or plan.get('state') != 'ready'
            or merge.inventory(directory / 'result') != plan.get('result')
            or merge.candidate_signature(directory) != operation.get('candidate_sha256')):
        raise ValueError('Source update has no current reviewed candidate ready to apply')
    service = self_review.Service(operation['tree'], operation['source'], operation['program'], None,
                                  coordinator=operation.get('coordinator'))
    record = service.status(operation['review_id'])
    subject = _clear(service, record, operation['subject_sha256'])
    from . import source_update_battery, update_composition
    source_update_battery.validate(operation, directory)
    composition = update_composition.signature(directory, operation)
    if composition != operation.get('composition_sha256'):
        raise ValueError('Installable composition changed after tests/review')
    expected = {'plan_id': operation['id'], 'input_sha256': operation['input_sha256'],
                'candidate_sha256': operation['candidate_sha256'], 'composition_sha256': composition}
    if subject.get('merge_identity') != expected:
        raise ValueError('Review is bound to another source-update candidate')
    return operation


def _stop(operation, forge, service):
    """Intent is already durable. Stop only existing work; never admit here."""
    if operation.get('stop_confirmed'):
        return
    confirmed = True
    if operation.get('review_id'):
        record = service.cancel(operation['review_id'])
        confirmed = record.get('execution') == 'cancelled'
    if operation.get('task_id'):
        directory = forge._unit_dir(operation['task_id'], 'agents', operation['unit_id'])
        try:
            directory.stat()
        except FileNotFoundError:
            # No unit admitted => nothing to stop; a missing previously admitted
            # unit is uncertainty, not an implicit successful cancellation.
            confirmed = confirmed and not operation.get('admission')
        else:
            receipt = read(directory / 'stop.json')
            if receipt.get('state') != 'settled':
                operation['stop_receipt'] = forge.agent(operation['task_id'], 'stop', agent_id=operation['unit_id'])
                receipt = read(directory / 'stop.json')
            confirmed = confirmed and receipt.get('state') == 'settled'
    operation['stop_confirmed'] = confirmed


def cancel(tree, forge, service, reason='Owner stopped the source update', *, directory=None):
    with _LOCK:
        request = (read(Path(directory) / 'pipeline.json') if directory is not None
                   else read(path(tree)))
        if not request:
            return {'state': 'none'}
        directory, _ = _bound(request)
        operation = read(directory / 'pipeline.json')
        _cancel_marker(directory, reason)
        operation.update(cancel_requested=True, state='cancelled', reason=reason)
        _persist(directory, operation)
        _stop(operation, forge, service)
        _persist(directory, operation)
        _save_request(tree, {**request, 'state': operation['state']})
        return operation


def _start_attempt(operation, directory, forge):
    _require_active(operation,directory)
    if operation['max_attempts'] > 0 and len(operation['attempts']) >= operation['max_attempts']:
        raise ValueError('Source merger correction budget exhausted; old application retained')
    merge.reopen(directory)
    attempt = {'task_id': 'code-' + uuid.uuid4().hex[:8], 'unit_id': 'agent-' + uuid.uuid4().hex[:8],
               'started': time.time(), 'before_sha256': merge.candidate_signature(directory)}
    if read(directory/'plan.json').get('app_source'):
        from . import update_composition
        update_composition.prepare(directory,operation)
        attempt['before_composition_sha256']=update_composition.signature(directory,operation)
    operation['attempts'].append(attempt)
    operation.update(**attempt, state='merging')
    operation.pop('admission', None)
    _persist(directory, operation)


def _merge_tick(operation, directory, forge):
    from . import source_update_worker
    _require_active(operation,directory)
    forge.source_update_task(operation['task_id'], str(directory), operation['id'],
                             operation['input_sha256'], source_update_worker.__file__, 1)
    drain = getattr(forge, 'tick_queued', None)
    if callable(drain):
        drain(operation['task_id'])
    unit = forge._unit_dir(operation['task_id'], 'agents', operation['unit_id'])
    if not unit.exists():
        # Persist the reserved identity before admission. A crash after spawn is
        # reconciled by its existing unit, never a replacement worker.
        brief = ('Merge the source conflicts, preserving both intents; inspect and repair the full '
                 'declared candidate for coupling, including automatically merged files, then call finish. '
                 'Every nonconflict repair requires its reason and current candidate SHA256. input_sha256=' + operation['input_sha256'] +
                 '. Independent reviewer findings: ' + json.dumps(operation.get('findings', []), ensure_ascii=False))
        _require_active(operation,directory)
        operation['admission'] = forge.agent(operation['task_id'], 'spawn', agent_id=operation['unit_id'],
                                            role='worker', brief=brief, max_iters=MAX_ITERS)
        if not unit.exists():
            if str(operation['admission']).startswith('Active worker slots exhausted'):
                return False
            raise ValueError('Restricted source merger admission failed: ' + str(operation['admission']))
    state = forge._unit_state(unit).get('status', 'unknown')
    result_path = unit / 'result.json'
    if not result_path.exists():
        if state not in {'queued', 'starting', 'running', 'finishing', 'unknown'}:
            raise ValueError('Source merger terminated without a complete result: ' + state)
        return False
    raw = result_path.read_bytes()
    result = json.loads(raw)
    plan = _fresh(operation, directory)
    candidate = merge.candidate_signature(directory)
    if result.get('status') == 'error' and result.get('error'):
        # Failure is never candidate acceptance. Preserve the worker's actual
        # cause instead of presenting a network/auth error as stale source.
        raise ValueError('Source merger failed: ' + str(result['error'])[:1200])
    # Recoverable read/finish errors remain in the trace. Successful finish and
    # exact current candidate proof, not a zero-error conversation, gate review.
    if (state != 'done' or result.get('status') != 'done' or result.get('complete') is not True
            or result.get('plan_id') != operation['id']
            or result.get('input_sha256') != operation['input_sha256']
            or result.get('candidate_verified') is not True or result.get('candidate_sha256') != candidate
            or plan.get('state') != 'ready' or plan.get('result') != merge.inventory(directory / 'result')
            or (unit / 'stop.json').exists()):
        raise ValueError('Source merger returned incomplete, stale or stopped candidate evidence')
    app_composition=None
    if plan.get('app_source'):
        from . import app_source_update,update_composition
        app_source_update.require_ready(directory)
        app_composition=update_composition.signature(directory,operation)
        if result.get('composition_verified') is not True or result.get('composition_sha256')!=app_composition:
            raise ValueError('Source merger returned incomplete or stale application composition evidence')
    unchanged=(app_composition==operation.get('before_composition_sha256') if app_composition is not None
               else candidate==operation['before_sha256'])
    if app_composition is not None and not operation.get('before_composition_sha256'):
        raise ValueError('Application correction has no admitted prior composition identity')
    if len(operation['attempts']) > 1 and unchanged:
        raise ValueError('Correction did not change the rejected candidate')
    operation.update(candidate_sha256=candidate, merger_result=str(result_path),
                     merger_result_sha256=hashlib.sha256(raw).hexdigest())
    if app_composition is not None:operation['composition_sha256']=app_composition
    return True


def _review_request(operation, directory, service):
    plan = _fresh(operation, directory)
    identity = {key: operation[key] for key in ('input_sha256', 'candidate_sha256')}
    identity['plan_id'] = operation['id']
    from . import update_composition, source_update_battery
    harness = _harness_material(operation)
    operation['composition_sha256'] = update_composition.signature(directory, operation)
    identity['composition_sha256'] = operation['composition_sha256']
    _persist(directory,operation)  # publish exact test composition before long work
    stopped = lambda: (_cancelled(directory) or _owner_blocked(operation['tree']) or job_blocked(operation)
                       or getattr(service, 'battery_cancelled', lambda: False)())
    battery = source_update_battery.prepare(operation, directory, harness, stopped)
    if stopped():raise InterruptedError('Owner stopped verification before review admission')
    # Automatic merges have no agent-issued verify call yet. Run (or reuse)
    # the exact full battery before validating its receipt and admitting review.
    try:source_update_battery.validate(operation, directory)
    except ValueError:
        if stopped():raise InterruptedError('Owner stopped verification before correction admission')
        feedback=(source_update_battery.failure_feedback(operation,directory)
                  if operation.get('resolution_owner')!='agent' else None)
        if feedback is None:raise
        if stopped():raise InterruptedError('Owner stopped failed-test correction admission')
        operation.setdefault('failed_batteries',[]).append(feedback)
        operation['findings']=feedback['findings']
        _start_attempt(operation,directory,service.forge)
        return
    if stopped():raise InterruptedError('Owner stopped independent review admission')
    record = service.request(trigger='contested_overlay',
        operation_id='source-update:' + operation['id'] + ':' + operation['composition_sha256'],
        overlay={'conflicts': plan['conflicts'], 'merge_identity': identity,
                 'repairs': [{'path': row['path'], 'reason': row.get('reason', '')} for row in plan.get('repairs', [])],
                 'compound_repairs':read(Path(directory)/'composition.json').get('repairs',[])},
        sources={**_materials(directory, plan), **harness, **battery},
        intent='Review isolated A/L/B/M source update. Preserve authored and upstream intent; never execute code.',
        criteria=['Inspect every conflicted path and the full candidate coupling.',
                  'harness/ is the complete final M application/resources, not merely Python context. distribution/ freezes the original B program/source manifests. Inspect recorded compound repair reasons and before/after hashes against core/app coupling. Opaque unchanged release assets are identity evidence, never decoded text.',
                  'Read battery/report.md and relevant diagnostics: mandatory full collected/executed evidence is bound to this exact core/app/runtime composition, not selected smoke. Green tests do not replace source review.',
                  'Absent entries mean explicit add/delete, not missing evidence. Modes and full inventories are binding.',
                  'Return findings for incorrect integration; any withheld or unverified material is insufficient.'])
    operation.update(state='reviewing', review_id=record['id'], subject_sha256=record['subject_sha256'])


def finish_for_verification(operation,directory):
    """Recover one agent's durable finish intent, then let the updater proceed.

    This grants no test/review/apply approval. Old journals without the new
    continuation policy retain their original protocol.
    """
    if operation.get('continuation')!=AUTOMATIC or not operation.get('finish_intent'):return False
    if _owner_blocked(operation['tree']) or job_blocked(operation) or _cancelled(directory):return False
    if (operation.get('coordinator') or {}).get('kind')=='waiting-selected-agent':
        # A durable finish can arrive before the incoming host takes custody.
        # Retain it for that exact host; absence of custody is not a bad draft.
        return False
    intent=operation['finish_intent'];directory=Path(directory)
    plan=_fresh(operation,directory)
    if (intent.get('input_sha256')!=operation['input_sha256']
            or intent.get('candidate_sha256')!=merge.candidate_signature(directory)):
        raise ValueError('Finished candidate changed; submit finish for the current bytes')
    try:
        from .app_source_update import finish as finish_app
        finish_app(directory)
        if plan.get('state')!='ready':
            merge.finish(directory,exe=merge.executable((operation.get('coordinator') or {}).get('program') or operation['program']))
        from . import update_composition
        if not update_composition.for_plan(directory,operation)['consistent']:
            raise ValueError('Reconcile the shared application before finishing the candidate')
        composition=update_composition.signature(directory,operation)
    except (OSError,ValueError,SyntaxError) as exc:
        operation.update(state='waiting_agent',finish_error=f'{type(exc).__name__}: {exc}')
        operation.pop('finish_intent',None);_persist(directory,operation)
        return False
    operation.update(state='verification_requested',candidate_sha256=intent['candidate_sha256'],
        composition_sha256=composition,review_clear=False)
    operation.pop('finish_intent',None);operation.pop('finish_error',None)
    operation.pop('verification_error',None)
    _persist(directory,operation)
    return True


def _verification_dependencies(operation,directory,stopped):
    """Acquire only publisher-declared pinned test wheels into this private job."""
    from . import source_update_battery,source_verification_bundle
    import sys
    root,digest,manifest=source_update_battery._bundle(operation)
    executor=dict(operation.get('verification_executor') or {})
    if executor.get('python_dependencies') or not manifest.get('dependencies',{}).get(sys.platform):return
    target=Path(directory)/'verification-dependencies'/digest
    receipt=target.with_name(target.name+'.receipt.json')
    if target.exists():
        if not receipt.is_file():raise ValueError('Verification dependency admission has an unknown outcome; retained target requires observation')
        evidence={'python_dependencies':str(target),'dependency_receipt':str(receipt),
            'dependency_receipt_sha256':source_verification_bundle.digest(receipt)}
    else:
        program=Path(operation['coordinator']['program'])
        python=executor.get('python') or program/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
        evidence=source_verification_bundle.provision(root,digest,operation['version'],python,target,stopped)
    executor.update(evidence);operation['verification_executor']=executor


def _advance(operation, directory, forge, service):
    from .update_supersession import blocked
    if blocked(operation,protocol='source'):return False
    if _owner_blocked(operation['tree']) or job_blocked(operation):
        return
    if getattr(forge, 'SOURCE_UPDATE_API', 0) != 1 or getattr(service.forge, 'SELF_REVIEW_API', 0) != 1:
        raise ValueError('Selected preserved Forge lacks source-update/self-review API v1; no generic agent fallback')
    if operation['state']=='waiting_agent':
        finish_for_verification(operation,directory)
        return
    if operation['state'] == 'verification_requested':
        plan=_fresh(operation,directory)
        from . import update_composition, source_update_battery
        if plan.get('state') != 'ready' or merge.inventory(directory/'result') != plan.get('result'):
            raise ValueError('Finish the complete candidate before verification')
        operation['candidate_sha256'] = merge.candidate_signature(directory)
        harness = _harness_material(operation)
        operation['composition_sha256'] = update_composition.signature(directory, operation)
        _persist(directory,operation)
        stopped = lambda: (_cancelled(directory) or _owner_blocked(operation['tree'])
                           or getattr(service, 'battery_cancelled', lambda: False)())
        try:
            _verification_dependencies(operation,directory,stopped)
            operation['verification_sources'] = source_update_battery.prepare(operation, directory, harness, stopped)
            source_update_battery.validate(operation, directory)
            operation.pop('verification_error', None)
        except (OSError, ValueError, RuntimeError) as exc:
            operation['verification_error'] = f'{type(exc).__name__}: {exc}'
        operation['state'] = ('review_requested' if operation.get('continuation')==AUTOMATIC
                              and not operation.get('verification_error') else 'waiting_agent')
        current = read(path(operation['tree']))
        current.update(state=operation['state'] if operation.get('continuation')==AUTOMATIC else 'pending', tries=0, retry_at=0)
        write(path(operation['tree']), current)
        return
    if operation['state'] == 'review_requested':
        _review_request(operation, directory, service)
    if operation['state'] == 'queued':
        plan=_fresh(operation,directory)
        if plan.get('state')=='ready' and not plan.get('repairs') and not plan.get('app_source'):
            # An automatic merge still goes through the incoming independent
            # panel. It needs no legacy agent API or redundant merger worker.
            if merge.inventory(directory/'result')!=plan.get('result'):
                raise ValueError('Automatic merge candidate changed before review')
            operation['candidate_sha256']=merge.candidate_signature(directory)
            operation['attempts'].append({'kind':'automatic-merge','before_sha256':operation['candidate_sha256']})
            _review_request(operation,directory,service)
        else:
            _start_attempt(operation, directory, forge)
    if operation['state'] == 'merging' and _merge_tick(operation, directory, forge):
        _review_request(operation, directory, service)
    if operation['state'] == 'reviewing':
        record = service.status(operation['review_id'])
        operation['review_evidence'] = review_evidence(record)
        if record.get('cancel_requested'):
            raise ValueError('Mandatory update panel was cancelled')
        if record.get('execution') in {'queued', 'running', 'unknown'}:
            return
        _fresh(operation,directory)
        if record.get('execution') == 'done' and record.get('verdict') == 'findings':
            operation['findings'] = [finding for critic in record['critics'] for finding in critic.get('findings', [])]
            if operation.get('resolution_owner')=='agent':
                rounds=operation.setdefault('agent_review_rounds',[])
                rounds.append({'review_id':record['id'],'subject_sha256':record['subject_sha256']})
                merge.reopen(directory);operation['state']='waiting_agent'
                operation.pop('review_id',None);operation.pop('subject_sha256',None)
                current=read(path(operation['tree']));current.update(state='pending',tries=0,retry_at=0)
                write(path(operation['tree']),current)
                return
            operation['attempts'][-1].update(review_id=record['id'], subject_sha256=record['subject_sha256'], verdict='findings')
            _start_attempt(operation, directory, forge)
            return
        _clear(service, record, operation['subject_sha256'])
        automatic=operation.get('continuation')==AUTOMATIC
        operation['state'] = ('waiting_agent' if operation.get('resolution_owner')=='agent' and not automatic else 'ready_to_apply')
        if operation.get('resolution_owner') == 'agent':
            operation['review_clear'] = True
            current = read(path(operation['tree']))
            current.update(state='ready_to_apply' if automatic else 'pending', tries=0, retry_at=0)
            write(path(operation['tree']), current)


def _signature(target):
    try:
        value = Path(target).stat()
        return value.st_ino, value.st_mtime_ns, value.st_ctime_ns, value.st_size
    except FileNotFoundError:
        return None


def _observe(tree, paused, stopped):
    """Discover atomic request publications; do not poll retired history forever.

    A restart rebuilds this disposable cache in small batches. Only a changed
    request directory enumerates history; unchanged records need no JSON read.
    Current owner controls are not queued behind that historical JSON backlog.
    """
    key = str(Path(tree).resolve())
    cache = _OBSERVERS.setdefault(key, {'records': {}, 'watch': {}, 'pending': {}})
    signature = _signature(_requests(tree))
    if 'directory' not in cache or cache['directory'] != signature:
        cache['directory'] = signature  # retain pre-scan identity for concurrent publishers
        present = set()
        for target in sorted(_requests(tree).glob('*.json')):
            present.add(target)
            stamp = _signature(target)
            if cache['records'].get(target) != stamp:
                cache['pending'][target] = stamp
                cache['records'][target] = stamp
        cache['records'] = {p: s for p, s in cache['records'].items() if p in present}
    # Bound the once-per-restart cold JSON work too: a thousand retained plans
    # must not delay the maintenance loop's next owner-control callback by seconds.
    for target in list(cache['pending'])[:16]:
        cache['pending'].pop(target)
        request = read(target)
        if request:
            cache['watch'][request['id']] = request
    current = _signature(path(tree))
    controls = bool(paused), bool(stopped)
    if 'current' not in cache or cache['current'] != current or cache.get('controls') != controls:
        cache['current'], cache['controls'] = current, controls
        request = read(path(tree))
        cache['current_id'] = request.get('id')
        if request:
            cache['watch'][request['id']] = request
    return cache


def _producer_pending(operation, forge, service):
    """Keep named live/unknown work observed even after a pipeline error."""
    if operation.get('review_id'):
        record = service.status(operation['review_id'])
        if record.get('execution') not in {'done', 'error', 'cancelled'}:
            return True
    if operation.get('task_id') and operation.get('unit_id'):
        unit = forge._unit_dir(operation['task_id'], 'agents', operation['unit_id'])
        if not unit.exists():
            return bool(operation.get('admission'))
        if forge._unit_state(unit).get('status') not in {'done', 'error', 'cancelled', 'stopped', 'lost'}:
            return True
    return False


def tick(tree, forge, review_service, paused=False, stopped=False, *, coordinator=None):
    """Runner maintenance hook: current producers only after initial discovery."""
    with _LOCK:
        changed = []
        cache = _observe(tree, paused, stopped)
        for identity, request in list(cache['watch'].items()):
            from .update_supersession import blocked
            if blocked(request,protocol='source',current_tree=tree):
                cache['watch'].pop(identity,None)
                continue
            operation_path = Path(request['directory']) / 'pipeline.json'
            operation = read(operation_path)
            if blocked(operation,protocol='source',current_tree=tree):continue
            if not operation:
                continue
            if operation.get('coordinator') != coordinator:
                # Incoming-runtime review has a separate owner. An ordinary
                # (possibly older) agent is not its producer or cancellation API.
                continue
            before = json.dumps(operation, sort_keys=True)
            current = identity == cache.get('current_id')
            active = operation['state'] in ACTIVE
            try:
                producer = False if active or operation.get('cancel_requested') else _producer_pending(operation, forge, review_service)
            except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
                producer = True
                operation['producer_error'] = str(exc)
            if stopped and (current or active or producer) and not operation.get('cancel_requested'):
                _cancel_marker(operation_path.parent, 'Owner Stop')
                operation.update(cancel_requested=True, state='cancelled', reason='Owner Stop')
            if _cancelled(operation_path.parent):
                operation.update(cancel_requested=True, state='cancelled')
            if current or active:
                operation['paused'] = bool(paused)
            if operation.get('cancel_requested'):
                _persist(operation_path.parent, operation)
                try:
                    _stop(operation, forge, review_service)
                except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
                    operation['stop_error'] = str(exc)
            elif operation['state'] in ACTIVE and not paused:
                try:
                    _advance(operation, Path(request['directory']), forge, review_service)
                except (OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError, SyntaxError) as exc:
                    operation.update(state='needs_attention', error=f'{type(exc).__name__}: {exc}')
            if json.dumps(operation, sort_keys=True) != before:
                _persist(operation_path.parent, operation)
                changed.append(operation)
            if request.get('state') != operation['state']:
                request = {**request, 'state': operation['state']}
                _save_request(tree, request)
                cache['watch'][identity] = request
            try:
                observe = (operation['state'] in ACTIVE
                           or (operation.get('cancel_requested') and not operation.get('stop_confirmed'))
                           or (not operation.get('cancel_requested') and _producer_pending(operation, forge, review_service)))
            except (OSError, ValueError, TypeError, AttributeError, KeyError):
                observe = True  # failed producer observation is uncertainty, not retirement
            if not observe:
                cache['watch'].pop(identity, None)
        return changed
