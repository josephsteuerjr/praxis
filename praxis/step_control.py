"""Exact-run force cancellation. Only owned process handles are signalled.

The controller writes one durable request per run. A separate reader works
while asyncio or a provider is blocked; outcomes survive in the run ledger.
"""
from __future__ import annotations

import json
import contextlib
import concurrent.futures
import datetime
import os
from pathlib import Path
import re
import secrets
import threading
import time

_ID = re.compile(r'run-[A-Za-z0-9_-]{1,100}\Z')
_registries = {}
_lock = threading.RLock()


class StepCancelled(RuntimeError):
    pass


class FutureHandle:
    """A running native thread is explicitly unconfirmed, never falsely killed."""
    def __init__(self, future, *, cancellable=False, settled=None):
        self.future = future
        self.cancellable = cancellable
        self.settled = settled
        self.pid = 0
        self.kind = 'transport' if cancellable else 'native-call'

    def poll(self):
        if self.settled is not None:
            return 0 if self.settled.is_set() else None
        return 0 if self.future.done() else None

    def stop_tree(self):
        if self.cancellable or not self.future.running():
            self.future.cancel()
        ended = self.poll() is not None
        return (ended, 'call ended' if ended else
                'native call still executing; outcome unconfirmed')


def base_path():
    return Path(os.environ.get('PRAXIS_BASE') or Path(__file__).resolve().parent)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def requested(run_id, base=None):
    if not _ID.fullmatch(str(run_id)):
        return False
    path = Path(base or base_path()) / 'memory/.control/run-cancel' / (run_id + '.json')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return value if isinstance(value, dict) and value.get('run_id') == run_id and value.get('action') == 'cancel' else False


