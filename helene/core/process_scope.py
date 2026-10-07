"""Owned process scopes for tool calls and Forge supervisors.

Cancellation closes admission under the spawn lock before terminating owned groups.
Windows children are created suspended, assigned to a non-inheritable kill-on-close
Job and only then resumed. POSIX owns a new session (intentional setsid escapes are
not an OS sandbox). No PID discovered by command text is ever signalled here.
"""
from __future__ import annotations
import contextvars
import ctypes
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager

_lock = threading.RLock()
_current = contextvars.ContextVar('process_scope', default='')
_closed: set[str] = set()
_children: dict[int, tuple[str, subprocess.Popen, object]] = {}
_admit = lambda: True
_external = {}
_read_requested = set()


def wants_read(run_id):
    with _lock: return run_id in _read_requested


def read_boundary(run_id):
    with _lock: _read_requested.discard(run_id)


def admit_external(terminate, resume):
    """Register an already suspended native child and resume under the cancel lock."""
    scope = current_scope()
    key = object()
    with _lock:
        if any(scope == s or scope.startswith(s + '/') for s in _closed) or not _admit():
            terminate()
            raise RuntimeError('process admission closed by owner/cancellation')
        _external[key] = (scope, terminate)
        try:
            resume()
        except BaseException:
            _external.pop(key, None)
            terminate()
            raise
    return key


def release_external(key):
    with _lock:
        _external.pop(key, None)


def configure_admission(predicate):
    global _admit
    _admit = predicate


def current_scope():
    explicit = _current.get()
    if explicit:
        return explicit
    try:
        from run_context import current_run
        run = current_run()
        return run.run_id if run else 'engine'
    except ImportError:
        return 'engine'


@contextmanager
def bind(scope):
    token = _current.set(str(scope))
    try:
        yield
    finally:
        _current.reset(token)


