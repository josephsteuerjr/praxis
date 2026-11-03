# -*- coding: utf-8 -*-
"""Windows desktop child custody owned by the Python supervisor.

One fresh kill-on-close Job per child generation, held beside its process handle.
CreateProcess is suspended; only after Job assignment and an owner-stop recheck
is its primary thread resumed. A leader exit is not a tree exit: terminate and
observe the Job before any replacement. No PID-only signalling or fallback spawn.
This adapter is not imported on macOS/Linux; their containment needs its own proof.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import msvcrt
import os
from pathlib import Path
import subprocess
import time
import _winapi

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 8
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

_kernel = ctypes.WinDLL("kernel32", use_last_error=True)


def _api(name, restype, *argtypes):
    fn = getattr(_kernel, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


_CreateJobObject = _api("CreateJobObjectW", wintypes.HANDLE, ctypes.c_void_p, wintypes.LPCWSTR)
_SetJob = _api("SetInformationJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.INT,
               ctypes.c_void_p, wintypes.DWORD)
_AssignJob = _api("AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
_TerminateJob = _api("TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
_QueryJob = _api("QueryInformationJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.INT,
                ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
_ResumeThread = _api("ResumeThread", wintypes.DWORD, wintypes.HANDLE)
_CloseHandle = _api("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_Wait = _api("WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
_GetExit = _api("GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_OpenProcess = _api("OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_InJob = _api("IsProcessInJob", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))


class BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class BasicAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in
                ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
    _fields_ += [(name, wintypes.DWORD) for name in
                 ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]


def _last_error(operation: str) -> OSError:
    return OSError(ctypes.get_last_error(), f"{operation}: {ctypes.FormatError(ctypes.get_last_error())}")


class CustodyFault(RuntimeError):
    """A child may still be live; no second launch is permitted in this process."""


class JobCreationRefused(RuntimeError):
    """Job setup failed before CreateProcess; no child exists to reconcile."""


_UNCONFIRMED_HANDLES: list[int] = []


class ChildJob:
    def __init__(self):
        self.sealed = False
        self.stop_requested = False
        self.capture_complete = False
        self.members: dict[int, int] = {}
        self.pending: set[int] = set()
        self.handle = _CreateJobObject(None, None)
        if not self.handle:
            raise _last_error("CreateJobObject")
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
        if not _SetJob(self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                       ctypes.byref(info), ctypes.sizeof(info)):
            error = _last_error("SetInformationJobObject")
            self.close()
            raise error

    def assign(self, process_handle: int) -> None:
        if self.stop_requested:raise CustodyFault('Stopped Job refuses process admission')
        if not _AssignJob(self.handle, process_handle):
            raise _last_error("AssignProcessToJobObject")

    def active(self) -> int:
        info = BasicAccounting()
        if not _QueryJob(self.handle, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                         ctypes.byref(info), ctypes.sizeof(info), None):
            raise _last_error("QueryInformationJobObject")
        return info.ActiveProcesses

    def terminate(self) -> None:
        if not _TerminateJob(self.handle, 1):
            raise _last_error("TerminateJobObject")

    def seal(self) -> None:
        """Stop future admissions before taking a finite member snapshot."""
        if self.sealed:return
        self.stop_requested=True
        info=ExtendedLimits()
        if not _QueryJob(self.handle,JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                         ctypes.byref(info),ctypes.sizeof(info),None):
            raise _last_error('Query job limits before Stop')
        info.BasicLimitInformation.LimitFlags|=JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit=0
        if not _SetJob(self.handle,JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                       ctypes.byref(info),ctypes.sizeof(info)):
            raise _last_error('Seal job process admission')
        confirmed=ExtendedLimits()
        if not _QueryJob(self.handle,JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                         ctypes.byref(confirmed),ctypes.sizeof(confirmed),None):
            raise _last_error('Confirm sealed job limits')
        flags=confirmed.BasicLimitInformation.LimitFlags
        if (flags&info.BasicLimitInformation.LimitFlags!=info.BasicLimitInformation.LimitFlags
                or confirmed.BasicLimitInformation.ActiveProcessLimit!=0):
            raise CustodyFault('Job process admission seal was not confirmed')
        self.sealed=True

    def capture(self) -> None:
        """Bind wait handles to the exact Job, never signal by enumerated PID."""
        if not self.sealed:raise CustodyFault('Job admissions were not sealed')
        capacity=16
        while True:
            class ProcessIds(ctypes.Structure):
                _fields_=[('assigned',wintypes.DWORD),('count',wintypes.DWORD),
                          ('pids',ctypes.c_size_t*capacity)]
            info=ProcessIds()
            ok=_QueryJob(self.handle,JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                         ctypes.byref(info),ctypes.sizeof(info),None)
            if not ok and ctypes.get_last_error()!=234:raise _last_error('Query job members')
            if info.count<info.assigned or not ok:
                capacity=max(capacity*2,info.assigned)
                if capacity>1048576:raise CustodyFault('Job member census exceeds bounded storage')
                continue
            if info.count>capacity or info.count>info.assigned:
                raise CustodyFault('Incomplete Job process identifier census')
            self.pending.update(int(pid) for pid in info.pids[:info.count] if pid not in self.members)
            self.capture_complete=True
            break
        for pid in tuple(self.pending):
            if not 0<pid<=0xffffffff:raise CustodyFault('Invalid Job process identifier')
            handle=_OpenProcess(0x00100000|0x1000,False,pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
            if not handle:
                error=ctypes.get_last_error()
                if error==87:
                    # Valid nonzero DWORD from the kernel census, with fixed
                    # access/argtypes: its process object has gone before open.
                    # Unlike active=0, this does not infer exit from accounting.
                    self.pending.remove(pid);continue
                raise OSError(error,'Open job member for Stop confirmation')
            belongs=wintypes.BOOL()
            if not _InJob(handle,self.handle,ctypes.byref(belongs)):
                error=_last_error('Verify exact child Job');_CloseHandle(handle);raise error
            if not belongs.value:
                done=_Wait(handle,0)==WAIT_OBJECT_0
                _CloseHandle(handle)
                if done:self.pending.remove(pid);continue
                raise CustodyFault('Enumerated process no longer belongs to the exact Job')
            self.members[pid]=handle;self.pending.remove(pid)

    def release(self) -> None:
        """Drop only the installer's outer crash fuse after owner handoff."""
        if self.stop_requested or self.members or self.pending:raise CustodyFault('Stopped Job cannot be handed off')
        info = ExtendedLimits()
        if not _SetJob(self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                       ctypes.byref(info), ctypes.sizeof(info)):
            raise _last_error("Release installer Job")
        self.close()

    def wait_empty(self, seconds: float = 5) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self.capture()
            for pid,handle in tuple(self.members.items()):
                remaining=max(0,int((deadline-time.monotonic())*1000))
                if _Wait(handle,remaining)!=WAIT_OBJECT_0:
                    raise CustodyFault('Job member termination remains unconfirmed')
                _CloseHandle(handle);del self.members[pid]
            if self.active()==0:
                self.capture()
                if not self.members and not self.pending:return
            if time.monotonic()>=deadline:
                raise CustodyFault('Job still has live members; replacement forbidden')
            # No compensating sleep can establish process termination. A fresh
            # census must identify a waitable member or Stop remains unknown.
            self.capture()
            if not self.members:raise CustodyFault('Active Job members have no confirmed wait handles')

    def close(self) -> None:
        if self.members or self.pending:raise CustodyFault('Unconfirmed Job member custody retained')
        if self.handle:
            _CloseHandle(self.handle)  # kill-on-close is the last crash fuse
            self.handle = None


