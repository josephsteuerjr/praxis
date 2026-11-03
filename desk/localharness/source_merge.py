"""Three immutable source snapshots and a Git merge, away from live code.

The private repository has no remotes, hooks, filters or owner Git settings.
Existing agent repositories are copied by migration and never rewritten here.
"""
from __future__ import annotations

import hashlib
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time

from .package_notice import read, write

EXCLUDED = {'.helene', '.git', '__pycache__', '.venv', 'venv', 'node_modules', 'memory',
            'soul', 'dossiers', 'graph', 'state', 'workspace', 'models', 'data',
            'profile', 'backups', 'archives', 'recovery', 'cache', '.cache', '.secrets', 'local_auth',
            '.pytest_cache', 'logs', '.source-updates', '.migration-webview', '.webview'}
PRIVATE = {'helene.json', 'auth.json', 'credentials.json', 'owner-stop.json'}


def relative(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
            or ':' in name or any(ord(c) < 32 for c in name)
            or path.as_posix() != name):
        raise ValueError('Invalid source path: ' + repr(name))
    return path


def allowed(name):
    path = relative(name)
    return (not any(p.lower() in EXCLUDED for p in path.parts)
            and path.name.lower() not in PRIVATE
            and not path.name.lower().startswith(('.env', '.python-', '.desktop-'))
            and not path.name.lower().endswith(('.pyc', '.log', '.lock', '.session', '.session-journal')))


def inventory(root,*,application=False):
    from .validation_read import checkpoint,digest
    checkpoint()
    if application:
        from .update_composition import app_inventory
        return app_inventory(root)
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    result = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        checkpoint()
        here = Path(directory)
        dirs[:] = sorted(d for d in dirs if allowed((here / d).relative_to(root).as_posix()))
        for name in [*dirs, *sorted(names)]:
            checkpoint()
            path = here / name
            rel = path.relative_to(root).as_posix()
            if not allowed(rel):
                continue
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('Linked source path: ' + rel)
            if path.is_file():
                with path.open('rb') as stream:measured=digest(stream)
                result[rel] = {'sha256': measured,
                               'mode': '100755' if os.name != 'nt' and path.stat().st_mode & 0o111 else '100644'}
    return result


