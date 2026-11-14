"""Durable Windows Job custody for local Forge units, bound before admission.

A missing leader or an unlocked journal never proves descendant extinction.
Only the exact non-breakaway Job's empty census (or destruction) does. Original
unit history is retained; recovery settles custody, not the unknown outcome.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

BINDING = 'windows-job.json'
FILE = 'custody-recovery.json'
SCHEMA = 'helene.forge.windows-job-recovery.v1'
LAUNCH_SCHEMA = 'helene.forge.windows-job-launch.v1'
WITNESS = 'custody-reboot-witness.json'
WITNESS_SCHEMA = 'helene.forge.windows-reboot-witness.v1'
REBOOT_SCHEMA = 'helene.forge.windows-reboot-recovery.v1'
KINDS = {'agents', 'processes', 'verifications'}
SUBJECTS = ('request.json', 'result.json', 'state.json', 'stop.json',
            'stop-request.json', 'checkpoint.json', 'progress.json', 'events.jsonl')


def host_identity() -> str:
    if os.name != 'nt':
        raise ValueError('Windows execution host required')
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        machine = str(winreg.QueryValueEx(key, 'MachineGuid')[0]).strip()
    if not machine:
        raise ValueError('Windows machine identity unavailable')
    return hashlib.sha256(machine.encode()).hexdigest()


def new_job_name() -> str:
    return 'Global\\Helene.Forge.' + uuid.uuid4().hex


def _bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError('custody subject cannot be a symlink')
    return path.read_bytes()


def _json(path: Path) -> dict:
    value = json.loads(_bytes(path))
    if not isinstance(value, dict):
        raise ValueError('custody metadata must be an object')
    return value


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _request_identity(request: dict) -> str:
    # Fields published after suspended launch, never assignment semantics.
    stable = {k: v for k, v in request.items() if k not in {
        'supervisor_pid', 'supervisor_started_at', 'process_identity',
        'command_identity', 'status', '_verified_request_path'}}
    return _sha(json.dumps(stable, sort_keys=True, ensure_ascii=False,
                           separators=(',', ':')).encode())


def _identity(directory: Path) -> dict:
    if directory.parent.name not in KINDS or any(p.is_symlink() for p in
            (directory, directory.parent, directory.parents[1])):
        raise ValueError('invalid local custody directory')
    request = _json(directory/'request.json')
    task = _json(directory.parents[1]/'task.json')
    if request.get('id') != directory.name or request.get('task_id') != directory.parents[1].name:
        raise ValueError('custody request identity mismatch')
    if (task.get('scope', 'self') != 'self' or request.get('scope', 'self') != 'self'
            or task.get('device_id') or request.get('device_id') or request.get('remote')):
        raise ValueError('local Job cannot prove remote or device execution ended')
    root = str(task.get('root') or '')
    if not root:
        raise ValueError('missing task root')
    if 'root' in request:
        if request['root'] != root:
            raise ValueError('custody task root mismatch')
    elif directory.parent.name == 'processes' and request.get('cwd'):
        if not Path(request['cwd']).resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('command is outside owning root')
    else:
        raise ValueError('missing execution root')
    return {'task_id': directory.parents[1].name, 'unit_id': directory.name,
            'unit_kind': directory.parent.name, 'task_root': root,
            'unit_directory': str(directory.resolve()),
            'request_sha256': _request_identity(request), 'host_id': host_identity()}


def bind(forge, directory: Path, process) -> dict:
    """Persist exact ownership while the initial thread is still suspended."""
    directory = Path(directory)
    if (directory/BINDING).exists():
        raise ValueError('unit already has a Windows launch binding')
    identity = process.custody_identity()
    name = identity.get('job_name', '')
    if identity.get('kind') != 'windows-job' or not re.fullmatch(r'Global\\Helene\.Forge\.[0-9a-f]{32}', name):
        raise ValueError('exact named Windows Job required')
    born = forge._proc_started_at(process.pid)
    if not born:
        raise ValueError('suspended supervisor birth unavailable')
    value = {**_identity(directory), 'schema': LAUNCH_SCHEMA,
             'job_name': name, 'pid': process.pid, 'started_at': born,
             'bound_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    forge._atomic_json(directory/BINDING, value)
    # Flush before resume: a durable request must never outlive its binding.
    with (directory/BINDING).open('r+b') as stream:
        os.fsync(stream.fileno())
    return value


def _binding(directory: Path) -> dict:
    value = _json(directory/BINDING)
    if value.get('schema') != LAUNCH_SCHEMA or any(value.get(k) != v for k, v in _identity(directory).items()):
        raise ValueError('Windows launch binding changed or belongs to another host')
    if not re.fullmatch(r'Global\\Helene\.Forge\.[0-9a-f]{32}', str(value.get('job_name') or '')):
        raise ValueError('invalid Windows Job identity')
    if type(value.get('pid')) is not int or value['pid'] <= 0 or not value.get('started_at'):
        raise ValueError('invalid launch process identity')
    request = _json(directory/'request.json')
    if (request.get('supervisor_pid') not in (None, value['pid'])
            or request.get('supervisor_started_at') not in (None, '', value['started_at'])):
        raise ValueError('supervisor identity changed after launch')
    return value


def _subjects(directory: Path) -> dict:
    return {name: _sha(_bytes(directory/name)) if (directory/name).exists() else ''
            for name in SUBJECTS}


def read_recovery(directory: Path) -> dict | None:
    """Pure read; never signal a Job or write a receipt during UI rendering."""
    directory = Path(directory)
    try:
        proof = _json(directory/FILE)
        if proof.get('schema') == REBOOT_SCHEMA:
            return read_reboot_recovery(directory)
        binding = _binding(directory)
        if (proof.get('schema') != SCHEMA or proof.get('binding_sha256') != _sha(_bytes(directory/BINDING))
                or any(proof.get(k) != binding[k] for k in _identity(directory))
                or proof.get('subjects') != _subjects(directory)
                or proof.get('evidence') not in {'job_absent', 'job_empty'}
                or not proof.get('actor') or not proof.get('observed_at')
                or proof.get('outcome') != 'Windows process tree extinct; historical outcome retained'):
            return None
        return proof
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


@lru_cache(maxsize=1)
def boot_boundary() -> dict:
    """A process cannot survive a Windows reboot; cache this WMI fact per process.

    GetTickCount/psutil.boot_time estimates shift with wall-clock corrections.
    Win32_OperatingSystem.LastBootUpTime is the recorded boot timestamp instead.
    """
    host = host_identity()
    shell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = ("$ErrorActionPreference='Stop'; "
               "(Get-CimInstance -ClassName Win32_OperatingSystem).LastBootUpTime."
               "ToUniversalTime().ToString('o',[Globalization.CultureInfo]::InvariantCulture)")
    result = subprocess.run([str(shell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    value = dt.datetime.fromisoformat(result.stdout.strip().replace('Z', '+00:00'))
    if value.tzinfo is None or value.timestamp() <= 0:
        raise ValueError('Windows boot boundary unavailable')
    recorded = value.astimezone(dt.timezone.utc).isoformat()
    return {'host_id': host, 'boot_id': _sha(recorded.encode()), 'boot_time': value.timestamp()}


def _witness(directory: Path) -> dict:
    value = _json(directory/WITNESS)
    if (value.get('schema') != WITNESS_SCHEMA
            or any(value.get(k) != v for k, v in _identity(directory).items())
            or value.get('subjects') != _subjects(directory)
            or value.get('host_binding') != 'verified-local-execution-host'
            or not value.get('actor') or not value.get('reason')
            or value.get('kernel_boundary', {}).get('host_id') != value['host_id']
            or not value.get('kernel_boundary', {}).get('boot_id')):
        raise ValueError('reboot witness changed or does not bind this execution host and history')
    return value


def read_reboot_recovery(directory: Path) -> dict | None:
    try:
        witness = _witness(directory)
        proof = _json(directory/FILE)
        current = boot_boundary(); previous = witness['kernel_boundary']; observed = proof['kernel_boundary']
        if (proof.get('schema') != REBOOT_SCHEMA
                or proof.get('witness_sha256') != _sha(_bytes(directory/WITNESS))
                or proof.get('subjects') != _subjects(directory)
                or any(proof.get(k) != v for k, v in _identity(directory).items())
                or current['host_id'] != previous['host_id'] or observed['host_id'] != previous['host_id']
                or observed['boot_id'] == previous['boot_id']
                or current['boot_time'] < observed['boot_time']
                or (current['boot_time'] == observed['boot_time'] and current['boot_id'] != observed['boot_id'])
                or proof.get('outcome') != 'Witnessed Windows reboot ended old process tree; historical outcome retained'):
            return None
        return proof
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return None


def recovery_control(forge, task_id: str, action: str, agent_id: str, expected_generation: str,
                     *, host_binding: str, reason: str) -> dict:
    """Explicit legacy migration: witness now, prove a later reboot, retain bytes.

    Merely finding an old launch timestamp or absent PID cannot replace a
    pre-reboot witness on the independently confirmed original execution host.
    """
    try:
        if os.name != 'nt':
            raise ValueError('legacy Windows recovery must run on its original Windows host')
        with forge._mutation_lock(task_id):
            task = forge.get(task_id)
            if not task:
                raise ValueError('task missing')
            if agent_id:
                directory = forge._unit_dir(task_id, 'agents', agent_id)
                if not (directory/'request.json').is_file():
                    raise ValueError('selected participant is missing')
                progress = forge._read_json(directory/'progress.json', {}) or {}
                if expected_generation and progress.get('generation') != expected_generation:
                    raise ValueError('generation changed')
            selected = []
            for kind in sorted(KINDS):
                for row in forge._units(task_id, kind):
                    owner = str(row['id'] if kind == 'agents' else row.get('parent_unit_id') or '')
                    if agent_id and (not owner or not forge._in_subtree(task_id, agent_id, owner)):
                        continue
                    if row.get('custody_recovered'):
                        continue
                    d = forge._unit_dir(task_id, kind, row['id'])
                    if (d/WITNESS).exists() or row.get('status') in {'unknown', 'lost'}:
                        selected.append(d)
                    elif row.get('status') in {'running', 'starting', 'finishing', 'queued'}:
                        raise ValueError('Stop the selected branch first; live or queued work remains')
            if not selected:
                return {'status': 'settled', 'action': action, 'task_id': task_id,
                        'agent_id': agent_id, 'recovered_units': [], 'reason': 'no unresolved units'}
            boundary = boot_boundary()
            prepared = []
            for d in selected:
                identity = _identity(d)
                request = _json(d/'request.json')
                pid = int(request.get('supervisor_pid') or 0)
                born = str(request.get('supervisor_started_at') or '')
                if pid and (not born or forge._pid_alive(pid, born)):
                    raise ValueError(f'{d.name}: supervisor exit or identity is unconfirmed; use Stop first')
                if (d/BINDING).exists():
                    raise ValueError(f'{d.name}: named Job exists; use ordinary Stop')
                if (d/FILE).exists():
                    raise ValueError(f'{d.name}: invalid existing recovery proof; history retained')
                if action == 'prepare_recovery':
                    if host_binding != 'verified-local-execution-host' or not reason.strip():
                        raise ValueError('confirm the original execution host and state a recovery reason')
                    if (d/WITNESS).exists():
                        _witness(d)  # idempotent preparation never resets the old boot boundary
                        continue
                    stop = forge._read_json(d/'stop.json', {}) or {}
                    if stop.get('state') not in {'pending', 'unresolved'} and not (d/'stop-request.json').exists():
                        raise ValueError(f'{d.name}: use Stop before preparing recovery')
                    value = {**identity, 'schema': WITNESS_SCHEMA, 'kernel_boundary': boundary,
                             'subjects': _subjects(d), 'actor': 'coding_control.prepare_recovery',
                             'host_binding': host_binding, 'reason': reason,
                             'observed_at': dt.datetime.now(dt.timezone.utc).isoformat()}
                    prepared.append((d, WITNESS, value))
                else:
                    witness = _witness(d)
                    old = witness['kernel_boundary']
                    if boundary['host_id'] != old['host_id'] or boundary['boot_id'] == old['boot_id']:
                        raise ValueError('Windows has not restarted since preparation; custody retained')
                    if boundary['boot_time'] <= old['boot_time']:
                        raise ValueError('Windows boot chronology is inconsistent; custody retained')
                    value = {**identity, 'schema': REBOOT_SCHEMA, 'kernel_boundary': boundary,
                             'witness_sha256': _sha(_bytes(d/WITNESS)), 'subjects': _subjects(d),
                             'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                             'outcome': 'Witnessed Windows reboot ended old process tree; historical outcome retained'}
                    prepared.append((d, FILE, value))
            # Validate the entire requested branch before any recovery write.
            for d, filename, value in prepared:
                forge._atomic_json(d/filename, value)
                with (d/filename).open('r+b') as stream:
                    os.fsync(stream.fileno())
                forge._event(task_id, 'custody_recovery_prepared' if filename == WITNESS else 'custody_recovered',
                             unit_id=d.name, unit_kind=d.parent.name,
                             recovery_sha256=_sha(_bytes(d/filename)))
            return {'status': 'prepared' if action == 'prepare_recovery' else 'settled',
                    'action': action, 'task_id': task_id, 'agent_id': agent_id,
                    'units': [d.name for d in selected],
                    'reason': ('Witness retained. Restart this Windows computer yourself, then verify recovery.'
                               if action == 'prepare_recovery' else
                               'Old process trees are extinct; historical outcomes and unsafe continuations are retained.')}
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        return {'status': 'refused', 'action': action, 'task_id': task_id,
                'agent_id': agent_id, 'reason': str(exc)}
def stop_job(name: str, timeout: float = 5.0) -> str:
    """No PID fallback. Access denial and unreadable census remain unknown."""
    from command_process import WindowsJob
    try:
        job = WindowsJob(name, open_existing=True, terminate=True)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 2:  # ERROR_FILE_NOT_FOUND only
            return 'job_absent'
        raise
    try:
        live = job.live()
        if live is None:
            raise OSError('Windows Job census unavailable')
        if live:
            job.stop()
        deadline = time.monotonic() + timeout
        while job.live() is not False:
            if time.monotonic() >= deadline:
                raise OSError('Windows Job tree exit unconfirmed')
            time.sleep(.025)
        return 'job_empty'
    finally:
        job.close()


def reconcile(forge, directory: Path, *, actor: str = 'coding_control.stop') -> dict:
    directory = Path(directory)
    with forge._mutation_lock(directory.parents[1].name):
        if (directory/FILE).exists():
            proof = read_recovery(directory)
            if not proof:
                raise ValueError('existing recovery proof no longer validates')
            return proof
        binding = _binding(directory)
        evidence = stop_job(binding['job_name'])
        # No managed process remains able to mutate unit history. A competing
        # coordinator is serialized by the same task admission lock.
        if binding != _binding(directory):
            raise ValueError('launch binding changed during Stop')
        proof = {**_identity(directory), 'schema': SCHEMA, 'actor': actor,
                 'binding_sha256': _sha(_bytes(directory/BINDING)),
                 'subjects': _subjects(directory), 'evidence': evidence,
                 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'outcome': 'Windows process tree extinct; historical outcome retained'}
        forge._atomic_json(directory/FILE, proof)
        forge._event(directory.parents[1].name, 'custody_recovered', unit_id=directory.name,
                     unit_kind=directory.parent.name, recovery_sha256=_sha(_bytes(directory/FILE)),
                     actor=actor, summary=proof['outcome'])
        return proof
