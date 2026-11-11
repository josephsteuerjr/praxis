"""A command owns its private group/job until all members have exited.

Popen.wait() only observes the shell. This wrapper keeps an unreaped POSIX
leader (pinning the group number), or a Windows job handle assigned before
the suspended child is resumed. Neither Stop nor poll targets a recycled PID.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time


def group_live(pgid: int) -> bool | None:
    if os.path.isdir('/proc'):
        try:
            with os.scandir('/proc') as entries:
                for entry in entries:
                    if not entry.name.isdigit():continue
                    try:
                        fields = (Path(entry.path)/'stat').read_text().rsplit(')', 1)[1].split()
                        if int(fields[2]) == pgid and fields[0] not in {'Z', 'X'}:return True
                    except FileNotFoundError:continue
            return False
        except (OSError, ValueError, IndexError):return None
    try:
        rows = subprocess.run(['ps', '-A', '-o', 'pgid=', '-o', 'stat='],
                              capture_output=True, text=True, timeout=3)
        if rows.returncode:return None
        return any(int(p[0]) == pgid and not p[1].startswith(('Z', 'X'))
                   for row in rows.stdout.splitlines() if len(p := row.split()) >= 2)
    except (OSError, ValueError, subprocess.TimeoutExpired):return None


class WindowsJob:
    """No breakaway permission: descendants inherit this exact job."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.api = k = c.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('CreateJobObjectW', [c.c_void_p, w.LPCWSTR], w.HANDLE),
            ('SetInformationJobObject', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            ('QueryInformationJobObject', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
            ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
            ('OpenThread', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            ('ResumeThread', [w.HANDLE], w.DWORD),
            ('CloseHandle', [w.HANDLE], w.BOOL)):
            fn = getattr(k, name);fn.argtypes = args;fn.restype = result
        class Limits(c.Structure):
            _fields_ = [('user', c.c_int64), ('job_user', c.c_int64), ('flags', w.DWORD),
                        ('min_ws', c.c_size_t), ('max_ws', c.c_size_t), ('active', w.DWORD),
                        ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Extended(c.Structure):
            _fields_ = [('basic', Limits), ('io', c.c_uint64 * 6),
                        ('process_memory', c.c_size_t), ('job_memory', c.c_size_t),
                        ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
        class Accounting(c.Structure):
            _fields_ = [('times', c.c_int64 * 4), ('faults', w.DWORD), ('total', w.DWORD),
                        ('active', w.DWORD), ('terminated', w.DWORD)]
        self.Accounting = Accounting
        self.Extended = Extended
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:raise c.WinError(c.get_last_error())
        limits = Extended();limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error());self.close();raise error

    def attach_and_resume(self, proc, resume=True):
        # CREATE_SUSPENDED prevents any spawn before the job association.
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise self.c.WinError(self.c.get_last_error())
        if resume:
            self.resume(proc)

    def resume(self, proc):
        import psutil
        threads = psutil.Process(proc.pid).threads()
        if len(threads) != 1:raise RuntimeError('Suspended process has no unique initial thread')
        thread = self.api.OpenThread(0x0002, False, threads[0].id)
        if not thread:raise self.c.WinError(self.c.get_last_error())
        try:
            if self.api.ResumeThread(thread) == 0xffffffff:
                raise self.c.WinError(self.c.get_last_error())
        finally:self.api.CloseHandle(thread)

    def live(self):
        info = self.Accounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, self.c.byref(info), self.c.sizeof(info), None):
            return None
        return info.active > 0

    def stop(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise self.c.WinError(self.c.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle);self.handle = None

    def release_survivors(self):
        """Home shell explicitly allows background jobs after its leader returns.

        Forge never calls this: its command custody keeps KILL_ON_JOB_CLOSE until
        the entire job is settled. Clearing this limit is only the ordinary
        shell's documented background-launch boundary.
        """
        limits = self.Extended()
        if not self.api.SetInformationJobObject(self.handle, 9, self.c.byref(limits), self.c.sizeof(limits)):
            raise self.c.WinError(self.c.get_last_error())
        self.close()


class CommandProcess:
    """Popen's small poll/wait surface, with command-group completion semantics.

    Explicit deferred POSIX calls reject preexec_fn before any child starts.
    Linux deferred custody requires close_fds=True and no extra pass_fds.
    """
    def __init__(self, command, **kwargs):
        self._lock = threading.RLock()
        self._job = WindowsJob() if os.name == 'nt' else None
        self.returncode = None
        self._stopped = False
        self._deferred = bool(kwargs.pop('defer_start', False))
        custody_directory = kwargs.pop('custody_directory', None)
        self._custody = None
        self._temporary_custody = None
        self._nonce = None
        self._admission_fd = None
        gate_read = None
        self._linux_custody = False
        if self._job:
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4  # CREATE_SUSPENDED
        else:
            if not all(hasattr(os, name) for name in ('waitid', 'WNOWAIT', 'WEXITED', 'P_PID')):
                raise OSError('Safe command custody requires waitid/WNOWAIT')
            kwargs['start_new_session'] = True
            if self._deferred:
                if kwargs.get('preexec_fn') is not None:
                    raise ValueError('Deferred POSIX commands cannot use preexec_fn')
                if sys.platform.startswith('linux') and (kwargs.get('pass_fds') or not kwargs.get('close_fds', True)):
                    raise ValueError('Deferred Linux commands require close_fds=True and empty pass_fds')
                if custody_directory is None:
                    self._temporary_custody = tempfile.TemporaryDirectory(prefix='helene-command-')
                    self._custody = Path(self._temporary_custody.name)
                else:
                    self._custody = Path(custody_directory).resolve()
                    self._custody.mkdir(mode=0o700)
                self._nonce = secrets.token_hex(24)
                args = ([os.fsdecode(command)] if isinstance(command, (str, bytes, os.PathLike))
                        else [os.fsdecode(value) for value in command])
                shell = bool(kwargs.pop('shell', False))
                executable = kwargs.pop('executable', None)
                executable = os.fsdecode(executable) if executable is not None else None
                request = {'command': args[0] if isinstance(command, (str, bytes, os.PathLike)) else args,
                           'shell': shell, 'executable': executable, 'nonce': self._nonce,
                           'deferred': True, 'parent_pid': os.getpid()}
                (self._custody/'request.json').write_text(json.dumps(request), encoding='utf-8')
                os.chmod(self._custody/'request.json', 0o600)
                self._linux_custody = sys.platform.startswith('linux')
                if self._linux_custody:
                    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
                        raise OSError('Linux command custody requires pidfds')
                    command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('command_child.py')),
                               str(self._custody)]
                else:
                    gate_read, self._admission_fd = os.pipe()
                    if kwargs.get('pass_fds') or kwargs.get('close_fds', True):
                        kwargs['pass_fds'] = (*kwargs.get('pass_fds', ()), gate_read)
                    else:
                        os.set_inheritable(gate_read, True)
                    command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('command_admission.py')),
                               str(self._custody), str(gate_read)]
        self._proc = None
        try:
            self._proc = subprocess.Popen(command, **kwargs)
            self.pid = self._proc.pid
            self.args = self._proc.args
            if self._job:self._job.attach_and_resume(self._proc, resume=not self._deferred)
        except BaseException:
            # Failure before resume cannot have produced descendants. Use the
            # Popen handle, never a PID-based replacement lookup.
            try:
                if self._proc is not None:
                    self._proc.kill();self._proc.wait()
            finally:
                if self._job:self._job.close()
                self._close_admission()
            raise
        finally:
            if gate_read is not None:
                os.close(gate_read)
        if self._custody:
            end = time.monotonic() + 3
            while not (self._custody/'ready').exists() and not self._leader_exited() and time.monotonic() < end:
                time.sleep(.01)

    def _close_admission(self):
        if self._admission_fd is not None:
            os.close(self._admission_fd)
            self._admission_fd = None

    def admit(self):
        """Resume only after the caller persisted this exact command identity."""
        with self._lock:
            if not self._deferred:
                return
            if self._leader_exited():
                raise OSError('Command custodian exited before admission')
            if self._job:
                self._job.resume(self._proc)
            elif self._linux_custody:
                temporary = self._custody/'admitted.tmp'
                temporary.write_text(self._nonce, encoding='utf-8')
                temporary.replace(self._custody/'admitted')
            else:
                token = self._nonce.encode('ascii')
                if os.write(self._admission_fd, token) != len(token):
                    raise OSError('Incomplete command admission')
                self._close_admission()
            self._deferred = False

    def custody_identity(self):
        if self._custody:
            return {'kind': 'linux-subreaper-v1' if self._linux_custody else 'posix-admission-v1',
                    'pid': self.pid, 'directory': str(self._custody), 'nonce': self._nonce}
        return {'kind': 'windows-job' if self._job else 'posix-group', 'pid': self.pid}

    def _leader_exited(self):
        if self._job:return self._proc.poll() is not None
        # Never call raw poll/wait while a descendant could still hold the group.
        return os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None

    def _live(self):
        return self._job.live() if self._job else group_live(self.pid)

    def poll(self):
        with self._lock:
            if self.returncode is not None:return self.returncode
            if self._leader_exited() and self._live() is False:
                if self._linux_custody:
                    try:
                        receipt = json.loads((self._custody/'settled.json').read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        return None
                    if receipt.get('nonce') != self._nonce or receipt.get('settled') is not True:
                        return None
                    self._proc.wait()
                    self.returncode = int(receipt['exit_code'])
                else:
                    self.returncode = self._proc.wait()
                self._close_admission()
                if self._temporary_custody is not None:
                    self._temporary_custody.cleanup()
                    self._temporary_custody = None
                if self._job:self._job.close()
            return self.returncode

    def wait(self, timeout=None):
        end = time.monotonic() + timeout if timeout is not None else float('inf')
        while True:
            code = self.poll()
            if code is not None:return code
            if time.monotonic() >= end:raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(.03)

    def stop_tree(self):
        with self._lock:
            phase='poll'
            try:
                if self.poll() is not None:return True, 'private command group already empty'
                if self._job:
                    self._job.stop()
                    self.wait(3)
                else:
                    # waitid also confirms we still own the unreaped child. A
                    # foreign waiter/reaper makes this fail closed before signals.
                    phase='unreaped-child identity'
                    self._leader_exited()
                    if self._linux_custody:
                        if self._leader_exited():
                            return False, f'Command subreaper exited without settlement: {self._custody}'
                        phase='subreaper stop'
                        os.kill(self.pid, signal.SIGTERM)
                        self.wait(5)
                        self._stopped = True
                        return True, 'Linux subreaper reaped every descendant'
                    phase='group census'
                    if self._live() is None:return False, 'private group census unavailable'
                    for sig in (signal.SIGTERM, signal.SIGKILL):
                        phase='signal '+str(sig)
                        try:os.killpg(self.pid, sig)
                        except (ProcessLookupError,PermissionError) as refused:
                            # Darwin can refuse a signal after the last live
                            # group member exits. Only a fresh complete poll
                            # (unreaped-child identity AND empty census) may
                            # turn that race into success. A live/unknown group
                            # keeps the original refusal and never falls back
                            # to a bare PID or another process generation.
                            # Allow the same bounded exit observation used
                            # after a delivered signal. Immediate Darwin exit
                            # can still be between census samples here.
                            try:self.wait(1)
                            except subprocess.TimeoutExpired:raise refused
                            break
                        phase='wait after signal '+str(sig)
                        try:self.wait(1);break
                        except subprocess.TimeoutExpired:
                            if self._live() is None:return False, 'private group census unavailable'
                    if self.returncode is None:return False, 'private group remains live'
                self._stopped = True
                return True, 'private command group exited'
            except (OSError, subprocess.TimeoutExpired) as exc:
                return False, f'{type(exc).__name__}: {exc}; phase={phase}; private group exit unconfirmed'