def signature(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def input_signature(value):
    """Bind the immutable sides, source identity and permitted resolution surface."""
    fields={key: value[key] for key in ('local_path','commits','files','conflicts','automatic')}
    if value.get('application'):fields['application']=True
    if value.get('app_source'):fields['app_source']=value['app_source']
    return signature(fields)


def validate_inputs(directory, expected=''):
    directory = Path(directory)
    value = read(directory / 'plan.json')
    if expected and input_signature(value) != expected:
        raise ValueError('Source merge input identity changed')
    for side in ('base', 'local', 'incoming'):
        if inventory(directory / side,application=value.get('application',False)) != value['files'][side]:
            raise ValueError('Frozen source merge side changed: ' + side)
    return value


def candidate_signature(directory):
    value=read(Path(directory)/'plan.json')
    return signature(inventory(Path(directory) / 'result',application=value.get('application',False)))


def reopen(directory):
    """Keep the previous candidate while reopening its declared source for repair."""
    directory = Path(directory)
    value = validate_inputs(directory)
    value['state'] = 'conflicts'
    write(directory / 'plan.json', value)
    return value


def executable(*programs):
    for program in programs:
        for name in ('runtime/git/cmd/git.exe', 'runtime/git/bin/git'):
            path = Path(program) / name
            if path.is_file():
                return str(path)
    found = shutil.which('git')
    if not found:
        raise RuntimeError('Git is missing from the application runtime')
    return found


def git(repo, *args, data=None, check=True, exe='git'):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME='Helene source update', GIT_AUTHOR_EMAIL='update@helene.local',
               GIT_COMMITTER_NAME='Helene source update', GIT_COMMITTER_EMAIL='update@helene.local',
               GIT_TERMINAL_PROMPT='0')
    # Git-for-Windows validates an absolute --git-dir before command config is
    # applied. A relative private repository plus local longpaths handles both
    # that early setup and later loose objects without changing user settings.
    repo = Path(repo).resolve()
    command = [exe, '-c', 'core.longpaths=true', '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=' + os.devnull,
               '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-c', 'core.quotepath=false',
               '--git-dir=' + repo.name, *args]
    from .validation_read import current,checkpoint
    if current() is not None and args==('rev-parse','HEAD') and data is None:
        checkpoint()
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=repo.parent,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            started=time.monotonic()
            try:
                while True:
                    checkpoint()
                    remaining=120-(time.monotonic()-started)
                    if remaining<=0:raise subprocess.TimeoutExpired(command,120)
                    try:output,error=process.communicate(timeout=min(.2,remaining))
                    except subprocess.TimeoutExpired:continue
                    checkpoint()
                    done=subprocess.CompletedProcess(command,process.returncode,output,error)
                    break
            except BaseException as failure:
                if process.poll() is None:process.kill()
                output,error=process.communicate()
                if isinstance(failure,subprocess.TimeoutExpired):
                    failure.output=output;failure.stderr=error
                raise
    else:
        done = subprocess.run(command, input=data, capture_output=True, env=env, cwd=repo.parent, timeout=120,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if check and done.returncode:
        raise RuntimeError('Source merge Git failed: ' + done.stderr.decode('utf-8', 'replace')[-1600:])
    return done


def snapshot(repo, root, files, parents=(), *, exe='git', label='source snapshot'):
    names = sorted(files)
    # hash-object --no-filters uses the actual bytes, regardless of attributes.
    paths = ''.join(json.dumps((Path(root) / n).as_posix(), ensure_ascii=False) + '\n' for n in names)
    hashes = git(repo, 'hash-object', '-w', '--no-filters', '--stdin-paths',
                 data=paths.encode(), exe=exe).stdout.decode().splitlines()
    if len(hashes) != len(names):
        raise RuntimeError('Incomplete source snapshot')
    git(repo, 'read-tree', '--empty', exe=exe)
    rows = b''.join((files[n]['mode'] + ' ' + oid + '\t' + n + '\0').encode()
                    for n, oid in zip(names, hashes))
    git(repo, 'update-index', '-z', '--index-info', data=rows, exe=exe)
    tree = git(repo, 'write-tree', exe=exe).stdout.decode().strip()
    args = ['commit-tree', tree]
    for parent in parents:
        args.extend(('-p', parent))
    return git(repo, *args, data=(label + '\n').encode(), exe=exe).stdout.decode().strip()


def extract(repo, revision, destination, *, exe='git',application=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for row in git(repo, 'ls-tree', '-r', '-z', revision, exe=exe).stdout.split(b'\0'):
        if not row:
            continue
        meta, raw = row.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        name = raw.decode('utf-8')
        relative(name)
        if mode not in ('100644', '100755') or kind != 'blob' or (not application and not allowed(name)):
            raise ValueError('Unsupported merged source entry: ' + name)
        entries.append((mode, oid, name))
    raw = git(repo, 'cat-file', '--batch', data=(''.join(oid + '\n' for _, oid, _ in entries)).encode(), exe=exe).stdout
    position = 0
    for mode, oid, name in entries:
        end = raw.index(b'\n', position)
        header = raw[position:end].split()
        length = int(header[2])
        if header[:2] != [oid.encode(), b'blob']:
            raise ValueError('Source object identity changed')
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw[end + 1:end + 1 + length])
        if os.name != 'nt':
            path.chmod(0o755 if mode == '100755' else 0o644)
        position = end + 2 + length


def create(base, local, incoming, directory, *, exe='git',application=False):
    directory = Path(directory)
    directory.mkdir(parents=True)
    repo = directory / 'repository.git'
    git(repo, 'init', '--bare', '--quiet', exe=exe)
    inventories = {key: inventory(path,application=application) for key, path in
                   (('base', base), ('local', local), ('incoming', incoming))}
    commits = {}
    for key, path in (('base', base), ('local', local), ('incoming', incoming)):
        commits[key] = snapshot(repo, path, inventories[key],
                                (commits['base'],) if key != 'base' else (), exe=exe, label=key)
        git(repo, 'update-ref', 'refs/heads/' + key, commits[key], exe=exe)
    done = git(repo, 'merge-tree', '--write-tree', '--name-only', '--messages',
               commits['local'], commits['incoming'], check=False, exe=exe)
    if done.returncode not in (0, 1):
        raise RuntimeError('Git 2.38 or newer is required for source updates: ' + done.stderr.decode('utf-8', 'replace')[-1000:])
    lines = done.stdout.decode('utf-8').splitlines()
    conflicts = []
    if done.returncode == 1:
        for name in lines[1:]:
            if not name:
                break
            name = json.loads(name) if name.startswith('"') else name
            relative(name)
            conflicts.append(name)
        if not conflicts:
            raise RuntimeError('Git reported a structural conflict without resolvable paths')
    for key, commit in commits.items():
        extract(repo, commit, directory / key, exe=exe,application=application)
        if inventory(directory / key,application=application) != inventories[key]:
            raise ValueError('Source changed while taking the ' + key + ' snapshot')
    extract(repo, lines[0], directory / 'result', exe=exe,application=application)
    value = {'format': 'helene-source-merge.v1', 'state': 'conflicts' if conflicts else 'prepared',
             'local_path': str(Path(local).resolve()), 'commits': commits, 'files': inventories,
             'conflicts': conflicts, 'resolved': {}, 'automatic': inventory(directory / 'result',application=application)}
    if application:value['application']=True
    write(directory / 'plan.json', value)
    if not conflicts:
        try:finish(directory, exe=exe)
        except SyntaxError as error:
            if not application:raise
            value.update(state='conflicts',compile_error=str(error));write(directory/'plan.json',value)
    return read(directory / 'plan.json')


def finish(directory, *, exe='git'):
    directory = Path(directory)
    value = validate_inputs(directory)
    actual = inventory(directory / 'result',application=value.get('application',False))
    if set(value['conflicts']) != set(value['resolved']):
        raise ValueError('Resolve every source conflict before finishing')
    for name in set(value['automatic']) | set(actual):
        if name not in value['conflicts'] and actual.get(name) != value['automatic'].get(name):
            repairs = [row for row in value.get('repairs', []) if row['path'] == name]
            if not repairs or repairs[-1]['after'] != actual.get(name):
                raise ValueError('Resolution changed an unrelated source file without a recorded repair: ' + name)
    for name, entry in actual.items():
        content = (directory / 'result' / name).read_bytes()
        if re.search(rb'(?m)^(<<<<<<< |=======$|>>>>>>> )', content):
            raise ValueError('Unresolved conflict markers: ' + name)
        if name.endswith('.py') and entry != value['files']['local'].get(name):
            compile(content, name, 'exec')
    repo = directory / 'repository.git'
    commit = snapshot(repo, directory / 'result', actual,
                      (value['commits']['local'], value['commits']['incoming']), exe=exe, label='merge upstream into local source')
    git(repo, 'update-ref', 'refs/heads/merged', commit, exe=exe)
    value.update(state='ready', result=actual, merged_commit=commit)
    value.pop('compile_error',None)
    revisions = value.setdefault('revisions', [])
    identity = signature(actual)
    if not revisions or revisions[-1]['candidate_sha256'] != identity:
        revisions.append({'candidate_sha256': identity, 'merged_commit': commit, 'at': time.time()})
    write(directory / 'plan.json', value)
    return value


def resolve(directory, name, content=None, *, delete=False, side='', reason='', expected_candidate=''):
    directory = Path(directory)
    value = validate_inputs(directory)
    relative(name)
    declared = set().union(*(set(side) for side in value['files'].values()))
    if value.get('state') != 'conflicts' or (not value.get('application') and not allowed(name)) or name not in declared:
        raise ValueError('Path is not a declared candidate source input')
    repair = name not in value['conflicts']
    before = inventory(directory / 'result',application=value.get('application',False))
    if expected_candidate and signature(before) != expected_candidate:
        raise ValueError('Candidate changed before resolution; read its current state')
    if repair and (not str(reason).strip() or not expected_candidate):
        raise ValueError('Nonconflict repair requires a reason and exact expected candidate SHA256')
    path = directory / 'result' / name
    if side:
        if side not in ('base', 'local', 'incoming') or delete or content is not None:
            raise ValueError('Choose one source side or supply a resolution')
        source = directory / side / name
        if source.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, path)
        else:
            path.unlink(missing_ok=True)
    elif delete:
        path.unlink(missing_ok=True)
    else:
        if not isinstance(content, str):
            raise ValueError('A complete UTF-8 resolution or explicit deletion is required')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8', newline='')
    if repair:
        after = inventory(directory / 'result',application=value.get('application',False)).get(name)
        value.setdefault('repairs', []).append({'path': name, 'reason': str(reason).strip(),
            'before': before.get(name), 'after': after, 'at': time.time(),
            'candidate_before_sha256': signature(before)})
    else:
        value['resolved'][name] = side or ('deleted' if delete else 'written')
    write(directory / 'plan.json', value)
    return value


