"""Process-owner observations for the existing Forge unit journals.

Readers do not probe processes or infer life/death from a heartbeat. The Forge
coordinator publishes dated observations, explicitly unknown on identity/probe
failure. These are presentation facts only, NEVER custody or Stop authority.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import time

_last: dict[str, float] = {}


def capture(pid: int) -> dict:
    """Pin the process birth using the installed cross-platform OS adapter."""
    try:
        import psutil
        process = psutil.Process(int(pid))
        return {'pid': int(pid), 'birth': process.create_time(), 'adapter': 'psutil-v1'}
    except Exception as exc:
        # psutil's platform errors are not all OSError subclasses. They mean
        # missing evidence, never a synthetic birth or an inference of death.
        return {'pid': int(pid), 'adapter': 'unknown', 'error_class': type(exc).__name__}


def observe(directory: Path, request: dict, write) -> dict:
    directory = Path(directory)
    pid = request.get('supervisor_pid')
    identity = request.get('process_identity')
    state, basis = 'unknown', 'process_identity_unavailable'
    if request.get('status') == 'queued' and not pid:
        state, basis = 'not_started', 'queued_before_process_admission'
    elif (isinstance(identity, dict) and identity.get('adapter') == 'psutil-v1'
          and identity.get('pid') == pid and isinstance(identity.get('birth'), (float, int))):
        try:
            import psutil
            process = psutil.Process(int(pid))
            if process.create_time() != identity['birth']:
                state, basis = 'exited', 'pid_reused'
            elif process.status() == psutil.STATUS_ZOMBIE:
                state, basis = 'exited', 'zombie'
            elif process.is_running():
                state, basis = 'alive', 'exact_pid_and_birth'
            else:
                state, basis = 'exited', 'process_not_running'
        except ImportError:
            basis = 'process_adapter_unavailable'
        except Exception as exc:
            if type(exc).__name__ == 'NoSuchProcess':
                state, basis = 'exited', 'exact_process_absent'
            else:
                basis = 'probe_' + type(exc).__name__
    row = {'schema': 1, 'task_id': request.get('task_id') or directory.parent.parent.name,
           'unit_id': request.get('id') or directory.name, 'supervisor_pid': pid,
           'process_identity': identity, 'state': state, 'basis': basis,
           'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
           'observer_pid': os.getpid()}
    write(directory / 'liveness.json', row)
    return row


def refresh(tasks_root: Path, write, current: dict, interval: float = 2.5) -> set:
    """Observe only unfinished identities from bounded admission discovery.

    Discovery includes old unfinished units after restart, not a recent-task
    window. A completed historical result is never a reason to probe/rewrite it.
    Already observed live processes remain tracked across result publication until
    an exact exit is seen. Missing birth stays unknown, never synthetic death.
    """
    root = Path(tasks_root)
    key, now = str(root.resolve()), time.monotonic()
    if now - _last.get(key, -float('inf')) < interval:
        return set()
    _last[key] = now
    retired = set()
    for directory in current:
        try:
            request = json.loads((directory / 'request.json').read_bytes())
            if not isinstance(request, dict):
                continue
            terminal = (directory / 'result.json').is_file()
            prior_path = directory / 'liveness.json'
            prior = json.loads(prior_path.read_bytes()) if prior_path.is_file() else {}
            if (terminal and prior.get('state') in {'exited', 'not_started'}
                    and prior.get('supervisor_pid') == request.get('supervisor_pid')
                    and prior.get('process_identity') == request.get('process_identity')):
                retired.add(directory)
                continue
            row = observe(directory, request, write)
            identity = request.get('process_identity')
            if terminal and (row['state'] in {'exited', 'not_started'} or
                    not isinstance(identity, dict) or identity.get('adapter') != 'psutil-v1'):
                # Legacy missing identity can never become provable by polling.
                # Preserve the dated unknown, not recurring history writes.
                retired.add(directory)
        except FileNotFoundError:
            if not directory.is_dir():
                retired.add(directory)
        except (OSError, ValueError, TypeError):
            # Keep the last observation; unreadable is not absence or exit.
            continue
    return retired