class WindowsJob:
    def __init__(self):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('user', ctypes.c_longlong), ('job', ctypes.c_longlong),
                        ('flags', w.DWORD), ('min', ctypes.c_size_t), ('max', ctypes.c_size_t),
                        ('active', w.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('CreateJobObjectW', [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            ('SetInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
            ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
            ('CloseHandle', [w.HANDLE], w.BOOL)):
            fn = getattr(self.k, name); fn.argtypes = args; fn.restype = result
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended(); info.basic.flags = 0x2000
        if not self.k.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.WinError(ctypes.get_last_error()); self.close(); raise error

    def adopt_resume(self, proc):
        if not self.k.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Popen closes the primary thread handle. Resume the still-suspended process
        # through its retained kernel handle, never by enumerating a recyclable PID.
        n = ctypes.WinDLL('ntdll')
        n.NtResumeProcess.argtypes = [ctypes.c_void_p]
        n.NtResumeProcess.restype = ctypes.c_long
        status = n.NtResumeProcess(int(proc._handle))
        if status < 0:
            raise OSError(f'NtResumeProcess failed: {status:#x}')

    def terminate(self):
        if self.handle and not self.k.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            if not self.k.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def spawn(args, **kwargs):
    scope = current_scope()
    with _lock:
        if any(scope == s or scope.startswith(s + '/') for s in _closed) or not _admit():
            raise RuntimeError('process admission closed by owner/cancellation')
        job = WindowsJob() if os.name == 'nt' else None
        if job:
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4  # CREATE_SUSPENDED
        else:
            kwargs['start_new_session'] = True
        proc = None
        try:
            proc = subprocess.Popen(args, **kwargs)
            if job:
                job.adopt_resume(proc)
            _children[proc.pid] = (scope, proc, job)
            return proc
        except BaseException:
            try:
                if proc is not None:
                    proc.kill(); proc.wait(timeout=5)
            finally:
                if job: job.close()
            raise


def terminate(proc):
    with _lock:
        entry = _children.get(proc.pid)
        if not entry or entry[1] is not proc:
            raise RuntimeError('process has no live ownership receipt')
        job = entry[2]
        if job:
            job.terminate()
        else:
            # A reaped child no longer reserves its PID. Never signal that number.
            if proc.returncode is not None:
                return
            try:
                os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS answers EPERM, not ESRCH, when the group leader is
                # already an unreaped zombie or the pgid was recycled to a
                # process outside our UID (shared CI runners). Either way the
                # signal is undeliverable, not worth failing the caller over.
                pass


def release(proc):
    with _lock:
        entry = _children.get(proc.pid)
        if entry and entry[1] is proc:
            if entry[2]: entry[2].close()
            _children.pop(proc.pid, None)


def cancel(scope):
    failures = []
    with _lock:
        _closed.add(str(scope))
        targets = [p for s, p, _ in _children.values() if s == str(scope) or s.startswith(str(scope) + '/')]
        for s, stop in list(_external.values()):
            if s == str(scope) or s.startswith(str(scope) + '/'):
                try: stop()
                except OSError as exc: failures.append(str(exc))
        for p in targets:
            try:
                terminate(p)
            except OSError as exc:
                failures.append(f'{p.pid}: {exc}')
    return {'requested': len(targets), 'failures': failures}


def cancel_all():
    with _lock:
        scopes = {s for s, _, _ in _children.values()} | {s for s, _ in _external.values()}
        return [cancel(s) for s in scopes]


def reap_if_done(proc):
    with _lock:
        if proc.returncode is not None:
            release(proc)
            return True
        if os.name == 'nt':
            done = proc.poll() is not None
        else:
            done = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        if done:
            terminate(proc)
            proc.wait(timeout=5)
            release(proc)
        return done


def stop_owned(pid):
    with _lock:
        entry = _children.get(int(pid))
        if not entry:
            return False
        if reap_if_done(entry[1]):
            return False
        terminate(entry[1])
        return True


def run(args, *, timeout=None, check=False, capture_output=False, input=None, **kwargs):
    if capture_output:
        if 'stdout' in kwargs or 'stderr' in kwargs:
            raise ValueError('capture_output conflicts with stdout/stderr')
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input is not None:
        kwargs['stdin'] = subprocess.PIPE
    if os.name != 'nt':
        return _run_posix(args, timeout=timeout, check=check, input=input, **kwargs)
    proc = spawn(args, **kwargs)
    try:
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = exc.output, exc.stderr
                for stream in (proc.stdout, proc.stderr):
                    if stream:
                        stream.close()
            raise subprocess.TimeoutExpired(args, timeout, output=out, stderr=err) from None
        if check and proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode, args, out, err)
        return subprocess.CompletedProcess(args, proc.returncode, out, err)
    finally:
        # A foreground call does not license background descendants to outlive it.
        try:
            if os.name == 'nt': terminate(proc)
        finally:
            release(proc)


def _run_posix(args, *, timeout, check, input, **kwargs):
    """Spool output, keep the leader unreaped until group teardown (WNOWAIT).

    Pipes held by escaped grandchildren cannot stall return; setsid escapes remain
    outside this process-group containment and are not claimed terminated.
    """
    import tempfile
    from contextlib import ExitStack
    text = kwargs.pop('text', False) or kwargs.pop('universal_newlines', False)
    encoding = kwargs.pop('encoding', None)
    errors = kwargs.pop('errors', None)
    text = text or encoding is not None or errors is not None
    with ExitStack() as stack:
        outputs = {}
        for key in ('stdout', 'stderr'):
            if kwargs.get(key) == subprocess.PIPE:
                outputs[key] = stack.enter_context(tempfile.TemporaryFile())
                kwargs[key] = outputs[key]
        if input is not None:
            src = stack.enter_context(tempfile.TemporaryFile())
            src.write(input.encode(encoding or 'utf-8', errors or 'strict') if isinstance(input, str) else input)
            src.seek(0); kwargs['stdin'] = src
        proc = spawn(args, **kwargs)
        expired = False
        start = time.monotonic()
        try:
            while True:
                state = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if state is not None:
                    break
                if timeout is not None and time.monotonic() - start >= timeout:
                    expired = True
                    break
                time.sleep(.02)
            # Kernel still reserves this child's PID: no check-after-reap PID reuse.
            terminate(proc)
            proc.wait(timeout=5)
            values = {}
            for key, stream in outputs.items():
                stream.seek(0); value = stream.read()
                values[key] = value.decode(encoding or 'utf-8', errors or 'strict') if text else value
            out, err = values.get('stdout'), values.get('stderr')
            if expired:
                raise subprocess.TimeoutExpired(args, timeout, output=out, stderr=err)
            if check and proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, args, out, err)
            return subprocess.CompletedProcess(args, proc.returncode, out, err)
        finally:
            if proc.returncode is None:
                terminate(proc)
                proc.wait(timeout=5)
            release(proc)


@contextmanager
def step():
    """A foreground step is not its run, nor its independently owned Forge work."""
    import json
    import uuid
    from pathlib import Path
    from run_context import current_run
    run_context = current_run()
    run_id = run_context.run_id if run_context else current_scope().split('/step/')[0]
    token = uuid.uuid4().hex
    scope = run_id + '/step/' + token
    path = None
    # Only the owner conversation advertises its step to the window. Child tasks
    # keep their own scopes and cannot replace this foreground control target.
    if run_context and run_context.kind == 'chat_turn':
        path = Path(os.environ.get('PRAXIS_BASE') or Path(__file__).resolve().parent) / 'memory/.control/current-step.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'token': token, 'scope': scope, 'run_id': run_id}), encoding='utf-8')
        os.replace(temp, path)
    try:
        with bind(scope):
            yield
    finally:
        if path:
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('token') == token:
                    path.unlink()
            except FileNotFoundError:
                pass
        # Closed scope ids cannot be reused; no next-step cancellation.


def consume_step_request(tree):
    import json
    from pathlib import Path
    folder = Path(tree) / 'memory/.control'
    request = folder / 'step-interrupt.json'
    claimed = folder / 'step-interrupt.processing.json'
    try:
        os.replace(request, claimed)
    except FileNotFoundError:
        return
    try:
        asked = json.loads(claimed.read_text(encoding='utf-8'))
        active = json.loads((folder / 'current-step.json').read_text(encoding='utf-8'))
        if asked.get('token') and asked['token'] == active.get('token'):
            with _lock:
                _read_requested.add(active['run_id'])
                cancel(active['scope'])
    except FileNotFoundError:
        pass  # the selected step already finished; never cancel its successor
    finally:
        claimed.unlink(missing_ok=True)
