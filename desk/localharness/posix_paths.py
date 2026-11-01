"""Canonicalize only Apple's fixed system aliases, never user symlinks."""
import os,sys
from pathlib import Path

def system_absolute(path):
    path=Path(os.path.abspath(path))
    if sys.platform=='darwin' and os.name!='nt':
        for name in ('var','tmp','etc'):
            alias=Path('/')/name;target=Path('/private')/name
            if path==alias or path.is_relative_to(alias):
                if alias.is_symlink() and alias.resolve()==target and not target.is_symlink():
                    return target/path.relative_to(alias)
                break
    return path
