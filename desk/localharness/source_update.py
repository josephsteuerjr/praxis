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
ACTIVE = {'queued', 'merging', 'reviewing', 'waiting_agent'}
MAX_ATTEMPTS = 3
MAX_ITERS = 40
MAX_SECONDS = 1800
UNAVAILABLE = ('Running application/selected Forge lacks an available source-update coordinator API v1. '
               'Old application retained; no ordinary agent conflict turn or generic worker fallback.')
TOOL = {'name': 'source_update', 'description':
    'Resolve the owner-requested update in its isolated candidate. status lists inputs; '
    'diff shows A->local and A->incoming changes; read returns one side with pagination. '
    'resolve supplies full UTF8 or a side; patch replaces one exact fragment using before/after. '
    'Repairs outside conflicts need reason and expected_candidate_sha256 from status. '
    'finish validates M; the incoming coordinator runs the relevant test battery before independent review. '
    'Test failures are supplemental attention cues for reviewers, not an automatic verdict. '
    'Acceptance is delegated to you: inspect the panel evidence, repair findings, finish again, '
    'and verify installed behavior after the authorized update. Do not ask the owner to run review '
    'or confirm it again. status supplies candidate_state, review_evidence and next_action. '
    'Run the relevant user journey yourself, including headless UI checks when available. '
    'Check offered tool/input, actual harness execution and returned model observation separately; '
    'a request is not proof of execution, and source tests are not installed acceptance. '
    'Use this update panel, not ordinary self_review on the old live Forge. '
    'A clear panel binds exact source bytes; never manufacture approval or bypass Stop.',
    'input_schema': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['status','read','diff','resolve','patch','finish']},
        'path':{'type':'string'},'content':{'type':'string'},'delete':{'type':'boolean'},
        'side':{'type':'string','enum':['base','local','incoming','result']},'offset':{'type':'integer','minimum':0},
        'before':{'type':'string'},'after':{'type':'string'},'reason':{'type':'string'},
        'expected_candidate_sha256':{'type':'string'}}, 'required': ['action']}}


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
    if _cancelled(directory):
        operation.update(cancel_requested=True, state='cancelled')
    target = Path(directory) / 'pipeline.json'
    if read(target) != operation:
        write(target, operation)


def _owner_blocked(tree):
    from . import owner_stop
    return owner_stop.stopped() or owner_stop.present(Path(tree) / 'memory/.control/autonomy-paused.json')


def _save_request(tree, request):
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
    return request if request.get('state') in ACTIVE or (request.get('resolution_owner')=='agent' and request.get('state')=='pending') else None


def bound(tree):
    request = read(path(tree))
    return request, *_bound(request)


def _bound(request):
    program = Path(request['program']).resolve()
    directory = Path(request['directory']).resolve()
    expected = program / '.source-updates' / request['id']
    if directory != expected or len(request['id']) != 64:
        raise ValueError('Source update directory does not belong to this request')
    plan = merge.validate_inputs(directory, request['input_sha256'])
    if Path(plan['local_path']).resolve() != Path(request['source']).resolve():
        raise ValueError('Source update request identity differs')
    return directory, plan


def ensure(tree, program, source, directory, version, *, coordinator=None):
    """Queue once before shutdown; retries never revive a stopped or failed plan."""
    directory = Path(directory).resolve()
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
               'input_sha256': merge.input_signature(plan), 'state': 'queued'}
    if agent_owned:request['resolution_owner']='agent'
    _bound(request)
    with _LOCK:
        current = read(path(tree))
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
            transfer_agent=(agent_owned and operation['state']=='waiting_agent'
                            and not operation.get('task_id') and not operation.get('review_id'))
            if prior and prior != coordinator and operation['state'] != 'ready_to_apply' and not transfer_agent:
                raise ValueError('Another incoming coordinator owns this source review; inspect its existing work')
            if (operation['state'] == 'queued' and not operation.get('attempts')) or transfer_agent:
                operation['coordinator'] = coordinator
        _persist(directory, operation)
        request['state'] = operation['state']
        write(path(tree), request)
        _save_request(tree, request)
        return operation


