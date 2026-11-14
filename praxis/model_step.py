"""Warm run-owned adapters. Routing, retries and accounting remain in llm.

An exclusive lease identifies each attempt; overlapping calls get additional
workers. Private framed JSON pipes carry frozen requests, never pickle/files.
Transport loss is an unknown outcome, never a reason for this pool to replay.
"""
from __future__ import annotations
from dataclasses import asdict
import atexit
import contextlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import uuid

VERSION = 1
_pools = {}
_lock = threading.RLock()


class ProviderOutcomeUnknown(RuntimeError):
    """The adapter may have sent a request; the caller still owns retry."""


def _error(error):
    import llm
    value = {'type': type(error).__name__, 'message': str(error)}
    if isinstance(error, llm.RelayTerminalError):
        value['terminal'] = {key: getattr(error, key) for key in
            ('code', 'slot', 'resets_at', 'attempts', 'synthetic')}
    if isinstance(error, llm.TornStreamError):
        value['partial'] = asdict(error.partial) if error.partial is not None else None
    if isinstance(error, llm.RunawayGenerationError):value['metrics'] = error.metrics
    status = getattr(error, 'status_code', None)
    if type(status) is int:value['status_code'] = status
    return value


def _raise(value):
    import llm
    name, message = value['type'], value['message']
    terminal = value.get('terminal')
    if terminal is not None:
        cls = llm._RELAY_TERMINAL_CLASSES.get(terminal.get('code'), llm.RelayTerminalError)
        raise cls(message, **terminal)
    if name == 'TornStreamError':
        partial = value.get('partial')
        raise llm.TornStreamError(message, partial=llm.LLMResponse(**partial) if partial else None)
    if name == 'RunawayGenerationError':raise llm.RunawayGenerationError(message, metrics=value.get('metrics'))
    if name in ('EmptyResponseError', 'BrokenChannelError'):
        raise getattr(llm, name)(message)
    known = {'ValueError': ValueError, 'TypeError': TypeError, 'RuntimeError': RuntimeError,
             'TimeoutError': TimeoutError, 'OSError': OSError}
    cls = known.get(name) or type(name, (RuntimeError,), {})
    error = cls(message)
    if 'status_code' in value:error.status_code = value['status_code']
    raise error


def _encode(value):
    body = json.dumps(value, ensure_ascii=False).encode('utf-8')
    return struct.pack('!Q', len(body)) + body


def _read_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        part = stream.read(count - len(data))
        if not part:raise EOFError('adapter pipe closed')
        data.extend(part)
    return bytes(data)


def _read(stream):
    size, = struct.unpack('!Q', _read_exact(stream, 8))
    if size > sys.maxsize:raise ValueError('invalid frame length')
    value = json.loads(_read_exact(stream, size))
    if not isinstance(value, dict):raise ValueError('invalid frame type')
    return value


def _write_all(stream, payload):
    view = memoryview(payload)
    while view:
        size = stream.write(view)
        if not size:raise EOFError('adapter pipe closed')
        view = view[size:]


def _validate_result(result, identity):
    if any(type(result.get(k)) is not type(v) or result.get(k) != v for k,v in identity.items()):
        raise ValueError('adapter response identity mismatch')
    if ('response' in result) == ('error' in result):raise ValueError('invalid adapter result')
    if 'response' in result:
        fields = {'text':str,'blocks':list,'stop_reason':str,'usage':dict,'framework':str,
                  'model':str,'vision':bool,'provider_observation':dict}
        value = result['response']
        if not isinstance(value,dict) or set(value) != set(fields) or any(type(value[k]) is not t for k,t in fields.items()):
            raise ValueError('invalid normalized response frame')
    else:
        value = result['error']
        if not isinstance(value,dict) or not all(isinstance(value.get(k),str) for k in ('type','message')):
            raise ValueError('invalid adapter error frame')


def _status(base, run_id):
    if not run_id:return ''
    paths = list((base / 'memory/runs').glob('*/' + run_id + '/manifest.json'))
    if len(paths) != 1:return ''
    try:return str(json.loads(paths[0].read_text(encoding='utf-8')).get('status') or '')
    except (OSError, ValueError):return ''


