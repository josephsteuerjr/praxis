"""One Windows first launch: retire old programmes, move data, open the app.

The portable folder remains the programme. Only the owner data has a fixed home.
No old interpreter, agent, update job or migration scheduler is started here.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from .ui_language import NativeValueError,NativeRuntimeError
import base64
import copy
import datetime as dt
import hashlib
import json
import os
from .windows_paths import Path, walk, wide
import shutil
import socket
import stat
import unicodedata
import uuid

FORMAT='helene-first-start.v1'
SETTINGS=('agent-default.json','window-preferences.json','owner-stop.json')


@dataclass(frozen=True)
class Places:
    profile: Path
    appdata: tuple[Path,...]
    program_files: tuple[Path,...]
    downloads: Path

    @property
    def data(self):return self.profile/'Helene/data'

    @classmethod
    def current(cls):
        from deskd.local_files import _known_folder
        profile=_known_folder('5E6C858F-0E22-4760-9AFE-EA3317B67173') or Path.home()
        local=_known_folder('F1B32785-6FBA-4FCF-9D55-7B8E7F157091') or Path(os.environ['LOCALAPPDATA'])
        roaming=_known_folder('3EB685DB-65F9-4CF6-A03A-E3EF65729F3D') or local.parent/'Roaming'
        downloads=_known_folder('374DE290-123F-4565-9164-39C4925E467B') or profile/'Downloads'
        pf=tuple(dict.fromkeys(Path(os.environ[key]) for key in ('ProgramFiles','ProgramFiles(x86)') if os.environ.get(key)))
        return cls(profile,(local,roaming,local.parent/'LocalLow'),pf,downloads)


def branded(name):
    folded=''.join(c for c in unicodedata.normalize('NFKD',name).casefold() if not unicodedata.combining(c))
    return 'helene' in folded or 'элен' in folded


def linked(path):
    try:info=Path(path).lstat()
    except FileNotFoundError:return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0)&0x400)


def plain(path):
    from .posix_paths import system_absolute
    path=Path(system_absolute(path))
    for part in (path,*path.parents):
        try:info=part.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise NativeValueError('first_linked_path', 'Linked first-start path: '+str(part), path=str(part))
    return normalized(path)


def normalized(path):
    result=Path(path).resolve();raw=str(result)
    if os.name=='nt':
        if raw.upper().startswith('\\\\?\\UNC\\'):raw='\\\\'+raw[8:]
        elif raw.startswith('\\\\?\\') and len(raw)>6 and raw[5:7]==':\\':raw=raw[4:]
    return Path(raw)


def load(path):
    from .essential_backup import load as read
    return read(Path(path))


def receipt(root):return Path(root)/'.helene/first-start.json'


def _write(path,value,*,private=False):
    from .vision_policy import _write as atomic,_document
    atomic(Path(path),_document(value),private=private)


def ready(root,places=None):
    root=plain(root)
    if not receipt(root).is_file():return False
    try:
        value=load(receipt(root))
        stamp=dt.datetime.fromisoformat(value['completed_at'])
        places=places or Places.current()
        data=normalized(places.data)
        if root==data or root.is_relative_to(data) or data.is_relative_to(root):return False
        return (value.get('format')==FORMAT and value.get('state')=='completed'
                and stamp.tzinfo is not None and normalized(value['data'])==normalized(places.data))
    except (OSError,ValueError,KeyError,TypeError):return False


def agent_exists(data):
    data=Path(data)
    identity=data/'memory/.control/profile-identity.json'
    if identity.is_file() and load(identity).get('agent_name'):return True
    for name in ('memory/llm.json','memory/groups','memory/life','soul'):
        path=data/name
        if path.is_file() and path.stat().st_size:return True
        if path.is_dir() and any(path.iterdir()):return True
    return False


def programme_marker(directory):
    """Product bytes/metadata, never a brand-like directory name alone."""
    directory=Path(directory)
    if linked(directory) or not directory.is_dir():return False
    def file(name):
        try:return plain(directory/name).is_file()
        except (OSError,ValueError):return False
    if any(file(name) for name in ('helene.exe','Helene.exe','Hélène.exe')):return True
    if file('app/python_app.py') and file('app/deskapp.py'):return True
    for name,kind in (('python-app.json','helene-python-app.v1'),
                      ('.helene/first-start.json',FORMAT),('python-migration.json','helene-python-migration.v1')):
        if not file(name):continue
        try:
            if load(directory/name).get('format')==kind:return True
        except (OSError,ValueError,TypeError):pass
    if file('helene.json'):
        try:
            value=load(directory/'helene.json')
            if value.get('mode','local') in ('local','remote') and (value.get('tree') or value.get('code')):return True
        except (OSError,ValueError,TypeError):pass
    return False


def programme_container(directory):
    todo=[(Path(directory),0)];skip={'app','runtime','tree','code','verification','.git','.helene','data','memory','node_modules'}
    while todo:
        here,depth=todo.pop()
        if linked(here):continue
        if programme_marker(here):return True
        if depth>=3:continue
        try:children=list(here.iterdir())
        except (FileNotFoundError,PermissionError):continue
        todo.extend((p,depth+1) for p in children if p.name.casefold() not in skip and p.is_dir() and not linked(p))
    return False


def _roots(base,current,*,require_marker=False,empty_only=None,temporary=()):
    """Direct programme roots or one vendor container; never traverse deeper.

    A vendor's directory is not itself a cleanup member. Outside AppData its
    named child must still carry programme evidence; registrations are separate.
    """
    result=[]
    def transient(path):
        return any(Path(path)==root or Path(path).is_relative_to(root) for root in temporary)
    if transient(base) or not base.is_dir():return result
    base=plain(base)
    try:entries=list(os.scandir(base))
    except PermissionError:return result
    nested=[]
    for entry in entries:
        if transient(entry.path) or branded(entry.name):continue
        try:
            # Use scandir's own entry metadata before entering one vendor
            # level. Do not lstat unrelated application/device paths through
            # linked(), and never follow a reparse point or symlink here.
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):continue
            if getattr(entry.stat(follow_symlinks=False),'st_file_attributes',0)&0x400:continue
            with os.scandir(wide(entry.path)) as children:
                nested.extend(child for child in children if branded(child.name))
        except OSError:
            continue  # An inaccessible unrelated vendor is not an installation.
    for entry in [*entries,*nested]:
        # A second level finds Vendor/Helene without admitting Vendor or
        # searching inside its unrelated application/data trees.
        if transient(entry.path) or not branded(entry.name):continue
        nested_entry=Path(entry.path).parent!=base
        if linked(entry.path):
            if not require_marker and not nested_entry:result.append(Path(entry.path))
            continue
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):continue
        try:path=plain(Path(entry.path))
        except ValueError:continue
        if current==path or current.is_relative_to(path):continue
        marked=programme_container(path) if require_marker or nested_entry else False
        empty_remnant=False
        if nested_entry and not require_marker and not marked:
            try:
                with os.scandir(path) as contents:empty_remnant=next(contents,None) is None
            except (FileNotFoundError,PermissionError):continue
        if not (require_marker or nested_entry) or marked or empty_remnant:
            result.append(path)
            if empty_remnant and empty_only is not None:empty_only.append(path)
    return result

def characteristic(path):
    if path.suffix.casefold()=='.exe':
        import re
        if path.stem.casefold() in ('helene','hélène'):return True
        if re.fullmatch(r'(?:helene|hélène)[_-]\d+\.\d+\.\d+(?:[-._a-z0-9]+)?(?:setup|installer)',path.stem.casefold()):return True
    if branded(path.name) and path.suffix.casefold()=='.zip':
        import zipfile
        if any(word in path.stem.casefold() for word in ('backup','бэкап','бекап','резерв')):return False
        try:
            with zipfile.ZipFile(path) as archive:
                names=set(archive.namelist())
                # A copied executable does not turn an archived owner profile
                # into disposable programme code. Never open or migrate it.
                private={'data','memory','soul','workspace','backups','profile','dossiers',
                         'attachments','.secrets','auth.json','credentials.json','helene.json','owner-stop.json'}
                for raw in names:
                    parts=raw.replace('\\','/').strip('/').split('/')
                    if parts[0].casefold()!='helene':return False
                    if len(parts)>1 and parts[0].casefold()=='helene' and parts[1].casefold() in private:return False
                if any(name in names for name in ('Helene/helene.exe','Helene/Helene.exe')):return True
                name='Helene/python-app.json'
                if name in names and archive.getinfo(name).file_size<=65536:
                    value=json.loads(archive.read(name))
                    if isinstance(value,dict) and value.get('format')=='helene-python-app.v1':return True
        except (OSError,ValueError,KeyError,zipfile.BadZipFile):pass
    if path.name.startswith(('.helene-update-','.helene-install-','.helene-migration-','.helene-source-admission-')) and path.suffix in ('.json','.lock'):return True
    if path.name.startswith(('update-job-ref-','.python-migrator-')) and path.suffix in ('.json','.lock'):return True
    return False


def discover(root,places,*,empty_only=None):
    root=plain(root)
    # Programs is an explicit Windows install container, not permission to
    # descend through every application's folders while searching for data.
    install_containers=tuple(p/'Programs' for p in places.appdata if p.name.casefold()=='local')
    local_bases=(*places.appdata,*install_containers)
    bases=tuple(plain(p) for p in (*local_bases,*places.program_files))
    import tempfile
    candidates=[*(p/'Temp' for p in places.appdata if p.name.casefold()=='local'),
                Path(tempfile.gettempdir()),
                *(Path(os.environ[key]) for key in ('TEMP','TMP') if os.environ.get(key))]
    # Only temporary containers inside this discovery scope are relevant. An
    # isolated fixture may itself live below the real OS temp directory; that
    # does not make its simulated AppData hierarchy an actual temporary root.
    temporary=tuple({normalized(p) for p in candidates if p.is_absolute()
        and any(normalized(p)==base or normalized(p).is_relative_to(base) for base in bases)})
    data=plain(places.data)
    if root==data or root.is_relative_to(data) or data.is_relative_to(root):
        raise NativeValueError('first_separate_program', 'Распакуй программу в отдельную папку, вне данных агента в профиле')
    if any(root==base or root.is_relative_to(base) for base in bases):
        raise NativeValueError('first_separate_install', 'Распакуй новую Hélène в отдельную папку вне AppData и Program Files: прежние установки там очищаются.')
    def group(base):
        empty=[]
        roots=_roots(base,root,require_marker=base not in local_bases,empty_only=empty,temporary=temporary)
        return roots,empty
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:groups=list(pool.map(group,bases))
    found=[path for roots,_empty in groups for path in roots]
    keep=root
    # Windows' default extraction adds an outer archive-name directory around
    # the shipped Helene/ root. Its neighbours, not a hardcoded Downloads path,
    # are the programme's cleanup neighbourhood.
    if root.name.casefold()=='helene' and branded(root.parent.name) and not programme_marker(root.parent):
        keep=root.parent
    neighbours=keep.parent
    siblings=[path for path in found if linked(path)]
    found=[path for path in found if not linked(path)]
    if neighbours.is_dir():
        for path in neighbours.iterdir():
            if any(path==temp or path.is_relative_to(temp) for temp in temporary):continue
            if path==keep or root==path or root.is_relative_to(path):continue
            if linked(path):
                continue
            if (path.is_dir() and branded(path.name) and programme_container(path)) or (path.is_file() and characteristic(path)):
                siblings.append(plain(path))
    result=[]
    for path in sorted(set(found),key=lambda p:len(p.parts)):
        if not any(path.is_relative_to(parent) for parent in result):result.append(path)
    if empty_only is not None:
        broader={path for roots,empty in groups for path in roots if path not in empty}
        empty_only.extend(path for _roots,empty in groups for path in empty if path in result and path not in broader)
    return result,siblings


def _data_allowed(path,places):
    return path==places.data.resolve() or any(path!=base.resolve() and path.is_relative_to(base.resolve()) for base in (*places.appdata,*places.program_files))


def configurations(roots,places):
    result=[]
    for root in roots:
        if not root.is_dir():continue
        for config in (root/'helene.json',*sorted((root/'agents').glob('*/helene.json'))):
            if not config.is_file():continue
            value=load(plain(config))
            if value.get('mode','local')!='local':continue
            tree=Path(value.get('tree') or 'data');tree=plain(tree if tree.is_absolute() else config.parent/tree)
            if tree.is_dir() and not _data_allowed(tree,places):
                raise NativeValueError('first_old_external', 'Старая копия хранит данные вне AppData/Program Files: '+str(tree)+'. Сначала перенеси эту папку в разрешённое место.', path=str(tree))
            code=Path(value.get('code') or 'tree');code=plain(code if code.is_absolute() else config.parent/code)
            mixed=tree==root or tree==code or ((tree/'agent.py').is_file() and (tree/'llm.py').is_file())
            if any(other.is_relative_to(tree) and (tree!=other or not mixed) for other in roots):
                raise NativeValueError('first_mixed_programs', 'Папка данных содержит другие программы: '+str(tree), path=str(tree))
            result.append({'program':str(root),'directory':str(config.parent),'tree':str(tree),
                           'config':value,'relative':config.relative_to(root).as_posix(),'mixed':mixed})
    return result


def receipted_programs(container,places):
    """An extracted archive often has an outer version directory and an
    inner Helene directory. Its receipt protects that outer folder too.
    """
    if linked(container) or not container.is_dir():return []
    result=[];todo=[container]
    skip={'app','runtime','tree','code','verification','components','node_modules','.git','data','memory','.helene'}
    while todo:
        path=todo.pop()
        if ready(path,places):result.append(path);continue
        try:children=list(path.iterdir())
        except PermissionError:continue
        todo.extend(child for child in children if child.name.casefold() not in skip and child.is_dir() and not linked(child))
    return result


def data_sources(roots,configs,places):
    found={Path(row['tree']) for row in configs if Path(row['tree']).is_dir() and _data_allowed(Path(row['tree']),places)}
    skip={'app','runtime','tree','code','node_modules','.git','components','verification','memory','soul','workspace','backups','archives'}
    for root in roots:
        if not root.is_dir():continue
        install_only=any(root.is_relative_to(base/'Programs') for base in places.appdata if base.name.casefold()=='local')
        if install_only and not any(Path(row['program'])==root for row in configs):
            continue  # No native/config binding: fallback data comes from Local itself.
        # Only the known small container depth: root/data, agents/name/data,
        # or Helene-Data/id/preserved-N. Never walk inside an actual data tree.
        todo=[(root,0)]
        while todo:
            here,depth=todo.pop()
            if agent_exists(here) and _data_allowed(here,places):
                configured=any(Path(row['tree'])==here for row in configs)
                if not configured and any((here/name).exists() for name in ('agent.py','llm.py','app','runtime')):
                    # An unbound mixed programme/memory root is not a known
                    # data folder. Leave it for guard_cleanup_data to refuse
                    # before retiring any programme or touching its bytes.
                    continue
                found.add(plain(here));continue
            if depth>=3:continue
            for child in here.iterdir():
                if child.name.casefold() in skip or linked(child) or not child.is_dir():continue
                path=plain(child)
                if path.name.casefold()=='data':
                    if _data_allowed(path,places):found.add(path)
                else:todo.append((path,depth+1))
    if places.data.is_dir():found.add(plain(places.data))
    result=[]
    for path in sorted(found,key=lambda p:len(p.parts)):
        if not any(path.is_relative_to(parent) for parent in result):result.append(path)
    return result

def guard_cleanup_data(candidates,places,admitted=()):
    """A missing config never authorizes deleting data in a cleanup candidate.

    This inspects only already-selected folders. It does not adopt data from
    Downloads or expand the allowed migration-source search.
    """
    from .validation_read import checkpoint
    checkpoint()
    skip={'app','runtime','tree','code','code-backup','verification','components',
          'node_modules','.git','__pycache__','test','tests'}
    for candidate in candidates:
        checkpoint()
        if linked(candidate) or not candidate.is_dir():continue
        todo=[candidate]
        while todo:
            checkpoint()
            here=plain(todo.pop())
            if any(here==source or here.is_relative_to(source) for source in admitted):continue
            memory=here/'memory'
            if agent_exists(here) or (memory.is_dir() and any(memory.iterdir())):
                reason='Найдены данные вне AppData/Program Files' if not _data_allowed(here,places) else 'Не определена структура прежних данных'
                raise NativeValueError('first_cleanup_data', reason+': '+str(here)+'. Исходные файлы сохранены.', reason=str(reason), path=str(here))
            for child in here.iterdir():
                checkpoint()
                if linked(child) or not child.is_dir() or child.name.casefold() in skip:continue
                if child.name.casefold()=='data':
                    if not any(child==source or child.is_relative_to(source) for source in admitted) and any(child.iterdir()):
                        raise NativeValueError('first_found_external', 'Найдены данные вне AppData/Program Files: '+str(child)+'. Эта папка сохранена.', path=str(child))
                else:todo.append(child)


def prepare_configuration(root,target,primary,selected,progress):
    """Validate settings and prepare required transport before touching old owners."""
    if selected:
        original=selected['config']
        if (original.get('setup_complete') is False or not (original.get('model') or {}).get('model')) and agent_exists(primary):
            recovered=_from_brain(primary,root)
            if (recovered.get('model') or {}).get('model'):
                original={**original,**{key:value for key,value in recovered.items()
                    if key not in ('tree','code','python','app','runner','port','installed','agent','owner')}}
                original['setup_complete']=True
                for field in ('agent','owner'):
                    if recovered.get(field):original[field]=recovered[field]
        value=_bind(original,Path(selected['directory']),root,target)
        from .standalone_relay import prepare_migration
        value=prepare_migration(selected['program'],root,[value],progress)[0]
    elif (root/'helene.json').is_file():value=_bind(load(root/'helene.json'),root,root,target)
    else:value=_from_brain(primary,root)
    return _bind(value,root,root,target)


def _score(path,configs):
    configured=any(Path(row['tree'])==path and row['config'].get('setup_complete') is not False for row in configs)
    history=path/'memory/groups'
    files=list(history.glob('*.jsonl')) if history.is_dir() else []
    newest=0.0;size=0
    for item in files:
        info=item.stat();size+=info.st_size
        if not info.st_size:continue
        with item.open('rb') as stream:
            stream.seek(max(0,info.st_size-65536));tail=stream.read()
        for line in reversed(tail.splitlines()):
            try:
                entry=json.loads(line)
                value=entry.get('timestamp') if isinstance(entry,dict) else None
                if isinstance(value,str):moment=dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
                elif isinstance(value,(int,float)):moment=float(value)
                else:continue
                newest=max(newest,moment);break
            except (ValueError,TypeError,OverflowError):continue
    if not newest:
        for name in ('memory/llm.json','memory/.state/helene-profile.json','soul/SOUL.md'):
            item=path/name
            if item.is_file() and item.stat().st_size:newest=max(newest,item.stat().st_mtime)
    return (agent_exists(path),configured,newest,size,str(path))

def _separate_data(source,destination):
    """Old Python editions mixed state with executable modules. Move state,
    workspace and assets out; leave the old code for immediate retirement.
    The deterministic destination also resumes an interrupted extraction.
    """
    code_dirs={'app','runtime','tree','code','core','hands','body','relay','scripts','serverd','tools',
               'node_modules','.git','.venv','venv','__pycache__','components','verification','target'}
    code_suffix={'.py','.pyi','.pyc','.pyd','.dll','.exe','.rs','.js','.mjs','.cjs','.ts','.tsx','.html','.css'}
    metadata={'helene.json','python-app.json','python-package-files.json','build.json','source-inputs.json',
              'portable-program.json','python-migration.json','helene-bootstrap.json','readme.md','start-here.md'}
    destination.mkdir(parents=True,exist_ok=True)
    for path in list(source.iterdir()):
        if destination==path or destination.is_relative_to(path):continue
        plain(path)
        if path.is_dir():
            if path.name.casefold() in code_dirs:continue
        elif path.suffix.casefold() in code_suffix or path.name.casefold() in metadata:continue
        out=destination/path.name
        if path.is_dir():_move(path,out)
        else:
            if out.exists():
                if path.read_bytes()!=out.read_bytes():raise NativeValueError('first_extracted_conflict', 'Conflicting extracted data: '+str(out), path=str(out))
                path.unlink()
            else:path.rename(out)


def _blank(root,data):
    from .windows_installation import blank_config
    return blank_config(root,data)


def _from_brain(data,root):
    path=data/'memory/llm.json'
    saved=data/'memory/.state/helene-profile.json'
    result=load(saved) if saved.is_file() else _blank(root,data)
    if not path.is_file():return result
    brain=load(path)
    for role,key in (('voice','model'),('evaluator','evaluator')):
        chosen=(brain.get('roles') or {}).get(role) or {}
        endpoint=(brain.get('frameworks') or {}).get(chosen.get('framework')) or {}
        if not chosen.get('model'):continue
        protocol=endpoint.get('protocol') or chosen.get('framework')
        if protocol not in ('openai','anthropic'):raise NativeValueError('first_model_protocol', 'Не распознан протокол сохранённой модели')
        result[key]={**chosen,'framework':protocol,'base_url':endpoint.get('base_url',''),
                     'key':endpoint.get('api_key','')}
        for field in ('api_mode','provider'):
            if endpoint.get(field):result[key][field]=endpoint[field]
    result['setup_complete']=bool(result['model'].get('model'))
    return result


def ensure_config(root,*,places=None,refresh_profile=False):
    places=places or Places.current();root=plain(root);data=plain(places.data)
    data.mkdir(parents=True,exist_ok=True)
    if refresh_profile or not (root/'helene.json').is_file():
        value=_bind(_from_brain(data,root),root,root,data)
        _write(root/'helene.json',value,private=True)
        _adopt_brain(root,data,value)
    return load(root/'helene.json')


def _bind(config,old,root,data):
    value=copy.deepcopy(config)
    value.update(tree=str(data),code=str(root/'tree'),python=str(root/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')),
                 app=str(root/'app/deskapp.py'),runner=str(root/'app/localharness/runner.py'))
    value['installed']=dict(value.get('installed') or {},service=False)
    value['read_dotenv']=False
    phone=value.get('phone') or {}
    if phone.get('mode')=='stable' or (phone.get('external') and type(phone.get('local_port')) is int):
        port=phone.get('local_port') or value.get('port')
        if type(port) is not int or not 1<=port<=65535:raise NativeValueError('first_channel_port', 'Для постоянного адреса телефона нужен сохранённый локальный порт канала')
        value['port']=port;value['phone']=dict(phone,local_port=port)
    else:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));value['port']=sock.getsockname()[1]
        if phone.get('mode')=='automatic':value['phone']=dict(phone,local_port=value['port'])
    for block,key in ((value.get('backup'),'dir'),(value.get('voice'),'tts_reference')):
        if isinstance(block,dict) and block.get(key):
            path=Path(block[key]).expanduser();path=path if path.is_absolute() else old/path
            block[key]=str(path)
    return value


def _move(source,target):
    """Rename on the same volume; cross-volume copies verify each file first."""
    source,target=plain(source),plain(target)
    if source==target:return
    from .data_files import inventory,entry,open_file,identity,materialize,unlink_regular
    directories,files,aliases=inventory(source) if source.is_dir() else ([],[],[])
    materialize(aliases)
    files.extend(alias.path for alias in aliases)
    groups={}
    for path in files:
        info=path.stat();key=(info.st_dev,info.st_ino) if info.st_ino>0 else (str(path),)
        groups.setdefault(key,[]).append(path)
    representatives=[members[0] for members in groups.values()]
    references=[(path,members[0],identity(path.stat())) for members in groups.values() for path in members[1:]]
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        before=source.stat()
        try:source.rename(target)
        except OSError as exc:
            if getattr(exc,'winerror',None)!=17 and exc.errno!=18:raise
        else:
            after=target.stat()
            if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise NativeRuntimeError('first_moved_identity', 'Moved data identity changed')
            return
    if source.is_file():
        import tempfile
        def digest(path):
            with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
        expected=digest(source)
        if target.exists():
            if not target.is_file() or digest(target)!=expected:raise NativeValueError('first_moved_conflict', 'Conflicting moved asset: '+str(target), path=str(target))
        else:
            descriptor,name=tempfile.mkstemp(prefix='.asset-transfer-',dir=target.parent);os.close(descriptor);part=Path(name)
            try:
                shutil.copyfile(source,part)
                if digest(part)!=expected:raise IOError('Asset copy verification failed')
                part.replace(target);shutil.copystat(source,target)
            finally:part.unlink(missing_ok=True)
        unlink_regular(source)
        return
    target.mkdir(parents=True,exist_ok=True)
    def digest_data(path):
        with open_file(path,target) as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    def location(relative,expected):
        out=target/relative;plain(out.parent).mkdir(parents=True,exist_ok=True)
        if os.path.lexists(out):
            if digest_data(out)==expected:return out,True
            out=target/'recovered'/hashlib.sha256(str(source).encode()).hexdigest()[:16]/relative
            plain(out.parent).mkdir(parents=True,exist_ok=True)
            if os.path.lexists(out):
                if digest_data(out)!=expected:raise NativeValueError('first_recovered_conflict', 'Conflicting recovered data: '+str(out), path=str(out))
                return out,True
        return out,False
    def transfer(path):
        relative=path.relative_to(source)
        def digest(p):
            h=hashlib.sha256()
            with plain(p).open('rb') as stream:
                while chunk:=stream.read(1024*1024):h.update(chunk)
            return h.hexdigest()
        expected=digest(path)
        out,matched=location(relative,expected)
        plain(path)
        if matched:unlink_regular(path);return path,out,expected
        try:path.rename(out)
        except OSError as exc:
            if getattr(exc,'winerror',None)!=17 and exc.errno!=18:raise
            import tempfile
            descriptor,name=tempfile.mkstemp(prefix='.first-start-',dir=out.parent);os.close(descriptor)
            part=Path(name)
            try:
                shutil.copyfile(path,part)
                if digest(part)!=expected:raise IOError('Data copy verification failed')
                part.replace(out);shutil.copystat(path,out);unlink_regular(path)
            finally:part.unlink(missing_ok=True)
        return path,out,expected
    # Regular files settle first. Their actual destinations can differ from
    # their old relative names when existing owner data required recovery.
    # Remaining hardlink entries bind to those transferred bytes without a
    # second large blob copy. Source entries stay regular across interruption.
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:
        moved={path:(out,digest) for path,out,digest in pool.map(transfer,representatives)}
    for path,representative,generation in references:
        if identity(plain(path).stat())!=generation:raise NativeValueError('first_source_changed', 'Source data reference changed')
        backing,expected=moved[representative]
        backing,_=entry(backing,target);before=backing.stat()
        out,matched=location(path.relative_to(source),expected)
        if not matched:
            try:os.link(backing,out)
            except OSError as error:
                import errno,tempfile
                if error.errno not in (errno.EXDEV,errno.EPERM,errno.ENOSYS,errno.EOPNOTSUPP) and getattr(error,'winerror',None) not in (1,17,50,1314):raise
                descriptor,name=tempfile.mkstemp(prefix='.alias-transfer-',dir=out.parent);os.close(descriptor);part=Path(name)
                try:
                    shutil.copyfile(backing,part)
                    with part.open('rb') as stream:
                        if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:raise IOError('Data alias copy verification failed')
                    part.replace(out)
                finally:part.unlink(missing_ok=True)
        if identity(backing.stat())!=identity(before):raise NativeValueError('first_alias_changed', 'Transferred alias target changed')
        if identity(plain(path).stat())!=generation:raise NativeValueError('first_source_changed', 'Source data reference changed')
        unlink_regular(path)
    for here,dirs,_files in walk(source,topdown=False):
        for name in dirs:(Path(here)/name).rmdir()
    source.rmdir()


def reset_execution(data):
    """Discard old executable queues/leases, retaining memory and task history."""
    data=plain(data);removed=0
    def discard(path):
        plain(path)
        def writable(function,name,_error):
            os.chmod(name,stat.S_IWRITE|stat.S_IREAD);function(name)
        try:
            if path.is_dir():shutil.rmtree(wide(path),onerror=writable)
            else:path.unlink()
        except PermissionError:
            os.chmod(path,stat.S_IWRITE|stat.S_IREAD)
            if path.is_dir():shutil.rmtree(wide(path),onerror=writable)
            else:path.unlink()
    for name in ('.desktop-owner.lock','.python-desktop-status','body/bridge/spool.db','body/bridge/spool.db-wal','body/bridge/spool.db-shm'):
        path=data/name
        if path.exists():discard(path);removed+=1
    control=data/'memory/.control'
    if control.is_dir():
        for path in control.iterdir():
            if path.name in ('profile-identity.json','autonomy-paused.json','autonomy-generation.json','update-supersessions'):continue
            discard(path)
            removed+=1
    # Only harness-owned state namespaces contain disposable leases. A
    # workspace Cargo.lock/poetry.lock/uv.lock is owner source, not a lease.
    states=(data/'memory/.state',data/'.state')
    leases={'harness.lock','authored-notes.lock','desires.lock','recall_builder.lock',
            'praxis_device_auth.lock','self-model.lock','workshop-mutations.lock'}
    queue_files={'queue.json','queues.json','inbox.json','outbox.json','pending.json','notification-queue.json'}
    for parent in states:
        for path in list(parent.iterdir()) if parent.is_dir() else []:
            if not path.is_file():continue
            lower=path.name.casefold()
            lock=lower in leases
            runtime=lower.startswith(('vision-policy-change','vision-policy-notice')) or lower in queue_files
            if lock or runtime:discard(path);removed+=1
    for name in ('memory/.state/life/.state-locks','memory/.state/life/.refresh-locks','memory/life/.journal-locks'):
        directory=data/name
        if directory.is_dir():discard(directory);removed+=1
    for parent in (data,data/'memory',*states):
        for name in ('queue','queues','inbox','outbox','pending','notification-queue'):
            path=parent/name
            if path.is_dir():discard(path);removed+=1
    for base in (data/'memory/.forge',data/'memory/forge',data/'memory/subagents',data/'memory/.subagents',data/'memory/.source-updates',data/'memory/.source-update-reviews'):
        for path in base.rglob('*.json') if base.is_dir() else []:
            if path.name not in ('request.json','task.json','state.json'):continue
            value=load(path);changed=False
            for field in ('status','state','execution'):
                if value.get(field) in ('queued','pending','starting','running','waiting','waiting_children','prepared'):
                    value[field]='cancelled';changed=True
            if changed:value['interruption']='clean first start';_write(path,value)
    return removed


def _prune_program(root,protected,backend):
    for path in list(root.iterdir()):
        if any(path==data or path.is_relative_to(data) for data in protected):continue
        if path.is_dir() and any(data.is_relative_to(path) for data in protected):
            _prune_program(path,protected,backend)
        else:backend.remove(path)


def _adopt_brain(root,data,config):
    target=data/'memory/llm.json'
    if not target.is_file():return
    from . import boot
    boot_profile=load(target);built=boot._consumer_brain_config(boot._brain_config(config),root/'tree')
    fingerprint=hashlib.sha256(json.dumps(built,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    _write(data/'memory/.state/brain_projection.json',{'fingerprint':fingerprint,
        'projected':boot._projected_fields(boot_profile),'adopted_existing_data':True},private=True)


def legacy_plan(root,places,backend):
    """The receipt, not a caller's keep-list, protects a modern programme."""
    empty_only=[]
    old_roots,siblings=discover(root,places,empty_only=empty_only)
    registered=getattr(backend,'registered',lambda _places:[])(places)
    for path in registered:
        path=plain(path)
        if path!=root and path not in old_roots:old_roots.append(path)
    # Local may contain both a valid profile and a second, unidentifiable
    # data-like subtree. Preserve the container and migrate only its explicit
    # profile. An unbound cache is excluded from every migration/cleanup step.
    preserved=[]
    keep_program_files=[]
    local=next((p for p in places.appdata if p.name.casefold()=='local'),None)
    for candidate in list(old_roots):
        if local is None or not candidate.is_relative_to(local) or linked(candidate):
            continue
        configs=configurations([candidate],places)
        try:
            sources=data_sources([candidate],configs,places)
            guard_cleanup_data([candidate],places,sources)
        except NativeValueError as exc:
            if exc.native_caption['code'] not in ('first_cleanup_data','first_found_external'):
                raise
        except OSError as exc:
            if not isinstance(exc,PermissionError) and getattr(exc,'winerror',None)!=206:
                raise
            if configs or candidate in registered:
                raise  # A selected profile must be readable before migrating it.
        else:
            continue
        preserved.append(candidate)
        if configs or candidate in registered:
            keep_program_files.append(candidate)
        else:
            old_roots.remove(candidate)
    all_roots=[*old_roots,*[p for p in siblings if p.is_dir() and not linked(p)]]
    admitted={path:receipted_programs(path,places) for path in all_roots}
    retained=list(dict.fromkeys(p for programs in admitted.values() for p in programs))
    old_roots=[path for path in old_roots if not admitted.get(path)]
    siblings=[path for path in siblings if not admitted.get(path)]
    owner_path=places.data/'memory/.control/desktop-session.json'
    owner=load(owner_path) if owner_path.is_file() else {}
    if owner.get('program'):
        bound=plain(owner['program'])
        if bound!=root and bound not in all_roots and ready(bound,places):retained.append(bound)
    all_roots=[*old_roots,*[p for p in siblings if p.is_dir() and not linked(p)],*retained]
    protected=list(dict.fromkeys([root,*retained]))
    containers=[path for path,programs in admitted.items() if programs]
    containers.extend(path for path in retained if not any(path.is_relative_to(base) for base in containers))
    stamps={str(path):hashlib.sha256(receipt(path).read_bytes()).hexdigest()
            for path in protected if ready(path,places)}
    plan={'old_roots':old_roots,'siblings':siblings,'all_roots':all_roots,
          'retained':retained,'retained_roots':containers,'protected':protected,'receipts':stamps,
          'empty_only':[path for path in empty_only if path in old_roots], 'preserved_local':preserved,'keep_program_files':keep_program_files}
    validate_legacy_plan(plan,places)
    return plan


