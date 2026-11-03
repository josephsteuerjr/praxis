"""A shipped, immutable source base is the eligibility for authored updates.

Legacy installations have only the clean replacement path. Release labels and
model claims never authorize a guessed three-way merge base.
"""
from pathlib import Path
import hashlib,json,os,zipfile
from .package_notice import read,write
from .source_merge import relative

PROTOCOL='helene-authored-source.v2'
FORMAT='helene-authored-baseline.v2'


def digest(path):
    from .validation_read import checkpoint,digest as measured
    checkpoint()
    with Path(path).open('rb') as stream:return measured(stream)


def _verify(program,surface,record):
    from .validation_read import checkpoint,digest as measured
    checkpoint()
    name='code-upstream' if surface=='tree' else 'app-upstream'
    archive=program/(name+'.zip');manifest=program/(name+'.json')
    value=read(manifest)
    if not value or record!={'archive_sha256':digest(archive),'manifest_sha256':digest(manifest)}:
        raise ValueError('Stored '+surface+' baseline identity changed')
    if value.get('sha256')!=record['archive_sha256'] or not isinstance(value.get('files'),dict):
        raise ValueError('Stored '+surface+' baseline inventory is missing')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or set(names)!=set(value['files']):raise ValueError('Baseline archive members differ')
        for name,row in value['files'].items():
            checkpoint()
            relative(name)
            entry=z.getinfo(name)
            if entry.is_dir() or ((entry.external_attr>>16)&0o170000)==0o120000:
                raise ValueError('Baseline contains a special member')
            if row.get('mode') not in ('100644','100755'):raise ValueError('Invalid baseline file mode')
            raw_mode=entry.external_attr>>16
            if raw_mode&0o170000 not in (0,0o100000):raise ValueError('Baseline contains a special file mode')
            actual_mode='100755' if raw_mode&0o111 else '100644'
            if actual_mode!=row['mode']:raise ValueError('Baseline archive file mode differs')
            with z.open(entry) as stream:actual=measured(stream)
            if actual!=row.get('sha256'):raise ValueError('Baseline source bytes changed: '+name)
    return value


def validate(program):
    from .validation_read import checkpoint
    checkpoint()
    program=Path(program).resolve();meta=read(program/'python-app.json')
    if meta.get('source_update_protocol')!=PROTOCOL:raise ValueError('Legacy installation requires a clean update')
    record=read(program/'source-update-baseline.json')
    if meta.get('source_update_baseline_sha256')!=digest(program/'source-update-baseline.json'):
        raise ValueError('Authored baseline admission changed')
    if record.get('format')!=FORMAT or set(record.get('surfaces',{}))!={'tree','app'}:
        raise ValueError('Authored update baseline is incomplete')
    return {surface:_verify(program,surface,record['surfaces'][surface]) for surface in ('tree','app')}


def mode(program,requested=None):
    if requested not in (None,'clean','preserve'):raise ValueError('Unknown source update mode')
    if requested=='clean':return 'clean'
    protocol=read(Path(program)/'python-app.json').get('source_update_protocol')
    if protocol and protocol!=PROTOCOL:raise ValueError('Unknown authored-update source protocol')
    if protocol!=PROTOCOL:
        if requested=='preserve':
            raise ValueError('Для прежней установки доступна только чистая замена кода с сохранением данных')
        return 'clean'
    validate(program)
    return 'preserve'


def capabilities(program):
    try:default=mode(program)
    except (OSError,ValueError,KeyError,TypeError) as error:
        return {'source_modes':['clean'],'default_source_mode':None,'source_mode_error':str(error)}
    return {'source_modes':['preserve','clean'] if default=='preserve' else ['clean'],
            'default_source_mode':default,'source_mode_error':''}


def seal(program):
    """Build-time only: publish complete pristine app A alongside tree A."""
    from .update_composition import app_inventory
    program=Path(program).resolve()
    if (program/'source-update-baseline.json').exists():
        return validate(program)  # Never bless authored M as a new pristine A.
    tree=program/'code-upstream.zip';tree_meta=program/'code-upstream.json'
    tree_record={'archive_sha256':digest(tree),'manifest_sha256':digest(tree_meta)}
    _verify(program,'tree',tree_record)
    files=app_inventory(program/'app')
    if not files:raise ValueError('Cannot seal an empty application baseline')
    archive=program/'app-upstream.zip'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for name,row in sorted(files.items()):
            relative(name);raw=(program/'app'/name).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Application changed during sealing')
            entry=zipfile.ZipInfo(name,(1980,1,1,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED
            entry.external_attr=(0o100755 if row['mode']=='100755' else 0o100644)<<16
            z.writestr(entry,raw)
    if app_inventory(program/'app')!=files:raise ValueError('Application changed during sealing')
    write(program/'app-upstream.json',{'format':'helene-app-upstream.v1','files':files,'sha256':digest(archive)})
    surfaces={'tree':tree_record,'app':{'archive_sha256':digest(archive),'manifest_sha256':digest(program/'app-upstream.json')}}
    write(program/'source-update-baseline.json',{'format':FORMAT,'surfaces':surfaces})
    meta=read(program/'python-app.json');meta.update(source_update_protocol=PROTOCOL,
        source_update_baseline_sha256=digest(program/'source-update-baseline.json'))
    write(program/'python-app.json',meta)
    return validate(program)


def extract(program,surface,destination):
    if surface not in ('tree','app'):raise ValueError('Unknown baseline surface')
    from .first_start import plain
    program=Path(program).resolve();record=validate(program)[surface];destination=plain(destination)
    archive=program/('code-upstream.zip' if surface=='tree' else 'app-upstream.zip')
    with zipfile.ZipFile(archive) as z:
        for name,row in record['files'].items():
            relative(name);target=plain(destination/name);target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(z.read(name))
            if os.name!='nt':target.chmod(0o755 if row['mode']=='100755' else 0o644)
    return record['files']
