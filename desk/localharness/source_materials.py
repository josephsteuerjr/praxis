"""Retire exact disposable update payloads after a confirmed successful startup."""
from pathlib import Path
import hashlib,os,re,time
from .package_notice import read,write
from .program_state import _plain,hidden
from . import source_merge as merge

FORMAT='helene-source-materials.v1'
PAYLOADS=('base','local','incoming','result','repository.git','compound','verification-dependencies')


def _hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _inventory(root):
    root=_plain(root);files={}
    for here,dirs,names in os.walk(root,followlinks=False):
        for name in [*dirs,*names]:
            path=_plain(Path(here)/name)
            if path.is_file():files[path.relative_to(root).as_posix()]={'sha256':_hash(path),'bytes':path.stat().st_size}
    return files


def _scope(directory,note):
    directory=_plain(directory)
    bases=[Path(p).resolve()/'memory/.source-updates' for p in note.get('data_paths',[])+note.get('old_data_paths',[])]
    bases.append(Path(note['old_root']).resolve()/'.source-updates')
    if directory.parent not in bases or not re.fullmatch('[0-9a-f]{64}',directory.name):
        raise ValueError('Update material directory leaves its selected data')
    return directory


def _review_scope(path,item):
    path=_plain(path)
    stores=[Path(item['tree']).resolve()/'memory'/name for name in ('.source-update-reviews','.self-review')]
    if path.name!='request.json' or path.parent.parent not in stores or path.parent.name in ('.','..'):
        raise ValueError('Review material leaves its source-update store')
    return path


def _root(path):
    path=_plain(path);info=path.stat()
    files=_inventory(path)
    directories=[str(Path(here).relative_to(path).as_posix()) for here,_,_ in os.walk(path,followlinks=False)
                 if Path(here)!=path]
    return {'path':str(path),'identity':[info.st_dev,info.st_ino],'files':files,'directories':directories}


def _remaining(path):
    entries=[]
    for here,dirs,names in os.walk(path,followlinks=False):
        for name in [*dirs,*names]:
            entry=Path(here)/name
            if entry==path/'subject.json':continue
            entries.append(entry.relative_to(path).as_posix())
            try:_plain(entry)
            except ValueError:
                if name in dirs:dirs.remove(name)
    return entries


def capture(program,note):
    """Record hashes of generated payloads; no additional code copy is made."""
    program=Path(program).resolve();plans=[]
    for row in read(program/'code-transition-plan.json').get('plans',[]):
        directory=_scope(row['directory'],note);plan=merge.validate_inputs(directory)
        if plan.get('state')!='ready' or merge.inventory(directory/'result')!=plan.get('result'):
            raise ValueError('Only the accepted source candidate may plan material retirement')
        operation=read(directory/'pipeline.json')
        roots=[_root(directory/name) for name in PAYLOADS if (directory/name).is_dir()]
        reviews=[]
        if operation:
            tree=Path(operation['tree']).resolve()
            for store in ('.source-update-reviews','.self-review'):
                for record_path in (tree/'memory'/store).glob('*/request.json'):
                    record=read(record_path)
                    prefix='source-update:'+operation['id']+':'
                    if not any(str(op).startswith(prefix) for op in record.get('operations',[])):continue
                    snapshot=_plain(record_path.parent/'subject')
                    if str(snapshot)!=record.get('snapshot'):raise ValueError('Review subject path differs')
                    terminal={'done','error','cancelled'}
                    if record.get('execution') not in terminal or any(c.get('execution') not in terminal for c in record.get('critics',[])):
                        raise ValueError('Review producers must settle before material retirement is planned')
                    from .self_review import validate_snapshot
                    validate_snapshot(snapshot,record['subject_sha256'])
                    subject=_root(snapshot)
                    subject['files'].pop('subject.json',None)  # retain the exact signed inventory
                    roots.append(subject);reviews.append(str(record_path))
        tree=operation.get('tree') or next((p for p in note['data_paths']
            if directory.parent==Path(p).resolve()/'memory/.source-updates'),note['data_paths'][0])
        plans.append({'directory':str(directory),'tree':str(tree),'input_sha256':merge.input_signature(plan),
                      'candidate_sha256':merge.signature(plan['result']),'operation':operation.get('id'),
                      'roots':roots,'reviews':reviews})
    value={'format':FORMAT,'id':note['id'],'target':note['new_root'],'plans':plans}
    path=program/'source-materials.json';write(path,value);hidden(path)
    return value


def _remap(path,note):
    transfer=note.get('data_relocation')
    if transfer:
        from .data_relocation import remap
        if transfer.get('format')!='helene-data-relocation.v1' or transfer.get('state') not in ('verified','retired','source_retained'):
            raise ValueError('Source material transfer is not verified')
        return Path(remap(path,transfer)).resolve()
    return Path(path).resolve()


def ensure_committed_materials(program,incoming,note):
    """Upgrade old live-preparer metadata only after this new startup succeeds.

    Earlier controllers prepare with their loaded modules, even though the
    incoming helper performs the switch. Their exact source choice and frozen
    plan remain authoritative; no old policy default is guessed or rewritten.
    """
    program=Path(program).resolve()
    if (not note.get('startup_proof') or note.get('state')!='completed'
            or Path(note['new_root']).resolve()!=program):
        raise ValueError('Legacy material preparation needs committed startup')
    from .update_source import selected
    choice=selected(program,incoming)
    if not choice or note.get('source_policy') not in (None,choice['mode']):
        raise ValueError('Committed source policy differs from its admitted choice')
    note['source_policy']=choice['mode'];note['source_choice']=choice
    manifest=read(program/'source-materials.json')
    if manifest.get('id')!=note['id'] or manifest.get('format')!=FORMAT or Path(manifest.get('target') or '.').resolve()!=program:
        capture(program,note)
        note['material_preparation']='incoming-after-confirmed-startup'


