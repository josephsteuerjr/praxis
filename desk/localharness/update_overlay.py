"""Prepare a complete recoverable file overlay without replacing program bytes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .update_io import stamp, proof, measured_digest, copy_verified, remove_created_file
from .update_progress import atomic_json


class RestorationIncomplete(RuntimeError):
    """Publication failed and the old program has not been fully restored."""
    restoration_completed = False


def journal(recovery, rows):
    """One complete durable intent before the first rename, constant-size passes."""
    path = Path(recovery) / 'update-files.json'
    part = path.with_suffix('.tmp')
    part.write_text(json.dumps(rows, indent=2), encoding='utf-8')
    with part.open('r+b') as stream:os.fsync(stream.fileno())
    deadline = time.monotonic() + 3
    while True:
        try:
            part.replace(path)
            break
        except PermissionError:
            if time.monotonic() >= deadline:raise
            time.sleep(.05)
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def _destination(target, relative):
    destination = target / relative
    from python_migrate import _program_path_identity
    if (not _program_path_identity(destination).is_relative_to(_program_path_identity(target)) or destination.is_symlink()
            or (hasattr(destination, 'is_junction') and destination.is_junction())):
        raise ValueError('Update destination leaves working folder or is linked')
    return destination


def _program_files(source):
    """The running setup window's browser profile is never program payload."""
    for directory,dirs,names in os.walk(source,followlinks=False):
        here=Path(directory)
        dirs[:]=sorted(d for d in dirs if d not in {'.migration-webview','.webview','__pycache__'})
        for name in [*dirs,*sorted(names)]:
            item=here/name
            if item.name.startswith('.python-') or item.suffix=='.pyc':continue
            if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):
                raise ValueError('Update contains a link: '+str(item))
            if item.is_file():yield item


def prepare(source, target, recovery, verified=None, progress=None):
    source, target, recovery = (Path(p).resolve() for p in (source, target, recovery))
    recovery.mkdir()
    rows = []
    inventory = {}
    for item in _program_files(source):
        relative = item.relative_to(source)
        if '__pycache__' in relative.parts or item.suffix == '.pyc':continue
        destination = _destination(target, relative)
        incoming = proof(item, (verified or {}).get(str(item)), progress)
        prior = measured_digest(destination, progress, 'existing') if destination.exists() else None
        inventory[relative.as_posix()] = {'incoming': incoming, 'prior': prior}
        if prior and prior['sha256'] == incoming['sha256'] and (os.name == 'nt' or prior['stat']['mode'] == incoming['stat']['mode']):
            continue
        row = {'path': relative.as_posix(), 'existed': prior is not None,
               'incoming': incoming['sha256'], 'previous': prior['sha256'] if prior else None}
        if prior:
            previous = recovery / relative
            previous.parent.mkdir(parents=True, exist_ok=True)
            temporary = previous.with_name('.' + previous.name + '.preimage-part')
            try:
                copy_verified(destination, temporary, prior['sha256'], progress, 'preimage_copy')
                if measured_digest(temporary, progress, 'preimage_verify')['sha256'] != prior['sha256']:
                    raise IOError('Saved preimage differs: ' + str(destination))
                temporary.replace(previous)
                row['preimage'] = {'sha256': prior['sha256'], 'stat': stamp(previous)}
            finally:temporary.unlink(missing_ok=True)
        rows.append(row)
        if progress:progress({'stage': 'preimages', 'files': len(inventory), 'file': relative.as_posix()})
    # Includes unchanged paths too: replacing an unchanged file after preparation
    # must not silently bless either changed incoming or a late old self-edit.
    prepared = {'format': 'helene-prepared-overlay.v1', 'source': str(source),
                'target': str(target), 'inventory': inventory, 'rows': rows}
    atomic_json(recovery / 'prepared-overlay.json', prepared)
    journal(recovery, rows)
    return prepared


def validate(source, target, recovery, progress=None):
    source, target, recovery = (Path(p).resolve() for p in (source, target, recovery))
    value = json.loads((recovery / 'prepared-overlay.json').read_text(encoding='utf-8'))
    if value['source'] != str(source) or value['target'] != str(target):
        raise ValueError('Prepared update identity differs')
    actual = {p.relative_to(source).as_posix() for p in _program_files(source)}
    if actual != set(value['inventory']):raise ValueError('Prepared update file inventory changed')
    for name, record in value['inventory'].items():
        item = source / name
        target_file = _destination(target, name)
        incoming = proof(item, record['incoming'], progress)
        if incoming != record['incoming']:raise ValueError('Prepared incoming file changed: ' + name)
        before = record['prior']
        if before is None:
            if target_file.exists():raise ValueError('New update path appeared after preparation: ' + name)
        elif not target_file.is_file() or proof(target_file, before, progress, 'existing') != before:
            raise ValueError('Working program changed after preparation: ' + name)
    rows = json.loads((recovery / 'update-files.json').read_text(encoding='utf-8'))
    if rows != value['rows']:raise ValueError('Recovery journal changed')
    for row in rows:
        if row['existed'] and proof(recovery / row['path'], row['preimage'], progress, 'preimage_verify') != row['preimage']:
            raise ValueError('Saved preimage changed: ' + row['path'])
    return value


def apply(source, target, recovery, *, consume=False, progress=None):
    source, target, recovery = (Path(p).resolve() for p in (source, target, recovery))
    changed = []
    try:
        value = validate(source, target, recovery, progress)
        for row in value['rows']:
            relative = Path(row['path'])
            item, destination = source / relative, _destination(target, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name('.' + destination.name + '.update-part')
            try:
                # Even consume=True must prove the real stream, not move a file
                # whose bytes may no longer match the extraction-time receipt.
                copy_verified(item, temporary, row['incoming'], progress, 'publish_copy')
                temporary.replace(destination)
                changed.append(row)
                if consume:item.unlink()
            finally:temporary.unlink(missing_ok=True)
            if progress:progress({'stage': 'switching', 'files': len(changed), 'total_files': len(value['rows']), 'file': row['path']})
        journal(recovery, value['rows'])
    except BaseException as failure:
        # Only paths actually replaced are restored. A failed preimage/journal
        # before any effect never copies half-written recovery bytes to the app.
        try:
            for row in reversed(changed):
                destination = target / row['path']
                if row['existed']:
                    temporary = destination.with_name('.' + destination.name + '.restore-part')
                    try:
                        copy_verified(recovery / row['path'], temporary, row['previous'], progress, 'restore_copy')
                        temporary.replace(destination)
                    finally:temporary.unlink(missing_ok=True)
                else:remove_created_file(destination)
        except BaseException as restoration:
            error = RestorationIncomplete('Update publication failed and restoration is incomplete: ' + str(restoration))
            error.add_note('Original publication failure: ' + repr(failure))
            raise error from restoration
        failure.restoration_completed = True
        raise
    return value
