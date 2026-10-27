"""A command owns its private group/job until all members have exited.

Popen.wait() only observes the shell. This wrapper keeps an unreaped POSIX
leader (pinning the group number), or a Windows job handle assigned before
the suspended child is resumed. Neither Stop nor poll targets a recycled PID.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
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
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:raise c.WinError(c.get_last_error())
        limits = Extended();limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error());self.close();raise error

    def attach_and_resume(self, proc):
        # CREATE_SUSPENDED prevents any spawn before the job association.
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise self.c.WinError(self.c.get_last_error())
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


class CommandProcess:
    """Popen's small poll/wait surface, with command-group completion semantics."""
    def __init__(self, command, **kwargs):
        self._lock = threading.RLock()
        self._job = WindowsJob() if os.name == 'nt' else None
        self.returncode = None
        self._stopped = False
        if self._job:
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4  # CREATE_SUSPENDED
        else:
            if not all(hasattr(os, name) for name in ('waitid', 'WNOWAIT', 'WEXITED', 'P_PID')):
                raise OSError('Safe command custody requires waitid/WNOWAIT')
            kwargs['start_new_session'] = True
        self._proc = None
        try:
            self._proc = subprocess.Popen(command, **kwargs)
            self.pid = self._proc.pid
            self.args = self._proc.args
            if self._job:self._job.attach_and_resume(self._proc)
        except BaseException:
            # Failure before resume cannot have produced descendants. Use the
            # Popen handle, never a PID-based replacement lookup.
            try:
                if self._proc is not None:
                    self._proc.kill();self._proc.wait()
            finally:
                if self._job:self._job.close()
            raise

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
                self.returncode = self._proc.wait()
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
            try:
                if self.poll() is not None:return True, 'private command group already empty'
                if self._job:
                    self._job.stop()
                    self.wait(3)
                else:
                    # waitid also confirms we still own the unreaped child. A
                    # foreign waiter/reaper makes this fail closed before signals.
                    self._leader_exited()
                    if self._live() is None:return False, 'private group census unavailable'
                    for sig in (signal.SIGTERM, signal.SIGKILL):
                        os.killpg(self.pid, sig)
                        try:self.wait(1);break
                        except subprocess.TimeoutExpired:
                            if self._live() is None:return False, 'private group census unavailable'
                    if self.returncode is None:return False, 'private group remains live'
                self._stopped = True
                return True, 'private command group exited'
            except (OSError, subprocess.TimeoutExpired) as exc:
                return False, f'{type(exc).__name__}; private group exit unconfirmed'
