"""Explicit current source selection and observed frontend-build bindings.

These are package-input facts, not acceptance of the resulting application.
No stale HELENE-SOURCE pin is reinterpreted, no newline normalization is applied.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from portable_zip import Entry, digest, encoded, relative

FRONTS = {'app': 'static', 'mobile': 'mobile', 'miniapp': 'miniapp'}
SKIP = {'.git', '.ouroboros', 'node_modules', '__pycache__', '.pytest_cache',
        '.venv', 'venv', 'target', '.cache', '_state'}
PRIVATE = {'memory', 'soul', 'dossiers', 'graph', 'state', 'workspace', 'models',
           'data', 'profile', 'backups', 'archives', 'recovery', 'cache',
           '.secrets', 'local_auth', 'logs', '.source-updates', '.webview', '.migration-webview'}
PRIVATE_NAMES = {'helene.json', 'auth.json', 'credentials.json', 'owner-stop.json',
                 'agents.md', 'secret-strings.txt', 'local-build.json'}


def linked(path):
    info = Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def checked(root, name):
    path = Path(root)
    for part in relative(name).parts:
        path = path / part
        if linked(path):
            raise ValueError('Linked source input: ' + name)
    return path


def public(name):
    p = relative(name)
    return (not any(n.lower() in SKIP | PRIVATE for n in p.parts)
            and p.name.lower() not in PRIVATE_NAMES
            and not p.name.lower().startswith(('.env', '.python-', '.desktop-'))
            and not p.name.lower().endswith(('.pyc', '.pyo', '.log', '.session', '.session-journal')))


def scan(root, *, exclude=(), strict=False):
    """Source-sized files only; regular inputs, bounded before their bytes are read."""
    root = Path(root)
    if not root.is_dir() or linked(root):
        raise ValueError('Regular source directory required: ' + str(root))
    result, total = {}, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        here = Path(directory)
        for name in [*dirs, *names]:
            p = here / name
            rel = p.relative_to(root).as_posix()
            if name in exclude or not public(rel):
                if strict:
                    raise ValueError('Private/generated input in sealed source: ' + rel)
                continue
            if linked(p):
                raise ValueError('Linked source input: ' + rel)
        dirs[:] = sorted(n for n in dirs if n not in exclude and public((here / n).relative_to(root).as_posix()))
        for name in sorted(names):
            p = here / name
            rel = p.relative_to(root).as_posix()
            if name in exclude or not public(rel):
                continue
            if not p.is_file() or p.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Nonregular/oversized source: ' + rel)
            total += p.stat().st_size
            if total > 256 * 1024 * 1024:
                raise ValueError('Source input budget exceeded')
            executable = '100755' if (os.name != 'nt' and p.stat().st_mode & 0o111) or name.endswith('.sh') else '100644'
            result[rel] = Entry(p.read_bytes(), executable)
    return result


def identities(entries):
    return {n: e.identity() for n, e in sorted(entries.items())}


def sealed(root):
    """Read a manifest-sealed artifact, including legitimate test soul/lock fixtures."""
    root = Path(root)
    if not root.is_dir() or linked(root):
        raise ValueError('Sealed artifact directory unavailable')
    entries, total = {}, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        here = Path(directory)
        for name in [*dirs, *names]:
            path = here / name
            relative(path.relative_to(root).as_posix())
            if linked(path):
                raise ValueError('Linked sealed artifact input')
        for name in sorted(names):
            path = here / name
            size = path.stat().st_size
            total += size
            if not path.is_file() or size > 32 * 1024 * 1024 or total > 512 * 1024 * 1024:
                raise ValueError('Sealed source artifact exceeds byte budget')
            executable = '100755' if os.name != 'nt' and path.stat().st_mode & 0o111 else '100644'
            entries[path.relative_to(root).as_posix()] = Entry(path.read_bytes(), executable)
    return entries


def frontend_inputs(source):
    source = Path(source)
    result = {}
    # The desktop imports remote settings through the shared window modules;
    # localization is shared too. Output projects alone are not the compiler's
    # input closure (the real app build exposed the remote qrcode dependency).
    for root in ('ui-kit', *FRONTS, 'remote', 'lang'):
        if root in ('remote', 'lang') and not (source / 'desk' / root).is_dir():
            continue
        for name, entry in scan(source / 'desk' / root, exclude={'dist', 'test'}).items():
            result['desk/' + root + '/' + name] = entry.identity()
    return dict(sorted(result.items()))


def frontend_outputs(source):
    result = {}
    for frontend in FRONTS:
        entries = scan(Path(source) / 'desk' / frontend / 'dist', strict=True)
        if 'index.html' not in entries:
            raise ValueError('Missing built frontend index: ' + frontend)
        result.update({'desk/' + frontend + '/dist/' + n: e.identity() for n, e in entries.items()})
    return dict(sorted(result.items()))


def write_receipt(source, path, commands, *, before=None):
    expected = [{'cwd': 'desk/' + n, 'argv': ['npm', 'run', 'build'], 'returncode': 0} for n in FRONTS]
    if commands != expected:
        raise ValueError('Receipt requires all three successful observed frontend builds')
    inputs = frontend_inputs(source)
    if before is not None and before != inputs:
        raise ValueError('Frontend inputs changed during build')
    value = {'format': 'helene-portable-frontends.v1', 'inputs': inputs,
             'outputs': frontend_outputs(source), 'commands': commands}
    with Path(path).open('xb') as out:
        out.write(encoded(value))
    return value


def run_frontend_build(npm, directory):
    """Use the selected npm, preserving package scripts and explicit diagnostics.

    Explicit pipe handles also work when the packaging host has no inherited
    Windows console. A missing/failed command is never retried via another npm.
    """
    command = [npm, 'run', 'build']
    done = subprocess.run(command, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = done.stdout.decode('utf8', 'replace')
    binary = getattr(sys.stdout, 'buffer', None)
    if binary is not None:
        binary.write(done.stdout)
        if not done.stdout.endswith(b'\n'):
            binary.write(b'\n')
        binary.flush()
    else:
        print(output, end='' if output.endswith('\n') else '\n', flush=True)
    if done.returncode:
        raise RuntimeError('Frontend build failed (' + str(done.returncode) + ') in '
                           + str(directory) + '; selected npm=' + str(npm) + '\n' + output)
    return done


def build_frontends(source, receipt):
    source = Path(source).resolve()
    if Path(receipt).exists():
        raise FileExistsError(receipt)
    before, commands = frontend_inputs(source), []
    npm = shutil.which('npm')
    if not npm:
        raise ValueError('Provision npm and the lock-matching dependencies first')
    for name in FRONTS:
        run_frontend_build(npm, source / 'desk' / name)
        commands.append({'cwd': 'desk/' + name, 'argv': ['npm', 'run', 'build'], 'returncode': 0})
    return write_receipt(source, receipt, commands, before=before)


def verify_frontends(source, receipt):
    value = json.loads(Path(receipt).read_text(encoding='utf-8'))
    expected = [{'cwd': 'desk/' + n, 'argv': ['npm', 'run', 'build'], 'returncode': 0} for n in FRONTS]
    if (value.get('format') != 'helene-portable-frontends.v1' or value.get('commands') != expected
            or value.get('inputs') != frontend_inputs(source) or value.get('outputs') != frontend_outputs(source)):
        raise ValueError('Frontend build receipt/source/output is stale or incomplete')
    return value


def compose_core(source):
    """One declared rule: current praxis followed by current helene/core overlay."""
    source = Path(source)
    selected, origins = {}, {}
    for tree in ('praxis', 'helene/core'):
        for name, entry in scan(source / tree).items():
            # Same allowed runtime inventory as source_merge. Lockfiles and
            # mirror-only infrastructure are carried in verification, not stock.
            if name.endswith('.lock') or name.startswith('relay/'):
                continue
            selected[name], origins[name] = entry, tree + '/' + name
    if not all(n in selected for n in ('agent.py', 'llm.py', 'turns.py')):
        raise ValueError('Composed core must contain agent, llm and turns')
    return selected, origins


def application(source):
    source = Path(source)
    desk = source / 'desk'
    selected, origins = {}, {}
    for folder in ('localharness', 'deskd', 'resources'):
        for name, entry in scan(desk / folder, exclude={'soul'}).items():
            dest = 'app/' + folder + '/' + name
            selected[dest], origins[dest] = entry, 'desk/' + folder + '/' + name
    # Seed skills/soul are application-owned templates, never the agent's soul.
    if (desk / 'resources/soul').is_dir():
        for name, entry in scan(desk / 'resources/soul').items():
            dest = 'app/resources/soul/' + name
            selected[dest], origins[dest] = entry, 'desk/resources/soul/' + name
    for p in sorted(desk.glob('*.py')):
        if p.name.startswith(('test_', 'scratch_')) or p.name in ('deskpkg.py', 'layout.py'):
            continue
        if linked(p):
            raise ValueError('Linked application input')
        dest = 'app/' + p.name
        selected[dest], origins[dest] = Entry(p.read_bytes()), 'desk/' + p.name
    for name in ('python_app.py', 'python_migrate.py', 'deskapp.py', 'helene_setup.py'):
        if 'app/' + name not in selected:
            raise ValueError('Missing portable application entry: ' + name)
    for frontend, destination in FRONTS.items():
        for name, entry in scan(desk / frontend / 'dist', strict=True).items():
            dest = 'app/' + destination + '/' + name
            selected[dest], origins[dest] = entry, 'desk/' + frontend + '/dist/' + name
    if (desk / 'server').is_dir():
        for name, entry in scan(desk / 'server').items():
            selected['server/' + name], origins['server/' + name] = entry, 'desk/server/' + name
    for name in ('windows_archive_entry.py', 'windows_launcher.rs'):
        p = desk / 'installer' / name
        if p.is_file():
            selected['app/installer/' + name], origins['app/installer/' + name] = Entry(p.read_bytes()), 'desk/installer/' + name
    for source_name, destination in (('desk/shell/icons/icon.ico', 'app/resources/python-app.ico'),
                                     ('desk/shell/icons/python-app.png', 'app/resources/python-app.png')):
        p = source / source_name
        if p.is_file():
            selected[destination], origins[destination] = Entry(p.read_bytes()), source_name
    return selected, origins
