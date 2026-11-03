"""Explicit code-only update policy, bound to one selected delivery and target."""
from pathlib import Path
import time
from .package_notice import read,write
from . import source_merge as merge

MODES=('preserve','clean')

class SourceChoiceCancelled(RuntimeError):pass

def choice_path(new):
    new=Path(new).resolve();current=new/'.helene/source-choice.json'
    legacy=new.parent/'source-choice.json'
    previous=read(legacy) if not current.exists() else {}
    if previous.get('format')=='helene-source-choice.v1' and previous.get('new')==str(new):
        return legacy  # preserve the exact authority of an already admitted job
    return current

def identity(old,new):
    old,new=Path(old).resolve(),Path(new).resolve()
    upstream=read(new/'code-upstream.json')
    if not upstream.get('files') or not upstream.get('sha256'):
        raise ValueError('Verified incoming source snapshot is required')
    return {'old':str(old),'new':str(new),'version':read(new/'python-app.json').get('version'),
            'upstream_sha256':upstream['sha256'],'files_sha256':merge.signature(upstream['files'])}

def select(old,new,mode,*,by,require_review=False,resolution_owner='agent',resolution_runtime=None):
    if mode not in MODES:raise ValueError('Choose preserve or clean source policy')
    from .authored_baseline import mode as eligible
    eligible(old,mode)
    if by not in ('window','owner-words','updater'):raise ValueError('Source choice requires an explicit owner action')
    if resolution_owner not in ('agent','incoming'):raise ValueError('Unknown source-resolution owner')
    from .app_migration import discover
    local_sources={str(Path(a['code']).resolve()):merge.inventory(a['code']) for a in discover(Path(old))['agents'] if a['value'].get('mode')!='remote'}
    value={'format':'helene-source-choice.v1',**identity(old,new),'mode':mode,'by':by,'at':time.time(),'local_sources':local_sources}
    if require_review:value['require_review']=True
    if resolution_owner=='incoming':value['resolution_owner']='incoming'
    if resolution_runtime is not None:value['resolution_runtime']=str(Path(resolution_runtime).resolve())
    previous=read(choice_path(new))
    if (previous and previous.get('mode')=='preserve' and mode=='clean'
            and read(Path(new)/'source-resolution-unavailable.json')==identity(old,new)):
        # No source writer is admitted before the capability check. Only an
        # explicit second owner choice may take the clean-code path.
        import uuid
        write(choice_path(new).with_name('source-choice-preserve-'+uuid.uuid4().hex+'.json'),previous)
        previous=None
    if previous and previous!=value:
        if {k:previous.get(k) for k in identity(old,new)}!=identity(old,new):raise ValueError('Update source choice belongs to another delivery')
        if previous['mode']!=mode:raise ValueError('Source policy is already selected for this operation; start a new update')
        if bool(previous.get('require_review'))!=bool(require_review):raise ValueError('Review policy is already bound to this operation; start a new update')
        if previous.get('resolution_owner','agent')!=resolution_owner:raise ValueError('Source-resolution owner is already bound; start a new update')
        if previous.get('resolution_runtime')!=value.get('resolution_runtime'):raise ValueError('Resolution runtime is already bound to this operation')
        return previous
    target=choice_path(new)
    if target.parent==Path(new).resolve()/'.helene':
        from .program_state import root
        root(new)
    write(target,value);return value

def selected(old,new):
    value=read(choice_path(new))
    if not value:return None
    expected=identity(old,new)
    if value.get('format')!='helene-source-choice.v1' or any(value.get(k)!=v for k,v in expected.items()) or value.get('mode') not in MODES:
        raise ValueError('Update source choice is stale or changed')
    from .authored_baseline import mode as eligible
    eligible(old,value['mode'])
    return value

def require(old,new,progress=None):
    value=selected(old,new)
    from .authored_baseline import mode as eligible
    if value:
        eligible(old,value['mode'])
        return value
    # Normal Python updates retain authored source. Legacy native migration
    # stays clean; explicit choices already bound above are never rewritten.
    mode=eligible(old)
    return select(old,new,mode,by='updater',require_review=mode=='preserve',
                  resolution_owner='incoming' if mode=='preserve' else 'agent')