class Worker:
    kind = 'provider-worker'

    def __init__(self, pool):
        from command_process import CommandProcess
        self.pool = pool
        self.generation = uuid.uuid4().hex
        self.state = 'starting'
        self.attempt = None
        self.lock = threading.RLock()
        self.process = None
        read_request, write_request = os.pipe()
        read_response, write_response = os.pipe()
        self.input = os.fdopen(write_request, 'wb', buffering=0)
        self.output = os.fdopen(read_response, 'rb', buffering=0)
        try:
            with os.fdopen(read_request, 'rb', buffering=0) as source, os.fdopen(write_response, 'wb', buffering=0) as target:
                self.process = CommandProcess([sys.executable, '-B', str(Path(__file__).resolve()),
                    '--worker', self.generation, pool.run_id],
                    cwd=str(Path(__file__).resolve().parent), stdin=source, stdout=target,
                    stderr=subprocess.DEVNULL, env=dict(os.environ), parent_lifeline=True)
            self.pid = self.process.pid
            self.state = 'idle'
        except BaseException:
            self.input.close();self.output.close()
            raise

    def poll(self):
        # Existing independent Registry watcher also collects terminal workers.
        with self.lock:state = self.state
        status = self.pool.run_status()
        if status in {'done', 'cancelled', 'failed'} or (state == 'idle' and status in {'paused', 'blocked', 'in_doubt'}):
            self.request_stop()
        code = self.process.poll()
        if code is not None:
            with self.lock:self.state = 'dead';self.attempt = None
            # A concurrent exchange owns its pipe objects until it returns.
            if not getattr(self, '_exchanging', False):
                self.input.close();self.output.close()
            self.pool.discard(self)
        return code

    def request_stop(self):
        with self.lock:
            if self.state == 'dead':return
            self.state = 'stopping'
        self.process.request_stop()

    def stop_tree(self):
        self.request_stop()
        result = self.process.stop_tree()
        self.poll()
        return result

    def exchange(self, request):
        import step_control
        identity = {key: request[key] for key in ('version', 'generation', 'run_id', 'lease', 'attempt')}
        self._exchanging = True
        try:
            _write_all(self.input, _encode(request))
            while True:
                result = _read(self.output)
                if 'progress' not in result:break
                if set(result) != set(identity) | {'progress'} or any(type(result.get(k)) is not type(v) or result.get(k) != v for k,v in identity.items()):
                    raise ValueError('adapter progress identity mismatch')
                with self.lock:
                    valid = self.state == 'busy' and self.attempt == request['attempt']
                if not valid:raise ValueError('adapter lease retired')
                if step_control.requested(self.pool.run_id,self.pool.base):
                    raise ValueError('progress after reset')
                import model_progress
                model_progress.emit(result['progress'])
            _validate_result(result, identity)
            with self.lock:
                valid = self.state == 'busy' and self.attempt == request['attempt']
            if not valid:raise ValueError('adapter lease retired')
            return result
        except (OSError, EOFError, ValueError, struct.error):
            self.stop_tree()
            if step_control.requested(self.pool.run_id, self.pool.base):
                raise step_control.StepCancelled('provider process killed by exact-run reset') from None
            # Do not include private frame/exception text or retry a request.
            raise ProviderOutcomeUnknown('provider worker transport lost; outcome unknown') from None
        finally:
            self._exchanging = False
            if self.state == 'dead':self.input.close();self.output.close()


class Pool:
    def __init__(self, base, run_id, task_id=''):
        self.base, self.run_id = base, run_id
        self.task_id = task_id
        self.key = (str(base), run_id, task_id) if task_id else (str(base), run_id)
        self.lock = threading.RLock()
        self.workers = []
        self.closed = False
        self.users = 0

    def run_status(self):
        # Forge workers bind their immutable origin RunContext plus task_id.
        # The origin chat turn can finish while that explicit task continues;
        # the worker process then owns provider lifetime (atexit + pidfd), not
        # the historical origin manifest. Explicit reset markers still apply.
        return '' if self.task_id else _status(self.base, self.run_id)

    def discard(self, worker):
        with _lock:
            with self.lock:
                if worker in self.workers:self.workers.remove(worker)
                if not self.workers and not self.users and _pools.get(self.key) is self:
                    _pools.pop(self.key, None)

    def acquire(self, attempt):
        import step_control
        with step_control.starting(self.run_id, self.base) if self.run_id else contextlib.nullcontext():
            if step_control.requested(self.run_id, self.base) or self.run_status() in {'done', 'cancelled', 'failed', 'paused'}:
                raise step_control.StepCancelled('run closed before provider admission')
            with self.lock:
                if self.closed:raise step_control.StepCancelled('provider pool closed')
                for worker in self.workers:
                    with worker.lock:
                        if worker.state == 'idle':
                            worker.state = 'busy';worker.attempt = attempt
                            return worker
            # No global/pool lock across process launch, I/O or adapter work.
            worker = Worker(self)
            try:
                with self.lock:
                    self.workers.append(worker)
                    with worker.lock:worker.state = 'busy';worker.attempt = attempt
                    closed = self.closed or bool(step_control.requested(self.run_id, self.base))
                step_control.track(worker, run_id=self.run_id, base=self.base)
            except BaseException:
                worker.stop_tree()
                raise
            if closed:
                worker.stop_tree()
                raise step_control.StepCancelled('provider admission cancelled during startup')
            return worker

    def release(self, worker, attempt):
        with worker.lock:
            if worker.state == 'busy' and worker.attempt == attempt:
                worker.attempt = None;worker.state = 'idle'
        if not self.run_id or self.closed:worker.stop_tree()

    def close(self):
        with self.lock:self.closed = True;workers = list(self.workers)
        for worker in workers:worker.request_stop()
        for worker in workers:worker.stop_tree()