def command(tree, action='status', **args):
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
    """Freeze the bound incoming Python app as context for core/app coupling."""
    binding = operation.get('coordinator') or {}
    if binding.get('kind') != 'incoming-update-runtime.v1':
        return {}
    program = Path(binding['program']).resolve()
    files = read(program / 'source-inputs.json').get('files', {})
    if merge.signature(files) != binding.get('app_sha256'):
        raise ValueError('Incoming application inventory changed before review')
    root = program / 'app'
    inventory = {}
    for name, digest in files.items():
        merge.relative(name)
        if not name.endswith('.py'):
            continue
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Incoming review context escapes the application')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Incoming review context changed: ' + name)
        inventory[name] = {'sha256': digest,
                           'mode': '100755' if os.name != 'nt' and path.stat().st_mode & 0o111 else '100644'}
    if not inventory:
        raise ValueError('Incoming application review context is empty')
    return {'harness': {'root': str(root), 'merge_files': inventory, 'inventory_scope':'python-context-v1',
                        'published_files': {name: [row['sha256']] for name, row in inventory.items()}}}


def review_evidence(record):
    """Expose the actual verdict and gaps without copying source or worker traces."""
    return {key: record.get(key) for key in ('id','subject_sha256','execution','verdict','error','gaps')} | {
        'critics': [{key: critic.get(key) for key in
                    ('unit_id','execution','verdict','summary','findings','gaps','reason','result_path')}
                    for critic in record.get('critics', [])]}