def apply_upstream(directory,target):
    """Publish the reviewed diff into independent incoming upstream code.
    No intermediate full copy of the former authored tree is needed.
    """
    directory,target=Path(directory),Path(target).resolve()
    value=validate_inputs(directory)
    from .source_update import validate_ready,requires_gate
    if requires_gate(value,directory):validate_ready(directory)
    if value.get('state')!='ready' or inventory(directory/'result')!=value['result']:
        raise ValueError('Reviewed result changed')
    if target==Path(value['local_path']).resolve():raise ValueError('Cannot replace the running tree')
    if inventory(value['local_path'])!=value['files']['local']:raise ValueError('Authored code changed after review')
    before=inventory(target) if target.exists() else {}
    if target.exists() and before!=value['files']['incoming']:
        raise ValueError('New upstream changed before publishing the reviewed diff')
    target.mkdir(parents=True,exist_ok=True)
    for name in sorted(set(before)-set(value['result']),reverse=True):
        path=target/name;path.unlink();parent=path.parent
        while parent!=target:
            try:parent.rmdir()
            except OSError:break
            parent=parent.parent
    for name,row in value['result'].items():
        if before.get(name)==row:continue
        path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.resolve().is_relative_to(target):raise ValueError('Merged path leaves upstream')
        fd,temporary=tempfile.mkstemp(prefix='.upstream-diff-',dir=path.parent);os.close(fd)
        try:shutil.copy2(directory/'result'/name,temporary);os.replace(temporary,path)
        finally:Path(temporary).unlink(missing_ok=True)
    if inventory(target)!=value['result']:raise RuntimeError('Published upstream differs from reviewed result')
    return value


