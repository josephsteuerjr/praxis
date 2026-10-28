"""Prepare an OFFLINE copied profile on another OS, using existing migration.

Run on the destination, never against a running source installation. Map former
absolute Windows roots to directories in the transferred copy. Data, selected
code, dirty Git bytes and preferences pass through app_migration; this command
neither starts an agent nor retires/deletes the source. Tokens stay private.
"""
from pathlib import Path, PureWindowsPath, PurePosixPath
import argparse
import copy
import json
import os
import sys
if not __package__:
    # The documented direct-script invocation also works under -I.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'localharness'


def relocate_config(value, aliases):
    if not isinstance(aliases,dict):raise ValueError('Expected old-root to copied-root map')
    def path(value):
        if not isinstance(value,str) or not value:return value
        win=PureWindowsPath(value)
        if win.is_absolute():
            for before,after in sorted(aliases.items(),key=lambda r:len(r[0]),reverse=True):
                try:relative=win.relative_to(PureWindowsPath(before))
                except ValueError:continue
                return str(Path(after).resolve().joinpath(*relative.parts))
            if os.name!='nt':raise ValueError('Missing offline path map for configured Windows path: '+value)
        elif PurePosixPath(value).is_absolute():
            for before,after in sorted(aliases.items(),key=lambda r:len(r[0]),reverse=True):
                prior=PurePosixPath(before)
                if not prior.is_absolute():continue
                try:relative=PurePosixPath(value).relative_to(prior)
                except ValueError:continue
                return str(Path(after).resolve().joinpath(*relative.parts))
        if os.name!='nt' and '\\' in value:return str(Path(*win.parts))
        return value
    result=copy.deepcopy(value)
    for key in ('tree','code','app','runner'):
        if key in result:result[key]=path(result[key])
    for section,key in (('backup','dir'),):
        if isinstance(result.get(section),dict) and key in result[section]:result[section][key]=path(result[section][key])
    return result


def prepare_offline(old, new, data_home, aliases, *, resume=False):
    from . import app_migration
    from .desktop_supervisor import _tree_hold
    from contextlib import ExitStack
    old,new,data_home=map(lambda p:Path(p).resolve(),(old,new,data_home))
    from .python_platform import require_runtime
    require_runtime(new)
    note_path=new/'python-migration.json';reuse=None
    if note_path.exists():
        if not resume:raise ValueError('Interrupted migration exists; use explicit --resume')
        reuse=json.loads(note_path.read_text(encoding='utf-8'))
        if (reuse.get('state')!='preserving' or reuse.get('startup_proof') or
                reuse.get('old_root')!=str(old) or reuse.get('new_root')!=str(new) or
                reuse.get('data_home')!=str(data_home) or reuse.get('path_aliases')!=aliases):
            raise ValueError('Only the same interrupted offline copy and path map can resume')
    elif resume:raise ValueError('No interrupted offline copy to resume')
    found=app_migration.discover(old,path_aliases=aliases)
    for row in found['agents']:
        for path in (Path(row['tree']),row['code']):
            if not path.is_relative_to(old) or not path.is_dir():
                raise ValueError('Map all effective data/code roots into the complete offline source copy before import')
    template=new/'helene.json';saved=template.read_bytes() if template.exists() else None
    if saved and not reuse:
        value=json.loads(saved)
        if value.get('setup_complete') is not False or (new/'data').exists() or (new/'agents').exists():
            raise ValueError('Import requires a fresh unpacked destination, not another agent home')
    with ExitStack() as holds:
        trees={Path(row['tree']) for row in found['agents']}
        if reuse:
            for value in reuse['data_paths']:
                path=Path(value).resolve()
                if not path.is_relative_to(data_home):raise ValueError('Interrupted data copy is outside its selected home')
                if path.exists():trees.add(path)
        for tree in sorted(trees):holds.enter_context(_tree_hold(tree))
        if reuse:app_migration.retain_attempt_configs(new,reuse)
        elif saved:template.unlink()
        try:
            return app_migration.prepare(old,new,data_home,old_service_stopped=True,
                                         preserve_voice=False,path_aliases=aliases,reuse=reuse)
        except BaseException:
            if saved is not None and not template.exists():template.write_bytes(saved)
            raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--offline-copy',type=Path,required=True);p.add_argument('--new',type=Path,required=True)
    p.add_argument('--data-home',type=Path,required=True);p.add_argument('--path-map',type=Path,required=True)
    p.add_argument('--resume',action='store_true',help='Resume only this saved interrupted copy with the identical path map')
    a=p.parse_args();note=prepare_offline(a.offline_copy,a.new,a.data_home,json.loads(a.path_map.read_text(encoding='utf-8')),resume=a.resume)
    print('Profile prepared; nothing started, source retained. Migration: '+note['id'])