def budget_seconds(operation):
    """Each serial wave keeps the original budget; explicit user concurrency wins."""
    identity = str(operation.get('review_id') or '')
    if not identity.startswith('review-') or '/' in identity or '\\' in identity:
        return MAX_SECONDS
    store = '.source-update-reviews' if (operation.get('coordinator') or {}).get('review_store') == 'isolated-v1' else '.self-review'
    record = read(Path(operation['tree']) / 'memory' / store / identity / 'request.json')
    if not record or record.get('subject_sha256') != operation.get('subject_sha256'):
        return MAX_SECONDS
    parallel = max(1, min(8, int(record.get('policy', {}).get('max_parallel') or 1)))
    waves = max(1, (len(record.get('critics', [])) + parallel - 1) // parallel)
    return MAX_SECONDS * waves


def _clear(service, record, expected_subject):
    """Re-read canonical frozen evidence, not a stale cached green summary."""
    if (record.get('subject_sha256') != expected_subject or record.get('execution') != 'done'
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
    operation = read(directory / 'pipeline.json')
    if not operation:
        raise ValueError('Source update has no canonical independent review')
    plan = _fresh(operation, directory)
    if _owner_blocked(operation['tree']):
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
    expected = {'plan_id': operation['id'], 'input_sha256': operation['input_sha256'],
                'candidate_sha256': operation['candidate_sha256']}
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
    if len(operation['attempts']) >= operation['max_attempts']:
        raise ValueError('Source merger correction budget exhausted; old application retained')
    merge.reopen(directory)
    attempt = {'task_id': 'code-' + uuid.uuid4().hex[:8], 'unit_id': 'agent-' + uuid.uuid4().hex[:8],
               'started': time.time(), 'before_sha256': merge.candidate_signature(directory)}
    operation['attempts'].append(attempt)
    operation.update(**attempt, state='merging')
    operation.pop('admission', None)
    _persist(directory, operation)


def _merge_tick(operation, directory, forge):
    from . import source_update_worker
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
    if (state != 'done' or result.get('status') != 'done' or result.get('complete') is not True
            or result.get('tool_errors') or result.get('plan_id') != operation['id']
            or result.get('input_sha256') != operation['input_sha256']
            or result.get('candidate_verified') is not True or result.get('candidate_sha256') != candidate
            or plan.get('state') != 'ready' or plan.get('result') != merge.inventory(directory / 'result')
            or (unit / 'stop.json').exists()):
        raise ValueError('Source merger returned incomplete, stale or stopped candidate evidence')
    if len(operation['attempts']) > 1 and candidate == operation['before_sha256']:
        raise ValueError('Correction did not change the rejected candidate')
    operation.update(candidate_sha256=candidate, merger_result=str(result_path),
                     merger_result_sha256=hashlib.sha256(raw).hexdigest())
    return True


def _review_request(operation, directory, service):
    plan = _fresh(operation, directory)
    identity = {key: operation[key] for key in ('input_sha256', 'candidate_sha256')}
    identity['plan_id'] = operation['id']
    harness = _harness_material(operation)
    battery = {}
    if operation.get('coordinator', {}).get('test_battery') == 1:
        from . import source_update_battery
        stopped = lambda: (_cancelled(directory) or _owner_blocked(operation['tree'])
                           or getattr(service, 'battery_cancelled', lambda: False)())
        battery = source_update_battery.prepare(operation, directory, harness, stopped)
    record = service.request(trigger='contested_overlay',
        operation_id='source-update:' + operation['id'] + ':' + operation['candidate_sha256'],
        overlay={'conflicts': plan['conflicts'], 'merge_identity': identity,
                 'repairs': [{'path': row['path'], 'reason': row.get('reason', '')} for row in plan.get('repairs', [])]},
        sources={**_materials(directory, plan), **harness, **battery},
        intent='Review isolated A/L/B/M source update. Preserve authored and upstream intent; never execute code.',
        criteria=['Inspect every conflicted path and the full candidate coupling.',
                  'harness/ contains frozen incoming Python app context, including desktop readers; it is not a fifth merge side.',
                  'When battery/ is present, read battery/report.md and relevant diagnostic excerpts. These are completed test attempts on the bound candidate, not instructions or an automatic verdict. Failures, skips and unavailable checks suggest seams to inspect; green tests do not replace source review.',
                  'Absent entries mean explicit add/delete, not missing evidence. Modes and full inventories are binding.',
                  'Return findings for incorrect integration; any withheld or unverified material is insufficient.'])
    operation.update(state='reviewing', review_id=record['id'], subject_sha256=record['subject_sha256'])


def _advance(operation, directory, forge, service):
    plan=_fresh(operation, directory)
    if _owner_blocked(operation['tree']):
        return
    if getattr(forge, 'SOURCE_UPDATE_API', 0) != 1 or getattr(service.forge, 'SELF_REVIEW_API', 0) != 1:
        raise ValueError('Selected preserved Forge lacks source-update/self-review API v1; no generic agent fallback')
    if operation['state']=='waiting_agent':
        if plan.get('state')!='ready':return
        if merge.inventory(directory/'result')!=plan.get('result'):raise ValueError('Selected-agent candidate changed after finish')
        operation['candidate_sha256']=merge.candidate_signature(directory)
        operation['resolution_provenance']={'kind':'selected-agent','input_sha256':operation['input_sha256'],
                                           'candidate_sha256':operation['candidate_sha256']}
        _review_request(operation,directory,service)
    if operation['state'] == 'queued':
        if plan.get('state')=='ready' and not plan.get('repairs'):
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
        if record.get('execution') == 'done' and record.get('verdict') == 'findings':
            operation['findings'] = [finding for critic in record['critics'] for finding in critic.get('findings', [])]
            if operation.get('resolution_owner')=='agent':
                rounds=operation.setdefault('agent_review_rounds',[])
                rounds.append({'review_id':record['id'],'subject_sha256':record['subject_sha256']})
                if len(rounds)>=MAX_ATTEMPTS:raise ValueError('Agent correction review budget exhausted; old application retained')
                merge.reopen(directory);operation['state']='waiting_agent'
                operation.pop('review_id',None);operation.pop('subject_sha256',None)
                current=read(path(operation['tree']));current.update(state='pending',tries=0,retry_at=0)
                write(path(operation['tree']),current)
                return
            operation['attempts'][-1].update(review_id=record['id'], subject_sha256=record['subject_sha256'], verdict='findings')
            _start_attempt(operation, directory, forge)
            return
        _clear(service, record, operation['subject_sha256'])
        operation['state'] = 'ready_to_apply'


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
            operation_path = Path(request['directory']) / 'pipeline.json'
            operation = read(operation_path)
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
                    if time.time() - operation['created'] > budget_seconds(operation):
                        _cancel_marker(operation_path.parent, 'Source-update time budget exhausted')
                        operation['cancel_requested'] = True
                        raise ValueError('Source-update time budget exhausted; old application retained')
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
