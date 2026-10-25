"""Darwin observed-process custody; not Linux subreaper or kernel-total containment.

An independent pipe guardian pins the root birth/group, watches NOTE_EXIT and
retires observed descendants plus exact inherited-marker members. Protected or
marker-shedding detached processes may escape observation; every receipt says so.
"""
from __future__ import annotations
import json
from pathlib import Path
import secrets
import select
import subprocess
import sys
if __package__:
    from . import posix_process as _posix
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import posix_process as _posix
CustodyFault = _posix.CustodyFault


class DarwinChild(_posix.PosixChild):
    cleanup_evidence = None

    def _message(self, seconds=0):
        message = super()._message(seconds)
        if message and 'evidence' in message:
            self.cleanup_evidence = message['evidence']
        return message

    @classmethod
    def spawn(cls, argv, *, cwd, env, log, stopped):
        if sys.platform != 'darwin':
            raise RuntimeError('Darwin child adapter requires native macOS')
        if stopped():
            raise RuntimeError('owner Stop before child preparation')
        with _posix._LEASE_LOCK:
            key = env.get('HELENE_TREE')
            leases = (_posix._LEASES.get(str(Path(key).resolve())),) if key else tuple(_posix._LEASES.values())
            leases = tuple(fd for fd in leases if fd is not None)
            guardian_env = {k: v for k, v in os.environ.items()
                            if not k.startswith('HELENE_CUSTODY_')}
            process = subprocess.Popen([sys.executable, '-I', '-u', str(Path(__file__).resolve())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=guardian_env, start_new_session=True, close_fds=True, pass_fds=leases)
        child = cls(process)
        try:
            child._send({'argv': list(argv), 'cwd': str(cwd), 'env': env, 'log': str(log)})
            ready = child._message(10)
            if not ready or ready.get('state') != 'ready':
                raise RuntimeError('Darwin guardian preparation failed: ' + str(ready))
            if stopped():
                raise RuntimeError('owner Stop before shim preparation')
            child._send({'action': 'start'})
            prepared = child._message(10)
            if not prepared or prepared.get('state') != 'prepared':
                raise RuntimeError('Darwin shim preparation failed: ' + str(prepared))
            child.pid = prepared['pid']
            child.root_identity = prepared
            # FINAL admission decision, after measurement/watch while the shim
            # is still blocked. EOF or Stop never releases target instructions.
            if stopped():
                raise RuntimeError('owner Stop before target release')
            child._send({'action': 'release-target'})
            started = child._message(10)
            if not started or started.get('state') != 'started':
                raise RuntimeError('Darwin target release unconfirmed: ' + str(started))
            return child
        except BaseException as original:
            process.stdin.close()  # EOF requests cleanup, not ownership transfer
            try:
                code = process.wait(15)
            except subprocess.TimeoutExpired as exc:
                fault = CustodyFault('Darwin admission cleanup unconfirmed')
                fault.children = (child,)
                raise fault from exc
            if code != 0:
                fault = CustodyFault('Darwin guardian failed during admission cleanup')
                fault.children = (child,)
                raise fault from original
            raise


import os
import signal
import time
import psutil

RESIDUAL = (
    "Detached descendants that left the observed tree/group and discarded "
    "or hid their inherited marker before observation cannot be ruled out."
)


class Census:
    """Attributed process identities, never command-name or same-user ownership."""
    def __init__(self, root, birth, pgid, marker):
        self.root = root
        self.birth = birth
        self.pgid = pgid
        self.marker = marker
        self.known = {root: birth}
        self.unattributed_unreadable = set()

    def scan(self):
        # Table enumeration failure propagates: it is not an empty census.
        pids = psutil.pids()
        rows = {}
        unreadable = set()
        self.unattributed_unreadable = set()

        for pid in pids:
            if pid <= 1 or pid == os.getpid():
                continue
            try:
                p = psutil.Process(pid)  # fresh identity, not cached process_iter
                birth = p.create_time()
                rows[pid] = {
                    "birth": birth,
                    "ppid": p.ppid(),
                    "status": p.status(),
                    "uid": p.uids().effective,
                    "process": p,
                }
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                unreadable.add(pid)

        # Root stays unreaped in the guardian until cleanup completes. Thus its
        # PGID remains pinned during these group-membership observations.
        for pid, row in rows.items():
            p = row["process"]
            if row["birth"] < self.birth:
                continue
            try:
                grouped = os.getpgid(pid) == self.pgid
            except ProcessLookupError:
                continue
            except PermissionError:
                grouped = False  # not affirmative attribution

            marked = False
            if row["uid"] == os.geteuid():
                try:
                    marked = p.environ().get(self.marker) == "1"
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied:
                    if pid not in self.known and not grouped:
                        self.unattributed_unreadable.add(pid)

            if grouped or marked:
                self.known[pid] = row["birth"]

        # Recover ancestry still visible in this snapshot, including children
        # whose environment was replaced. Parent PID alone is not enough:
        # its current birth must match the previously attributed identity.
        changed = True
        while changed:
            changed = False
            for pid, row in rows.items():
                if pid in self.known:
                    continue
                parent = row["ppid"]
                parent_row = rows.get(parent)
                if (
                    parent in self.known
                    and parent_row is not None
                    and parent_row["birth"] == self.known[parent]
                    and row["birth"] >= parent_row["birth"]
                ):
                    self.known[pid] = row["birth"]
                    changed = True

        alive, unknown = [], []
        for pid, birth in self.known.items():
            row = rows.get(pid)
            if row is None:
                # Missing from one table snapshot is not sufficient: settle
                # this previously attributed identity directly.
                try:
                    p = psutil.Process(pid)
                    current_birth = p.create_time()
                    status = p.status()
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied:
                    unknown.append(pid)
                    continue
            else:
                current_birth, status = row["birth"], row["status"]

            if current_birth != birth:
                continue  # recorded identity ended; recycled PID is not ours
            if status != psutil.STATUS_ZOMBIE:
                alive.append(pid)
        return alive, unknown

    def signal_known(self, pid, sig):
        """Signal a measured identity; report errors, never label them exit."""
        birth = self.known[pid]
        try:
            p = psutil.Process(pid)
            if p.create_time() != birth:
                return
            if p.status() == psutil.STATUS_ZOMBIE:
                return
            p.send_signal(sig)
        except psutil.NoSuchProcess:
            return
        # AccessDenied and other signal errors reach the guardian's evidence.
        # Revalidation still has an unavoidable validate-then-signal race.

    def cleanup(self, *, fault, grace=3.0, report_after=12.0):
        begun = time.monotonic()
        quiet = 0
        sent = set()
        reported = False
        while True:
            try:
                alive, unknown = self.scan()
            except (OSError, psutil.Error) as exc:
                alive, unknown = [], list(self.known)
                if not reported:
                    fault("Darwin census unavailable: " + str(exc))
                    reported = True

            sig = (
                signal.SIGTERM
                if time.monotonic() - begun < grace
                else signal.SIGKILL
            )
            for pid in alive:
                identity = (pid, self.known[pid], sig)
                if identity in sent:
                    continue
                try:
                    self.signal_known(pid, sig)
                    sent.add(identity)
                except (OSError, psutil.Error) as exc:
                    if not reported:
                        fault("Darwin signal unconfirmed: " + str(exc))
                        reported = True

            if alive or unknown:
                quiet = 0
            else:
                quiet += 1
                if quiet >= 2:
                    return {
                        "scope": "observed-tree+group+inherited-marker",
                        "known_alive": [],
                        "known_unreadable": [],
                        "unattributed_environment_unreadable":
                            len(self.unattributed_unreadable),
                        "residual": RESIDUAL,
                    }

            if (
                time.monotonic() - begun >= report_after
                and not reported
            ):
                fault(
                    "Darwin cleanup unconfirmed; alive="
                    + repr(alive) + "; unknown=" + repr(unknown)
                )
                reported = True

            # After a fault, retain the guardian and inherited tree lease while
            # known ownership remains unsettled. A caller timeout does not free
            # admission for another writer.
            time.sleep(0.1)


# The shim cannot execute argv until its independently watched owner releases it.
_SHIM = """import os,sys
fd=int(sys.argv[1])
release=os.read(fd,1)
os.close(fd)
if release != b'S': raise SystemExit(125)
os.execvpe(sys.argv[2],sys.argv[2:],os.environ)
"""


def report(log, value):
    """Reporting ENOSPC/permission failure must never cancel physical cleanup."""
    try:
        with log.with_name(log.name+'.custody.jsonl').open('ab', buffering=0) as output:
            output.write((json.dumps(value) + '\n').encode())
    except OSError as exc:
        value = dict(value, log_error=str(exc))
    _posix._emit(value)


def guardian():
    if sys.platform != 'darwin' or not hasattr(select, 'kqueue'):
        raise RuntimeError('native Darwin kqueue required')
    request = json.loads(sys.stdin.buffer.readline())
    log = Path(request['log'])
    log.parent.mkdir(parents=True, exist_ok=True)
    def emit(value):
        report(log, value)
    emit({'state': 'ready'})
    command = sys.stdin.buffer.readline()
    if not command or json.loads(command).get('action') != 'start':
        return
    read_fd, release_fd = os.pipe()
    child = None
    census = None
    watch = None
    released = False
    stopping = False
    def stop(_sig, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        marker = 'HELENE_CUSTODY_' + secrets.token_hex(16)
        env = dict(request['env'], **{marker: '1', 'HELENE_PARENT_PID': str(os.getpid())})
        with log.open('ab', buffering=0) as output:
            child = subprocess.Popen([sys.executable, '-I', '-u', '-c', _SHIM,
                                      str(read_fd), *request['argv']],
                cwd=request['cwd'], env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=output, start_new_session=True,
                close_fds=True, pass_fds=(read_fd,))
        os.close(read_fd)
        read_fd = None
        birth = psutil.Process(child.pid).create_time()
        pgid = os.getpgid(child.pid)
        census = Census(child.pid, birth, pgid, marker)
        watch = select.kqueue()
        event = select.kevent(child.pid, filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE, fflags=select.KQ_NOTE_EXIT)
        watch.control([event], 0, 0)
        emit({'state': 'prepared', 'pid': child.pid, 'birth': birth, 'pgid': pgid})
        # This is the second owner decision, not the earlier start frame.
        command = sys.stdin.buffer.readline()
        if stopping or not command or json.loads(command).get('action') != 'release-target':
            return
        os.write(release_fd, b'S')
        os.close(release_fd)
        release_fd = None
        emit({'state': 'started', 'pid': child.pid})
        while not stopping:
            if watch.control(None, 1, .05):
                break  # do NOT poll/reap the root before group cleanup
            try:
                root = psutil.Process(child.pid)
                if root.create_time() == birth:
                    for descendant in root.children(recursive=True):
                        try:
                            census.known[descendant.pid] = descendant.create_time()
                        except psutil.NoSuchProcess:
                            pass
                        except psutil.AccessDenied:
                            pass  # final census/direct known identity read remains authoritative
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass  # never used as an empty cleanup receipt
            if not released and select.select([sys.stdin.buffer], [], [], 0)[0]:
                command = sys.stdin.buffer.readline()
                if not command:
                    break
                action = json.loads(command).get('action')
                if action == 'release':
                    released = True
                    emit({'state': 'released', 'evidence': {
                        'scope': 'explicit-lifetime-transfer', 'residual': RESIDUAL,
                        'cleanup': 'not asserted; replacement owns its children'}})
                elif action == 'stop':
                    break
    finally:
        if read_fd is not None:
            os.close(read_fd)
        if release_fd is not None:
            os.close(release_fd)  # blocked shim exits on EOF, never execs target
        if child is not None:
            if census is None:
                # No target was released before census/watch establishment.
                child.wait(10)
            elif released and not stopping:
                child.wait()  # explicitly transferred descendants are not killed
            else:
                evidence = census.cleanup(fault=lambda error: emit({
                    'state': 'fault', 'error': error,
                    'evidence': {'scope': 'observed-tree+group+inherited-marker',
                                 'residual': RESIDUAL}}))
                code = child.wait()
                emit({'state': 'empty', 'code': code, 'evidence': evidence})
        if watch is not None:
            watch.close()


if __name__ == '__main__':
    guardian()
