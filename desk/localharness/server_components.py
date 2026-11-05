"""Persistent dependencies shared only by the container's two application roles.

The supervisor provisions these paths before dropping privileges. Desktop
private files keep their original owner-only policy; no arbitrary group grant.
"""
import os
from pathlib import Path
import stat


def roles():
    if os.name != 'posix' or os.environ.get('HELENE_SUPERVISOR') != 'serverboot':
        return None
    import pwd
    try:
        agent, desk = (pwd.getpwnam(name) for name in ('helene', 'desk'))
    except KeyError:
        return None
    if agent.pw_gid != desk.pw_gid or os.geteuid() not in (0, agent.pw_uid, desk.pw_uid):
        return None
    return {'agent': agent.pw_uid, 'desk': desk.pw_uid, 'gid': agent.pw_gid}


def shared_key(info):
    who = roles()
    return bool(who and info.st_uid in (0, who['agent'], who['desk'])
                and info.st_gid == who['gid'] and stat.S_IMODE(info.st_mode) == 0o640)


def share_fish(path):
    """After a verified private write, admit the other owned speech role."""
    who = roles()
    if not who:
        return
    path = Path(path)
    if (path.parent.name, path.parent.parent.name) not in (
            ('fish', '.secrets'), ('fish-speech', '.state')):
        raise ValueError('Invalid server speech path')
    for directory in (path.parent.parent, path.parent):
        if directory.is_symlink():
            raise ValueError('Linked server speech path')
        info = directory.stat()
        if info.st_uid == os.geteuid() or os.geteuid() == 0:
            os.chown(directory, -1, who['gid'])
            directory.chmod(0o2770)
        elif info.st_gid != who['gid'] or info.st_mode & 0o007 or info.st_mode & 0o070 != 0o070:
            raise PermissionError('Server speech directory is not shared privately')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (info.st_uid != os.geteuid() and os.geteuid() != 0):
        raise PermissionError('Server speech file ownership is unconfirmed')
    os.chown(path, who['desk'] if os.geteuid() == 0 else -1, who['gid'])
    path.chmod(0o640 if path.name == 'api-key' else 0o660)
    lock = path.parent / '.agent-default.lock'
    if lock.exists() and lock.stat().st_uid == os.geteuid():
        os.chown(lock, -1, who['gid']); lock.chmod(0o660)


def prepare(program, tree):
    """Root-only provision, outside versioned code; retain all prior objects."""
    who = roles()
    if not who or os.geteuid() != 0:
        return
    from . import component_store as store
    program, tree = Path(program), Path(tree)
    root = store.root()
    root.mkdir(parents=True, exist_ok=True, mode=0o2770)
    for directory, dirs, files in os.walk(root, followlinks=False):
        folder = Path(directory)
        if folder.is_symlink() or any((folder / name).is_symlink() for name in dirs + files):
            raise ValueError('Linked persistent server dependency')
        os.chown(folder, who['desk'], who['gid']); folder.chmod(0o2770)
        for name in files:
            path = folder / name
            mode = path.stat().st_mode
            os.chown(path, who['desk'], who['gid'])
            path.chmod(0o660 | (mode & 0o111))
    for owner in (program, tree):
        pin = store.pin_file(owner)
        if owner == program and pin.parent != program:
            pin.parent.mkdir(mode=0o2770, parents=True, exist_ok=True)
            os.chown(pin.parent, who['desk'], who['gid']); pin.parent.chmod(0o2770)
        for path in (pin, pin.parent / '.agent-default.lock'):
            if path.exists():
                if path.is_symlink(): raise ValueError('Linked component metadata')
                os.chown(path, who['desk'], who['gid']); path.chmod(0o660)
    for folder in (tree / 'memory/.secrets/fish', tree / 'memory/.state/fish-speech'):
        if not folder.exists(): continue
        for path in folder.iterdir():
            if path.is_file(): share_fish(path)
