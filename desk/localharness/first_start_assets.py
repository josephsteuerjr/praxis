"""Move named owner backup/audio assets before their programme is retired."""
import copy,hashlib,os,tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .package_notice import read,write

FORMAT='helene-first-start-assets.v1'


def prepare(target,retiring,data_sources,profiles,active,selected):
    """Pure custody plan and reference rebinding; no old files are changed."""
    from .first_start import plain
    target=plain(target);retiring=[plain(path) for path in retiring]
    data_sources=[plain(path) for path in data_sources]
    journal=target/'memory/.state/first-start-assets.json';previous=read(journal)
    if previous and (previous.get('format')!=FORMAT or not isinstance(previous.get('assets'),dict)):
        raise ValueError('Invalid retained-asset receipt')
    known=previous.get('assets',{});moves={};bindings=[]
    def bind(value,directory):
        directory=plain(directory)
        for section,field,default in (('backup','dir','backups'),('voice','tts_reference',None)):
            block=value.get(section)
            if block is not None and not isinstance(block,dict):raise ValueError('Invalid '+section+' settings')
            raw=(block or {}).get(field) or default
            if not raw:continue
            source=Path(raw).expanduser();source=plain(source if source.is_absolute() else directory/source)
            if not any(source==root or source.is_relative_to(root) for root in retiring):continue
            if any(source==root or root.is_relative_to(source) for root in retiring):
                raise ValueError('Named asset contains a programme root')
            if any(source==data or data.is_relative_to(source) for data in data_sources):
                raise ValueError('Named asset contains agent data')
            key=str(source);identity=hashlib.sha256(os.path.normcase(key).encode()).hexdigest()[:24]
            destination=target/'retained-assets'/identity/source.name
            prior=known.get(key)
            if isinstance(prior,dict) and prior.get('source')==key:
                saved=plain(prior['destination'])
                if not saved.is_relative_to(target/'retained-assets'):raise ValueError('Retained asset receipt leaves data scope')
                destination=saved
            row={'source':key,'destination':str(destination),'kind':'directory' if section=='backup' else 'file'}
            moves[key]=row
            block=copy.deepcopy(block or {});value[section]=block;bindings.append((block,field,key))
    for profile in profiles:
        profile['config']=copy.deepcopy(profile['config'])
        bind(profile['config'],Path(profile['directory']))
    bind(active,Path(selected['directory']) if selected else target)
    directories=sorted((row for row in moves.values() if row['kind']=='directory'),key=lambda row:len(Path(row['source']).parts))
    for key,row in moves.items():
        source=Path(key)
        parent=next((other for other in directories if other['source']!=key and source.is_relative_to(Path(other['source']))),None)
        if parent:
            row['destination']=str(Path(parent['destination'])/source.relative_to(Path(parent['source'])))
            row['covered_by']=parent['source']
        destination=plain(row['destination']);prior=known.get(key)
        owned=isinstance(prior,dict) and prior.get('destination')==str(destination)
        if parent:
            prior_parent=known.get(parent['source'])
            owned=owned or (isinstance(prior_parent,dict) and prior_parent.get('destination')==parent['destination'])
        if destination.exists() and not owned:raise ValueError('Retained asset destination already exists without its receipt')
        effective=source if source.exists() else destination
        if row['kind']=='file' and not effective.exists():raise FileNotFoundError('Не найден голосовой образец: '+str(source))
        if effective.exists() and ((row['kind']=='directory' and not effective.is_dir()) or (row['kind']=='file' and not effective.is_file())):
            raise ValueError('Named asset has the wrong file type')
    for block,field,key in bindings:block[field]=moves[key]['destination']
    return {'format':FORMAT,'journal':str(journal),'assets':{**known,**moves},'moves':[row for row in moves.values() if not row.get('covered_by')],
            'aliases':[row for row in moves.values() if row.get('covered_by')]}


def snapshot(path):
    """Actual named asset bytes, including empty directories; reject links."""
    from .first_start import plain
    from .validation_read import checkpoint,current,digest as measured,parallel
    check=current();checkpoint(check);path=plain(path)
    def digest(source):
        checkpoint(check)
        with plain(source).open('rb') as stream:return measured(stream,check)
    if path.is_file():return {'kind':'file','sha256':digest(path)}
    if not path.is_dir():raise FileNotFoundError(path)
    directories=['.'];files=[]
    def unreadable(error):raise error
    for here,dirs,names in os.walk(path,followlinks=False,onerror=unreadable):
        checkpoint(check)
        for name in dirs:
            checkpoint(check)
            child=plain(Path(here)/name);directories.append(child.relative_to(path).as_posix())
        for name in names:checkpoint(check);files.append(plain(Path(here)/name))
    hashes=dict(zip((p.relative_to(path).as_posix() for p in files),parallel(digest,files,workers=os.cpu_count() or 1)))
    return {'kind':'directory','directories':sorted(directories),'files':hashes}


def copy_asset(source,destination,expected):
    """Copy owner assets, never code; the trial keeps old references usable."""
    from .first_start import plain
    from .update_io import copy_verified
    source,destination=plain(source),plain(destination)
    def one(incoming,out,digest):
        out.parent.mkdir(parents=True,exist_ok=True)
        if out.exists():
            if snapshot(out)!={'kind':'file','sha256':digest}:raise ValueError('Copied asset destination changed')
            return
        descriptor,raw=tempfile.mkstemp(prefix='.asset-copy-',dir=out.parent);os.close(descriptor)
        temporary=Path(raw);temporary.unlink()
        try:
            copy_verified(incoming,temporary,digest);os.replace(temporary,out)
        finally:temporary.unlink(missing_ok=True)
    if expected['kind']=='file':one(source,destination,expected['sha256'])
    else:
        for name in expected['directories']:plain(destination/name).mkdir(parents=True,exist_ok=True)
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:
            list(pool.map(lambda row:one(plain(source/row[0]),plain(destination/row[0]),row[1]),expected['files'].items()))
    if snapshot(source)!=expected or snapshot(destination)!=expected:raise ValueError('Owner assets changed while preparing the trial')


def retain(plan,*,keep_sources=()):
    """Recorded verified moves survive a failed cleanup and a later retry."""
    from .first_start import plain,_move
    journal=plain(plan['journal'])
    write(journal,{'format':FORMAT,'assets':plan['assets']})
    for row in plan['moves']:
        source,destination=plain(row['source']),plain(row['destination'])
        preserve=any(source.is_relative_to(plain(root)) for root in keep_sources)
        copied=preserve and source.exists()
        if source.exists():
            if preserve:
                row['snapshot']=snapshot(source);copy_asset(source,destination,row['snapshot'])
            else:_move(source,destination)
        elif not destination.exists() and row['kind']=='directory':destination.mkdir(parents=True)
        if not destination.exists():raise RuntimeError('Named asset transfer was not confirmed')
        row['state']='copied' if copied else 'retained'
        plan['assets'][row['source']]=row
        write(journal,{'format':FORMAT,'assets':plan['assets']})
    for row in plan.get('aliases',[]):
        if not plain(row['destination']).exists():raise RuntimeError('Contained asset transfer was not confirmed')
        row['state']='retained';plan['assets'][row['source']]=row
    write(journal,{'format':FORMAT,'assets':plan['assets']})
