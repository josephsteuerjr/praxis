"""Optional independent MIT praxis-relay. The base application carries no compiler."""
from pathlib import Path
import json,os,shutil,tempfile,tarfile,zipfile
from .python_platform import runtime_target

PIN=json.loads(Path(__file__).with_name('relay-release.json').read_text(encoding='utf-8'))
PROJECT=PIN['project']

def executable(program,config=None):
    relay=(config or {}).get('relay') or {}
    if relay.get('executable'):
        path=Path(relay['executable']).expanduser()
        if not path.is_absolute():path=Path(program)/path
        return path.resolve()
    home=Path(program)/'relay-product'
    current=home/('praxis-relay-headless.exe' if os.name=='nt' else 'praxis-relay-headless')
    if current.is_file():return current
    # Preserve already installed owner bytes across upgrades; no re-download.
    legacy=Path(program)/('helene-relay.exe' if os.name=='nt' else 'helene-relay')
    return legacy if legacy.is_file() else current

def status(program):
    installed=executable(program).is_file();asset=PIN['platforms'].get(runtime_target())
    return {'id':'relay','ready':bool(asset),'installed':installed,'download_bytes':asset['size'] if asset else 0,
            'project':PROJECT,'license':'MIT','error':'' if asset or installed else 'Укажи путь к отдельному praxis-relay в настройках подписки.'}

def unpack(archive,destination,binary):
    """Select regular payload files; never follow archive paths or links."""
    allowed={binary,'LICENSE','LICENSE-APACHE','NOTICE','BUILD-PASSPORT.json','README.md','PLATFORMS.md'}
    found=set()
    def write(name,size,source):
        name=name.replace('\\','/');parts=name.split('/')
        if any(p in ('..','') for p in parts) or name.startswith('/') or ':' in name:raise ValueError('Unsafe relay archive member')
        leaf=parts[-1]
        if leaf not in allowed:source.close();return
        if leaf in found or not 0<=size<=64*1024*1024:raise ValueError('Invalid relay archive payload')
        found.add(leaf)
        with source as src,(destination/leaf).open('wb') as target:
            shutil.copyfileobj(src,target)
        if (destination/leaf).stat().st_size!=size:raise ValueError('Truncated relay payload')
    if archive.name.endswith('.zip'):
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                if item.is_dir():continue
                if (item.external_attr>>16)&0o170000==0o120000:raise ValueError('Relay archive symlink')
                write(item.filename,item.file_size,bundle.open(item))
    else:
        with tarfile.open(archive) as bundle:
            for item in bundle.getmembers():
                if item.isdir():continue
                if not item.isfile():raise ValueError('Relay archive contains a special file')
                write(item.name,item.size,bundle.extractfile(item))
    if not {binary,'LICENSE'}<=found:raise ValueError('Relay executable or license missing')
    (destination/binary).chmod(0o755)

def install(program,progress=None):
    asset=PIN['platforms'].get(runtime_target())
    if asset is None:raise RuntimeError(status(program)['error'])
    from .python_updates import _download
    program=Path(program);home=program/'relay-product';binary='praxis-relay-headless.exe' if os.name=='nt' else 'praxis-relay-headless';target=home/binary
    from .config_transaction import transaction
    with transaction(program/'relay-product-install'):
        if target.is_file():return {'installed':True,'path':str(target),'restart_required':True}
        with tempfile.TemporaryDirectory(prefix='.relay-install-',dir=program) as temporary:
            stage=Path(temporary);archive=stage/asset['name']
            result=_download(asset['url'],archive,asset['sha256'],progress)
            if result['bytes']!=asset['size']:raise ValueError('Standalone relay size differs from the release')
            payload=stage/'product';payload.mkdir();unpack(archive,payload,binary)
            (payload/'provenance.json').write_text(json.dumps({'product':'praxis-relay','version':PIN['version'],'license':'MIT','source_commit':PIN['source_commit'],'release':PROJECT,**asset},indent=2),encoding='utf-8')
            home.mkdir(exist_ok=True)
            for path in payload.iterdir():os.replace(path,home/path.name)
    return {'installed':True,'path':str(target),'restart_required':True}

def preserve(previous,current):
    previous,current=Path(previous),Path(current)
    source=previous/'relay-product';target=current/'relay-product'
    if source.is_dir() and not target.exists():shutil.copytree(source,target)
    for name in ('helene-relay.exe','helene-relay'):
        source=previous/name;target=current/name
        if source.is_file() and not target.exists():shutil.copy2(source,target)
