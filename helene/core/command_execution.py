"""Shared foreground launch and evidence tail; Forge keeps its admission/custody owner."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import contextvars
import contextlib

# Desktop fence installs these at startup; Forge retains its own custody path.
home_prepare = None
home_runner = None
home_pipe_sid = ''
home_file_policy = None
output_decoder = lambda raw: raw.decode('utf-8', 'replace')
_HOME_OBSERVATION = contextvars.ContextVar('home_process_observation', default=None)


@contextlib.contextmanager
def observe_home():
    record = {}
    token = _HOME_OBSERVATION.set(record)
    try:
        yield record
    finally:
        _HOME_OBSERVATION.reset(token)


def observe_launch(argv, cwd, pid=None):
    record = _HOME_OBSERVATION.get()
    if record is not None:
        record.update(argv=list(argv), cwd=str(cwd), handler='desktop_fence.run')
        if pid is not None: record['pid'] = int(pid)


def shell_argv(command):
    if os.name != 'nt':
        return ['bash', '-lc', command]
    # CreateProcess searches Windows directories before PATH. Resolve explicitly
    # and never admit System32/bash.exe (the WSL launcher) as a native shell.
    windows = Path(os.environ.get('SystemRoot', r'C:\Windows')).resolve()
    candidates = [Path(sys.executable).parent/'bash.exe']
    candidates += [Path(part)/'bash.exe' for part in os.environ.get('PATH','').split(os.pathsep) if part]
    for path in candidates:
        if path.is_file() and not path.resolve().is_relative_to(windows):
            return [str(path.resolve()), '-lc', command]
    raise FileNotFoundError('Native bash unavailable; the package requires runtime/bash.exe')


def launch(command, *, cwd, stdout, stdin=None, env=None, shell=False,
           executable=None, custody_directory=None, defer_start=False):
    import hand_bridge
    env = hand_bridge.child_environment(env)
    kwargs = dict(cwd=str(cwd), stdout=stdout, stderr=subprocess.STDOUT,
                  stdin=stdin if stdin is not None else subprocess.DEVNULL,
                  env=env, shell=shell, executable=executable,
                  start_new_session=(os.name != 'nt'),
                  creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
    if custody_directory is not None:
        from command_process import CommandProcess
        return CommandProcess(command, custody_directory=custody_directory,
                              defer_start=defer_start, **kwargs)
    if defer_start:
        raise ValueError('deferred launch requires a custody directory')
    if os.name == 'nt':
        return _HomeWindowsProcess(command, **kwargs)
    return _HomePosixProcess(command, **kwargs)


class _HomeWindowsProcess:
    """Bind before resume, kill the whole timed-out job, retain explicit background work."""
    def __init__(self, command, **kwargs):
        from command_process import WindowsJob
        self.job = WindowsJob()
        self.proc = None
        self.admission = None
        try:
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4
            self.proc = subprocess.Popen(command, **kwargs)
            self.pid, self.args = self.proc.pid, self.proc.args
            self.job.attach_and_resume(self.proc, resume=False)
            import process_scope
            self.admission = process_scope.admit_external(self.job.stop, lambda: self.job.resume(self.proc))
        except BaseException:
            try:
                if self.proc is not None:
                    self.proc.kill()
                    self.proc.wait()
            finally:
                self.job.close()
            raise

    def wait(self, timeout=None):
        code = self.proc.wait(timeout)
        import process_scope
        process_scope.release_external(self.admission)
        if self.job.handle:
            self.job.release_survivors()
        return code

    def stop_tree(self):
        self.job.stop()
        self.proc.wait(5)
        import process_scope
        process_scope.release_external(self.admission)
        self.job.close()
        return True, 'Windows command job terminated'


class _HomePosixProcess:
    def __init__(self, command, **kwargs):
        import process_scope
        self.proc = process_scope.spawn(command, **kwargs)
        self.pid, self.args = self.proc.pid, self.proc.args

    def wait(self, timeout=None):
        import process_scope
        deadline = time.monotonic() + timeout if timeout is not None else None
        while os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(.01)
        # Keep the PID reserved until cancellation ownership is released.
        process_scope.release(self.proc)
        return self.proc.wait()

    def stop_tree(self):
        import process_scope
        try:
            process_scope.terminate(self.proc)
            self.proc.wait(5)
        finally:
            process_scope.release(self.proc)
        return True, 'POSIX command group terminated'


def _git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, timeout=15)
    if p.returncode:
        raise OSError('Git observation unavailable')
    return p.stdout


def _fingerprint(path):
    if path.is_symlink():
        return ('link', os.readlink(path))
    if not path.exists():
        return ('missing',)
    if not path.is_file():
        return ('directory', path.stat().st_mtime_ns)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return ('file', digest.hexdigest(), path.stat().st_mode & 0o7777)


def workspace_snapshot(cwd):
    """Only the selected Git workspace; arbitrary commands can also affect other places."""
    try:
        root = Path(os.fsdecode(_git(cwd, 'rev-parse', '--show-toplevel')).removesuffix('\n'))
        try:
            head = _git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
        except OSError:
            head = ''  # an unborn repository still has observable new files
        parts = _git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all').split(b'\0')
        paths, i = set(), 0
        while i < len(parts):
            row = parts[i];i += 1
            if not row:continue
            paths.add(os.fsdecode(row[3:]))
            if b'R' in row[:2] or b'C' in row[:2]:
                paths.add(os.fsdecode(parts[i]));i += 1
        return dict(root=str(root), head=head, files={p:_fingerprint(root/p) for p in paths})
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def workspace_changes(before, cwd):
    after = workspace_snapshot(cwd)
    if before is None or after is None or before['root'] != after['root']:
        return 'not measured: Git workspace unavailable; external effects are not inventoried'
    paths = {p for p in before['files'].keys() | after['files'].keys()
             if before['files'].get(p) != after['files'].get(p)}
    if before['head'] != after['head']:
        if not before['head'] or not after['head']:
            return 'Git HEAD changed from/to an unborn state; file delta not fully measured'
        try:
            paths.update(os.fsdecode(p) for p in _git(after['root'], 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z',
                         before['head'], after['head']).split(b'\0') if p)
        except (OSError, subprocess.SubprocessError):
            return 'Git HEAD changed; exact file delta unavailable'
    detail = ', '.join(sorted(paths)) if paths else 'no working-tree byte/mode delta or HEAD file delta observed'
    return (f"{after['root']}: {detail}. Observation only; concurrent changes are not attributed "
            'and effects outside this Git workspace are not measured')


def tail(code, status, changes, *, duration=None, log='', unresolved=''):
    facts = [str(status)]
    if duration is not None:facts.append(f'{duration}s')
    if log:facts.append('log '+str(log))
    if unresolved:facts.append(f'process_id={unresolved} (poll/stop; finish blocked)')
    return '\n[command: '+'; '.join(facts)+']\n[changes: '+changes+']\n[exit '+str(code)+']'
