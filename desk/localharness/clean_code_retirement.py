"""Move old in-program Git aside for cutover, restore on failure, retire on commit."""
from pathlib import Path
import os,shutil,stat,uuid
from .package_notice import read,write
from .program_state import _plain
from .source_merge import relative
from .update_io import remove_created_file


def _paths(program,recovery):
    program,recovery=_plain(program),_plain(recovery)
    return program/'tree/.git',recovery/'retired-code-git',recovery/'code-git.json'


def git_signature(path):
    """Bind the old Git bytes without making another repository copy."""
    import hashlib,json
    path=_plain(path)
    if not path.exists():return None
    if path.is_file():return hashlib.sha256(path.read_bytes()).hexdigest()
    files={}
    for here,dirs,names in os.walk(path,followlinks=False):
        for name in [*dirs,*names]:
            item=_plain(Path(here)/name)
            if item.is_file():files[item.relative_to(path).as_posix()]=hashlib.sha256(item.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()


def prepare(program,recovery,data_paths):
    target,saved,journal=_paths(program,recovery)
    if not target.exists():return
    if any(target.is_relative_to(Path(root).resolve()) for root in data_paths):
        raise ValueError('The selected data contains the code Git directory; separate data before clean recovery')
    _plain(target)
    if saved.exists():raise ValueError('A previous Git cutover needs recovery before another switch')
    info=target.stat()
    write(journal,{'format':'helene-clean-code-git.v1','program':str(Path(program).resolve()),
                   'identity':[info.st_dev,info.st_ino],'state':'planned'})
    target.rename(saved)


def restore(program,recovery):
    target,saved,journal=_paths(program,recovery);record=read(journal)
    if not record:return
    if record.get('format')!='helene-clean-code-git.v1' or record.get('program')!=str(Path(program).resolve()):
        raise ValueError('Git cutover recovery identity differs')
    if not saved.exists():return
    _plain(saved);info=saved.stat()
    if [info.st_dev,info.st_ino]!=record['identity']:raise ValueError('Saved Git generation changed')
    if target.exists():
        _plain(target)
        later=Path(recovery)/'later-self-edits'/('new-git-'+uuid.uuid4().hex)
        later.parent.mkdir(exist_ok=True);target.rename(later)
    saved.rename(target)
    write(journal,dict(record,state='restored'))


def retire(program,recovery):
    _,saved,journal=_paths(program,recovery);record=read(journal)
    if not record:return {'state':'none'}
    if record.get('format')!='helene-clean-code-git.v1' or record.get('program')!=str(Path(program).resolve()):
        raise ValueError('Git retirement identity differs')
    if not saved.exists():return {'state':'absent'}
    _plain(saved);info=saved.stat()
    if [info.st_dev,info.st_ino]!=record['identity']:raise ValueError('Saved Git generation changed')
    def readonly(function,path,failure):
        path=Path(path);info=path.lstat()
        if (os.name!='nt' or function is not os.unlink or not isinstance(failure[1],PermissionError)
                or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&stat.S_IWRITE):raise failure[1]
        path.chmod(info.st_mode|stat.S_IWRITE);function(path)
    try:
        if saved.is_dir():shutil.rmtree(saved,onerror=readonly)
        else:saved.unlink()
    except OSError as exc:
        result={'state':'retained','error':str(exc)}
    else:result={'state':'retired'}
    write(journal,dict(record,**result));return result


def retire_selected_sources(program,recovery,note):
    """Discard unchanged selected internal code only after confirmed startup.

    Originals stay available for rollback until commit. No old source copy is
    created. External code, selected data and unknown/changed files stay put.
    """
    import hashlib
    program,recovery=_plain(program),_plain(recovery)
    if note.get('source_policy') not in ('clean','preserve') or note.get('state')!='completed':
        raise ValueError('Selected code retirement requires completed startup')
    if note.get('source_policy')=='preserve' and not note.get('startup_proof'):
        raise ValueError('Preserved code retirement needs confirmed startup')
    data=[_plain(p) for p in note.get('data_paths',[])]
    result={'removed_files':0,'retained':[],'git':[]}
    transition=read(program/'code-transition.json')
    rows=transition.get('sources',[]) if note.get('source_policy')=='clean' else transition.get('merges',[])
    for row in rows:
        source=_plain(row['source'])
        if not source.is_relative_to(program/'code') or not source.is_dir():continue
        if any(source.is_relative_to(p) or p.is_relative_to(source) for p in data):
            result['retained'].append({'source':str(source),'reason':'selected data overlaps source'})
            continue
        if note.get('source_policy')=='preserve':
            from .app_migration import discover
            selected=[Path(a['code']).resolve() for a in discover(program)['agents'] if a['value'].get('mode')!='remote']
            if any(p==source or p.is_relative_to(source) for p in selected):
                result['retained'].append({'source':str(source),'reason':'source is still selected'});continue
            from .source_merge import inventory
            if (inventory(source)!=row.get('previous_files') or
                    git_signature(source/'.git')!=row.get('git_signature')):
                result['retained'].append({'source':str(source),'reason':'source or Git changed after preparation'});continue
        parents=set()
        for name,proof in row.get('previous_files',{}).items():
            relative(name);path=_plain(source/name)
            if not path.is_file():continue
            if hashlib.sha256(path.read_bytes()).hexdigest()!=proof['sha256']:
                result['retained'].append({'path':str(path),'reason':'changed after clean admission'});continue
            try:
                if os.name=='nt' and not path.stat().st_mode&stat.S_IWRITE and path.stat().st_nlink!=1:
                    raise PermissionError('Read-only shared code retained')
                remove_created_file(path)
            except OSError as exc:result['retained'].append({'path':str(path),'reason':str(exc)})
            else:
                result['removed_files']+=1
                parents.update(p for p in path.parents if p==program/'code' or p.is_relative_to(program/'code'))
        git=source/'.git'
        if git.exists():
            _plain(git)
            token=hashlib.sha256(str(source).encode('utf8')).hexdigest()[:20]
            saved=recovery/('retired-selected-git-'+token)
            if saved.exists():raise ValueError('Earlier selected-code Git retirement is unresolved')
            git.rename(saved)  # same-volume move; never copy history
            try:
                if saved.is_dir():
                    def readonly(function,path,failure):
                        item=Path(path);info=item.lstat()
                        if (os.name!='nt' or function is not os.unlink or not isinstance(failure[1],PermissionError)
                                or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&stat.S_IWRITE):raise failure[1]
                        item.chmod(info.st_mode|stat.S_IWRITE);function(item)
                    shutil.rmtree(saved,onerror=readonly)
                else:remove_created_file(saved)
            except OSError as exc:result['git'].append({'source':str(source),'state':'retained','error':str(exc)})
            else:result['git'].append({'source':str(source),'state':'retired'})
            parents.add(source)
        for parent in sorted(parents,key=lambda p:len(p.parts),reverse=True):
            try:parent.rmdir()  # only empty known programme/code ancestors
            except OSError:pass
    write(recovery/'selected-code-retirement.json',result)
    return result
