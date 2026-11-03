"""Recover publication of accepted app M into an inactive portable folder.

Frozen B and M already exist in the composition. Keep only an exact intent;
recovery validates every member before restoring B, without another app copy.
"""
from pathlib import Path
import os
import uuid

from .package_notice import read, write, replace_with_retry
from . import source_merge as merge, update_composition as composition


def _marker(new):
    return Path(new).parent/'portable-app-publication.json'


def _copy(source, destination, expected, *, temporary_root):
    from .first_start import plain
    from .update_io import copy_verified
    plain(destination.parent).mkdir(parents=True, exist_ok=True)
    # Stage outside app: an abrupt process death must not add an undeclared
    # app member that would defeat the very recovery this intent provides.
    temporary = plain(temporary_root)/('.portable-app-publish-'+uuid.uuid4().hex)
    try:
        copy_verified(plain(source), temporary, expected)
        replace_with_retry(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _inputs(new, overlay):
    """Validate retained B/M bytes independently of the partially published app."""
    from .first_start import plain
    base = None
    backing = None
    for row in overlay['identities']:
        directory = plain(row['directory'])
        state = read(directory/'composition.json')
        if Path(state.get('program', '')).resolve() != new:
            raise ValueError('App publication belongs to another programme')
        frozen = composition.app_inventory(directory/'compound/base-app')
        if frozen != state['base_app'] or (base is not None and frozen != base):
            raise ValueError('App publication frozen B changed')
        base, backing = frozen, directory/'compound/base-app'
    if base is None:
        raise ValueError('App publication has no frozen composition')
    desired = dict(base)
    for raw, row in overlay['files'].items():
        merge.relative(raw)
        if not raw.startswith('app/'):
            raise ValueError('App publication leaves application surface')
        name = raw[len('app/'):]
        merge.relative(name)
        if row.get('delete'):
            desired.pop(name, None)
        else:
            source = plain(row['source'])
            if composition._hash(source) != row['sha256']:
                raise ValueError('App publication accepted M changed')
            # Preserve executable modes, as the existing stream copier does.
            desired[name] = {'sha256':row['sha256'],
                'mode':'100755' if os.name != 'nt' and source.stat().st_mode & 0o111 else '100644'}
    return base, desired, backing


def recover(new):
    """Before normal B-or-M validation, restore an interrupted exact batch to B."""
    from .first_start import plain
    new = plain(new)
    marker = _marker(new)
    intent = read(marker)
    if not intent:
        if marker.exists():
            raise ValueError('Unreadable app publication intent; incoming folder retained')
        return False
    overlay = read(new.parent/'compound-overlay.json')
    if (intent.get('format') != 'helene-portable-app-publication.v1'
            or intent.get('program') != str(new) or intent.get('overlay') != overlay):
        raise ValueError('App publication intent identity changed')
    base, desired, backing = _inputs(new, overlay)
    actual = composition.app_inventory(new/'app')
    # This intent only owns old B or admitted M members. A later different edit
    # or an added file is retained, never overwritten under recovery authority.
    for name in set(base) | set(desired) | set(actual):
        if actual.get(name) not in (base.get(name), desired.get(name)):
            raise ValueError('App publication destination changed: '+name)
    for raw in overlay['files']:
        name = raw[len('app/'):]
        destination = plain(new/raw)
        if name in base:
            _copy(backing/name, destination, base[name]['sha256'], temporary_root=new.parent)
        else:
            destination.unlink(missing_ok=True)
    if composition.app_inventory(new/'app') != base:
        raise ValueError('App publication restoration is incomplete')
    marker.unlink()
    return True


def publish(new):
    from .first_start import plain
    new = plain(new)
    recover(new)
    files = composition.selected_files(new)
    if not files:
        return
    overlay = read(new.parent/'compound-overlay.json')
    base, desired, _ = _inputs(new, overlay)
    actual = composition.app_inventory(new/'app')
    if actual == desired:
        return  # Exact previously completed M, not a new approval.
    if actual != base:
        raise ValueError('App publication destination is not frozen B')
    write(_marker(new), {'format':'helene-portable-app-publication.v1',
        'program':str(new), 'overlay':overlay})
    # Keep the intent on any error, including an interrupted restoration.
    # The normal retry path invokes recover before composition validation.
    for raw, row in files.items():
        destination = plain(new/raw)
        if row.get('delete'):
            destination.unlink(missing_ok=True)
        else:
            _copy(Path(row['source']), destination, row['sha256'], temporary_root=new.parent)
    if composition.app_inventory(new/'app') != desired:
        raise ValueError('App publication did not produce exact accepted M')
    _marker(new).unlink()
