"""Owner-requested update conflicts: isolated merger -> frozen panel -> correction.

The main agent is not sent an ordinary conflict turn. This coordinator shares
Forge's process/Stop authority and the existing self_review service. Syntax-ready
source is NOT review-ready, and neither worker prose nor an author disposition can
substitute for a completed clear panel on this exact candidate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

from .package_notice import read, write
from . import source_merge as merge, self_review

_LOCK = threading.RLock()
_OBSERVERS = {}  # disposable per-tree discovery signatures/current producers, never authority
ACTIVE = {'queued', 'merging', 'reviewing'}
MAX_ATTEMPTS = 3
MAX_ITERS = 40
MAX_SECONDS = 1800
TOOL = {'name': 'source_update', 'description':
    'Read the isolated update pipeline status. Conflicts are handled by a restricted '
    'merger and independent reviewers; this tool cannot edit, approve or install code.',
    'input_schema': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['status']}}, 'required': ['action']}}


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
    return request if request.get('state') in ACTIVE else None


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


def ensure(tree, program, source, directory, version):
    """Queue once before shutdown; retries never revive a stopped or failed plan."""
    directory = Path(directory).resolve()
    plan = merge.validate_inputs(directory)
    request = {'id': directory.name, 'directory': str(directory), 'version': str(version),
               'source': str(Path(source).resolve()), 'program': str(Path(program).resolve()),
               'input_sha256': merge.input_signature(plan), 'state': 'queued'}
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
            if (runtime.get('source_update_supported') is not True
                    or runtime.get('app_root') != str(Path(program).resolve())
                    or not 0 <= time.time() - float(runtime.get('at') or 0) < 60):
                operation.update(state='needs_attention', error=
                    'Running application/selected Forge lacks an available source-update coordinator API v1. '
                    'Old application retained; no ordinary agent conflict turn or generic worker fallback.')
            _persist(directory, operation)
        if operation['input_sha256'] != request['input_sha256']:
            raise ValueError('Update pipeline input binding changed')
        _persist(directory, operation)
        request['state'] = operation['state']
        write(path(tree), request)
        _save_request(tree, request)
        return operation


def command(tree, action='status', **args):
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
    """Compatibility no-op: never turn an update conflict into owner-agent speech."""
    return None


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
    return {label: {'root': str(Path(directory) / side),
                    'merge_files': {name: files[side].get(name) for name in sorted(names)}}
            for side, label in (('base', 'base'), ('local', 'mine'),
                                ('incoming', 'release'), ('result', 'resolved'))}


def _clear(service, record, expected_subject):
    """Re-read canonical frozen evidence, not a stale cached green summary."""
    if (record.get('subject_sha256') != expected_subject or record.get('execution') != 'done'
            or record.get('verdict') != 'clear' or record.get('cancel_requested')
            or record.get('gaps') or not record.get('critics')):
        raise ValueError('Mandatory contested review is not completed and clear')
    subject = self_review.validate_snapshot(record['snapshot'], expected_subject)
    if subject.get('gaps') or service.freshness(record['id'])['stale']:
        raise ValueError('Reviewed source material changed or has gaps')
    for critic in record['critics']:
        if critic.get('execution') != 'done' or critic.get('verdict') != 'clear':
            raise ValueError('Critic is incomplete or not clear')
        result_path = Path(critic['result_path'])
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
    plan = _fresh(operation, directory)
    if _owner_blocked(operation['tree']):
        raise ValueError('Owner Stop/Pause forbids source-update apply; old application retained')
    if (operation.get('state') != 'ready_to_apply' or operation.get('cancel_requested')
            or operation.get('paused') or plan.get('state') != 'ready'
            or merge.inventory(directory / 'result') != plan.get('result')
            or merge.candidate_signature(directory) != operation.get('candidate_sha256')):
        raise ValueError('Source update has no current reviewed candidate ready to apply')
    service = self_review.Service(operation['tree'], operation['source'], operation['program'], None)
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


def cancel(tree, forge, service, reason='Owner stopped the source update'):
    with _LOCK:
        request = read(path(tree))
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
        brief = ('Merge the listed source conflicts, preserving both intents; inspect full candidate '
                 'for coupling and call finish. input_sha256=' + operation['input_sha256'] +
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
    record = service.request(trigger='contested_overlay',
        operation_id='source-update:' + operation['id'] + ':' + operation['candidate_sha256'],
        overlay={'conflicts': plan['conflicts'], 'merge_identity': identity},
        sources=_materials(directory, plan),
        intent='Review isolated A/L/B/M source update. Preserve authored and upstream intent; never execute code.',
        criteria=['Inspect every conflicted path and the full candidate coupling.',
                  'Absent entries mean explicit add/delete, not missing evidence. Modes and full inventories are binding.',
                  'Return findings for incorrect integration; any withheld or unverified material is insufficient.'])
    operation.update(state='reviewing', review_id=record['id'], subject_sha256=record['subject_sha256'])


def _advance(operation, directory, forge, service):
    _fresh(operation, directory)
    if _owner_blocked(operation['tree']):
        return
    if getattr(forge, 'SOURCE_UPDATE_API', 0) != 1 or getattr(service.forge, 'SELF_REVIEW_API', 0) != 1:
        raise ValueError('Selected preserved Forge lacks source-update/self-review API v1; no generic agent fallback')
    if operation['state'] == 'queued':
        _start_attempt(operation, directory, forge)
    if operation['state'] == 'merging' and _merge_tick(operation, directory, forge):
        _review_request(operation, directory, service)
    if operation['state'] == 'reviewing':
        record = service.status(operation['review_id'])
        if record.get('cancel_requested'):
            raise ValueError('Mandatory update panel was cancelled')
        if record.get('execution') in {'queued', 'running', 'unknown'}:
            return
        if record.get('execution') == 'done' and record.get('verdict') == 'findings':
            operation['findings'] = [finding for critic in record['critics'] for finding in critic.get('findings', [])]
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


def tick(tree, forge, review_service, paused=False, stopped=False):
    """Runner maintenance hook: current producers only after initial discovery."""
    with _LOCK:
        changed = []
        cache = _observe(tree, paused, stopped)
        for identity, request in list(cache['watch'].items()):
            operation_path = Path(request['directory']) / 'pipeline.json'
            operation = read(operation_path)
            if not operation:
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
                    if time.time() - operation['created'] > MAX_SECONDS:
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