def commit(program,note):
    """Close exact source requests on the installed data, including relocation."""
    from . import source_update
    program=Path(program).resolve();value=read(program/'source-materials.json')
    if not value:return []  # older admitted operations retain their recovery
    if (value.get('format')!=FORMAT or value.get('id')!=note['id'] or Path(value['target']).resolve()!=program
            or not note.get('startup_proof') or note.get('state') not in ('new_app_started','completed','retirement_incomplete')):
        raise ValueError('Material retirement requires this exact confirmed startup')
    applied=[]
    for item in value['plans']:
        _scope(item['directory'],note)
        if Path(item['tree']).resolve() not in [Path(p).resolve() for p in note.get('data_paths',[])+note.get('old_data_paths',[])]:
            raise ValueError('Source material tree differs from the selected data')
        directory=_plain(_remap(item['directory'],note));tree=_remap(item['tree'],note)
        plan=read(directory/'plan.json')
        if merge.input_signature(plan)!=item['input_sha256'] or merge.signature(plan['result'])!=item['candidate_sha256']:
            raise ValueError('Accepted source material identity changed')
        operation=read(directory/'pipeline.json')
        if not operation:continue
        if operation.get('id')!=item['operation'] or operation.get('state') not in ('ready_to_apply','applied','cancelled'):
            raise ValueError('Source update did not reach its accepted candidate')
        receipt={'id':note['id'],'program':str(program),'tree':str(tree),
                 'input_sha256':item['input_sha256'],'candidate_sha256':item['candidate_sha256']}
        previous=operation.get('installed_receipt')
        if previous:
            if any(previous.get(k)!=v for k,v in receipt.items()):raise ValueError('Source job already has another installation receipt')
            receipt=previous
        else:receipt['at']=time.time()
        installed_state='cancelled' if operation.get('state')=='cancelled' else 'applied'
        operation.update(state=installed_state,installed_program=str(program),installed_receipt=receipt,applied_at=receipt['at'],startup_receipt=note['id'])
        source_update._persist(directory,operation)
        # Preserve old input/path evidence; only the current mechanical index
        # moves to the newly selected data. Historical conversation is untouched.
        current=read(source_update.path(tree))
        selected=current if current.get('id')==operation['id'] else read(source_update._requests(tree)/(operation['id']+'.json'))
        if selected.get('id')==operation['id']:
            selected.update(state=installed_state,tree=str(tree),directory=str(directory),installed_program=str(program),
                           installed_receipt=receipt,startup_receipt=note['id'],applied_at=receipt['at'])
            source_update._save_request(tree,selected)
        applied.append(operation['id'])
    note['applied_source_jobs']=applied
    return applied


def retire(program,note):
    """Delete only captured unchanged leaves, preserving unknown/later material."""
    from .update_io import remove_created_file
    program=Path(program).resolve();value=read(program/'source-materials.json')
    if not value:return {'state':'none'}
    commit(program,note)
    previous=read(program/'source-materials-retired.json')
    if previous.get('id')==note['id'] and previous.get('state')=='retired':return previous
    result={'id':note['id'],'removed_files':0,'removed_bytes':0,'retained':[]}
    for item in value['plans']:
        for root in item['roots']:
            original=_plain(root['path']);path=_plain(_remap(original,note))
            # Captured roots have only these two forms; editable metadata cannot
            # nominate an arbitrary folder of owner data for cleanup.
            directory=Path(item['directory'])
            review_paths=[_review_scope(p,item).parent/'subject' for p in item['reviews']]
            if original not in [directory/name for name in PAYLOADS] and original not in review_paths:
                raise ValueError('Unknown source payload root')
            if not path.exists():continue
            if not path.is_dir():
                result['retained'].append({'path':str(path),'reason':'payload directory replaced'});continue
            info=path.stat()
            if path==original and [info.st_dev,info.st_ino]!=root['identity']:
                result['retained'].append({'path':str(path),'reason':'directory identity changed'});continue
            for name,proof in root['files'].items():
                merge.relative(name);file=_plain(path/name)
                if not file.exists():continue
                if not file.is_file() or _hash(file)!=proof['sha256']:
                    result['retained'].append({'path':str(file),'reason':'changed material'});continue
                try:remove_created_file(file)
                except OSError as exc:result['retained'].append({'path':str(file),'reason':str(exc)})
                else:result['removed_files']+=1;result['removed_bytes']+=proof['bytes']
            # No recursive delete: unexpected files keep their directory alive.
            for name in sorted(root.get('directories',[]),key=lambda p:len(Path(p).parts),reverse=True):
                merge.relative(name)
                try:_plain(path/name).rmdir()
                except (OSError,ValueError):pass
            try:path.rmdir()
            except OSError:pass
            left=_remaining(path) if path.exists() else []
            if left:result['retained'].append({'path':str(path),'reason':'uncaptured or changed files','files':left})
        for record_path in item['reviews']:
            _review_scope(record_path,item)
            record_path=_remap(record_path,note);record=read(record_path)
            if record.get('snapshot'):record['snapshot']=str(_remap(record['snapshot'],note))
            record['material_retirement']=note['id'];write(record_path,record)
    result['state']='retained-material' if result['retained'] else 'retired'
    path=program/'source-materials-retired.json';write(path,result);hidden(path)
    return result
