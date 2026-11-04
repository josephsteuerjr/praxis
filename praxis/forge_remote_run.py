"""Task-owned remote synchronous commands: admission is short, polling is unlocked."""
import hashlib
from pathlib import Path
import json
import ntpath
import time
import uuid

TERMINAL = {'done', 'failed', 'error', 'stopped', 'timed_out'}
ACTIVE = {'queued', 'starting', 'running', 'finishing', 'stopping'}
STATUS_ALIASES = {'ok': 'done', 'completed': 'done', 'succeeded': 'done',
                  'cancelled': 'stopped', 'timeout': 'timed_out',
                  # hostproc.poll / body process.status: no proven outcome.
                  'lost': 'unknown', 'in_doubt': 'unknown'}


def _call(forge, task, action, request, *, command='', cwd='.', timeout=0):
    remote = forge._remote_client(task)
    ident = request.get('remote_id', '')
    ident = ident or request.get('backend_operation_id', '')
    with forge._remote_observations(task):
        if task['scope'] == 'host':
            if action == 'start':
                return remote.call('op.start', {'root_value': task['root'], 'command': command,
                                               'cwd': cwd, 'timeout': timeout},
                                   request_id=request['backend_request_id'])
            return remote.call('op.' + ('stop' if action == 'stop' else 'poll'),
                               {'operation_id': ident, **({'tail': 30000} if action != 'stop' else {})})
        if action == 'start':
            place = task['root'] if cwd in ('', '.') else ntpath.join(task['root'], cwd)
            return remote.process_start(command=command, shell='power_shell', cwd=place, timeout=timeout,
                                        request_id=request['backend_request_id'],
                                        operation_id=request['backend_operation_id'])
        return (remote.call('process.cancel', {'operation_id': ident, 'tail': 30000})
                if action == 'stop' else remote.process_status(ident, 30000))


def tree_settled(row,operation_id=""):
    if not isinstance(row,dict):return False
    proof=row.get('tree_exit') or {}
    if not isinstance(proof,dict):return False
    receipt=proof.get('receipt') or {}
    if not isinstance(receipt,dict):return False
    try:receipt_sha=hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    except (TypeError,ValueError,OverflowError):return False
    return (proof.get('contract')=='command-tree-v1' and proof.get('settled') is True
            and receipt.get('settled') is True and isinstance(receipt.get('nonce'),str) and bool(receipt.get('nonce'))
            and type(receipt.get('exit_code')) is int
            and (not operation_id or proof.get('operation_id')==operation_id)
            and proof.get('receipt_sha256')==receipt_sha)

def custody_status(request,result,scope=None):
    if not isinstance(request,dict) or not isinstance(result,dict):return 'unknown'
    status=result.get('status')
    if status is not None and not isinstance(status,str):return 'unknown'
    if scope and request.get('scope') and request['scope']!=scope:return 'unknown'
    scope=scope or request.get('scope')
    if scope is not None and not isinstance(scope,str):return 'unknown'
    if request.get('remote') and scope not in {'host','windows'}:return 'unknown'
    if scope=='host' and status in TERMINAL and result.get('prelaunch_refused') is not True:
        if not tree_settled(result.get('remote_result') or {},request.get('remote_id') or request.get('backend_operation_id') or ''):return 'unknown'
    return status

def project_result(directory,request,result,scope=None):
    """Read-only old bytes + exact-operation exit reconciliation, never replacement."""
    if not isinstance(request,dict) or not isinstance(result,dict):return {'status':'unknown'}
    if custody_status(request,result,scope) in TERMINAL:return result
    if any((directory/name).is_symlink() for name in ('result.json','exit-reconciliation.json')):
        return {**result,'status':'unknown'}
    try:
        original=(directory/'result.json').read_bytes()
        raw=(directory/'exit-reconciliation.json').read_bytes();receipt=json.loads(raw)
    except (OSError,ValueError):return {**result,'status':'unknown'}
    if not isinstance(receipt,dict):return {**result,'status':'unknown'}
    value=receipt.get('observation') or {}
    operation=request.get('remote_id') or request.get('backend_operation_id')
    if (receipt.get('original_result_sha256')!=hashlib.sha256(original).hexdigest()
            or receipt.get('operation_id')!=operation or custody_status(request,value,scope) not in TERMINAL):
        return {**result,'status':'unknown'}
    return {**value,'original_result_status':result.get('status'),
            'original_result_sha256':receipt['original_result_sha256'],
            'exit_reconciliation':{'sha256':hashlib.sha256(raw).hexdigest(),'at':receipt.get('at')}}

