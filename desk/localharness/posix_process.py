"""Linux child custody for the Python desktop owner.

A private guardian becomes a subreaper BEFORE starting the selected process.
Closing the owner's pipe (including SIGKILL) retires every guardian descendant,
including double-fork/setsid orphans. Leader exit is withheld until cleanup.
This is cooperative same-user custody, not an adversarial privilege sandbox.
Darwin has no Linux subreaper: do not silently substitute process-group parity.
"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time


class CustodyFault(RuntimeError):
    """Physical cleanup is unknown; never admit a replacement generation."""


# Guardians inherit the same open-file description as the tree lease. The
# parent's death cannot free admission while a guardian still retires children.
from contextlib import contextmanager
import stat
import threading
_LEASES = {}
_LEASE_LOCK = threading.Lock()


@contextmanager
def tree_hold(tree):
    import fcntl
    tree = Path(tree).resolve(strict=True)
    path = tree / '.desktop-owner.lock'
    fd = os.open(path, os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError('desktop lease is not a regular file')
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        def record(word):
            os.lseek(fd, 0, os.SEEK_SET); os.ftruncate(fd, 0)
            os.write(fd, word); os.fsync(fd)
        record(b'active\n')
        with _LEASE_LOCK:
            _LEASES[str(tree)] = fd
        yield lambda: record(b'clean\n')
    finally:
        with _LEASE_LOCK:
            if _LEASES.get(str(tree)) == fd:
                _LEASES.pop(str(tree))
        # Do not LOCK_UN: inherited guardian descriptors must keep admission
        # closed after an owner crash until their own cleanup actually ends.
        os.close(fd)


class PosixChild:
    def __init__(self, process):
        self.process = process
        self.handle = process  # same lifecycle predicate as the Windows adapter
        self.pid = None
        self.buffer = b''
        self.code = None

    def _message(self, seconds=0):
        end = time.monotonic() + seconds
        while True:
            if b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                return json.loads(line)
            remaining = max(0, end-time.monotonic())
            if not select.select([self.process.stdout], [], [], remaining)[0]:
                return None
            data = os.read(self.process.stdout.fileno(), 65536)
            if not data:
                raise CustodyFault('guardian ended without a terminal cleanup receipt')
            self.buffer += data
            if len(self.buffer) > 65536:
                raise CustodyFault('guardian receipt exceeds its protocol')

    def _send(self, value):
        self.process.stdin.write((json.dumps(value)+'\n').encode())
        self.process.stdin.flush()

    @classmethod
    def spawn(cls, argv, *, cwd, env, log, stopped):
        if not sys.platform.startswith('linux'):
            raise RuntimeError('POSIX desktop custody currently qualified on Linux only; Darwin needs a native adapter')
        if stopped():
            raise RuntimeError('owner Stop before child preparation')
        with _LEASE_LOCK:
            key = env.get('HELENE_TREE')
            leases = (_LEASES.get(str(Path(key).resolve())),) if key else tuple(_LEASES.values())
            leases = tuple(fd for fd in leases if fd is not None)
            process = subprocess.Popen([sys.executable, '-I', '-u', str(Path(__file__).resolve()), '--guardian'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, close_fds=True, pass_fds=leases)
        child = cls(process)
        try:
            child._send({'argv': list(argv), 'cwd': str(cwd), 'env': env, 'log': str(log)})
            message = child._message(10)
            if not message or message.get('state') != 'ready':
                raise RuntimeError('guardian preparation failed: '+str(message))
            if stopped():
                child._send({'action':'stop'})
                process.wait(10)
                raise RuntimeError('owner Stop before child release')
            child._send({'action':'start'})
            message = child._message(10)
            if not message or message.get('state') != 'started':
                raise RuntimeError('child launch refused: '+str(message))
            child.pid = message['pid']
            return child
        except BaseException:
            # EOF requests cleanup even if the launch reply was lost.
            process.stdin.close()
            try:
                process.wait(12)
            except subprocess.TimeoutExpired as exc:
                raise CustodyFault('guardian cleanup after failed admission unconfirmed') from exc
            raise

    def poll(self):
        if self.handle is None:
            return self.code
        message = self._message()
        if message is None:
            return None
        if message.get('state') != 'empty':
            raise CustodyFault('guardian cleanup fault: '+str(message))
        self.code = message['code']
        self.process.wait(5)
        if self.process.returncode != 0:
            raise CustodyFault('guardian failed after cleanup receipt')
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass  # terminal empty receipt already proved cleanup
        self.process.stdout.close(); self.process.stderr.close()
        self.handle = None
        return self.code

    def request_stop(self):
        """Send before waiting, so one uncertain child cannot delay its siblings."""
        if self.handle is not None:
            # Failed admission already closed this pipe to request EOF cleanup.
            # Retaining its handle must still allow reading the final receipt.
            if self.process.stdin.closed:
                return
            try:
                self._send({'action': 'stop'})
            except BrokenPipeError:
                pass  # a broken pipe proves no cleanup; poll still owes its receipt
            except ValueError:
                if not self.process.stdin.closed:
                    raise  # unrelated protocol errors are not EOF evidence

    def stop(self):
        if self.handle is None:
            return
        self.request_stop()
        end = time.monotonic()+15
        while self.handle is not None:
            self.poll()
            if time.monotonic() >= end:
                raise CustodyFault('guardian subtree Stop unconfirmed')
            time.sleep(.02)

    def release(self):
        """Updater transfers lifetime: guardian only reaps, never kills on app exit."""
        self._send({'action':'release'})
        message = self._message(5)
        if not message or message.get('state') != 'released':
            raise CustodyFault('guardian lifetime handoff unconfirmed')
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass  # terminal empty receipt already proved cleanup
        self.process.stdout.close(); self.process.stderr.close()
        self.handle = None


def _emit(value):
    try:
        print(json.dumps(value), flush=True)
    except BrokenPipeError:
        pass  # dead parent cannot receive; cleanup remains our own obligation


def _descendants():
    import psutil
    try:
        return psutil.Process().children(recursive=True)
    except psutil.Error as exc:
        raise CustodyFault('cannot observe guardian descendants') from exc


def _cleanup():
    """Repeated enumeration closes fork/reparent races; ECHILD proves no writer."""
    import psutil
    deadline = time.monotonic()+3
    fault_sent = False
    while True:
        for process in reversed(_descendants()):
            try:
                if time.monotonic() < deadline:
                    process.terminate()
                else:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                if not fault_sent:
                    _emit({'state':'fault', 'error':str(exc)})
                    fault_sent = True
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return  # all live children + adopted orphans have ended
            if pid == 0:
                break
        time.sleep(.02)


def guardian():
    # Only this isolated guardian is a subreaper; never mutate the GUI process.
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Linux subreaper required')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'subreaper unavailable')
    import psutil  # check dependency before any target instruction
    request = json.loads(sys.stdin.buffer.readline())
    _emit({'state':'ready'})
    command = sys.stdin.buffer.readline()
    if not command or json.loads(command).get('action') != 'start':
        return
    # Parent may have gone between the release frame and spawn. EOF/Stop before
    # spawning refuses; a later death is handled by this same guardian loop.
    if select.select([sys.stdin.buffer], [], [], 0)[0]:
        return
    env = dict(request['env'], HELENE_PARENT_PID=str(os.getpid()))
    log = Path(request['log']); log.parent.mkdir(parents=True, exist_ok=True)
    child = None
    released = False
    stopping = False
    def stop(_sig, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with log.open('ab', buffering=0) as output:
            child = subprocess.Popen(request['argv'], cwd=request['cwd'], env=env,
                stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                start_new_session=True, close_fds=True)
        _emit({'state':'started', 'pid':child.pid})
        code = None
        while not stopping:
            # waitid observes exit WITHOUT releasing the PID before descendant
            # cleanup. Linux subreaper keeps orphaned nested sessions discoverable.
            status = os.waitid(os.P_PID, child.pid, os.WEXITED|os.WNOHANG|os.WNOWAIT)
            if status is not None:
                code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
                break
            if not released and select.select([sys.stdin.buffer], [], [], .03)[0]:
                data = sys.stdin.buffer.readline()
                if not data:
                    break
                action = json.loads(data).get('action')
                if action == 'release':
                    released = True
                    _emit({'state':'released'})
                elif action == 'stop':
                    break
            elif released:
                time.sleep(.03)
    finally:
        if released and not stopping:
            # The new application owns its own role guardians. It may launch a
            # detached next updater before exiting; killing adopted descendants
            # here would destroy that valid handoff. Remain only a reaper until
            # all transferred descendants naturally finish. Explicit TERM still
            # retires this outer generation; ordinary GUI exit does not.
            while not stopping:
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if pid == 0:
                    time.sleep(.03)
        if not released or stopping:
            _cleanup()
    if not released:
        _emit({'state':'empty', 'code':code if code is not None else -signal.SIGTERM})


if __name__ == '__main__':
    guardian()