class JobChild:
    def __init__(self, process_handle, pid: int, job: ChildJob):
        self.handle = process_handle
        self.pid = pid  # diagnostic only; signalling uses retained handles/Job
        self.job = job

    @classmethod
    def spawn(cls, argv: list[str], *, cwd: Path, env: dict[str, str], log: Path,
              stopped) -> "JobChild":
        """Own the complete suspended→Job→Stop→resume transaction.

        `stopped` is the supervisor's owner-stop predicate, checked both before
        creation and after Job assignment. No child code runs on failed admission.
        """
        if stopped():
            raise RuntimeError("owner stop before child launch")
        try:
            job = ChildJob()
        except OSError as exc:
            raise JobCreationRefused("Windows Job unavailable; child launch refused") from exc
        process = thread = None
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(os.devnull, "rb") as null, open(log, "ab", buffering=0) as out:
                inp_handle, out_handle = msvcrt.get_osfhandle(null.fileno()), msvcrt.get_osfhandle(out.fileno())
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESTDHANDLES
                startup.hStdInput, startup.hStdOutput, startup.hStdError = inp_handle, out_handle, out_handle
                startup.lpAttributeList = {"handle_list": [inp_handle, out_handle]}
                # STARTUPINFOEX handle_list constrains inheritance to the two
                # selected descriptors rather than leaking the supervisor's Job.
                os.set_handle_inheritable(inp_handle, True)
                os.set_handle_inheritable(out_handle, True)
                try:
                    process, thread, pid, _ = _winapi.CreateProcess(
                        argv[0], subprocess.list2cmdline(argv), None, None, True,
                        CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT |
                        EXTENDED_STARTUPINFO_PRESENT,
                        env, str(cwd), startup)
                finally:
                    os.set_handle_inheritable(inp_handle, False)
                    os.set_handle_inheritable(out_handle, False)
            job.assign(int(process))
            if stopped():
                raise RuntimeError("owner stop before child resume")
            if _ResumeThread(int(thread)) == 0xFFFFFFFF:
                raise _last_error("ResumeThread")
            return cls(process, pid, job)
        except BaseException as original:
            # The child is still suspended or already in our Job. It can never
            # become a live unowned process after a *confirmed* termination.
            # If OS termination itself cannot be proved, retain the exact handle
            # and refuse the entire supervisor rather than buying a retry.
            unconfirmed = False
            if process is not None:
                try:
                    _winapi.TerminateProcess(process, 1)
                    unconfirmed = _Wait(int(process), 5000) != WAIT_OBJECT_0
                except OSError:
                    unconfirmed = True
                if unconfirmed:
                    _UNCONFIRMED_HANDLES.append(process)
                else:
                    _winapi.CloseHandle(process)
            job.close()
            if unconfirmed:
                raise CustodyFault("child termination after refused Job admission unconfirmed") from original
            if process is not None and not stopped():
                raise CustodyFault("child Job admission/resume failed; no new launch") from original
            raise
        finally:
            if thread is not None:
                _winapi.CloseHandle(thread)

    def poll(self) -> int | None:
        result = _Wait(int(self.handle), 0)
        if result == WAIT_TIMEOUT:
            return None
        if result != WAIT_OBJECT_0:
            raise _last_error("WaitForSingleObject")
        code = wintypes.DWORD()
        if not _GetExit(int(self.handle), ctypes.byref(code)):
            raise _last_error("GetExitCodeProcess")
        # The leader exited, but its grandchildren can still write. Terminate
        # the Job and prove emptiness before revealing the exit to retry policy.
        self.stop()
        return ctypes.c_int32(code.value).value

    def request_stop(self) -> None:
        """Request kernel termination without waiting for another child's teardown."""
        if self.job.handle:
            if getattr(self,'_uncaptured_stop',False):
                try:self.job.terminate()
                except (OSError,RuntimeError) as exc:
                    raise CustodyFault('Initial Job snapshot unavailable; termination retry failed') from exc
                raise CustodyFault('Initial Job Stop member snapshot remains unavailable')
            fault=None
            try:self.job.seal();self.job.capture()
            except (OSError,RuntimeError) as exc:fault=exc
            if fault is not None and not self.job.capture_complete:
                # Even a failed termination call can have partial effects.
                # Preserve the lost initial witness before entering the kernel.
                self._uncaptured_stop=True
            # A failed census must not prevent the termination request itself.
            if not getattr(self, '_termination_requested', False):
                self.job.terminate()
                self._termination_requested = True  # request, not measured emptiness
            if fault is not None:raise CustodyFault('Child Job Stop capture remains unconfirmed') from fault

    def stop(self) -> None:
        if self.job.handle:
            self.request_stop()
            self.job.wait_empty()
            # An empty Job count can precede the leader's signalled process
            # handle on Windows. Do not release custody/leases in that gap.
            if self.handle is not None and _Wait(int(self.handle),5000)!=WAIT_OBJECT_0:
                raise CustodyFault('Child leader termination unconfirmed after Job emptied')
            self.job.close()
        if self.handle is not None:
            _winapi.CloseHandle(self.handle)
            self.handle = None

    def release(self) -> None:
        """Installation handoff only; the child retains its own internal Jobs."""
        self.job.release()
        if self.handle is not None:
            _winapi.CloseHandle(self.handle)
            self.handle = None
