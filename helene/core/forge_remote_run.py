"""Task-owned remote synchronous commands: admission is short, polling is unlocked."""
import json
import ntpath
import time

TERMINAL = {'done', 'failed', 'error', 'stopped', 'timed_out'}
STATUS_ALIASES = {'ok': 'done', 'completed': 'done', 'succeeded': 'done',
                  'cancelled': 'stopped', 'timeout': 'timed_out',
                  # hostproc.poll / body process.status: no proven outcome.
                  'lost': 'unknown', 'in_doubt': 'unknown'}


def _call(forge, task, action, request, *, command='', cwd='.', timeout=0):
    remote = forge._remote_client(task)
    ident = request.get('remote_id', '')
    with forge._remote_observations(task):
        if task['scope'] == 'host':
            if action == 'start':
                return remote.call('op.start', {'root_value': task['root'], 'command': command,
                                               'cwd': cwd, 'timeout': timeout})
            return remote.call('op.' + ('stop' if action == 'stop' else 'poll'),
                               {'operation_id': ident, **({'tail': 30000} if action != 'stop' else {})})
        if action == 'start':
            place = task['root'] if cwd in ('', '.') else ntpath.join(task['root'], cwd)
            return remote.process_start(command=command, shell='power_shell', cwd=place, timeout=timeout)
        return (remote.call('process.cancel', {'operation_id': ident, 'tail': 30000})
                if action == 'stop' else remote.process_status(ident, 30000))


def refresh(forge, directory, task, *, stop=False):
    request = forge._read_json(directory / 'request.json', {}) or {}
    result = forge._read_json(directory / 'result.json', {}) or {}
    if result.get('status') in TERMINAL:
        return result
    if not request.get('remote_id'):
        return {**request, 'status': 'unknown', 'error': 'Remote admission has no confirmed operation identity'}
    try:
        if stop: _call(forge, task, 'stop', request)
        row = _call(forge, task, 'poll', request)
    except Exception as exc:
        row = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    terminal = row.get('terminal') or {}
    if not isinstance(terminal, dict): terminal = {}
    status = str(terminal.get('status') or row.get('status') or 'unknown') if row.get('ok') else 'unknown'
    if status == 'exited':
        status = 'done' if terminal.get('exit_code', row.get('exit_code')) == 0 else 'failed'
    status = STATUS_ALIASES.get(status, status)
    # A cancel request alone never proves termination. Only a subsequent poll does.
    value = {**request, 'status': status, 'remote_result': row}
    forge._atomic_json(directory / 'state.json', value)
    if row.get('ok') and status in TERMINAL:
        forge._atomic_json(directory / 'result.json', value)
    return value


def start_unlocked(forge, task, command, cwd, timeout, *, parent_unit_id='', name=''):
    """Caller owns the admission mutex/lease; retain every backend outcome."""
    task_id = task['id']
    unit = forge._id('proc')
    directory = forge._unit_dir(task_id, 'processes', unit)
    request = {'id': unit, 'task_id': task_id, 'remote': True, 'scope': task['scope'],
               'parent_unit_id': parent_unit_id, 'name': name, 'command': command, 'cwd': cwd,
               'created': forge._now(), 'status': 'unknown'}
    forge._atomic_json(directory / 'request.json', request)
    try:
        row = _call(forge, task, 'start', request, command=command, cwd=cwd, timeout=timeout)
        identity = row.get('id') if task['scope'] == 'host' else row.get('operation_id')
        request['remote_result'] = row
        if identity:
            request['remote_id'] = identity
        if row.get('ok') and identity:
            request['status'] = 'running'
        elif (not row.get('ok') and not identity and task['scope'] == 'host'
              and (row.get('type') == 'result' or row.get('code') in {'unavailable', 'operation_cap', 'disk_low'})):
            # op.start's typed result refusal is pre-launch. Protocol/transport
            # errors can follow a spawn and must remain unknown.
            request['status'] = 'failed'
            forge._atomic_json(directory / 'result.json', request)
    except Exception as exc:
        request['error'] = f'{type(exc).__name__}: {exc}'
    forge._atomic_json(directory / 'request.json', request)
    return request


def run(forge, task_id, command, cwd, timeout, parent_request_path):
    with forge._mutation_lock(task_id) as beat:
        reason = forge._admission_reason(task_id, parent_request_path, general=True)
        if reason: return 'Run refused: ' + reason
        task = forge.get(task_id)
        parent, _ = forge._worker_parent(task_id, parent_request_path)
        beat(lease_sec=120)
        request = start_unlocked(forge, task, command, cwd, timeout, parent_unit_id=parent)
    if request['status'] != 'running':
        return json.dumps(request, ensure_ascii=False)
    unit = request['id']
    directory = forge._unit_dir(task_id, 'processes', unit)
    deadline = time.monotonic() + timeout if timeout else float('inf')
    while True:
        value = refresh(forge, directory, task)
        if value['status'] in TERMINAL or value['status'] == 'unknown': break
        if time.monotonic() >= deadline:
            value = refresh(forge, directory, task, stop=True); break
        time.sleep(.1)
    forge._event(task_id, 'command', command=command, process_id=unit,
                 status=value['status'], summary='Remote command: ' + value['status'])
    return json.dumps(value, ensure_ascii=False)
