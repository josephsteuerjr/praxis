"""Retain common Git objects and rebind a transported worktree independently.

Snapshotting a .git pointer alone loses its history. Planning adds the actual
common/admin directories to the reviewed payload; rebinding only changes the
new staged copy. Original repositories and the original archive stay intact.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

from .carry_scope import inventory
from .private_files import write_private


def standalone(repository, destination, *, git=None):
    """Copy Git metadata into a portable .git, without modifying the source repo.

    Used by the legacy forward archive as well as full payloads: a .git file
    alone cannot survive the move away from its external common object store.
    """
    repository = Path(repository).resolve(strict=True)
    pointer = repository / '.git'
    if pointer.is_symlink():
        raise ValueError('Метаданные Git являются ссылкой: ' + str(pointer))
    admin = pointer
    if pointer.is_file():
        if pointer.stat().st_size > 4096:
            raise ValueError('Слишком длинный указатель Git: ' + str(pointer))
        raw = pointer.read_text(encoding='utf-8').strip()
        if not raw.startswith('gitdir: '):
            raise ValueError('Неверный указатель Git: ' + str(pointer))
        admin = Path(raw[8:])
        admin = (admin if admin.is_absolute() else repository / admin).resolve(strict=True)
    common = admin
    if (admin / 'commondir').is_file():
        raw = Path((admin / 'commondir').read_text(encoding='utf-8').strip())
        common = (raw if raw.is_absolute() else admin / raw).resolve(strict=True)
    for path in set((common, admin)):
        checked = inventory(path)
        if checked['errors'] or checked['special'] or checked['links']:
            raise ValueError('Не удалось целиком перенести метаданные Git: ' + str(path))
    if (common / 'objects/info/alternates').exists():
        raise ValueError('Git использует внешнее хранилище objects/alternates: ' + str(common))
    git = git or shutil.which('git')
    if not git:
        raise ValueError('Для проверки переносимой истории нужен Git.')
    destination = Path(destination)
    shutil.copytree(common, destination, ignore=lambda folder, names:
                    ['worktrees'] if Path(folder) == common and 'worktrees' in names else [])
    if admin != common:
        for item in admin.iterdir():
            if item.name in ('commondir', 'gitdir'):
                continue
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)

    def command(*args):
        result = subprocess.run([git, *args], cwd=repository, env=environment,
                                capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError('Git не подтвердил переносимую историю: ' + str(repository))

    for name in ('config', 'config.worktree'):
        if name == 'config' or (destination / name).exists():
            command('config', '--file', str(destination / name), 'core.bare', 'false')
            command('config', '--file', str(destination / name), 'core.worktree', '..')
    command('--git-dir=' + str(destination), '--work-tree=' + str(repository), 'fsck', '--connectivity-only')
    return destination


def plan(raw_roots):
    roots={name:Path(path).resolve(strict=True) for name,path in raw_roots.items()};records=[]
    def reference(path):
        path=path.resolve(strict=True)
        for name,root in sorted(roots.items(),key=lambda item:len(item[1].parts),reverse=True):
            if path.is_relative_to(root):return {'root':name,'path':path.relative_to(root).as_posix()}
        name='git_'+hashlib.sha256(str(path).encode()).hexdigest()[:20]
        roots[name]=path;return {'root':name,'path':'.'}
    for name,root in list(roots.items()):
        scanned=inventory(root)
        if scanned['errors']:raise ValueError('Не удалось проверить Git и файлы выбранной папки: '+str(root))
        for repo in scanned['git']:
            gitdir=root/repo['path'] if repo['kind']=='directory' else Path(repo['gitdir'])
            common=gitdir if repo['kind']=='directory' else Path(repo['common'])
            if (common/'objects/info/alternates').exists():
                raise ValueError('Git использует внешнее хранилище objects/alternates. Добавь его сопоставление в план до переноса: '+str(common))
            if repo['kind']=='directory':continue
            for path in (common,gitdir):
                checked=inventory(path)
                if checked['errors'] or checked['special'] or checked['links']:
                    raise ValueError('Метаданные Git содержат недоступные файлы или ссылки; перенос требует проверки: '+str(path))
            records.append({'root':name,'path':str(Path(repo['path']).parent.as_posix()),
                            'common':reference(common),'admin':reference(gitdir)})
    return {'roots':{name:str(path) for name,path in roots.items()},'worktrees':records}


def rebind(payload_home,records,*,git=None):
    """Turn each staged worktree into an independent repository, resumably."""
    home=Path(payload_home).resolve(strict=True);git=git or shutil.which('git')
    if records and not git:raise ValueError('Для проверки перенесённой истории нужен Git.')
    ready=json.loads((home/'.payload-ready.json').read_text(encoding='utf8'))
    if ready.get('activated'):raise ValueError('Активную копию нельзя переподготавливать.')
    roots={name:Path(path).resolve(strict=True) for name,path in ready['roots'].items()}
    def resolve(row):
        if row.get('root') not in roots:raise ValueError('Неизвестный раздел Git.')
        path=(roots[row['root']]/row['path']).resolve(strict=True)
        if not path.is_relative_to(home):raise ValueError('Путь Git выходит за пределы новой копии.')
        return path
    def command(*args):
        environment={key:value for key,value in os.environ.items() if not key.upper().startswith('GIT_')}
        environment.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull)
        result=subprocess.run([git,*args],cwd=home,env=environment,capture_output=True,timeout=120)
        if result.returncode:raise ValueError('Git не подтвердил целостность новой копии; исходные данные сохранены.')
    completed=[]
    for row in records:
        repository=resolve(row);common=resolve(row['common']);admin=resolve(row['admin'])
        identity=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()[:24]
        journal=home/'.git-rebind'/identity;journal.mkdir(parents=True,exist_ok=True,mode=0o700)
        status=journal/'state.json';state=json.loads(status.read_text()) if status.exists() else {}
        target=repository/'.git';temporary=repository/('.git-portable-'+identity)
        if not state:
            if not target.is_file() or target.is_symlink():raise ValueError('Ожидался сохранённый указатель Git-worktree.')
            state={'row':row,'phase':'copying'};write_private(status,json.dumps(state).encode())
        if state.get('row')!=row:raise ValueError('План Git изменился после начала подготовки.')
        if state['phase']=='copying':
            if temporary.exists():temporary.rename(journal/('interrupted-'+secrets.token_hex(8)))
            # Historical registrations are preserved in the captured common root,
            # but are not active worktrees of this newly independent repository.
            shutil.copytree(common,temporary,ignore=lambda directory,names:['worktrees'] if Path(directory)==common and 'worktrees' in names else [])
            for item in admin.iterdir():
                if item.name in ('commondir','gitdir'):continue
                to=temporary/item.name
                if item.is_dir():shutil.copytree(item,to,dirs_exist_ok=True)
                else:shutil.copy2(item,to)
            command('config','--file',str(temporary/'config'),'core.bare','false')
            command('config','--file',str(temporary/'config'),'core.worktree','..')
            if (temporary/'config.worktree').exists():
                command('config','--file',str(temporary/'config.worktree'),'core.worktree','..')
                command('config','--file',str(temporary/'config.worktree'),'core.bare','false')
            command('--git-dir='+str(temporary),'--work-tree='+str(repository),'fsck','--connectivity-only')
            state['phase']='committing';write_private(status,json.dumps(state).encode())
        if state['phase']=='committing':
            backup=journal/'original-git-pointer'
            if target.is_file():
                if backup.exists():raise ValueError('Конфликт сохранённых указателей Git.')
                target.rename(backup)
            if temporary.exists():
                if target.exists():raise ValueError('Конфликт подготовленных каталогов Git.')
                temporary.rename(target)
            if not target.is_dir():raise ValueError('Не найдены подготовленные метаданные Git.')
            state['phase']='done';write_private(status,json.dumps(state).encode())
        completed.append({'root':row['root'],'path':row['path'],'independent':True})
    return {'worktrees':completed,'activated':False}