class Registry:
    def __init__(self, base):
        self.base = Path(base).resolve()
        self.handles = {}
        self.stop_jobs = {}
        self.settled = {}
        self.owner = secrets.token_hex(16)
        self.finalized = False
        self.unowned = {}
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._watch, name='run-step-control', daemon=True)
        self.thread.start()

    def track(self, run_id, process):
        if not _ID.fullmatch(str(run_id)):raise ValueError('invalid control run identity')
        with self.lock:
            self.finalized = False
            process.control_handle = secrets.token_hex(12)
            self.unowned.pop(run_id, None)
            with self.resource_lock(run_id):
                _write(self.resource_path(run_id, process), {'owner':self.owner,
                    'handle':process.control_handle,'kind':getattr(process,'kind','process'),
                    'pid':process.pid})
                self.handles.setdefault(run_id, []).append(process)
                receipt = self.base / 'memory/.control/run-cancel' / (run_id + '.receipt')
                with contextlib.suppress(FileNotFoundError):receipt.unlink()
        return process

    def resource_directory(self, run_id):
        return self.base / 'memory/.control/run-resources' / run_id

    def resource_path(self, run_id, process):
        return self.resource_directory(run_id) / (process.control_handle + '.json')

    def resource_lock(self, run_id):
        # Reuse the proven cross-thread/process advisory-lock implementation.
        # Never hold this census lock while acquiring the run's ledger lock.
        from run_manager import RunManager
        return RunManager(self.base)._locked(self.resource_directory(run_id))

    def forget_resource(self, run_id, process):
        path = self.resource_path(run_id, process)
        try:value = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:return
        if value.get('owner') == self.owner and value.get('handle') == process.control_handle:
            path.unlink()

    def pending_resources(self, run_id):
        rows = []
        for path in sorted(self.resource_directory(run_id).glob('*.json')):
            try:value = json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):value = {'handle':path.stem,'state':'unknown'}
            rows.append(value)
        return rows

    def close(self):
        if self.finalized:return
        self.closed.set()
        self.thread.join(6)
        import model_step
        with self.lock:runs = list(self.handles)
        for run_id in runs:model_step.close_run(run_id, self.base)
        self.once()
        self.finalized = True

    def _stop(self, process):
        """Fan out independently; one backend cannot delay another signal."""
        key = process.control_handle
        job = self.stop_jobs.get(key)
        if job is None or (job[0].done() and not job[0].result()[0] and time.monotonic()-job[1] > .2):
            future = concurrent.futures.Future()
            def execute():
                try:future.set_result(process.stop_tree())
                except Exception as error:future.set_result((False, 'stop unconfirmed: '+type(error).__name__))
            self.stop_jobs[key] = (future, time.monotonic())
            threading.Thread(target=execute, name='run-resource-stop', daemon=True).start()
            job = self.stop_jobs[key]
        return job[0]

    def once(self):
        directory = self.base / 'memory/.control/run-cancel'
        for path in directory.glob('run-*.json'):
            run_id = path.stem
            request = requested(run_id, self.base)
            if not request:
                continue
            receipt = directory / (run_id + '.receipt')
            with self.lock:
                handles = list(self.handles.get(run_id, ()))
                known = dict(self.settled.get(run_id, {}))
                if not handles and not known and run_id in self.unowned and self.unowned[run_id] == request.get('id'):
                    continue
            # A late registration after cancellation must also be stopped.
            if receipt.exists() and not handles and not known:
                continue
            try:
                # The durable request already closes dispatch admission. Do
                # not delay OS stop signals behind a busy/conflicting ledger.
                jobs = [(process, self._stop(process)) for process in handles]
                from run_manager import RunManager
                manager = RunManager(self.base)
                manifest = manager.request_cancel(run_id, actor='desk:owner',
                    reason=str(request.get('reason') or 'immediate reset from MiniApp'))
                # Other executors can observe the same durable request. Only
                # the executor holding this run's resource handles may attest
                # their death; an observer cannot acknowledge an empty census.
                if not handles and not known:
                    with self.lock:
                        self.unowned[run_id] = request.get('id')
                    continue
                prior = {}
                try:
                    prior = json.loads(receipt.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
                if prior.get('request_id') != request.get('id'):
                    prior = {}
                observed = {p['handle']: p for p in prior.get('processes', []) if 'handle' in p}
                observed.update(known)
                for process, job in jobs:
                    ok, note = job.result() if job.done() else (False, 'resource stop pending')
                    observed[process.control_handle] = {'handle': process.control_handle,
                        'owner': self.owner,
                        'kind': getattr(process, 'kind', 'process'),
                        'pid': process.pid, 'stopped': ok, 'note': note}
                stopped = list(observed.values())
                value = {'request_id': request.get('id'), 'run_id': run_id,
                    'status': manifest.get('status'), 'processes': stopped,
                    'at': time.time(), 'ok': all(p['stopped'] for p in stopped)}
                changed = any(prior.get(k) != value.get(k) for k in ('request_id', 'status', 'processes', 'ok'))
                with self.lock, self.resource_lock(run_id):
                    local = {p.control_handle:observed[p.control_handle] for p in handles}
                    try:prior = json.loads(receipt.read_text(encoding='utf-8'))
                    except (OSError,ValueError):prior = {}
                    if prior.get('request_id') != request.get('id'):prior = {}
                    observed = {p['handle']:p for p in prior.get('processes',[]) if 'handle' in p}
                    observed.update(known);observed.update(local)
                    for process in handles:
                        if local[process.control_handle]['stopped']:self.forget_resource(run_id,process)
                    pending = self.pending_resources(run_id)
                    value.update(processes=list(observed.values()),pending_resources=pending,
                        ok=bool(observed) and all(p['stopped'] for p in observed.values()) and not pending)
                    changed = any(prior.get(k) != value.get(k) for k in
                        ('request_id','status','processes','pending_resources','ok'))
                    if changed:_write(receipt,value)
                    for process in handles:
                        if observed[process.control_handle]['stopped']:
                            self.settled.setdefault(run_id, {})[process.control_handle] = observed[process.control_handle]
                            self.handles[run_id].remove(process)
                            self.stop_jobs.pop(process.control_handle, None)
                if changed:
                    manager.append_event(run_id, 'step_force_stop',
                                         **{k:v for k,v in value.items() if k != 'run_id'})
            except Exception as exc:
                _write(receipt, {'request_id': request.get('id'), 'run_id': run_id,
                    'ok': False, 'error': type(exc).__name__, 'at': time.time()})
        with self.lock:inventory = [(run_id, list(ps)) for run_id, ps in self.handles.items()]
        for run_id, processes in inventory:
            if not requested(run_id, self.base):
                ended = [p for p in processes if p.poll() is not None]
                with self.lock, self.resource_lock(run_id):
                    for process in ended:
                        if process in self.handles.get(run_id, ()):
                            self.forget_resource(run_id,process)
                            self.handles[run_id].remove(process)
            with self.lock:
                if run_id in self.handles and not self.handles[run_id]:del self.handles[run_id]

    def _watch(self):
        while not self.closed.wait(.05):
            try:
                self.once()
            except Exception:
                # A failure remains retryable; no signal is sent by numeric PID.
                import logging
                logging.getLogger(__name__).exception('run step control failed')


def start(base=None):
    home = Path(base or base_path()).resolve()
    with _lock:
        registry = _registries.get(str(home))
        if registry is None or registry.closed.is_set():
            registry = _registries[str(home)] = Registry(home)
        return registry


def track(process, *, run_id=None, base=None):
    if run_id is None:
        import run_context
        context = run_context.current_run()
        run_id = context.run_id if context else ''
    if run_id:
        start(base).track(run_id, process)
    return process


def suppresses_replay(chat_id, last_inbound_ts=None, *, base=None):
    """Do not turn explicitly reset input into an automatic new task.

    The run's immutable start bounds the original input. A newer incoming
    message remains eligible; missing/ambiguous evidence never hides it.
    """
    home = Path(base or base_path())
    try:
        if last_inbound_ts is None:
            meta = json.loads((home / 'memory/.state/buf_meta.json').read_text(encoding='utf-8'))
            last_inbound_ts = meta[str(chat_id)]['last_ts']
        stamp = float(last_inbound_ts)
        if stamp <= 0:
            return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
    for path in (home / 'memory/.control/run-cancel').glob('run-*.json'):
        if not requested(path.stem, home):
            continue
        manifests = list((home / 'memory/runs').glob('*/' + path.stem + '/manifest.json'))
        if len(manifests) != 1:
            continue
        try:
            context = json.loads(manifests[0].read_text(encoding='utf-8'))['context']
            peer = context.get('origin_chat_id') or context.get('delivery_chat_id')
            created = datetime.datetime.fromisoformat(context['created_at'].replace('Z', '+00:00'))
            if str(peer) == str(chat_id) and created.tzinfo is not None and stamp <= created.timestamp():
                return True
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return False


@contextlib.contextmanager
def starting(run_id, base=None):
    """Keep reset unconfirmed across spawn → handle publication, including races."""
    future = concurrent.futures.Future()
    future.set_running_or_notify_cancel()
    try:
        track(FutureHandle(future), run_id=run_id, base=base)
        if requested(run_id, base):
            raise StepCancelled('run cancelled before step admission')
        yield
    finally:
        future.set_result(None)
