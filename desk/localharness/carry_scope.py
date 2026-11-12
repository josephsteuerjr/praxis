"""Read-only scope discovery before a full move; never follow links silently.

Project candidates need a destination mapping in the reviewed transfer plan.
The presence of a mount or Forge task alone is not permission to copy every
directory reachable by that task. Nothing in this module starts or stops agents.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import stat


def linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0)&0x400)


def inventory(root):
    """Count ordinary files and empty directories; report every link/error."""
    root=Path(root).absolute()
    result={'path':str(root),'files':0,'bytes':0,'directories':0,'empty_directories':[],
            'links':[],'special':[],'errors':[],'git':[]}
    try:top=root.lstat()
    except OSError as error:
        result['errors'].append({'path':str(root),'error':type(error).__name__});return result
    if linked(top):
        result['errors'].append({'path':str(root),'error':'root_is_link'});return result
    if not stat.S_ISDIR(top.st_mode):
        result['errors'].append({'path':str(root),'error':'root_is_not_directory'});return result
    def failure(error):result['errors'].append({'path':str(error.filename),'error':type(error).__name__})
    for current,dirs,files in os.walk(root,followlinks=False,onerror=failure):
        base=Path(current);relative=base.relative_to(root).as_posix();result['directories']+=1
        if not dirs and not files:result['empty_directories'].append(relative)
        keep=[]
        for name in sorted(dirs+files):
            path=base/name;rel=path.relative_to(root).as_posix()
            try:
                info=path.lstat()
                if linked(info):
                    try:target=str(path.resolve(strict=True));error=''
                    except (OSError,RuntimeError):target='';error='unresolved_link'
                    result['links'].append({'path':rel,'target':target,'error':error})
                    continue
                if stat.S_ISDIR(info.st_mode):
                    keep.append(name)
                    if name=='.git':result['git'].append({'path':rel,'kind':'directory'})
                elif stat.S_ISREG(info.st_mode):
                    result['files']+=1;result['bytes']+=info.st_size
                    if name=='.git':
                        if info.st_size>4096:raise ValueError('oversized_git_pointer')
                        raw=path.read_text(encoding='utf8').strip()
                        if not raw.startswith('gitdir: '):raise ValueError('invalid_git_pointer')
                        gitdir=Path(raw[8:]);gitdir=gitdir if gitdir.is_absolute() else base/gitdir
                        gitdir=gitdir.resolve(strict=True);common=gitdir/'commondir'
                        shared=gitdir
                        if common.exists():
                            if common.stat().st_size>4096:raise ValueError('oversized_git_common_pointer')
                            raw_common=Path(common.read_text(encoding='utf8').strip())
                            shared=(raw_common if raw_common.is_absolute() else gitdir/raw_common).resolve(strict=True)
                        result['git'].append({'path':rel,'kind':'worktree','gitdir':str(gitdir),'common':str(shared)})
                else:result['special'].append({'path':rel,'mode':stat.S_IFMT(info.st_mode)})
            except (OSError,ValueError,RuntimeError) as error:
                result['errors'].append({'path':rel,'error':str(error) if isinstance(error,ValueError) else type(error).__name__})
        dirs[:]=keep
    return result


def discover(config_path):
    """Find actual configured roots plus local project candidates, without copying."""
    from . import carry
    config_path=Path(config_path).resolve();cfg=carry._read_config(config_path)
    home=carry.tree_of(config_path,cfg).resolve();program=config_path.parent
    code=Path(str(cfg.get('code') or 'tree'));code=code if code.is_absolute() else program/code
    roots=[{'kind':'data','path':str(home)},{'kind':'code','path':str(code.resolve())}]
    app=Path(str(cfg.get('app') or 'app/deskapp.py'));app=app if app.is_absolute() else program/app
    app=app.resolve().parent
    if app.is_dir():roots.append({'kind':'application','path':str(app)})
    candidates={};unavailable=[]
    def candidate(raw,via):
        if not isinstance(raw,str) or not raw.strip():return
        path=Path(raw)
        if not path.is_absolute():
            unavailable.append({'path':raw,'via':via,'reason':'relative_project_path'});return
        try:resolved=path.resolve(strict=True)
        except (OSError,RuntimeError):
            unavailable.append({'path':raw,'via':via,'reason':'project_unavailable'});return
        if not resolved.is_dir():
            unavailable.append({'path':raw,'via':via,'reason':'project_not_directory'});return
        if resolved.is_relative_to(home):return
        key=os.path.normcase(str(resolved))
        value=candidates.setdefault(key,{'path':str(resolved),'via':[]})
        if via not in value['via']:value['via'].append(via)
    mounts=(cfg.get('sandbox') or {}).get('mounts') or []
    for item in mounts:
        candidate(item if isinstance(item,str) else item.get('path') if isinstance(item,dict) else '', 'sandbox.mounts')
    tasks=home/'memory/.forge/tasks'
    if tasks.is_dir():
        for path in sorted(tasks.glob('*/task.json')):
            try:
                if path.stat().st_size>1024*1024:raise ValueError('oversized_task')
                value=json.loads(path.read_text(encoding='utf8'))
                if not isinstance(value,dict):raise ValueError('invalid_task')
                # Device/host bindings are not portable evidence that a path is
                # on this computer, even if its text resembles a local path.
                if value.get('device_id') or value.get('scope') in ('host','windows') or value.get('root_binding'):
                    unavailable.append({'path':str(value.get('root') or ''),'via':str(path.relative_to(home)),
                                        'reason':'bound_project_requires_mapping'});continue
                candidate(value.get('root'),'forge:'+path.parent.name)
            except (OSError,ValueError) as error:
                unavailable.append({'path':str(path.relative_to(home)),'reason':type(error).__name__})
    scanned=[]
    for root in roots:
        value=inventory(root['path']);scanned.append({**value,'kind':root['kind']})
        for link in value['links']:
            if link['target']:candidate(link['target'],root['kind']+':'+link['path'])
    return {'format':'helene.carry.scope.v1','roots':scanned,'projects':list(candidates.values()),
            'unavailable':unavailable,'complete':not unavailable and not candidates and
            all(not row['errors'] and not row['special'] and not row['links'] and
                all(git['kind']=='directory' for git in row['git']) for row in scanned)}
