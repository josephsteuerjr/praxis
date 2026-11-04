"""Opt-in local snapshots of explicitly reviewed memory Markdown; never runs on import."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


class HistoryError(ValueError):
    pass


# This is a ceiling, not discovery: every file still needs an exact manifest entry.
FAMILIES = {'people', 'rooms', 'notes'}
ROOT_TEXTS = {'home.md', 'appetite.md'}
DENIED = re.compile(r'(?i)(secret|credential|token|session|mailbox|runtime|password|\.bak|\.log)')
SECRET = re.compile(
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----|'
    r'(?i:bearer\s+[a-z0-9._~+/-]{12,}|'
    r'(?:password|passwd|api[_-]?key|access[_-]?token|secret|authorization)'
    r'[\s"\x27]*[:=][\s"\x27]*[^\s"\x27]{4,}|'
    r'\b\d{6,12}:[a-z0-9_-]{25,}|'
    r'\b(?:gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|sk-[a-z0-9_-]{20,})|'
    r'https?://[^\s/:]+:[^\s/@]+@)'
)


def plain_path(path: Path) -> Path:
    path = path.absolute()
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise HistoryError('symlink path refused')
    return path.resolve()


def collect(memory: Path, manifest: Path) -> dict[str, bytes]:
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(entries, list) or not entries:
        raise HistoryError('manifest must be a nonempty list of exact relative paths')
    result = {}
    seen = set()
    for name in entries:
        if not isinstance(name, str):
            raise HistoryError('invalid manifest entry')
        parts = name.split('/')
        if (name in seen or any(not p or p.startswith('.') or p in {'..', '.'} for p in parts)
                or '\\' in name or DENIED.search(name) or not name.endswith('.md')
                or not ((len(parts) == 2 and parts[0] in FAMILIES)
                        or (len(parts) == 1 and name in ROOT_TEXTS))
                or name.endswith('/_пример.md')):
            raise HistoryError('path outside canonical text allowlist')
        seen.add(name)
        path = plain_path(memory / name)
        if not path.is_relative_to(memory):
            raise HistoryError('path escapes memory')
        # An approved deleted file disappears from the next tree. No directory traversal.
        if not path.exists():
            continue
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 2_000_000:
                raise HistoryError('not a bounded, single-link regular file')
            data = stream.read(2_000_001)
        if len(data) > 2_000_000 or b'\x00' in data:
            raise HistoryError('invalid text')
        text = data.decode('utf-8')
        if SECRET.search(text):
            raise HistoryError('possible secret: snapshot refused (contents withheld)')
        result[name] = data
    return result


def snapshot(memory: Path, history: Path, manifest: Path, source: Path, *, initialize=False) -> str:
    memory, history, source = map(plain_path, (memory, history, source))
    if any(history.is_relative_to(p) or p.is_relative_to(history) for p in (memory, source)):
        raise HistoryError('history must be separate from source and memory')
    ancestors = (history.parent, *history.parent.parents)
    if any((parent / '.git').exists()
           or ((parent / 'HEAD').is_file() and (parent / 'objects').is_dir()
               and (parent / 'refs').is_dir()) for parent in ancestors):
        raise HistoryError('history cannot be inside another Git repository')
    if not memory.is_dir() or not source.is_dir():
        raise HistoryError('source and memory must exist')
    # Validate before creating any objects, index, or repository.
    files = collect(memory, manifest)
    if not history.parent.is_dir():
        raise HistoryError('prepare a private parent directory first')
    lock = history.parent / ('.' + history.name + '.lock')
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(fd)
        return _snapshot(history, files, initialize)
    finally:
        lock.unlink()


def _snapshot(history: Path, files: dict[str, bytes], initialize: bool) -> str:
    # Do not inherit Git routing, config injection, credentials, alternates, or hooks.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_AUTHOR_NAME='Praxis local memory',
               GIT_AUTHOR_EMAIL='memory@localhost', GIT_COMMITTER_NAME='Praxis local memory',
               GIT_COMMITTER_EMAIL='memory@localhost')

    def git(*args, data=None, check=True):
        return subprocess.run(['git', '--no-replace-objects', '--git-dir=' + str(history),
                               '-c', 'core.hooksPath=' + os.devnull,
                               '-c', 'protocol.allow=never', *args], input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                              check=check).stdout.decode().strip()

    marker = history / 'praxis-local-history-v1'
    if not history.exists():
        if not initialize:
            raise HistoryError('initialization requires explicit reviewed opt-in')
        history.mkdir(mode=0o700)
        git('init', '--bare', '--template=')
        marker.write_text('Local only. No remotes. Not a backup.\n')
    if history.stat().st_mode & 0o077 or not marker.is_file():
        raise HistoryError('not a private managed history repository')
    for directory, dirs, names in os.walk(history, followlinks=False):
        for entry in [Path(directory), *(Path(directory) / n for n in dirs + names)]:
            info = entry.lstat()
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022
                    or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                raise HistoryError('unsafe repository storage ownership, links or permissions')
    if any(plain_path(history / name) != history / name for name in ('config', 'objects', 'refs')):
        raise HistoryError('symlink repository storage refused')
    raw_config = (history / 'config').read_text().lower()
    if re.search(r'\[\s*(remote|include|includeif|url|extensions)\b', raw_config):
        raise HistoryError('remote or routing configuration refused')
    if git('rev-parse', '--is-bare-repository') != 'true':
        raise HistoryError('bare repository required')
    config = git('config', '--local', '--list').lower()
    if any(word in config for word in ('remote.', 'include.', 'includeif.', 'url.', 'extensions.', 'core.worktree')):
        raise HistoryError('remote or routing configuration refused')
    if (history / 'objects/info/alternates').exists():
        raise HistoryError('alternate object stores refused')
    old = git('rev-parse', '--verify', 'refs/heads/memory', check=False)
    with tempfile.TemporaryDirectory(prefix='praxis-memory-index-') as tmp:
        env['GIT_INDEX_FILE'] = str(Path(tmp) / 'index')
        git('read-tree', '--empty')
        for name, data in sorted(files.items()):
            blob = git('hash-object', '-w', '--stdin', data=data)
            git('update-index', '--add', '--cacheinfo', '100644', blob, name)
        tree = git('write-tree')
        if old and tree == git('rev-parse', old + '^{tree}'):
            return old
        args = ['commit-tree', tree]
        if old:
            args += ['-p', old]
        commit = git(*args, data=b'Local canonical memory snapshot\n')
        git('update-ref', 'refs/heads/memory', commit, old or '0' * 40)
        return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('memory', 'history', 'manifest', 'source'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--initialize-reviewed', action='store_true')
    args = parser.parse_args()
    try:
        print(snapshot(args.memory, args.history, args.manifest, args.source,
                       initialize=args.initialize_reviewed))
    except (HistoryError, OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError):
        parser.exit(1, 'Memory history refused; review paths, manifest, permissions and secret policy.\n')


if __name__ == '__main__':
    main()
