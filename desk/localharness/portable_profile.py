"""Independent profile custody and relocatable configuration for archives."""
from pathlib import Path
import hashlib
import os
import shutil


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def detach_links(source, destination, relocated):
    # A portable profile cannot depend on a package path or another worktree.
    # Keep local links local; materialize external links without following cycles.
    for path in list(destination.rglob('*')):
        if not path.is_symlink():continue
        original=source/path.relative_to(destination)
        target=original.resolve(strict=True)
        if original.parent.resolve().is_relative_to(target):
            raise ValueError('Cyclic profile link requires attention: '+str(original))
        replacement=None
        for before,after in relocated.items():
            if target.is_relative_to(before):
                replacement=after/target.relative_to(before);break
        path.unlink()
        if replacement is not None:
            path.symlink_to(os.path.relpath(replacement,path.parent),target_is_directory=target.is_dir())
        elif target.is_dir():
            # Reject cycles before copytree follows a link. A clear refusal
            # retains the installed application and never authorizes removal.
            for child in target.rglob('*'):
                if child.is_symlink() and child.parent.resolve().is_relative_to(child.resolve(strict=True)):
                    raise ValueError('Cyclic external profile link: '+str(child))
            shutil.copytree(target,path,symlinks=False)
        else:shutil.copy2(target,path)


def verify_copy(source,destination,progress=None):
    ephemeral={'.desktop-owner.lock','.python-desktop-status'}
    pairs=[]
    for path in source.rglob('*'):
        relative=path.relative_to(source)
        if path.name in ephemeral or path.name.startswith(('.python-desktop-status.','.python-window-')):continue
        if relative == Path('.git') and path.is_file():continue  # independent worktree repository
        other=destination/relative
        if path.is_file():pairs.append((path,other))
    from .parallel_files import verify_pairs
    verify_pairs(pairs,progress)


def relative_config(value,config,root):
    value=dict(value)
    for key in ('tree','code','python','app','runner'):
        if not value.get(key):continue
        path=Path(value[key])
        if path.is_absolute() and path.is_relative_to(root):
            value[key]=os.path.relpath(path,config.parent)
    backup=value.get('backup')
    if isinstance(backup,dict) and backup.get('dir'):
        path=Path(backup['dir'])
        if path.is_absolute() and path.is_relative_to(root):
            value['backup']=dict(backup,dir=os.path.relpath(path,config.parent))
    return value