def validate_legacy_plan(plan,places):
    for path in plan.get('empty_only',[]):
        if not os.path.lexists(path):continue  # A prior confirmed empty-only removal is idempotent.
        if linked(path) or not path.is_dir() or any(path.iterdir()):
            raise NativeValueError('first_empty_changed', 'Прежняя пустая папка изменилась. Повтори подготовку: '+str(path), path=str(path))
    for raw,digest in plan['receipts'].items():
        path=plain(raw)
        if not ready(path,places) or hashlib.sha256(receipt(path).read_bytes()).hexdigest()!=digest:
            raise NativeValueError('first_receipt_changed', 'Квитанция прежней программы изменилась. Повтори обновление.')
    for candidate in [*plan['old_roots'],*plan['siblings']]:
        if any(candidate==path or candidate.is_relative_to(path) or path.is_relative_to(candidate)
               for path in [places.data,*plan['protected']]):
            raise NativeValueError('first_protected_cleanup', 'Очистка пересекается с защищённой программой или данными')
        if not linked(candidate) and receipted_programs(candidate,places):
            raise NativeValueError('first_started_receipt', 'В папке появилась квитанция успешного запуска. Повтори обновление.')


def archive_profiles(configs,data):
    if not configs:return
    archive=data/'memory/.state/imported-profiles.json'
    imported=load(archive) if archive.is_file() else {'format':'helene-imported-profiles.v1','profiles':{}}
    if imported.get('format')!='helene-imported-profiles.v1':raise NativeValueError('first_profile_record', 'Unknown imported profile record')
    for row in configs:
        record={key:copy.deepcopy(row[key]) for key in ('program','relative','config')}
        original=Path(row['directory'])/'helene.json'
        record['raw_config']=base64.b64encode(original.read_bytes()).decode('ascii')
        identity=hashlib.sha256(json.dumps(record,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        imported['profiles'][identity]=record
    _write(archive,imported,private=True)


def settle_legacy_jobs(root,places,plan,configs):
    """After old owners stop, before even mixed code/data is separated."""
    if 'supersession_result' in plan:return plan['supersession_result']
    from . import update_supersession
    old_roots,siblings=plan['old_roots'],plan['siblings']
    job_roots=[p for p in [*old_roots,*siblings] if p.is_dir() and not linked(p)]
    profiles=[row for row in configs if any(Path(row['program'])==base or Path(row['program']).is_relative_to(base) for base in job_roots)]
    job_programs=list(dict.fromkeys([*job_roots,*[Path(row['program']) for row in profiles]]))
    jobs=update_supersession.capture(root,job_programs,
        list(dict.fromkeys([places.data,*[Path(row['tree']) for row in profiles]])))
    _write(Path(root)/'.helene/legacy-supersession.json',jobs,private=True)
    # Read and settle original identities before any programme/data is moved.
    settled=update_supersession.retire(jobs)
    _write(Path(root)/'.helene/legacy-supersession-result.json',settled,private=True)
    if settled['blocked']:raise NativeRuntimeError('first_legacy_jobs', 'Прежние задания обновления требуют проверки завершения процессов')
    plan['supersession']=jobs;plan['supersession_result']=settled
    return settled


def retire_legacy(root,target,places,backend,plan,sources,primary,report,*,modern=False,shortcuts=True):
    """Shared retirement/move boundary; callers retain their own config policy."""
    validate_legacy_plan(plan,places)
    old_roots,siblings=plan['old_roots'],plan['siblings']
    keep_files=plan.get('keep_program_files',[])
    settle_legacy_jobs(root,places,plan,configurations([*old_roots,*[p for p in siblings if p.is_dir()]],places))
    if old_roots:report({'stage':'removing','label':'Удаляем прежние программы'})
    for old in old_roots:
        if old in plan.get('empty_only',[]):continue
        backend.retire(old)
        if old.is_dir() and old not in keep_files:
            if hasattr(backend,'prune'):backend.prune(old,sources)
            else:_prune_program(old,sources,backend)
    report({'stage':'data','label':'Переносим данные' if any(p!=target for p in sources) else 'Подключаем данные'})
    def move(source):
        if modern:reset_execution(source)
        try:_move(source,target)
        except OSError:
            backend.release(source);_move(source,target)
    if primary.exists() and primary!=target:move(primary)
    target.mkdir(parents=True,exist_ok=True)
    for source in sources:
        if source.exists() and source!=target:move(source)
    roots=plan['all_roots'] if shortcuts else [*old_roots,*[p for p in siblings if p.is_dir() and not linked(p)]]
    backend.assert_quiet(roots)
    getattr(backend,'committed_data',lambda _target:None)(target)
    report({'stage':'cleanup','label':'Убираем старые копии и ярлыки'})
    validate_legacy_plan(plan,places)
    for path in [*old_roots,*siblings]:
        if path not in keep_files and os.path.lexists(path):
            if path in plan.get('empty_only',[]):path.rmdir()  # Atomic empty-only removal; never recursive/elevated.
            else:backend.remove(path)
    backend.shortcuts(roots,root if shortcuts else None)
    backend.assert_quiet(roots)
    return cleanup_leftovers(backend,[p for p in [*old_roots,*siblings] if p not in keep_files])+[str(path) for path in plan.get('preserved_local',[])]


def cleanup_leftovers(backend,roots,*,notes=True):
    """11.11 (the owner): what Windows protects from a non-administrator is named, not elevated.

    A former folder may remain only because of paths the backend recorded as protected; any
    other remainder is still a failure. Returns messages for the person.
    """
    kept=[Path(p) for p in getattr(backend,'leftovers',[]) if os.path.lexists(p)]
    messages=list(getattr(backend,'notes',[])) if notes else []
    for path in roots:
        if not os.path.lexists(path):continue
        inside=[k for k in kept if k==Path(path) or k.is_relative_to(path)]
        if not inside:raise NativeRuntimeError('first_leftovers', 'Прежние файлы ещё не удалены: '+str(path), path=str(path))
        messages.append(f'Windows не дала удалить без прав администратора {len(inside)} элемент(ов) прежней программы в {path}. '
                        'Новая Hélène работает с твоими данными; эти файлы можно удалить вручную. '
                        'Прежнюю программу оттуда не запускай: данные теперь у новой.')
    return messages


def sweep_legacy(root,*,places=None,backend=None,progress=None):
    """Update cleanup after old-owner exit, before the reviewed new owner starts.

    The prepared programme/configuration and canonical execution state stay
    authoritative. Only unreceipted legacy programmes and their state move.
    """
    places=places or Places.current();root=plain(root);target=plain(places.data)
    if not ready(root,places):raise NativeValueError('first_preparation_receipt', 'Для обновления нужна квитанция подготовки программы')
    if backend is None:
        from .windows_first_start import Backend
        backend=Backend(root)
    report=progress or (lambda value:None)
    with backend.exclusive(target):
        if not target.is_dir() or not agent_exists(target):raise NativeValueError('first_prepared_data', 'В профиле не найдены данные подготовленного агента')
        active=configurations([root],places)
        if any(Path(row['tree'])!=target and not Path(row['tree']).is_relative_to(target) for row in active):
            raise NativeValueError('first_profile_location', 'Данные подготовленной программы должны находиться в профиле')
        plan=legacy_plan(root,places,backend)
        backend.excluded=plan['protected']
        backend.process_excluded=[root]
        roots=[*plan['old_roots'],*[p for p in plan['siblings'] if p.is_dir() and not linked(p)]]
        configs=configurations(roots,places)
        sources=data_sources(plan['old_roots'],configs,places)
        guard_cleanup_data([*plan['old_roots'],*plan['siblings']],places,[*sources,*plan.get('keep_program_files',[])])
        snapshots={Path(row['directory'])/'helene.json':hashlib.sha256((Path(row['directory'])/'helene.json').read_bytes()).hexdigest() for row in configs}
        from .first_start_assets import prepare as prepare_assets,retain as retain_assets
        prepare_assets(target,roots,sources,copy.deepcopy(configs),{},None)
        validate_legacy_plan(plan,places)
        # Shared canonical data grants no process-killing affinity here. The
        # transaction has stopped its exact old session. Other admitted old
        # supervisors must also settle; their programme FILES remain protected.
        identities=[path for path in sources if path!=target and not path.is_relative_to(target)]
        report({'stage':'stopping','label':'Завершаем прежние установки'})
        stop_roots=list(dict.fromkeys([*roots,*plan['retained']]))
        backend.stop(stop_roots,identities)
        settled_configs={Path(row['directory'])/'helene.json' for row in configurations(roots,places)}
        if settled_configs!=set(snapshots) or any(not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest for path,digest in snapshots.items()):
            raise NativeValueError('first_settings_changed', 'Прежние настройки изменились во время подготовки. Повтори запуск.')
        validate_legacy_plan(plan,places)
        # Shutdown can publish final data or backups after the preview. Never
        # prune using a discovery snapshot from before the writers stopped.
        sources=data_sources(plan['old_roots'],configs,places)
        guard_cleanup_data([*plan['old_roots'],*plan['siblings']],places,[*sources,*plan.get('keep_program_files',[])])
        assets=prepare_assets(target,roots,sources,configs,{},None)
        for source in sources:
            if any(source.is_relative_to(base) for base in places.program_files):
                getattr(backend,'data_access',lambda _path:None)(source)
        settle_legacy_jobs(root,places,plan,configs)
        retain_assets(assets)
        for row in configs:
            original=Path(row['tree'])
            if row.get('mixed') and original.is_dir():
                extracted=original/('.helene-data-'+hashlib.sha256(str(original).encode()).hexdigest()[:16])/'data'
                _separate_data(original,extracted)
                row['tree']=str(extracted);row['mixed']=False
                sources=[path for path in sources if path!=original]
                if extracted not in sources:sources.append(extracted)
        archive_profiles(configs,target)
        leftovers=retire_legacy(root,target,places,backend,plan,sources,target,report,modern=True,shortcuts=False)
        backend.assert_quiet(stop_roots)
        _write(target/'memory/.state/legacy-cleanup.json',{'format':'helene-legacy-cleanup.v1',
            'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'program':str(root),
            'retired':[str(p) for p in [*plan['old_roots'],*plan['siblings']] if not os.path.lexists(p)],
            'leftovers':leftovers},private=True)
        if leftovers:report({'stage':'cleanup','label':'Программа готова · остались прежние файлы','cleanup_errors':leftovers})
        return {'state':'completed','data':str(target),'retired':len(plan['old_roots'])+len(plan['siblings']),
                'cleanup_errors':leftovers}


def prepare(root,*,places=None,backend=None,progress=None):
    places=places or Places.current();root=plain(root);target=plain(places.data)
    if backend is None:
        from .windows_first_start import Backend
        backend=Backend(root)
    report=progress or (lambda value:None)
    with backend.exclusive(target):
        if ready(root,places):return {'state':'completed','data':str(target),'agent':agent_exists(target)}
        # A clean archive beside the known Python owner already has its full
        # programme from extraction. Adopt only that exact selected data and
        # owner; do not discover/hash every old/new programme or run a trial.
        from . import clean_update
        owner=load(target/'memory/.control/desktop-session.json') if (target/'memory/.control/desktop-session.json').is_file() else {}
        previous=owner.get('program')
        if previous and agent_exists(target):
            old=plain(previous)
            if old!=root and (old/'python-app.json').is_file() and (old/'helene.json').is_file():
                config=load(old/'helene.json');data=Path(config.get('tree') or 'data')
                data=plain(data if data.is_absolute() else old/data)
                if data==target:
                    clean_update.roots(old,root)
                    value=clean_update.prepare(old,root,attached_data=target)
                    value['note']['app_retirement']=True
                    from .python_source_updater import prepared_install_path
                    from .package_notice import write as write_note
                    write_note(prepared_install_path(root),value);write_note(root/'python-migration.json',value['note'])
                    report({'stage':'stopping','label':'Завершаем прежнюю Hélène'})
                    backend.stop([old],[target]);backend.assert_quiet([old],[target])
                    clean_update.relocate(old,root,value)
                    report({'stage':'completed','label':'Готово'})
                    return {'state':'completed','data':str(target),'agent':True,'clean_switch':True}
        required=('runtime/python.exe','app/python_app.py','app/deskapp.py','app/windows_start.py',
                  'app/resources/first-start.html','tree/agent.py','tree/llm.py','python-app.json')
        if any(not (root/name).is_file() for name in required):
            raise NativeValueError('first_archive_incomplete', 'Архив распакован не полностью. Распакуй его целиком в новую папку.')
        plan=legacy_plan(root,places,backend)
        old_roots,siblings,all_roots,retained=[plan[key] for key in ('old_roots','siblings','all_roots','retained')]
        owner=load(target/'memory/.control/desktop-session.json') if (target/'memory/.control/desktop-session.json').is_file() else {}
        # Data discovery stays bounded. The exact previously admitted owner
        # may live on Desktop or elsewhere; its receipt still protects its
        # programme and supplies current settings without any directory scan.
        configs=configurations(all_roots,places)
        def priority(row):
            program=Path(row['program'])
            stamp=load(receipt(program)).get('completed_at','') if program in retained else ''
            return (str(program)==owner.get('program'),stamp)
        configs.sort(key=priority,reverse=True)
        if (root/'helene.json').is_file():
            existing=load(root/'helene.json');bound=Path(existing.get('tree') or 'data')
            bound=plain(bound if bound.is_absolute() else root/bound)
            if bound.is_dir() and bound!=target:
                raise NativeValueError('first_already_bound', 'Эта папка уже подключена к другим данным. Распакуй новый архив в отдельную папку.')
        previous={}
        if receipt(root).is_file():
            previous=load(receipt(root))
            if previous.get('format')!=FORMAT:raise NativeValueError('first_unknown_receipt', 'Unknown first-start receipt')
        preview_sources=data_sources(old_roots,configs,places)
        guard_cleanup_data([*old_roots,*siblings],places,[*preview_sources,*plan.get('keep_program_files',[])])
        primary=target if agent_exists(target) else max(preview_sources,key=lambda p:_score(p,configs),default=target)
        selected=next((row for row in configs if Path(row['tree'])==primary),None)
        snapshots={Path(row['directory'])/'helene.json':hashlib.sha256((Path(row['directory'])/'helene.json').read_bytes()).hexdigest() for row in configs}
        # Capture old data identities before preparation creates the new profile.
        # A first launch must not turn its own empty target into an old owner.
        protected=list(dict.fromkeys(preview_sources))
        target_existed=target.is_dir()
        config=prepare_configuration(root,target,primary,selected,report)
        from .first_start_assets import prepare as prepare_assets,retain as retain_assets
        prepare_assets(target,[*old_roots,*[p for p in siblings if p.is_dir() and not linked(p)],*retained],preview_sources,copy.deepcopy(configs),copy.deepcopy(config),selected)
        if any(not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest for path,digest in snapshots.items()):
            raise NativeValueError('first_settings_changed', 'Прежние настройки изменились во время подготовки. Повтори запуск.')
        report({'stage':'stopping' if all_roots or protected else 'preparing',
                'label':'Завершаем прежнюю Hélène' if all_roots or protected else 'Подготавливаем новый профиль'})
        backend.stop(all_roots,protected)
        # Discover data after all previous supervisors/body/bridge have settled.
        sources=data_sources(old_roots,configs,places)
        for raw in previous.get('sources',[]):
            path=plain(raw)
            if not _data_allowed(path,places):raise NativeValueError('first_source_outside', 'Recorded data source leaves allowed roots')
            if path.is_dir() and path not in sources:sources.append(path)
        owner_sources=[p for p in sources if p!=target or target_existed]
        if set(owner_sources)-set(protected):backend.stop(all_roots,owner_sources)
        else:backend.assert_quiet(all_roots,protected)
        sources=list(dict.fromkeys([*sources,*data_sources(old_roots,configs,places)]))
        guard_cleanup_data([*old_roots,*siblings],places,[*sources,*plan.get('keep_program_files',[])])
        settled_configs={Path(row['directory'])/'helene.json' for row in configurations(all_roots,places)}
        if settled_configs!=set(snapshots) or any(not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest for path,digest in snapshots.items()):
            raise NativeValueError('first_settings_finish', 'Прежние настройки изменились при завершении. Исходные файлы сохранены. Повтори запуск.')
        assets=prepare_assets(target,[*old_roots,*[p for p in siblings if p.is_dir() and not linked(p)],*retained],sources,configs,config,selected)
        for source in sources:
            if any(source==base or source.is_relative_to(base) for base in places.program_files):
                getattr(backend,'data_access',lambda _path:None)(source)
        settle_legacy_jobs(root,places,plan,configs)
        retain_assets(assets,keep_sources=retained)
        mixed=[row for row in configs if row.get('mixed') and Path(row['tree']).is_dir()]
        if mixed:
            if not (root/'helene.json').is_file():
                _write(root/'helene.json',config,private=True)
            transfers=[]
            for row in mixed:
                original=Path(row['tree'])
                extracted=original/('.helene-data-'+hashlib.sha256(str(original).encode()).hexdigest()[:16])/'data'
                transfers.append((row,original,extracted))
            _write(receipt(root),{'format':FORMAT,'state':'preparing','data':str(target),
                'sources':[str(p) for p in [*sources,*[t[2] for t in transfers]]]})
            for row,original,extracted in transfers:
                _separate_data(original,extracted)
                if primary==original:primary=extracted
                row['tree']=str(extracted);row['mixed']=False
                sources=[p for p in sources if p!=original]
                if extracted not in sources:sources.append(extracted)
        modern_adoption=bool(retained) and primary==target and agent_exists(target)
        if selected:
            for name in SETTINGS:
                path=Path(selected['program'])/name
                if path.is_file() and not (root/name).exists():
                    from .vision_policy import _write as private_write
                    private_write(root/name,path.read_bytes(),private=True)
        # Credentials/settings from secondary legacy profiles are owner data
        # too. Keep their small private records before retiring any programme;
        # this neither copies code nor creates a programme-folder backup.
        archive_profiles(configs,primary if primary.is_dir() else target)
        # Emergency Stop is owner intent, not an executable queue or lease.
        for base in places.appdata:
            old_stop=base/'Helene/owner-stop.json'
            if old_stop.is_file():
                from .vision_policy import _write as private_write
                private_write(root/'owner-stop.json',old_stop.read_bytes(),private=True)
                break
        for block,key in ((config.get('backup'),'dir'),(config.get('voice'),'tts_reference')):
            if isinstance(block,dict) and block.get(key):
                path=Path(block[key])
                if path.is_relative_to(primary):block[key]=str(target/path.relative_to(primary))
        from .portable_profile import relative_config
        _write(root/'helene.json',relative_config(config,root/'helene.json',root),private=True)
        _write(receipt(root),{'format':FORMAT,'state':'preparing','data':str(target),
            'sources':[str(path) for path in sources]})
        leftovers=retire_legacy(root,target,places,backend,plan,sources,primary,report,modern=modern_adoption) or []
        for _row,_original,extracted in transfers if mixed else []:
            if extracted.parent.exists():backend.remove(extracted.parent)
            if extracted.parent.exists():
                leftovers.append('Временная копия разделённых данных осталась (Windows защитила файлы): '+str(extracted.parent))
        report({'stage':'reset','label':'Освобождаем данные от прежних запусков'})
        # A modern Python folder is another version of the same application,
        # not legacy migration debris. Preserve its durable work queues.
        if not modern_adoption:reset_execution(target)
        saved_stop=root/'owner-stop.json'
        if saved_stop.is_file():
            from .vision_policy import _write as private_write
            private_write(target/'owner-stop.json',saved_stop.read_bytes(),private=True)
            saved_stop.unlink()
        recovered=target/'recovered'
        if recovered.is_dir():
            for directory in recovered.iterdir():
                if directory.is_dir():reset_execution(directory)
        _adopt_brain(root,target,config)
        _write(target/'memory/.state/helene-profile.json',
               {key:value for key,value in config.items() if key not in ('tree','code','python','app','runner','port','installed')},private=True)
        for name in ('python-migration.json','migration-ready.json','portable-program.json'):
            path=root/name
            if path.exists():path.unlink()
        if os.name=='nt':
            from .program_state import tidy_program
            tidy_program(root)
        from .portable_retirement import capture,seal
        retirement=capture(root,plan,places,assets,mode='archive')
        _write(receipt(root),{'format':FORMAT,'state':'completed','completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                             'data':str(target),**({'cleanup_errors':leftovers} if leftovers else {})})
        seal(root,retirement)
        report({'stage':'completed','label':'Готово · остались прежние файлы' if leftovers else 'Готово',
                **({'cleanup_errors':leftovers} if leftovers else {})})
        return {'state':'completed','data':str(target),'agent':agent_exists(target),'cleanup_errors':leftovers}


def handover(root,*,places=None):
    """Reopening any admitted version may take over the shared data owner.
    It never imports changes from, or deletes, the other programme folder.
    """
    places=places or Places.current();root=plain(root)
    from . import session_host,migration_lifecycle
    descriptor=places.data/'memory/.control/desktop-session.json'
    if not descriptor.is_file():return False
    value=load(descriptor);other=plain(value.get('program') or root)
    if other==root:return False
    state=session_host.process_state(value);admitted=ready(other,places)
    if state=='unknown':
        raise NativeRuntimeError('first_previous_owner', 'Не удалось подтвердить прежнего владельца данных')
    from .windows_first_start import Backend
    backend=Backend(root)
    with backend.exclusive(places.data):
        current=load(descriptor)
        if current.get('generation')!=value.get('generation') or current.get('pid')!=value.get('pid'):
            raise NativeRuntimeError('first_owner_changed', 'Владелец данных сменился во время запуска')
        if state=='alive':
            if normalized(value.get('tree',''))!=normalized(places.data):raise NativeValueError('first_process_data', 'Прежний процесс принадлежит другой папке данных')
            if admitted:
                try:migration_lifecycle.stop(other)
                except (OSError,RuntimeError):pass
            roots=[other] if admitted else []
            backend.stop(roots,[places.data]);backend.assert_quiet(roots,[places.data])
        config=other/'helene.json'
        if admitted and config.is_file():
            saved=load(config)
            data=Path(saved.get('tree') or 'data');data=normalized(data if data.is_absolute() else other/data)
            if data!=normalized(places.data):raise NativeValueError('first_config_data', 'Настройки прежней версии указывают на другие данные')
            saved=_bind(saved,other,root,places.data)
            _write(places.data/'memory/.state/helene-profile.json',
                {key:entry for key,entry in saved.items() if key not in ('tree','code','python','app','runner','port','installed')},private=True)
    return admitted or state=='alive'


def creation_config(previous, values):
    """Keep interface, frame and personal fields independent at primary birth.

    Old callers omit the new choices and retain the legacy defaults. Validation
    happens before the one-use birth admission or any credential publication.
    """
    from .language_settings import validate
    config = copy.deepcopy(previous)
    agent = config.get('agent')
    owner = config.get('owner')
    config['agent'] = dict(agent if isinstance(agent, dict) else {},
                           name=str(values.get('name') or '').strip())
    config['owner'] = dict(owner if isinstance(owner, dict) else {},
                           name=str(values.get('owner') or '').strip())
    config['setup_complete'] = True
    if 'lang' in values:
        config['lang'] = values['lang']
    for key in ('frame_language', 'living_prose'):
        if key in values:
            config['agent'][key] = values[key]
    validate(config)
    return config


def create_agent(root,values,*,places=None,progress=None):
    from contextlib import nullcontext
    places=places or Places.current()
    if os.name=='nt':
        from .windows_first_start import Backend
        guard=Backend(root).exclusive(places.data)
    else:guard=nullcontext()
    with guard:return _create_agent(root,values,places=places,progress=progress)


def keyless_local_endpoint(base_url)->bool:
    """True only for an http(s) address on loopback, a private network or *.local."""
    import ipaddress
    from urllib.parse import urlsplit
    try:parts=urlsplit(str(base_url or '').strip());host=(parts.hostname or '').lower()
    except ValueError:return False
    if parts.scheme not in ('http','https') or parts.username or parts.password or not host:return False
    if host=='localhost' or host.endswith(('.localhost','.local')):return True
    try:address=ipaddress.ip_address(host)
    except ValueError:return False
    return (address.is_loopback or address.is_private or address.is_link_local
            or (address.version==4 and address in ipaddress.ip_network('100.64.0.0/10')))


def _create_agent(root,values,*,places,progress=None):
    places=places or Places.current();root=plain(root)
    if not ready(root,places):raise NativeValueError('first_preparation_needed', 'Сначала нужно завершить подготовку')
    from .boot import birth_custody
    with birth_custody(plain(places.data)):
        return _create_agent_locked(root,values,places=places,progress=progress)


def _create_agent_locked(root,values,*,places,progress=None):
    if not ready(root,places):raise NativeValueError('first_preparation_needed', 'Сначала нужно завершить подготовку')
    name=str(values.get('name') or '').strip();owner=str(values.get('owner') or '').strip()
    provider=values.get('provider');model=str(values.get('model') or '').strip();key=str(values.get('key') or '').strip()
    if not name or not model or provider not in ('openai','anthropic','chatgpt'):
        raise NativeValueError('first_agent_fields', 'Введи имя агента и модель, выбери подключение')
    if provider!='chatgpt' and not key:
        # An unauthenticated OpenAI-compatible server on this machine or the local network
        # (llama.cpp, Ollama, LM Studio) needs no key. Store the same placeholder the
        # settings page uses for its local provider; public endpoints still require a key.
        if provider=='openai' and keyless_local_endpoint(values.get('base_url')):key='local'
        else:raise NativeValueError('first_api_key', 'Введи ключ доступа к API')
    previous=load(root/'helene.json');config=creation_config(previous,values)
    # Explicit birth choice lives with the profile, even while subscription
    # sign-in is pending and before the first runner has created soul/SOUL.md.
    # Legacy callers that omit this field retain their existing canonical path.
    seed=None
    if 'soul' in values:
        soul=values['soul']
        if not isinstance(soul,dict) or soul.get('kind') not in ('canonical','text'):
            raise NativeValueError('first_soul_choice', 'Выбери конституцию Hélène или свой текст')
        from .agents import _soul_seed_text
        seed=_soul_seed_text(root,soul,None)
        if seed is None:
            from .boot import soul_text
            seed=soul_text(config)
    from . import boot,first_birth
    if 'soul' not in values:seed=boot.initial_soul_seed(root/'helene.json',places.data)
    choice=first_birth.intent(config,seed)
    ticket=first_birth.admit(root,places.data,choice,
        configured=previous.get('setup_complete') is True,has_agent=agent_exists(places.data))
    if provider=='chatgpt':
        import secrets
        from deskd.readers import RELAY_PORT_DEFAULT
        from .standalone_relay import ensure_configured
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',RELAY_PORT_DEFAULT))
            except OSError:sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        key='sk-frame-'+secrets.token_hex(24)
        config['relay']=dict(config.get('relay') or {},enabled=True,port=port,key=key)
        config['model']={'framework':'openai','model':model,'key':key,'base_url':f'http://127.0.0.1:{port}'}
        config['onboarding']=dict(config.get('onboarding') or {},subscription_login=True)
        # Installation failure leaves the creation form retryable. No agent
        # identity or completed setup is published until the component exists.
        ensure_configured(root,config,progress)
        from .owner_stop import change_pause
        change_pause(places.data,True,reason='Войди в ChatGPT и нажми «Продолжить»',via='first-start')
    else:
        config['relay']=dict(config.get('relay') or {},enabled=False)
        config['model']={'framework':provider,'model':model,'key':key,
            'base_url':str(values.get('base_url') or ('https://api.anthropic.com' if provider=='anthropic' else 'https://api.openai.com/v1')).strip()}
    if seed is not None:
        if 'soul' in values:
            from .vision_policy import _write as atomic
            atomic(plain(places.data/'memory/.state/soul-seed.md'),seed.encode('utf-8'),private=True)
    selected=boot.initial_soul_seed(root/'helene.json',places.data)
    birth=first_birth.materialize(root,places.data,config,choice,ticket,selected)
    _write(root/'helene.json',config,private=True)
    _write(places.data/'memory/.control/profile-identity.json',{'format':'helene-profile-identity.v1',
        'id':uuid.uuid4().hex,'agent_name':name,'owner_name':owner})
    _write(places.data/'memory/.state/helene-profile.json',
           {key:value for key,value in config.items() if key not in ('tree','code','python','app','runner','port','installed')},private=True)
    first_birth.published(root,birth)
    (root/'portable-program.json').unlink(missing_ok=True)
    return {'created':True,'name':name}
