"""Exact bounded simple12 / Exit-hotfix preparer source fixtures.

Frozen from exit-hotfix-3110/verification/repository/desk/localharness, 2026-10-31.
Only namespace imports are supplied here; function bodies remain exact.
"""
__package__='localharness'
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import datetime as dt
import hashlib,os
from localharness import app_migration,portable_switch
from localharness.essential_backup import configs
from localharness.package_notice import read,write
FORMAT=portable_switch.FORMAT
scratch_scope=portable_switch.scratch_scope

SOURCE_FRAGMENTS={'portable_switch.py:prepare': '38feeaf8125f4b3521aa097e62f1209bba0c0aafacfdf2b324fcbbea4fa7fea8', 'update_profile.py:selection': '3a6e88ecab0d9aecc9a650b79fbf6a25bbc7395d735b9c90cf4a79f7a242a245', 'portable_switch.py:payload': '1b07e3c54c13c91176d949309e198be6b3a6e796cb1665a3399bf6b2d7428ea0'}

def prepare(old,new,progress=None,*,verified=None,attached_data=None):
    from . import code_transition,update_composition,update_profile
    from .first_start import _bind,_write,FORMAT as FIRST_FORMAT,receipt
    from .python_source_updater import prepared_install_path
    from .essential_backup import configs,load
    from .portable_profile import relative_config
    old,new=Path(old).resolve(),Path(new).resolve()
    if old==new or new.is_relative_to(old) or old.is_relative_to(new):raise ValueError('The new portable programme must be independent of the old folder')
    before=update_profile.selection(old)
    code_transition.validate(new)
    note=update_profile.prepare(old,new,progress,attached_data=attached_data)
    note['portable_folder_switch']=True
    for data in note['data_paths']:
        path=Path(data).resolve()
        if path==old or path.is_relative_to(old) or path==new or path.is_relative_to(new):
            raise ValueError('Portable update data must be outside both programme folders')
    code_transition.prepare(old,new,note,progress=progress)
    # Shared harness changes are part of the same reviewed composition.
    for name,row in update_composition.selected_files(new).items():
        from .source_merge import relative
        relative(name);destination=new/name
        if row.get('delete'):destination.unlink(missing_ok=True)
        else:
            from .update_io import copy_verified
            copy_verified(Path(row['source']),destination,row['sha256'])
    relocations=[]
    for config in configs(new):
        if not config.is_file():continue
        value=load(config);selected=value.get('code')
        data=Path(value['tree']).resolve()
        value=_bind(value,config.parent,new,data);value['code']=selected
        for block,field in (('backup','dir'),('voice','tts_reference')):
            entry=value.get(block)
            if not isinstance(entry,dict) or not entry.get(field):continue
            source=Path(entry[field]);source=source if source.is_absolute() else config.parent/source
            source=source.resolve()
            if source.is_relative_to(old) and source.exists():
                destination=data/'retained-assets'/note['id']/block/source.name
                relocations.append({'source':str(source),'destination':str(destination)})
                entry[field]=str(destination)
        def independent(item):
            if isinstance(item,dict):
                for child in item.values():independent(child)
            elif isinstance(item,list):
                for child in item:independent(child)
            elif isinstance(item,str):
                path=Path(item)
                if path.is_absolute() and path.resolve().is_relative_to(old):
                    raise ValueError('New settings still depend on the old programme; it cannot be retired')
        independent(value)
        _write(config,relative_config(value,config,new),private=True)
    if update_profile.selection(old)!=before:raise ValueError('Old settings/source changed during preparation')
    note.update(normal_python_update=True,source_policy=note.get('source_policy','preserve'),remove_old=True,
        installation_flow='portable-folder-switch',data_home=note['data_paths'][0])
    write(new/'python-migration.json',note)
    # Preparation is complete; the success trial below has separate authority.
    _write(receipt(new),{'format':FIRST_FORMAT,'state':'completed',
        'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'data':note['data_paths'][0]})
    value={'format':FORMAT,'old':str(old),'new':str(new),'selection':before,'note':note,
        'payload':payload(new),'progress':str(progress.path) if progress else '',
        'relocations':relocations,
        'scratch':scratch_scope(old,new,required=False)}
    write(prepared_install_path(new),value)
    if progress:progress.phase('prepared',preparation_completed=True)
    return value


def selection(old):
    """Bind settings and executable/Git metadata, not the untouched profile.

    code_transition.validate independently hashes the selected executable source
    at the final quiescent boundary. Git metadata is included because index and
    history can change without changing working source bytes.
    """
    from .source_merge import allowed
    from .update_io import stamp
    old = Path(old).resolve()
    found = app_migration.discover(old)
    result = {'settings': {}, 'sources': {}, 'git': {}}
    for config in [*configs(old), *(old / n for n in ('agent-default.json', 'window-preferences.json', 'owner-stop.json'))]:
        if config.is_file():
            result['settings'][config.relative_to(old).as_posix()] = hashlib.sha256(config.read_bytes()).hexdigest()
    for code in dict.fromkeys(str(Path(row['code']).resolve()) for row in found['agents'] if row['value'].get('mode') != 'remote'):
        root = Path(code)
        files = {}
        for directory, dirs, names in os.walk(root, followlinks=False):
            here = Path(directory)
            dirs[:] = sorted(n for n in dirs if allowed((here / n).relative_to(root).as_posix()))
            for name in sorted(names):
                path = here / name
                relative = path.relative_to(root).as_posix()
                if allowed(relative):files[relative] = stamp(path)
        result['sources'][code] = files
        git = root / '.git'
        roots = []
        if git.is_file():
            content = git.read_text(encoding='utf-8').strip()
            if not content.startswith('gitdir: '):raise ValueError('Unrecognized Git pointer')
            roots.append((root / content[8:]).resolve())
            result['git'][str(git)] = stamp(git)
            common = roots[0] / 'commondir'
            if common.is_file():roots.append((roots[0] / common.read_text(encoding='utf-8').strip()).resolve())
        elif git.is_dir():roots.append(git)
        for repo in roots:
            for path in sorted(repo.rglob('*')):
                if path.is_file():result['git'][str(path)] = stamp(path)
    return result


def payload(program,*,runtime=True):
    from .source_merge import inventory
    from .update_composition import app_inventory
    program=Path(program)
    from .essential_backup import configs
    result={'tree':inventory(program/'tree'),'app':app_inventory(program/'app'),
        'settings':{p.relative_to(program).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in configs(program) if p.is_file()},
        'upstream':hashlib.sha256((program/'code-upstream.zip').read_bytes()).hexdigest(),
        'passport':hashlib.sha256((program/'python-package-files.json').read_bytes()).hexdigest()}
    if runtime:
        names=[name for name in read(program/'python-package-files.json')['files'] if name.startswith(('runtime/','bin/')) or name=='helene.exe']
        actual=set()
        from .first_start import plain
        for here,dirs,files in os.walk(program/'runtime',followlinks=False):
            for name in [*dirs,*files]:plain(Path(here)/name)
            for name in files:
                path=Path(here)/name
                if path.suffix=='.pyc' and path.parent.name=='__pycache__':
                    import importlib.util
                    try:
                        if Path(importlib.util.source_from_cache(str(path))).is_file():continue
                    except ValueError:pass
                actual.add(path.relative_to(program).as_posix())
        if actual!={name for name in names if name.startswith('runtime/')}:
            raise ValueError('Portable runtime contains undeclared files or import hooks')
        def measured(name):
            from .first_start import plain
            with plain(program/name).open('rb') as stream:return name,hashlib.file_digest(stream,'sha256').hexdigest()
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:result['runtime']=dict(pool.map(measured,names))
        declared=read(program/'python-package-files.json')['files']
        expected={name:declared[name]['sha256'] if isinstance(declared[name],dict) else declared[name] for name in names}
        if result['runtime']!=expected:raise ValueError('Portable runtime differs from admitted distribution')
    return result