def apply(directory, target):
    directory, target = Path(directory), Path(target).resolve()
    value = validate_inputs(directory)
    from .source_update import validate_ready, requires_gate
    if requires_gate(value, directory):
        validate_ready(directory)
    if value.get('state') != 'ready' or inventory(directory / 'result') != value['result']:
        raise ValueError('Source merge is not ready or its result changed')
    if inventory(target) != value['files']['local']:
        raise ValueError('Local code changed during update preparation; merge again')
    # Target is the independent migration copy, never the running source.
    if target == Path(value['local_path']).resolve():
        raise ValueError('Cannot apply a merge over running source')
    if inventory(value['local_path']) != value['files']['local']:
        raise ValueError('Local code changed during update preparation; merge again')
    for name in sorted(set(value['files']['local']) - set(value['result']), reverse=True):
        removed = target / name
        removed.unlink()
        # Git may replace a whole directory with a file. Only empty ancestors
        # of these planned deletions are removable, never unplanned contents.
        parent = removed.parent
        while parent != target:
            try:
                parent.rmdir()
            except OSError as exc:
                if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                    break
                raise
            parent = parent.parent
    for name, entry in value['result'].items():
        if entry == value['files']['local'].get(name):
            continue
        destination = target / name
        if destination.is_dir():
            destination.rmdir()  # only an empty directory may become a file
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.resolve().is_relative_to(target):
            raise ValueError('Merged path leaves target')
        fd, tmp = tempfile.mkstemp(prefix='.source-update-', dir=destination.parent)
        os.close(fd)
        try:
            shutil.copy2(directory / 'result' / name, tmp)
            os.replace(tmp, destination)
        finally:
            Path(tmp).unlink(missing_ok=True)
    if inventory(target) != value['result']:
        raise RuntimeError('Applied source differs from the reviewed merge')
