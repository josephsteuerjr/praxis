"""Update preserved agent source against its recorded shipped hashes.

The old selected tree, Git HEAD and index are never edited. Ambiguous overlapping
edits stop the update with both candidates retained, rather than silently running
the whole old tree under a new application version.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import shutil


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def baseline(program):
    result={}
    # snapshot_files describes the historical donor, not the derived shipped
    # edition. Only the final files map is a valid old-program baseline.
    for name,key in (('edition-inventory.json','files'),('selected-source.json','files')):
        path=program/name
        if path.is_file():
            for relative,value in json.loads(path.read_text(encoding='utf-8')).get(key,{}).items():
                if isinstance(value,dict) and isinstance(value.get('sha256'),str):result[relative]=value['sha256']
    path=program/'python-package-files.json'
    if path.is_file():
        for relative,value in json.loads(path.read_text(encoding='utf-8')).get('files',{}).items():
            if relative.startswith('tree/'):result[relative[5:]]=value
    path=program/'shipped-code.json'
    if path.is_file():result.update(json.loads(path.read_text(encoding='utf-8'))['files'])
    return result


def prepare(old,new,note):
    from .essential_backup import configs,load
    from .app_migration import preserve_source
    stock=new/'tree'
    if not stock.is_dir():return
    old_hashes=baseline(old)
    incoming={p.relative_to(stock).as_posix():digest(p) for p in stock.rglob('*') if p.is_file() and not p.is_symlink() and '.git' not in p.relative_to(stock).parts and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    rows=[];conflicts=[];selected={}
    for source in note['source_paths']:
        original=Path(source['old']).resolve()
        target=Path(source['selected']).resolve()
        if original in selected:
            source['selected']=str(selected[original]);continue
        if target==original:
            target=Path(note['data_home'])/note['id']/('code-'+str(len(selected)))
            preserve_source(original,target)
            source['selected']=str(target)
        selected[original]=target
        for relative in sorted(set(old_hashes)|set(incoming)):
            path=PurePosixPath(relative)
            if path.is_absolute() or '..' in path.parts or '\\' in relative or ':' in relative:
                raise ValueError('Invalid shipped code path')
            current=target/relative
            if not current.resolve().is_relative_to(target) or current.is_symlink():
                raise ValueError('Code update path leaves preserved source: '+relative)
            before=digest(current) if current.is_file() else None
            shipped=old_hashes.get(relative)
            after=incoming.get(relative)
            if before==after:continue
            if before==shipped:
                if after is None:current.unlink(missing_ok=True)
                else:
                    current.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(stock/relative,current)
                rows.append({'code':str(target),'path':relative,'action':'updated' if after else 'removed_stock'})
            elif after==shipped:
                rows.append({'code':str(target),'path':relative,'action':'preserved_authored'})
            else:
                conflicts.append({'code':str(target),'path':relative,'old_shipped_sha256':shipped,
                    'owner_sha256':before,'incoming_sha256':after,
                    'owner_file':str(current),'incoming_file':str(stock/relative) if after else None})
    # Every config follows its updated independent tree; duplicate agents can
    # share a source, as they did before migration.
    for config in configs(new):
        if not config.is_file():continue
        value=load(config)
        prior=Path(value.get('code','')).resolve()
        for source in note['source_paths']:
            if prior==Path(source['old']).resolve():value['code']=source['selected']
        config.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'format':'helene-code-transition.v1','migration_id':note['id'],'changes':rows,'conflicts':conflicts}
    path=new/'code-transition.json';path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    if conflicts:
        raise RuntimeError('Agent code conflicts; previous program retained. Compare preserved owner and incoming files in '+str(path))
    note['code_transition']=str(old/'code-transition.json')
