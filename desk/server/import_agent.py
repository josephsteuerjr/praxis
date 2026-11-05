"""Restartable first import, before the installation has ever started.

The complete imported data/config/code is prepared beside the destination. A
durable journal precedes the rename sequence; retries finish that sequence and
never overwrite the agent's later life with a second archive import. Previous
files are retained for recovery. Caller holds install.sh's installation lock.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys


def sync(directory):
    fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try: os.fsync(fd)
    finally: os.close(fd)


def save(path, value):
    # Supplied by the updater worker: same protected primitive on every host.
    from private_files import write_private
    write_private(path, (json.dumps(value, ensure_ascii=False) + '\n').encode())
    sync(path.parent)


def install(root, archive):
    import carry
    root, archive = Path(root).absolute(), Path(archive).resolve()
    if root.is_symlink() or root.resolve() != root:
        raise ValueError('Installation path must be canonical')
    with archive.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    marker = root / '.transfer-import.sha256'
    if marker.exists():
        if marker.read_text().strip() != digest:
            raise ValueError('This installation belongs to another archive')
        return {'imported': False, 'recovered': True}
    folder = root / '.transfer-import'
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_uid != os.geteuid():
        raise ValueError('Import journal is not owned by this installer')
    folder.chmod(0o700)
    journal = folder / 'state.json'
    state = json.loads(journal.read_text()) if journal.exists() else {}
    if state and state.get('sha256') != digest:
        raise ValueError('Unfinished import belongs to another archive')
    incoming, previous = folder / 'incoming', folder / 'previous'
    if not state:
        state = {'sha256': digest, 'phase': 'preparing', 'done': []}
        save(journal, state)
    if state['phase'] == 'preparing':
        # Incomplete extraction is retained, never mistaken for a live agent.
        if incoming.exists():
            incoming.rename(folder / ('interrupted-' + os.urandom(6).hex()))
        incoming.mkdir(mode=0o700)
        for name in ('app', 'tree'):
            if (root / name).is_symlink(): raise ValueError('Linked programme is not an import destination')
            shutil.copytree(root / name, incoming / name)
        shutil.copy2(root / 'helene.json', incoming / 'helene.json')
        carry.import_(incoming / 'helene.json', archive)
        # A move never resumes an agent. The desktop Stop is outside data/;
        # translate it into the server's owner-only persistent latch explicitly.
        latch = incoming / 'data/.owner-stop'
        latch.mkdir(mode=0o700, parents=True, exist_ok=True)
        save(latch / 'stop.json', {'by': 'owner', 'via': 'transfer'})
        for path in incoming.rglob('*'):
            if path.is_file():
                with path.open('rb') as source: os.fsync(source.fileno())
        for path in sorted((p for p in incoming.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):sync(path)
        sync(incoming)
        state['phase'] = 'committing'
        save(journal, state)
    previous.mkdir(mode=0o700, exist_ok=True)
    for name in ('app', 'tree', 'data', 'helene.json'):
        if name in state['done']: continue
        source, target, backup = incoming / name, root / name, previous / name
        if target.is_symlink() or source.is_symlink() or backup.is_symlink():
            raise ValueError('Linked import destination refused')
        if source.exists():
            if target.exists():
                if backup.exists(): raise ValueError('Import recovery has conflicting destinations')
                target.rename(backup); sync(root); sync(previous)
            source.rename(target); sync(root); sync(incoming)
        elif not target.exists():
            raise ValueError('Import recovery requires the retained archive')
        state['done'].append(name)
        save(journal, state)
    from private_files import write_private
    write_private(marker, (digest + '\n').encode())
    sync(root)
    state['phase'] = 'done'; save(journal, state)
    return {'imported': True, 'stopped': True}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--root', required=True); p.add_argument('--archive', required=True)
    a = p.parse_args()
    sys.path[:0] = [str(Path(a.root) / 'app/localharness'), str(Path(a.root) / 'app')]
    print(json.dumps(install(a.root, a.archive)))
