"""Shared foreground launch and evidence tail; Forge keeps its admission/custody owner."""
from __future__ import annotations
import hashlib
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


class ArgvRefusal(ValueError):
    """argv is unusable; nothing was launched."""


def validate_argv(argv) -> list[str]:
    """A program and its exact arguments. Never re-parsed, joined or shell-expanded."""
    if not isinstance(argv, (list, tuple)) or not argv:
        raise ArgvRefusal('argv must be a non-empty array of strings')
    for index, item in enumerate(argv):
        if not isinstance(item, str):
            raise ArgvRefusal(f'argv[{index}] must be a string, got {type(item).__name__}')
        if '\0' in item:
            raise ArgvRefusal(f'argv[{index}] contains NUL, which no program can receive')
    if not argv[0].strip():
        raise ArgvRefusal('argv[0] must name the program')
    return list(argv)


def resolve_program(program: str, cwd, env=None) -> str | None:
    """The file the OS launcher will execute, or None when it cannot be found.

    A bare name searches the child's PATH (as subprocess does for an env); a name with a
    separator is relative to cwd. This is the observation before spawn, not a lock against
    the file changing afterwards.
    """
    if os.sep in program or (os.altsep and os.altsep in program):
        path = Path(program)
        path = path if path.is_absolute() else Path(cwd) / path
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    search = os.pathsep.join(os.get_exec_path(env)) if env is not None else None
    return shutil.which(program, path=search)


@contextlib.contextmanager
def text_input(text):
    """Exact UTF-8 bytes on an unnamed private stream; EOF follows the last byte."""
    if not isinstance(text, str):
        raise ArgvRefusal('stdin_text must be a string')
    with tempfile.TemporaryFile() as stream:
        stream.write(text.encode('utf-8'))
        stream.seek(0)
        yield stream


_ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def validate_env(env) -> dict:
    """Explicit [{name, value}] pairs for one child (an array: OpenAI strict schemas cannot
    carry a free-form map). No settings or secrets are read here."""
    if env is None:
        return {}
    if not isinstance(env, (list, tuple)):
        raise ArgvRefusal('env must be an array of {"name": ..., "value": ...} objects')
    out = {}
    for row in env:
        if not isinstance(row, dict) or set(row) != {'name', 'value'}:
            raise ArgvRefusal('each env item must be exactly {"name": ..., "value": ...}')
        name, value = row['name'], row['value']
        if name in out:
            raise ArgvRefusal(f'env {name} is given twice')
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ArgvRefusal(f'env name {name!r} is not a plain variable name')
        if name.upper().startswith('PRAXIS_'):
            raise ArgvRefusal(f'env {name}: PRAXIS_* variables carry runtime-owned context '
                              '(hand bridge, edit binding, base); they cannot be set per call')
        if not isinstance(value, str) or '\0' in value:
            raise ArgvRefusal(f'env {name}: value must be a string without NUL')
        out[name] = value
    return out


def child_env(inherited, env):
    """inherited + explicit pairs; a runtime PYTHONPATH entry stays first, the caller's follows."""
    if not env:
        return None if inherited is None else dict(inherited)
    merged = dict(os.environ if inherited is None else inherited)
    for name, value in env.items():
        if name == 'PYTHONPATH' and inherited is not None and inherited.get('PYTHONPATH'):
            value = os.pathsep.join(filter(None, [inherited['PYTHONPATH'], value]))
        merged[name] = value
    return merged


def declared_paths(paths, cwd, *, label) -> list:
    """Declared output/scratch paths, relative to cwd; lexically normalised, never followed."""
    if paths is None:
        return []
    if not isinstance(paths, (list, tuple)):
        raise ArgvRefusal(f'{label} must be an array of paths')
    out = []
    for item in paths:
        if not isinstance(item, str) or not item.strip() or '\0' in item:
            raise ArgvRefusal(f'each {label} path must be a non-empty string without NUL')
        path = Path(item)
        out.append(Path(os.path.normpath(path if path.is_absolute() else Path(cwd) / path)))
    return out


def check_scratch(paths, cwd) -> None:
    """Scratch is removed after the run, so it must be ours to remove: inside cwd, no symlink,
    nothing Git tracks there (a real edit can never be declared away as scratch)."""
    base = Path(os.path.realpath(cwd))
    for path in paths:
        parent = Path(os.path.realpath(path.parent))
        if path == base or not (parent == base or base in parent.parents):
            raise ArgvRefusal(f'scratch {path} must be strictly inside cwd {base}')
        if path.is_symlink():
            raise ArgvRefusal(f'scratch {path} is a symlink; declare the real path')
        try:
            tracked = _git(base, 'ls-files', '-z', '--', str(path))
        except (OSError, subprocess.SubprocessError):
            tracked = b''  # no Git here: nothing can be tracked
        if tracked.strip(b'\0'):
            raise ArgvRefusal(f'scratch {path} is tracked by Git; only untracked throwaway files qualify')