def close_run(run_id, base=None):
    import step_control
    home = Path(base or step_control.base_path()).resolve()
    with _lock:pools = [p for p in _pools.values() if p.base == home and p.run_id == run_id]
    for pool in pools:pool.close()


def call(framework, model, kwargs, config, *, run_id='', base=None, single_attempt=False):
    import llm
    import run_context
    import step_control
    context = run_context.current_run()
    run_id = run_id or (context.run_id if context else '')
    home = Path(base or step_control.base_path()).resolve()
    # Freeze before admission. No config fingerprint or payload is published.
    frozen = json.loads(json.dumps({'framework': framework, 'model': model, 'kwargs': kwargs,
        'config': config, 'single_attempt': bool(single_attempt)}, ensure_ascii=False))
    task_id = str(context.forge_task_id) if context and context.run_id == run_id else ''
    key = (str(home), run_id, task_id) if task_id else (str(home), run_id)
    with _lock:
        pool = _pools.setdefault(key, Pool(home, run_id, task_id)) if run_id else Pool(home, '')
        with pool.lock:pool.users += 1
    attempt = uuid.uuid4().hex
    worker = None
    try:
        worker = pool.acquire(attempt)
        request = dict(frozen, version=VERSION, generation=worker.generation,
                       run_id=run_id, lease=uuid.uuid4().hex, attempt=attempt)
        result = worker.exchange(request)
        if step_control.requested(run_id, home):
            worker.stop_tree()
            raise step_control.StepCancelled('provider attempt cancelled before result admission')
        if 'error' in result:_raise(result['error'])
        return llm.LLMResponse(**result['response'])
    except BaseException:
        if worker is not None and 'result' not in locals():worker.stop_tree()
        raise
    finally:
        if worker is not None:pool.release(worker, attempt)
        with pool.lock:pool.users -= 1
        pool.discard(None)


def worker(generation, run_id):
    # Reserve protocol stdout before importing anything with diagnostics.
    output = os.fdopen(os.dup(1), 'wb', buffering=0)
    with open(os.devnull, 'wb') as sink:os.dup2(sink.fileno(), 1)
    import llm
    import model_progress
    source = sys.stdin.buffer
    try:
        while True:
            try:request = _read(source)
            except EOFError:return
            if (request.get('version') != VERSION or request.get('generation') != generation
                    or request.get('run_id') != run_id or not request.get('attempt') or not request.get('lease')):
                return
            config = request['config']
            for name, cached in list(llm._CLIENTS.items()):
                fw = (config.get('frameworks') or {}).get(name) or {}
                if cached[0] != (fw.get('base_url') or '', fw.get('api_key') or ''):
                    llm._CLIENTS.pop(name, None)
                    with contextlib.suppress(Exception):cached[1].close()
            llm._config = lambda: config
            identity = {key: request[key] for key in ('version', 'generation', 'run_id', 'lease', 'attempt')}
            try:
                with model_progress.observe(lambda metrics: _write_all(output, _encode(dict(identity, progress=metrics)))):
                    with llm.single_provider_attempt() if request['single_attempt'] else contextlib.nullcontext():
                        response = llm._call(request['framework'], request['model'], _resolved=True, **request['kwargs'])
                result = {'response': asdict(response)}
            except Exception as error:result = {'error': _error(error)}
            identity = {key: request[key] for key in ('version', 'generation', 'run_id', 'lease', 'attempt')}
            _write_all(output, _encode(dict(identity, **result)))
    finally:
        for cached in llm._CLIENTS.values():
            with contextlib.suppress(Exception):cached[1].close()
        output.close()


def _close_all():
    with _lock:pools = list(_pools.values())
    for pool in pools:
        with pool.lock:
            pool.closed = True
            for owned in pool.workers:owned.request_stop()
    for pool in pools:pool.close()
    control = sys.modules.get('step_control')
    if control is not None:
        for registry in list(control._registries.values()):registry.close()


atexit.register(_close_all)
if __name__ == '__main__':worker(sys.argv[2], sys.argv[3])
