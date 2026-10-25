"""Data-preserving handoff from an existing installation to the Python app.

The update asset can call this after unpacking its embedded runtime. Discovery
uses each old config's directory, never the new program's relative-path base.
Preparation never deletes the old installation. Retirement is a separate action
following successful startup, and never removes configured data directories.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from .essential_backup import configs, load, tree_path, snapshot


def preserve_source(source: Path, destination: Path, progress=None):
    """Copy executable self-authored bytes and make Git history independent.

    A linked worktree's .git file points outside its directory. Materialize the
    common repository first, then overlay every working byte (including dirty
    and untracked files); never reset or checkout over the agent's changes.
    """
    if not source.is_dir():
        raise FileNotFoundError(source)
    # An OS lease describes a running generation, not the agent's durable
    # state. A copy of `active` would falsely claim ownership in the new tree.
    # Old records remain in the old installation for diagnosis; no source,
    # memory, Git index or owner Stop/pause record is removed.
    ephemeral = {'.desktop-owner.lock', '.python-desktop-status'}
    def ignore(directory, names):
        return [n for n in names if n in ephemeral or n.startswith(('.python-desktop-status.', '.python-window-'))]
    from .parallel_files import copytree
    copied = 0
    progress_lock=threading.Lock()
    def copy_file(src, dst):
        nonlocal copied
        path=Path(src)
        relative=path.relative_to(source)
        # Model checkpoints are immutable inputs, not authored memory/code.
        # Same-volume links keep both names valid after legacy retirement and
        # avoid another multi-gigabyte copy. Replacement downloads use rename.
        addressed=('blobs' in relative.parts and len(path.name) in (40,64) and
                   all(c in '0123456789abcdef' for c in path.name))
        model=(relative.parts[0]=='models' and (addressed or path.suffix in ('.bin','.pth','.safetensors','.onnx')))
        if model and path.stat().st_dev==Path(dst).parent.stat().st_dev:
            os.link(src,dst);result=str(dst)
        else:
            result = shutil.copy2(src, dst)
        with progress_lock:
            copied += Path(src).stat().st_size
            if progress: progress({'stage':'preserving', 'bytes':copied, 'source':str(source)})
        return result
    if (source / '.git').is_file():
        def git(*args):
            return subprocess.check_output(['git', '-C', str(source), *args]).decode('utf-8').strip()
        head = git('rev-parse', 'HEAD')
        subprocess.run(['git', 'clone', '--no-hardlinks', '--no-checkout', str(source), str(destination)],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', str(destination), 'update-ref', 'HEAD', head],
                       check=True, capture_output=True)
        gitdir = Path(git('rev-parse', '--absolute-git-dir'))
        if (gitdir / 'index').is_file():
            shutil.copy2(gitdir / 'index', destination / '.git/index')
            # A split index names an immutable sharedindex.<hash> beside it.
            # The linked-worktree clone does not carry that per-worktree file.
            shared=git('rev-parse','--shared-index-path')
            if shared:
                path=Path(shared)
                if not path.is_absolute():path=source/path
                shutil.copy2(path,destination/'.git'/path.name)
        for path in source.iterdir():
            if path.name == '.git' or path.name in ignore(source, [path.name]):
                continue
            target = destination / path.name
            if path.is_dir() and not path.is_symlink():
                copytree(path, target, symlinks=True, ignore=ignore, copy_function=copy_file)
            elif path.is_symlink():
                target.symlink_to(os.readlink(path), target_is_directory=path.is_dir())
            else:
                copy_file(path, target)
    else:
        copytree(source, destination, symlinks=True, ignore=ignore, copy_function=copy_file)
    return destination


def code_path(config, value):
    code=Path(str(value.get('code') or '../../live'))
    return (code if code.is_absolute() else Path(config).parent/code).resolve()


def retain_attempt_configs(new, note):
    directory=new/'.migration-attempts'/(str(note['id'])+'-'+uuid.uuid4().hex)
    existing=[p for p in configs(new) if p.is_file()]
    if not existing:return
    directory.mkdir(parents=True);write_note(directory,note)
    for config in existing:
        target=directory/config.relative_to(new);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(config,target)
    for config in existing:config.unlink()


def discover(root: Path, *, path_aliases=None):
    root = root.resolve()
    agents = []
    for config in configs(root):
        if config.is_file():
            value = load(config)
            if path_aliases is not None:
                from .profile_paths import relocate_config
                value = relocate_config(value, path_aliases)
            tree = tree_path(config, value)
            agents.append({"config": str(config), "relative": config.relative_to(root).as_posix(),
                           "tree": str(tree), "inside_old_install": tree.is_relative_to(root),
                           "value": value, 'code':code_path(config,value)})
    if not agents:
        raise ValueError("No readable Hélène configuration in old installation")
    return {"old_root": str(root), "agents": agents}


def write_note(root, note):
    path=Path(root)/'python-migration.json'
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        stream.write(json.dumps(note,indent=2));stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)
    if os.name != 'nt':
        directory=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)


def refresh_preserved(source:Path,destination:Path,delta:Path,progress=None):
    """Resume a recorded failed copy after both owners stopped, not another clone.

    Equal bytes are reused. Every replaced byte is retained in delta first;
    extra files from the unsuccessful generation are retained there, not merged
    into the selected old owner's message queues or executable source.
    """
    from .app_components import digest
    ephemeral={'.desktop-owner.lock','.python-desktop-status'}
    def linked(path):return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())
    def safe_parent(path,base):
        for relative in path.relative_to(base).parents:
            if linked(base/relative):raise ValueError('Retained copy contains a directory link; inspect it before resuming')
    def ignored(directory,names):
        return [n for n in names if Path(directory)==source and (n in ephemeral or n.startswith('.python-desktop-status.'))]
    wanted={p.relative_to(source) for p in source.rglob('*')}
    for item in source.rglob('*'):
        if item.is_dir() and not linked(item) and linked(destination/item.relative_to(source)):
            raise ValueError('Retained copy contains a directory link; inspect it before resuming')
    for item in sorted(destination.rglob('*'),key=lambda p:len(p.parts),reverse=True):
        relative=item.relative_to(destination)
        operational=len(relative.parts)==1 and (item.name in ephemeral or item.name.startswith('.python-desktop-status.'))
        original=source/relative
        type_changed=relative in wanted and ((item.is_dir() and not linked(item)) != (original.is_dir() and not linked(original)))
        if relative not in wanted or operational or type_changed:
            saved=delta/relative;safe_parent(saved,delta);saved.parent.mkdir(parents=True,exist_ok=True)
            if item.is_dir() and not linked(item):
                if not any(item.iterdir()):
                    saved.mkdir(parents=True,exist_ok=True)
                    item.rmdir()
            else:item.replace(saved)
    reused=0;copied=0
    def copy_file(src,dst):
        nonlocal reused,copied
        src,dst=Path(src),Path(dst)
        safe_parent(dst,destination)
        expected=digest(src)
        if dst.is_file() and not dst.is_symlink() and src.stat().st_size==dst.stat().st_size and digest(dst)==expected:
            reused+=1
        else:
            if dst.exists() or dst.is_symlink():
                saved=delta/dst.relative_to(destination);safe_parent(saved,delta);saved.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(dst,saved,follow_symlinks=False)
            temporary=dst.with_name(dst.name+'.migration-part');shutil.copy2(src,temporary)
            if digest(temporary)!=expected:raise IOError('Preserved data copy changed')
            temporary.replace(dst);copied+=1
        if progress:progress({'stage':'preserving','source':str(source),'files':reused+copied,'reused_files':reused,'copied_files':copied})
        return str(dst)
    # copytree recreates symlinks itself; retain the existing link first.
    for item in source.rglob('*'):
        if item.is_symlink():
            target=destination/item.relative_to(source)
            if target.is_symlink():
                saved=delta/item.relative_to(source);safe_parent(saved,delta);saved.parent.mkdir(parents=True,exist_ok=True)
                target.replace(saved)
    shutil.copytree(source,destination,dirs_exist_ok=True,symlinks=True,ignore=ignored,copy_function=copy_file)
    return {'reused_files':reused,'copied_files':copied,'prior_delta':str(delta)}


def preservation_roots(found, old_root, *, copy_external=False):
    """One inventory for preservation and platform migration admission."""
    roots=set()
    for row in found['agents']:
        roots.update(p for p in (Path(row['tree']),row['code']) if copy_external or p.is_relative_to(old_root))
        for key in ('app','runner'):
            if not row['value'].get(key):continue
            script=Path(str(row['value'][key]))
            script=(script if script.is_absolute() else Path(row['config']).parent/script).resolve()
            stock={'app':('deskapp.py','app/deskapp.py'),
                   'runner':('localharness/runner.py','app/localharness/runner.py')}[key]
            if ((copy_external or script.is_relative_to(old_root)) and script.is_file()
                    and not any(script==(old_root/p).resolve() for p in stock)):
                roots.add(script.parent)
    for source in list(roots):
        for parent in (source,*source.parents):
            if not copy_external and not parent.is_relative_to(old_root):break
            if (parent/'.git').exists():
                roots.add(parent);break
    return roots


def prepare(old_root: Path, new_root: Path, data_home: Path, *, old_service_stopped=False, progress=None,reuse=None,preserve_voice=True,path_aliases=None,fresh_template=None,independent=False,copy_external=False,preserve_downloads=True):
    old_root, new_root, data_home = (p.resolve() for p in (old_root, new_root, data_home))
    if new_root == old_root or new_root.is_relative_to(old_root) or old_root.is_relative_to(new_root):
        raise ValueError("New and old program directories must be separate")
    if data_home.is_relative_to(old_root) or (data_home.is_relative_to(new_root) and not independent):
        raise ValueError("Migrated data must live outside both program directories")
    if independent and data_home != new_root/'profile':
        raise ValueError('Portable profile must be inside the new application profile directory')
    found = discover(old_root, path_aliases=path_aliases)
    had_service = any((r["value"].get("installed") or {}).get("service") is True for r in found["agents"])
    if had_service and not old_service_stopped:
        raise ValueError("Stop the old installed service before preparing the desktop-owner migration")
    template = new_root / 'helene.json'
    # Offline callers hold the destination installation lock and pass the exact
    # validated template. Keep it through backup: an uncatchable exit before the
    # journal must still enter the fresh-install wizard on ordinary startup.
    if fresh_template is not None:
        if (reuse or json.loads(fresh_template).get('setup_complete') is not False
                or not template.is_file() or template.read_bytes() != fresh_template):
            raise ValueError('Fresh destination template changed before migration')
    elif template.exists():
        raise FileExistsError("New owner directory already contains configuration")
    operation = uuid.uuid4().hex
    data_home.mkdir(parents=True, exist_ok=True)
    backup = snapshot(old_root, data_home / "migration-backups", "before-python-migration",
                      resolved_trees={r['relative']:Path(r['tree']) for r in found['agents']})
    staged = new_root.parent / (".helene-migrate-" + operation)
    staged.mkdir()
    copied = []
    relocated = {}
    source_paths = []
    previous={}
    if reuse:
        if Path(reuse.get('old_root','')).resolve()!=old_root or reuse.get('state') not in ('failed_before_retirement','prepared','preserving') or reuse.get('startup_proof'):
            raise ValueError('Only an unstarted failed copy of this old installation can be resumed')
        previous.update((Path(a).resolve(),Path(b).resolve()) for a,b in zip(reuse['old_data_paths'],reuse['data_paths']))
        previous.update((Path(row['old']).resolve(),Path(row['selected']).resolve()) for row in reuse['source_paths'])
        previous.update((Path(a).resolve(),Path(b).resolve()) for a,b in reuse.get('preserved_roots',{}).items())
    try:
        # Copy enclosing roots first so shared/nested data and code stay shared.
        roots=preservation_roots(found,old_root,copy_external=copy_external or independent)
        if independent:
            for source in roots:
                if not source.is_dir() or source == Path(source.anchor) or new_root.is_relative_to(source):
                    raise ValueError('Configured profile/code is missing or contains the new program: '+str(source))
        for source in sorted(roots,key=lambda p:len(p.parts)):
            if source==new_root or new_root.is_relative_to(source) or data_home==source or data_home.is_relative_to(source):
                raise ValueError('Preserved source overlaps the new program or data destination')
            if any(source.is_relative_to(parent) for parent in relocated):continue
            destination=previous.get(source)
            if destination is not None and not (source/'.git').is_file():
                if not destination.is_relative_to(data_home) or (destination.exists() and not destination.is_dir()):raise ValueError('Recorded preserved copy is outside the selected data home')
            else:
                destination=data_home/operation/('preserved-'+str(len(relocated)))
            relocated[source]=destination
            copied.append(str(destination))
        def selected(source):
            for parent,destination in relocated.items():
                if source.is_relative_to(parent):return destination/source.relative_to(parent)
            return source
        # Name every in-progress copy BEFORE its first data byte, so a killed
        # process can resume the same partial directories rather than orphan GBs.
        pending={'format':'helene-python-migration.v1','id':operation,'state':'preserving',
                 'old_root':str(old_root),'new_root':str(new_root),'backup':backup['path'],
                 'old_data_paths':[row['tree'] for row in found['agents']],
                 'data_paths':[str(selected(Path(row['tree']))) for row in found['agents']],
                 'source_paths':[{'old':str(row['code']),'selected':str(selected(row['code']))} for row in found['agents']],
                 'copied_data':copied,'preserved_roots':{str(a):str(b) for a,b in relocated.items()},
                 'data_home':str(data_home),'path_aliases':path_aliases}
        new_root.mkdir(parents=True,exist_ok=True);write_note(new_root,pending)
        if fresh_template is not None:
            if template.read_bytes() != fresh_template:
                raise ValueError('Fresh destination template changed during backup')
            template.unlink()  # the existing preserving journal now owns recovery
        for index,(source,destination) in enumerate(relocated.items()):
            if destination.exists():
                refresh_preserved(source,destination,data_home/operation/('previous-delta-'+str(index)),progress)
            else:preserve_source(source,destination,progress)
            if independent:
                from .portable_profile import detach_links, verify_copy
                detach_links(source,destination,relocated)
        if independent:
            for source,destination in relocated.items():
                verify_copy(source,destination,progress)
        for row in found["agents"]:
            source = Path(row["tree"])
            destination = selected(source)
            value = dict(row["value"])
            value["tree"] = str(destination)
            if had_service:
                value["installed"] = dict(value.get("installed") or {}, service=False)
            # Match the runner's actual default, not a guessed stock tree.
            old_code = row['code']
            value['code']=str(selected(old_code))
            if path_aliases is not None or independent:
                value['backup']=dict(value.get('backup') or {},dir=str(data_home/'backups'/Path(row['relative']).parent))
            source_paths.append({"old": str(old_code), "selected": value["code"]})
            value['python']=str(new_root/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3'))
            # Preserve explicitly selected custom entry scripts. Stock paths are
            # replaced by the new shell/runner; self-edited code remains selected.
            for key,default,stock in (("app","app/deskapp.py",('deskapp.py','app/deskapp.py')),
                                      ("runner","app/localharness/runner.py",('localharness/runner.py','app/localharness/runner.py'))):
                prior=value.get(key)
                if not prior:
                    if key=='app':value[key]=str(new_root/default)
                    continue
                prior_path=Path(str(prior))
                resolved=(prior_path if prior_path.is_absolute() else Path(row['config']).parent/prior_path).resolve()
                standard=any(resolved==(old_root/p).resolve() for p in stock)
                value[key]=str(new_root/default) if standard else str(selected(resolved))
            target = staged / row["relative"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        note = {"format": "helene-python-migration.v1", "id": operation,
                "old_root": str(old_root), "new_root": str(new_root), "state": "prepared",
                "data_paths": [str(tree_path(staged / r["relative"], load(staged / r["relative"]))) for r in found["agents"]],
                "old_data_paths": [r["tree"] for r in found["agents"]],
                "copied_data": copied, "backup": backup["path"]}
        note['source_paths'] = source_paths
        note['preserved_roots']=pending['preserved_roots']
        note['data_home']=str(data_home);note['path_aliases']=path_aliases
        if independent:
            note['portable']=True
            from .portable_profile import relative_config
            for config in configs(staged):
                if config.is_file():
                    value=relative_config(load(config),new_root/config.relative_to(staged),new_root)
                    config.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        if reuse:note['resumed_copy']=reuse['id']
        (staged / "python-migration.json").write_text(json.dumps(note, indent=2), encoding="utf-8")
        new_root.mkdir(parents=True, exist_ok=True)
        # Program files may already be unpacked; only absent config paths land.
        for config in configs(staged):
            if config.is_file():
                target = new_root / config.relative_to(staged)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise FileExistsError(target)
                shutil.copyfile(config, target)
        for name in ('window-preferences.json','agent-default.json'):
            preferences=old_root/name
            if preferences.is_file():shutil.copy2(preferences,new_root/name)
        from .component_downloads import preserve_cache,preserve_installed
        if preserve_downloads:preserve_cache(old_root,new_root)
        if preserve_voice:preserve_installed(old_root,new_root)
        write_note(new_root,note)
        return note
    finally:
        shutil.rmtree(staged)


def confirm_started(new_root: Path, operation: str, *, proof):
    """Called only after the new controller identifies its own channel/runner."""
    path = new_root / "python-migration.json"
    note = load(path)
    if note["id"] != operation or Path(note["new_root"]).resolve() != new_root.resolve():
        raise ValueError("migration startup identity differs")
    if note['state'] in ('completed','retirement_incomplete','failed_before_retirement'):
        return note
    for data in note["data_paths"]:
        if not Path(data).is_dir():
            raise FileNotFoundError(data)
    config=load(new_root/'helene.json')
    if not isinstance(proof,dict):raise ValueError('Channel identity proof is absent')
    remote=config.get('mode')=='remote'
    if proof.get('stopped') is True:
        from . import owner_stop
        if not owner_stop.stopped() or proof.get('root')!=str(new_root.resolve()):
            raise ValueError('Paused startup must retain the actual owner Stop')
        if not all(Path(p['selected']).is_dir() for p in note['source_paths']):
            raise ValueError('Preserved executable source is unavailable')
    elif remote:
        if not proof.get('remote') or proof.get('base')!=config.get('base') or not proof.get('connected'):
            raise ValueError('Remote channel connection is unconfirmed')
    elif proof.get('tree') not in note['data_paths'] or not proof.get('channel_pid'):
        raise ValueError('Channel identity proof is absent')
    from .desktop_supervisor import runner_required, BASE_ID
    if not remote and not proof.get('stopped') and runner_required(config, proof.get('id', BASE_ID)) and (not proof.get('runner_pid') or proof.get('code') not in [p['selected'] for p in note['source_paths']]):
        raise ValueError('Preserved runner source execution is unconfirmed')
    note['startup_proof']=proof
    note["state"] = "new_app_started"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(note, indent=2), encoding="utf-8")
    temporary.replace(path)
    return note


def retire_program(new_root: Path, operation: str, old_program_files: list[str] | dict[str, str]):
    """Remove only the old package's explicit file inventory, never a directory tree.

    Service/uninstaller coordination belongs to the updater before this call.
    A name match or arbitrary directory list is not an installed package inventory.
    """
    note = load(new_root / "python-migration.json")
    if note["id"] != operation or note["state"] != "new_app_started":
        raise ValueError("New Python application startup is not confirmed")
    old = Path(note["old_root"])
    data = [Path(p).resolve() for p in note["data_paths"] + note.get("old_data_paths", [])]
    data += [Path(p[k]).resolve() for p in note.get('source_paths',[]) for k in ('old','selected')]
    removed, kept = [], []
    for relative in old_program_files:
        path = (old / relative).resolve()
        if not path.is_relative_to(old) or path == old or any(path == p or path.is_relative_to(p) for p in data):
            raise ValueError("Package retirement overlaps data or leaves old root")
        if path.name in {"helene.json", "helene.json.bak"}:
            continue
        if path.is_file():
            if isinstance(old_program_files, dict):
                from .app_components import digest
                if digest(path) != old_program_files[relative]:
                    kept.append(relative)
                    continue
            path.unlink()
            removed.append(relative)
    return {"ok": True, "removed": removed, "modified_retained": kept, "old_config_retained": True}