def refresh(forge, directory, task, *, stop=False):
    request = forge._read_json(directory / 'request.json', {}) or {}
    result = forge._read_json(directory / 'result.json', {}) or {}
    if not isinstance(request,dict) or not isinstance(result,dict):
        return {'status':'unknown','error':'remote custody metadata is malformed; no RPC admitted'}
    scope=str(task.get('scope') or 'self')
    if scope not in {'host','windows'} or (request.get('scope') and request['scope']!=scope):
        return {**request,'status':'unknown','error':'remote scope contradicts owning task; no RPC admitted'}
    projected=project_result(directory,request,result,task.get('scope')) if result else {}
    if projected.get('status') in TERMINAL:return projected
    expected = request.get('backend_operation_id')
    if not request.get('remote_id') and not expected:
        return {**request, 'status': 'unknown', 'error': 'Remote admission has no confirmed operation identity'}
    try:
        if stop: _call(forge, task, 'stop', request)
        row = _call(forge, task, 'poll', request)
    except Exception as exc:
        row = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    if not isinstance(row,dict):row={'ok':False,'error':'remote reply is not an object; custody unknown'}
    if not request.get('remote_id') and expected:
        identity = row.get('id') if task['scope'] == 'host' else row.get('operation_id')
        if row.get('ok') and identity == expected:
            request['remote_id'] = expected
            forge._atomic_json(directory / 'request.json', request)
        else:
            # A missing operation is still ambiguous; never replay admission.
            row = {**row, 'ok': False}
    terminal = row.get('terminal')
    terminal_valid = terminal is None or isinstance(terminal,dict)
    terminal = terminal if isinstance(terminal,dict) else {}
    observed = terminal.get('status') if 'status' in terminal else row.get('status')
    status = observed if row.get('ok') and terminal_valid and isinstance(observed,str) else 'unknown'
    if status == 'exited':
        code=terminal.get('exit_code',row.get('exit_code'))
        status = ('done' if code==0 else 'failed') if type(code) is int else 'unknown'
    status = STATUS_ALIASES.get(status, status)
    if status not in TERMINAL|ACTIVE|{'unknown'}:status='unknown'
    if task['scope']=='host' and status in TERMINAL and not tree_settled(row,request.get('remote_id') or expected or ''):
        status='unknown'
        row={**row,'custody_error':'backend terminal lacks command-tree-v1 settlement proof'}
    # A cancel request alone never proves termination. Only a subsequent poll does.
    value = {**request, 'status': status, 'remote_result': row}
    forge._atomic_json(directory / 'state.json', value)
    if row.get('ok') and status in TERMINAL:
        original=directory/'result.json'
        if original.exists():
            forge._atomic_json(directory/'exit-reconciliation.json',{
                'original_result_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),
                'operation_id':request.get('remote_id') or request.get('backend_operation_id'),
                'observation':value,'at':forge._now()})
            value=project_result(directory,request,result,task.get('scope'))
        else:forge._atomic_json(original, value)
    return value


def start_unlocked(forge, task, command, cwd, timeout, *, parent_unit_id='', name=''):
    """Caller owns the admission mutex/lease; retain every backend outcome."""
    task_id = task['id']
    unit = forge._id('proc')
    directory = forge._unit_dir(task_id, 'processes', unit)
    return start_at(forge,task,directory,unit,command,cwd,timeout,parent_unit_id=parent_unit_id,name=name)


def start_at(forge,task,directory,unit,command,cwd,timeout,*,parent_unit_id='',name=''):
    if (directory/'request.json').exists():raise ValueError('remote admission identity already exists; reconcile instead of replay')
    task_id=task['id']
    request = {'id': unit, 'task_id': task_id, 'remote': True, 'scope': task['scope'],
               'parent_unit_id': parent_unit_id, 'name': name, 'command': command, 'cwd': cwd,
               'created': forge._now(), 'status': 'unknown'}
    request['backend_request_id'] = uuid.uuid4().hex
    if task['scope'] == 'host':
        # broker.dispatch derives this exact operation identity before op.start.
        # Persist it before transport so a lost reply can be polled/stopped without
        # replaying admission or borrowing an unrelated call's retry identity.
        request['backend_operation_id'] = 'op-' + hashlib.sha256(
            request['backend_request_id'].encode()).hexdigest()[:16]
    else:
        request['backend_operation_id'] = 'op-' + uuid.uuid4().hex
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
              and ((row.get('type') == 'result' and not row.get('code'))
                   or row.get('code') in {'unavailable', 'operation_cap', 'disk_low','root_binding_changed'})):
            # op.start's typed result refusal is pre-launch. Protocol/transport
            # errors can follow a spawn and must remain unknown.
            request['status'] = 'failed'
            request['prelaunch_refused'] = True
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