def path_state(path) -> str:
    try:
        if path.is_symlink():
            return 'symlink -> ' + os.readlink(path)
        if path.is_dir():
            return 'directory'
        if path.is_file():
            kind = _fingerprint(path)
            return f'file sha256={kind[1]} bytes={path.stat().st_size}'
    except OSError as exc:
        return f'unreadable: {type(exc).__name__}'
    return 'missing'


def output_facts(paths, before) -> list:
    rows = []
    for path, prior in zip(paths, before):
        now = path_state(path)
        change = ('missing' if now == 'missing' and prior == 'missing' else
                  'deleted' if now == 'missing' else 'created' if prior == 'missing' else
                  'unchanged' if now == prior else 'modified')
        rows.append({'path': str(path), 'change': change, 'state': now})
    return rows


def remove_scratch(paths, *, settled, launched=True) -> list:
    """Remove declared scratch only after the program ran and its tree is known to be gone."""
    rows = []
    for path in paths:
        if not launched or not settled:
            rows.append({'path': str(path), 'removed': False,
                         'note': 'kept: nothing was launched' if not launched else
                                 'kept: the process tree was not proven stopped'})
            continue
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)  # does not follow symlinked directories
            else:
                rows.append({'path': str(path), 'removed': False, 'note': 'absent'})
                continue
            rows.append({'path': str(path), 'removed': True})
        except OSError as exc:
            rows.append({'path': str(path), 'removed': False, 'note': f'{type(exc).__name__}: {exc}'})
    return rows


def argv_facts(*, executor, cwd, argv, program, stdin_text, started, code=None,
               timed_out=False, stop=None, env_names=(), outputs=None, scratch=None) -> str:
    """One machine-readable line about an argv launch; absent observations stay absent."""
    facts = {'executor': executor, 'cwd': str(cwd), 'argv': argv,
             'started': 'unknown' if started is None else bool(started)}
    if program and program != argv[0]:
        facts['program'] = program
    facts['stdin'] = 'closed' if stdin_text is None else f'text {len(stdin_text.encode("utf-8"))} bytes then EOF'
    facts['output'] = 'stdout and stderr combined in one stream'
    if isinstance(code, int) and not timed_out:
        facts['exit_code'] = code
        if code < 0 and os.name != 'nt':
            facts['signal'] = -code
    if timed_out:
        facts['timed_out'] = True
    if stop is not None:
        facts['tree_stop'] = stop
    if env_names:
        facts['env_set'] = sorted(env_names)  # names only; values are never echoed
    if outputs is not None:
        facts['outputs'] = outputs
    if scratch is not None:
        facts['scratch'] = scratch
    return '\n[run_command ' + json.dumps(facts, ensure_ascii=False) + ']'


def launch(command, *, cwd, stdout, stdin=None, env=None, shell=False,
           executable=None, custody_directory=None, defer_start=False, parent_lifeline=False):
    import hand_bridge
    import run_context
    import step_control
    context = run_context.current_run()
    controlled = context is not None and not os.environ.get('PRAXIS_TEST')
    if controlled and step_control.requested(context.run_id):
        raise step_control.StepCancelled('run cancellation requested before process launch')
    env = hand_bridge.child_environment(env)
    kwargs = dict(cwd=str(cwd), stdout=stdout, stderr=subprocess.STDOUT,
                  stdin=stdin if stdin is not None else subprocess.DEVNULL,
                  env=env, shell=shell, executable=executable,
                  start_new_session=(os.name != 'nt'),
                  creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0))
    if custody_directory is not None or controlled:
        from command_process import CommandProcess
        with step_control.starting(context.run_id) if controlled else contextlib.nullcontext():
            process = CommandProcess(command, custody_directory=custody_directory,
                                     defer_start=defer_start, parent_lifeline=parent_lifeline, **kwargs)
            try:return step_control.track(process) if controlled else process
            except BaseException:
                process.stop_tree()
                raise
    if defer_start:
        raise ValueError('deferred launch requires a custody directory')
    return subprocess.Popen(command, **kwargs)


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


def workspace_changes(before, cwd, exclude=()):
    """exclude: declared scratch paths, already removed by the caller, are not a change."""
    after = workspace_snapshot(cwd)
    if before is None or after is None or before['root'] != after['root']:
        return 'not measured: Git workspace unavailable; external effects are not inventoried'
    paths = {p for p in before['files'].keys() | after['files'].keys()
             if before['files'].get(p) != after['files'].get(p)}
    if exclude:
        root = Path(after['root'])
        skip = [Path(os.path.realpath(x.parent)) / x.name for x in exclude]
        paths = {p for p in paths if not any(root / p == s or s in (root / p).parents for s in skip)}
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