def clean_prepare(old,new,note,choice):
    """Select the verified shipped tree directly; no source/Git duplicates."""
    from .essential_backup import configs,load
    old,new=Path(old).resolve(),Path(new).resolve()
    if selected(old,new)!=choice or choice['mode']!='clean':raise ValueError('Clean source choice changed')
    from .app_migration import discover
    current={str(Path(a['code']).resolve()):merge.inventory(a['code']) for a in discover(old)['agents'] if a['value'].get('mode')!='remote'}
    if current!=choice.get('local_sources'):raise ValueError('Local code changed after clean update consent')
    upstream=read(new/'code-upstream.json');actual=merge.inventory(new/'tree')
    if actual!=upstream['files']:raise ValueError('Clean update requires exact pristine incoming source')
    # Verify archive bytes and every source member independently before copying.
    import hashlib,zipfile
    archive=new/'code-upstream.zip'
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=upstream['sha256']:
            raise ValueError('Pristine source archive differs')
    with zipfile.ZipFile(archive) as reader:
        names=reader.namelist()
        if len(names)!=len(set(names)) or set(names)!=set(actual):raise ValueError('Pristine source archive inventory differs')
        for name in names:
            if not merge.allowed(name):raise ValueError('Non-code member in pristine source archive')
            with reader.open(name) as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=actual[name]['sha256']:
                    raise ValueError('Pristine source archive member differs: '+name)
    targets={};receipts=[]
    for row in note['source_paths']:
        original=Path(row['old']).resolve()
        if original not in targets:
            target=new/'tree'
            targets[original]=target
            receipts.append({'source':str(original),'selected':str(target),'previous_files':merge.inventory(original),
                             'incoming_files':actual,'upstream_sha256':upstream['sha256'],'layout':'shipped-tree'})
        row['selected']=str(targets[original])
    for config in configs(new):
        if not config.is_file():continue
        value=load(config);previous=Path(value.get('code','')).resolve()
        if previous in targets:
            value.update(code=str(targets[previous]))
            # Clean recovery runs only incoming distribution entry points.
            value.update(app=str(new/'app/deskapp.py'),runner=str(new/'app/localharness/runner.py'))
            value['installed']=dict(value.get('installed') or {},service=False)
            write(config,value)
    note['source_policy']='clean';note['source_choice']=choice;note['source_layout']='shipped-tree'
    write(new/'code-transition-plan.json',{'format':'helene-clean-code-plan.v1','old':str(old),
        'incoming':actual,'choice':choice,'sources':receipts})
    write(new/'code-transition.json',{'format':'helene-code-transition.v3','source_policy':'clean','sources':receipts})

def validate_clean(new,record):
    new=Path(new).resolve();old=Path(record['old']).resolve()
    choice=selected(old,new)
    if not choice or choice['mode']!='clean' or choice!=record.get('choice'):raise ValueError('Clean update lacks its explicit source choice')
    if merge.inventory(new/'tree')!=record['incoming'] or read(new/'code-upstream.json').get('files')!=record['incoming']:
        raise ValueError('Pristine incoming source changed')
    from .app_migration import discover
    sources={str(Path(a['code']).resolve()) for a in discover(old)['agents'] if a['value'].get('mode')!='remote'}
    if sources!={r['source'] for r in record['sources']}:raise ValueError('Selected local sources changed before clean update')
    for row in record['sources']:
        if merge.inventory(row['source'])!=row['previous_files']:raise ValueError('Local code changed after clean update choice')
        target=Path(row['selected']).resolve()
        if row.get('layout')=='shipped-tree':
            if target!=new/'tree' or merge.inventory(target)!=record['incoming']:
                raise ValueError('Clean shipped source was changed')
            continue
        # Already admitted older operations retain their original candidate/Git
        # contract. They are not silently converted into a new clean request.
        if not target.is_relative_to(new/'code') or merge.inventory(target)!=record['incoming']:
            raise ValueError('Clean candidate was changed')
        if merge.git(target/'.git','rev-parse','HEAD',exe=merge.executable(new,old)).stdout.decode().strip()!=row['clean_commit']:
            raise ValueError('Clean candidate commit was changed')
