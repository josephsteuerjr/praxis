"""Validate explicit custody recovery without relabelling historical outcomes.

Original request/result/Stop bytes stay untouched. This proves extinction of an
old execution boundary, never success or a clean Stop. Windows named-Job and
prepared reboot proofs live in the shipped companion. Remote-device executions
and executions without an independent local boundary proof remain unknown.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
from functools import lru_cache
from pathlib import Path
import sys

SCHEMA = 'praxis.forge.reboot-recovery.v1'
FILE = 'custody-recovery.json'
KINDS = {'agents':('agent_spawned','agent_id'),
         'processes':('process_started','process_id'),
         'verifications':('verification_started','verification_id')}
TERMINAL = {'done','error','failed','stalled','stopped','timed_out','passed','passed_with_skips','cancelled'}
HISTORY_WITNESS = 'custody-history-witness.json'
HISTORY_SNAPSHOT = 'custody-history.jsonl'
HISTORY_PROOF = 'custody-history-recovery.json'
HISTORY_SCHEMA = 'helene.forge.history-reboot-recovery.v1'
WITNESS_SCHEMA = 'helene.forge.history-reboot-witness.v1'


class HistoryRefusal(ValueError):
    def __init__(self, code: str, **evidence):
        self.diagnostic = {'code': code, **evidence}
        super().__init__(code)


def _rows(raw: bytes, *, offset=0, line=1):
    """Strict evidence reader. An unknown record cannot be a negative census."""
    digest = hashlib.sha256(raw).hexdigest()
    identities = {kind:identity for kind,identity in KINDS.values()}
    identities.update(agent_dequeued='agent_id',agent_resumed='agent_id')
    for part in raw.splitlines(keepends=True):
        try:
            row = json.loads(part.decode('utf-8'))
            if not isinstance(row, dict):
                raise ValueError('object required')
            if not isinstance(row.get('kind'),str) or not row['kind']:
                raise ValueError('event kind required')
            timestamp(row.get('at'))
            identity = identities.get(row['kind'])
            if identity and (not isinstance(row.get(identity),str) or not row[identity]):
                raise ValueError('execution identity required')
        except (ValueError, UnicodeError):
            raise HistoryRefusal('forge_history_corrupt', line=line, offset=offset,
                record_size=len(part), record_sha256=hashlib.sha256(part).hexdigest(),
                segment_sha256=digest) from None
        yield part.rstrip(b'\r\n'), row
        line += 1
        offset += len(part)


def history_diagnostic(directory: Path) -> dict | None:
    try:
        directory = Path(directory)
        raw = _bytes(directory.parents[1]/'events.jsonl')
        if (directory/HISTORY_PROOF).exists():
            try:
                _read_history_recovery(directory)
                return None
            except HistoryRefusal:
                raise
            except (OSError,ValueError,TypeError,KeyError,StopIteration,AttributeError):
                return {'code':'forge_history_proof_changed','journal_sha256':hashlib.sha256(raw).hexdigest()}
        list(_rows(raw))
    except HistoryRefusal as error:
        return {**error.diagnostic, 'journal_sha256': hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError):
        return {'code': 'forge_history_unreadable'}
    return None


@lru_cache(maxsize=1)
def windows_custody():
    # Desk loads this validator by absolute path. Load its companion from the
    # same shipped tree, never a mutable profile module on ambient sys.path.
    path = Path(__file__).with_name('forge_windows_custody.py')
    spec = importlib.util.spec_from_file_location('_helene_windows_custody_validator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    for raw, row in _rows(_bytes(directory.parents[1]/'events.jsonl')):
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
    if request.get('scope') and request['scope'] != task.get('scope','self'):
        raise ValueError('request scope contradicts owning task')
    # Historical local process requests recorded cwd, not root. Accept only an
    # exact owning-root cwd for that format; never infer a host/device binding
    # from an arbitrary descendant path or a missing execution directory.
    legacy_local_process = (directory.parent.name == 'processes' and 'root' not in request
                            and scope == 'self' and not request.get('remote')
                            and request.get('task_id') == directory.parents[1].name
                            and bool(task.get('root')) and request.get('cwd') == task['root'])
    if request.get('root') != task.get('root') and not legacy_local_process:
        raise ValueError('request root contradicts owning task')
    if request.get('remote') and scope != 'host':
        raise ValueError('remote execution needs its own bound host proof')
    return scope


def read_recovery(directory: Path) -> dict | None:
    """Validate a previously issued proof against its unchanged subject/history."""
    directory = Path(directory)
    try:
        if (directory/HISTORY_PROOF).exists():
            return _read_history_recovery(directory)
        if any(p.is_symlink() for p in (directory,directory.parent,directory.parents[1])):
            return None
        proof = json.loads(_bytes(directory/FILE))
        if not isinstance(proof,dict):
            return None
        if proof.get('schema') in {'helene.forge.windows-job-recovery.v1', 'helene.forge.windows-reboot-recovery.v1'}:
            return windows_custody().read_recovery(directory)
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


def _history_subjects(directory: Path) -> dict:
    return {name: hashlib.sha256(_bytes(directory/name)).hexdigest()
            if (directory/name).exists() else '' for name in
            ('request.json','result.json','state.json','stop.json','checkpoint.json','progress.json','events.jsonl',FILE)}


def _history_identity(directory: Path) -> dict:
    if any(p.is_symlink() for p in (directory,directory.parent,directory.parents[1])):
        raise HistoryRefusal('forge_history_unsafe_path')
    request = json.loads(_bytes(directory/'request.json'))
    if request.get('id') != directory.name or request.get('task_id') != directory.parents[1].name:
        raise HistoryRefusal('forge_history_unit_changed')
    task = json.loads(_bytes(directory.parents[1]/'task.json'))
    return {'task_id': directory.parents[1].name, 'unit_kind': directory.parent.name,
            'unit_id': directory.name, 'task_root': str(task.get('root') or ''),
            'scope': _scope(directory, request)}


def _history_witness(directory: Path) -> tuple[dict, bytes]:
    witness = json.loads(_bytes(directory/HISTORY_WITNESS))
    if (witness.get('schema') != WITNESS_SCHEMA
            or any(witness.get(k) != v for k,v in _history_identity(directory).items())
            or witness.get('subjects') != _history_subjects(directory)
            or witness.get('host_binding') != 'verified-local-execution-host'
            or not witness.get('actor') or not witness.get('reason')):
        raise HistoryRefusal('forge_history_witness_changed')
    snapshot = _bytes(directory/HISTORY_SNAPSHOT)
    if (len(snapshot) != witness['journal']['size']
            or hashlib.sha256(snapshot).hexdigest() != witness['journal']['sha256']):
        raise HistoryRefusal('forge_history_snapshot_changed')
    return witness, snapshot


def _history_tail(directory: Path, snapshot: bytes, boundary: dict) -> None:
    journal = _bytes(directory.parents[1]/'events.jsonl')
    if not journal.startswith(snapshot):
        raise HistoryRefusal('forge_history_prefix_changed')
    kind, identity = KINDS[directory.parent.name]
    # Only the exact pre-reboot snapshot is covered by the witnessed boundary.
    # Every later byte must be readable; a hidden relaunch still blocks custody.
    line = len(snapshot.splitlines()) + int(not snapshot or snapshot.endswith((b'\n',b'\r')))
    for _, row in _rows(journal[len(snapshot):], offset=len(snapshot), line=line):
        if (row.get(identity) == directory.name
                and row.get('kind') in {kind,'agent_dequeued','agent_resumed'}
                and timestamp(row.get('at')) >= int(boundary['boot_time'])):
            raise HistoryRefusal('forge_history_new_execution')


def _later_boundary(old: dict, observed: dict) -> None:
    if (not old.get('host_id') or old['host_id'] != observed.get('host_id')
            or not old.get('boot_id') or old['boot_id'] == observed.get('boot_id')
            or int(observed['boot_time']) <= int(old['boot_time'])):
        raise HistoryRefusal('forge_history_reboot_required')


def _read_history_recovery(directory: Path) -> dict | None:
    witness, snapshot = _history_witness(directory)
    proof = json.loads(_bytes(directory/HISTORY_PROOF))
    if (proof.get('schema') != HISTORY_SCHEMA
            or any(proof.get(k) != v for k,v in _history_identity(directory).items())
            or proof.get('witness_sha256') != hashlib.sha256(_bytes(directory/HISTORY_WITNESS)).hexdigest()
            or proof.get('subjects') != _history_subjects(directory)
            or proof.get('outcome') != 'Witnessed Linux reboot ended old process tree; historical outcome retained'):
        raise HistoryRefusal('forge_history_proof_changed')
    observed = proof['kernel_boundary']; current = kernel_boundary()
    _later_boundary(witness['kernel_boundary'], observed)
    if (observed['host_id'] != current['host_id'] or int(current['boot_time']) < int(observed['boot_time'])
            or (int(current['boot_time']) == int(observed['boot_time']) and current['boot_id'] != observed['boot_id'])):
        raise HistoryRefusal('forge_history_host_changed')
    _history_tail(directory,snapshot,observed)
    return proof


def _history_plan(forge, directory: Path, action: str, boundary: dict, host_binding: str, reason: str):
    identity = _history_identity(directory)
    request = json.loads(_bytes(directory/'request.json'))
    pid = int(request.get('supervisor_pid') or 0); born = str(request.get('supervisor_started_at') or '')
    if pid and (not born or forge._pid_alive(pid,born)):
        raise HistoryRefusal('forge_history_stop_required')
    if (directory/HISTORY_PROOF).exists():
        raise HistoryRefusal('forge_history_invalid_existing_proof')
    if action == 'prepare_recovery':
        if host_binding != 'verified-local-execution-host' or not reason.strip():
            raise HistoryRefusal('forge_history_host_confirmation_required')
        if (directory/HISTORY_WITNESS).exists():
            previous,_ = _history_witness(directory)  # Never reset the witnessed boot.
            if previous['kernel_boundary']['host_id'] != boundary['host_id']:
                raise HistoryRefusal('forge_history_host_changed')
            return None
        stop = forge._read_json(directory/'stop.json',{}) or {}
        if stop.get('state') not in {'pending','unresolved'} and not (directory/'stop-request.json').exists():
            raise HistoryRefusal('forge_history_stop_required')
        snapshot = _bytes(directory.parents[1]/'events.jsonl')
        value = {**identity, 'schema': WITNESS_SCHEMA, 'kernel_boundary': boundary,
                 'subjects': _history_subjects(directory), 'actor': 'coding_control.prepare_recovery',
                 'host_binding': host_binding, 'reason': reason,
                 'journal': {'size':len(snapshot),'sha256':hashlib.sha256(snapshot).hexdigest()},
                 'diagnostic':history_diagnostic(directory),
                 'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        return HISTORY_WITNESS,value,snapshot
    if not (directory/HISTORY_WITNESS).exists():
        raise HistoryRefusal('forge_history_witness_required', diagnostic=history_diagnostic(directory))
    witness,snapshot = _history_witness(directory)
    _later_boundary(witness['kernel_boundary'],boundary)
    _history_tail(directory,snapshot,boundary)
    value = {**identity, 'schema': HISTORY_SCHEMA, 'kernel_boundary':boundary,
             'witness_sha256':hashlib.sha256(_bytes(directory/HISTORY_WITNESS)).hexdigest(),
             'subjects':_history_subjects(directory),
             'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
             'outcome':'Witnessed Linux reboot ended old process tree; historical outcome retained'}
    return HISTORY_PROOF,value,None


def recovery_control(forge, task_id: str, action: str, agent_id: str, expected_generation: str,
                     *, host_binding: str, reason: str) -> dict:
    """Witness an opaque history now; require an actual later reboot of this host.

    No journal line is deleted, reinterpreted or silently skipped. This releases
    process custody only; unknown actions and unsafe continuations stay unknown.
    """
    import frame_language
    try:
        if action not in {'prepare_recovery','recover'}:
            raise HistoryRefusal('forge_history_invalid_action')
        boundary = kernel_boundary()
        with forge._mutation_lock(task_id):
            if not forge.get(task_id):
                raise HistoryRefusal('forge_history_task_missing')
            if agent_id:
                directory = forge._unit_dir(task_id,'agents',agent_id)
                if not (directory/'request.json').is_file():
                    raise HistoryRefusal('forge_history_unit_changed')
                progress = forge._read_json(directory/'progress.json',{}) or {}
                if expected_generation and progress.get('generation') != expected_generation:
                    raise HistoryRefusal('forge_history_generation_changed')
            selected = []
            for kind in sorted(KINDS):
                for row in forge._units(task_id,kind):
                    owner = str(row['id'] if kind == 'agents' else row.get('parent_unit_id') or '')
                    if agent_id and (not owner or not forge._in_subtree(task_id,agent_id,owner)):
                        continue
                    if row.get('custody_recovered'):
                        continue
                    directory = forge._unit_dir(task_id,kind,row['id'])
                    if (directory/HISTORY_WITNESS).exists() or row.get('status') in {'unknown','lost','stop_pending'}:
                        selected.append(directory)
                    elif row.get('status') in {'running','starting','finishing','queued'}:
                        raise HistoryRefusal('forge_history_stop_required')
            plans = [(d,_history_plan(forge,d,action,boundary,host_binding,reason)) for d in selected]
            # Validate every selected unit before publishing any witness/proof.
            for directory,plan in plans:
                if plan is None:
                    continue
                filename,value,snapshot = plan
                if snapshot is not None:
                    path = directory/HISTORY_SNAPSHOT
                    if path.exists():
                        if _bytes(path) != snapshot:
                            raise HistoryRefusal('forge_history_snapshot_changed')
                    else:
                        with path.open('xb') as stream:
                            stream.write(snapshot);stream.flush();os.fsync(stream.fileno())
                forge._atomic_json(directory/filename,value)
                with (directory/filename).open('r+b') as stream:
                    os.fsync(stream.fileno())
                forge._event(task_id,'custody_history_prepared' if filename == HISTORY_WITNESS else 'custody_recovered',
                    unit_id=directory.name,unit_kind=directory.parent.name,
                    recovery_sha256=hashlib.sha256(_bytes(directory/filename)).hexdigest())
            return {'status':'prepared' if action == 'prepare_recovery' and selected else 'settled',
                    'action':action,'task_id':task_id,'agent_id':agent_id,'units':[d.name for d in selected],
                    'reason':frame_language.text('forge.history.none' if not selected else
                        'forge.history.prepared' if action == 'prepare_recovery' else 'forge.history.recovered')}
    except (OSError,ValueError,TypeError,KeyError,StopIteration,AttributeError) as error:
        return {'status':'refused','action':action,'task_id':task_id,
                'diagnostic':getattr(error,'diagnostic',{'code':'forge_history_unavailable'}),
                'reason':frame_language.text('forge.history.refused')}


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
