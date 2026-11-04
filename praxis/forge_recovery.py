"""Explicit local/host custody recovery from a witnessed kernel reboot.

Original request/result/Stop bytes stay untouched. This proves extinction of an
old execution boundary, never success or a clean Stop. Windows/remote-device
executions and unbound launch histories remain unknown.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

SCHEMA = 'praxis.forge.reboot-recovery.v1'
FILE = 'custody-recovery.json'
KINDS = {'agents':('agent_spawned','agent_id'),
         'processes':('process_started','process_id'),
         'verifications':('verification_started','verification_id')}
TERMINAL = {'done','error','failed','stalled','stopped','timed_out','passed','passed_with_skips','cancelled'}


def kernel_boundary() -> dict:
    if not sys.platform.startswith('linux'):
        raise ValueError('kernel reboot recovery requires the Linux execution host')
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    boot_time = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines()
                     if line.startswith('btime '))
    machine = Path('/sys/class/dmi/id/product_uuid').read_text().strip()
    if not boot_id or boot_time <= 0 or not machine:
        raise ValueError('kernel boundary unavailable')
    return {'boot_id':boot_id,'boot_time':boot_time,
            'host_id':hashlib.sha256(machine.encode()).hexdigest()}


def timestamp(value) -> float:
    parsed = dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('launch timestamp needs a timezone')
    return parsed.timestamp()


def _bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError('recovery subject cannot be a symlink')
    return path.read_bytes()


def _subjects(directory: Path) -> dict:
    return {name:hashlib.sha256(_bytes(directory/name)).hexdigest()
            if (directory/name).exists() else ''
            for name in ('request.json','result.json','state.json')}


def _launch(directory: Path, before: float, digest: str = '') -> dict:
    kind, identity = KINDS[directory.parent.name]
    found = None
    for raw in _bytes(directory.parents[1]/'events.jsonl').splitlines():
        row = json.loads(raw)
        if row.get(identity) != directory.name:
            continue
        if row.get('kind') in {kind,'agent_dequeued','agent_resumed'}:
            if timestamp(row.get('at')) >= before:
                raise ValueError('unit has execution evidence after the reboot boundary')
            actual = hashlib.sha256(raw).hexdigest()
            if row.get('kind') == kind and (not digest or digest == actual):
                found = {'sha256':actual,'at':row['at'],'kind':kind}
    if found is None:
        raise ValueError('no exact pre-reboot launch event for this unit')
    return found


def _scope(directory: Path, request: dict) -> str:
    task = json.loads(_bytes(directory.parents[1]/'task.json'))
    if not isinstance(task,dict) or not isinstance(request,dict):
        raise ValueError('recovery metadata must be objects')
    scope = str(request.get('scope') or task.get('scope') or 'self')
    if (scope not in {'self','host'} or task.get('scope','self') not in {'self','host'}
            or request.get('device_id') or task.get('device_id')):
        raise ValueError('device or foreign-host custody cannot use a local reboot proof')
    if request.get('remote') and scope != 'host':
        raise ValueError('remote execution needs its own bound host proof')
    return scope


def read_recovery(directory: Path) -> dict | None:
    """Validate a previously issued proof against its unchanged subject/history."""
    directory = Path(directory)
    try:
        if any(p.is_symlink() for p in (directory,directory.parent,directory.parents[1])):
            return None
        proof = json.loads(_bytes(directory/FILE))
        if not isinstance(proof,dict):
            return None
        if proof.get('schema') != SCHEMA or proof.get('unit_id') != directory.name:
            return None
        if proof.get('task_id') != directory.parents[1].name or proof.get('unit_kind') != directory.parent.name:
            return None
        if not proof.get('actor') or not proof.get('reason') or proof.get('host_binding') != 'verified-local-execution-host':
            return None
        if proof.get('subjects') != _subjects(directory):
            return None
        request = json.loads(_bytes(directory/'request.json'))
        if proof.get('scope') != _scope(directory, request):
            return None
        task = json.loads(_bytes(directory.parents[1]/'task.json'))
        if proof.get('task_root') != str(task.get('root') or ''):
            return None
        boundary = proof['kernel_boundary'];current = kernel_boundary()
        if (not boundary.get('host_id') or current.get('host_id') != boundary['host_id']
                or int(current['boot_time']) < int(boundary['boot_time'])
                or (int(current['boot_time']) == int(boundary['boot_time'])
                    and current['boot_id'] != boundary['boot_id'])):
            return None
        if timestamp(request.get('created')) >= int(boundary['boot_time']):
            return None
        launch = _launch(directory, int(boundary['boot_time']), proof['launch']['sha256'])
        if launch != proof['launch']:
            return None
        return proof
    except (OSError, ValueError, TypeError, KeyError, StopIteration, AttributeError):
        return None


def reconcile_unit(forge, task_id: str, kind: str, unit_id: str, *, actor: str,
                   reason: str, host_binding: str) -> dict:
    """Operator migration, called on the verified execution host, never a timer.

    host_binding is an explicit operator attestation: old self/host jobs used
    this same kernel host. A timestamp or absent PID alone is not sufficient;
    the canonical launch event and unchanged unit subject are also bound.
    """
    if kind not in KINDS or not actor.strip() or not reason.strip():
        raise ValueError('explicit actor, reason and supported unit kind required')
    if host_binding != 'verified-local-execution-host':
        raise ValueError('independent local execution-host binding required')
    directory = forge._unit_dir(task_id,kind,unit_id)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError('recovery subject cannot traverse a symlink')
    with forge._mutation_lock(task_id):
        if (directory/FILE).exists():
            existing = read_recovery(directory)
            if existing is None:
                raise ValueError('existing recovery proof is no longer valid')
            return existing
        request = json.loads(_bytes(directory/'request.json'))
        if request.get('id') != unit_id or request.get('task_id') != task_id:
            raise ValueError('unit request identity mismatch')
        scope = _scope(directory,request);boundary = kernel_boundary()
        if timestamp(request.get('created')) >= boundary['boot_time']:
            raise ValueError('unit does not predate the witnessed reboot')
        # A new bound writer must be treated as live even if its creation text
        # was inherited from an older queued request.
        pid = int(request.get('supervisor_pid') or 0)
        born = str(request.get('supervisor_started_at') or '')
        if pid and born and forge._pid_alive(pid,born):
            raise ValueError('bound supervisor is alive; reboot recovery refused')
        launch = _launch(directory,boundary['boot_time'])
        proof = {'schema':SCHEMA,'task_id':task_id,'unit_kind':kind,'unit_id':unit_id,
                 'task_root':str((forge.get(task_id) or {}).get('root') or ''),
                 'subjects':_subjects(directory),'kernel_boundary':boundary,'launch':launch,
                 'scope':scope,'host_binding':host_binding,'actor':actor,'reason':reason,
                 'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                 'outcome':'old execution boundary extinct; historical outcome retained'}
        forge._atomic_json(directory/FILE,proof)
        forge._event(task_id,'custody_recovered',unit_id=unit_id,unit_kind=kind,
                     recovery_sha256=hashlib.sha256(_bytes(directory/FILE)).hexdigest(),
                     actor=actor,summary=proof['outcome'])
        return proof
