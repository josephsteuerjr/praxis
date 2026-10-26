"""Python desktop updates: download beside Helene, apply into its working folder."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
import threading

_update_guard = threading.Lock()
_update_targets = set()

EXTENSION_ASSETS = {'xtts':('Helene-components-xtts-model.zip','Helene-components-xtts-runtime.zip'),
                    'whisper':('Helene-components-whisper.zip',)}
COMPONENTS = tuple(name for names in EXTENSION_ASSETS.values() for name in names)


def installed_extensions(root):
    """Keep the owner's current choices on update; wizard defaults apply only once."""
    root=Path(root)
    return [name for name,path in (('xtts',root/'components/xtts'),('whisper',root/'voice/site-packages'))
            if path.is_dir() or (name=='xtts' and (root/'voice-xtts').is_dir()) or (name=='whisper' and any((root/'runtime'/site/'faster_whisper').is_dir() for site in ('','Lib/site-packages')))]


def asset_sha(url):
    secure_url(url)
    if not url.startswith('https://'):raise ValueError('Release hashes require HTTPS')
    parsed=urllib.parse.urlsplit(url)
    address=urllib.parse.urlunsplit(parsed._replace(path=parsed.path+'.sha256'))
    with download_http().open(address,timeout=30) as response:
        text=response.read(4096).decode('ascii').split()
    if not text or not re.fullmatch('[0-9a-fA-F]{64}',text[0]):raise ValueError('Invalid release SHA256')
    return text[0].lower()


def download_http():
    class SecureRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            if urllib.parse.urlsplit(newurl).scheme!='https':raise ValueError('Update redirect must remain HTTPS')
            secure_url(newurl)
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    return urllib.request.build_opener(SecureRedirect())


def secure_url(value):
    parsed=urllib.parse.urlsplit(value.strip())
    if parsed.username or parsed.password or not parsed.hostname:raise ValueError('Invalid server URL')
    local=parsed.hostname in ('localhost','127.0.0.1','::1') or parsed.hostname.endswith('.local')
    try:local=local or ipaddress.ip_address(parsed.hostname).is_private
    except ValueError:pass
    if parsed.scheme!='https' and not (parsed.scheme=='http' and local):raise ValueError('Use HTTPS for public servers')
    return value.strip()


def request_json(url,headers=None):
    secure_url(url)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*a,**kw):return None
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with http.open(urllib.request.Request(url,headers=headers or {}),timeout=15) as response:
        return json.load(response)


def version(root):
    path=root/'python-app.json'
    value=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return value.get('version','1.9.0')


def parts(value):
    text=value.strip().lstrip('vV')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?',text):raise ValueError('Unrecognized release version')
    return tuple(int(v) for v in text.split('-')[0].split('.')[:3])


def update_check(owner,url):
    value=request_json(url)
    latest=value.get('version') or value.get('tag_name','')
    current=version(owner.root)
    assets=value.get('assets',[])
    from .python_platform import asset_name, target
    name=asset_name(latest.lstrip('vV'),target(owner.root))
    candidates=[a for a in assets if a.get('name')==name]
    asset=candidates[0] if len(candidates)==1 else {}
    address=value.get('url') if value.get('version') else asset.get('browser_download_url',value.get('html_url',''))
    notes=value.get('notes') or value.get('body','')
    sha=asset.get('digest','').removeprefix('sha256:')
    if not sha and asset:
        for line in notes.splitlines():
            if asset['name'] in line:
                found=re.search(r'\b[0-9a-fA-F]{64}\b',line)
                if found:sha=found[0].lower();break
    return {'current':current,'latest':latest.lstrip('vV'),'newer':parts(latest)>parts(current),
            'url':address,'notes':next((s.strip() for s in notes.splitlines() if s.strip() and not s.startswith('#')),'')[:200],'sha256':sha}


def _download(url, target, expected='', progress=None):
    secure_url(url)
    if not url.startswith('https://'):raise ValueError('Update archives require HTTPS')
    expected=expected.removeprefix('sha256:').lower()
    if expected and not re.fullmatch('[a-f0-9]{64}',expected):raise ValueError('Invalid SHA256')
    target=Path(target)
    if target.exists():
        with target.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if expected and actual==expected:
            size=target.stat().st_size
            if progress:progress({'stage':'downloading','asset':target.name,'bytes':size,'total_bytes':size,'reused':True})
            return {'path':str(target),'bytes':size,'sha256':actual,'sha_ok':True,'reused':True}
        raise FileExistsError('Existing download differs or has no published hash; retained: '+str(target))
    stage=target.parent/('.'+uuid.uuid4().hex+'.part')
    digest=hashlib.sha256();count=0
    try:
        http=download_http();reported=0.0
        with http.open(url,timeout=120) as source,stage.open('xb') as output:
            length=source.headers.get('Content-Length')
            total=int(length) if length and length.isdecimal() else None
            while chunk:=source.read(262144):
                count+=len(chunk)
                if count>8_000_000_000:raise ValueError('Update archive exceeds 8 GB')
                digest.update(chunk);output.write(chunk)
                if progress and time.monotonic()-reported>=.2:
                    progress({'stage':'downloading','asset':target.name,'bytes':count,'total_bytes':total});reported=time.monotonic()
        if progress:progress({'stage':'downloading','asset':target.name,'bytes':count,'total_bytes':total})
        actual=digest.hexdigest()
        if expected and expected!=actual:raise ValueError('Update SHA256 differs')
        # A hard link publishes without replacing a racing existing download.
        os.link(stage,target)
        return {'path':str(target),'bytes':count,'sha256':actual,'sha_ok':True if expected else None}
    finally:stage.unlink(missing_ok=True)


def update_download(owner,url,sha256=None,extensions=None):
    parsed=urllib.parse.urlsplit(url)
    name=Path(urllib.parse.unquote(parsed.path)).name
    from .python_platform import asset_name, target
    selected=target(owner.root)
    suffix='' if selected=='windows-x86_64' else '-'+selected
    ending=suffix+'.zip'
    release=name[len('Helene-'):-len(ending)] if name.startswith('Helene-') and name.endswith(ending) else ''
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?',release) or name != asset_name(release,selected):
        raise ValueError('Expected an update archive for this application target')
    folder=owner.root.resolve().parent
    report=lambda value:owner.event('package-progress',value)
    selected=installed_extensions(owner.root) if extensions is None else extensions
    if not isinstance(selected,list) or any(s not in EXTENSION_ASSETS for s in selected):raise ValueError('Unknown extension selection')
    result=_download(url,folder/name,sha256 or asset_sha(url),report)
    components=[]
    cache=folder/'.helene-downloads'/name.removesuffix('.zip');cache.mkdir(parents=True,exist_ok=True)
    from . import component_downloads
    manifest=component_downloads.incoming(result['path'])
    if manifest is not None:
        changed=component_downloads.changed_extensions(owner.root,manifest,selected) if extensions is None else selected
        components=component_downloads.fetch(owner.root,manifest,changed,report)
    else:
        # Old packages have no map. Only that legacy format uses same-tag URLs.
        for component in dict.fromkeys(n for s in selected for n in EXTENSION_ASSETS[s]):
            component_url=urllib.parse.urlunsplit(parsed._replace(path=parsed.path.rsplit('/',1)[0]+'/'+component))
            components.append(_download(component_url,cache/component,asset_sha(component_url),report))
    receipt=folder/(name+'.download.json')
    receipt.write_text(json.dumps({'archive':result,'components':components,'extensions':selected},indent=2),encoding='utf-8')
    result['components']=components
    return result


def _extract(archive, destination, progress=None, prefix='Helene/'):
    from .parallel_files import extract
    return extract(archive,destination,progress,prefix)


def update_install(owner,path,forceExtensions=False):
    target=os.path.normcase(str(owner.root.resolve()))
    with _update_guard:
        if target in _update_targets:
            raise RuntimeError('Python update is already preparing or handed off for this installation')
        _update_targets.add(target)
    try:
        result = _update_install(owner,path,forceExtensions)
        if isinstance(result, dict) and result.get('state') == 'needs_resolution':
            with _update_guard:_update_targets.discard(target)
        return result
    except BaseException:
        with _update_guard:_update_targets.discard(target)
        raise


def await_admission(child, staging, target, timeout=15):
    """A created process is not admitted. Keep the old owner until its helper
    proves the target lease, before waiting for that owner's exit.
    """
    receipt=Path(staging)/'updater-admitted.json';deadline=time.monotonic()+timeout
    try:
        while True:
            if receipt.is_file():
                value=json.loads(receipt.read_text(encoding='utf-8'))
                if value.get('pid')!=child.pid or Path(value.get('target','')).resolve()!=Path(target).resolve():
                    raise ValueError('Updater admission identity differs')
                return value
            if child.poll() is not None:raise RuntimeError('Updater refused admission; previous application remains open')
            if time.monotonic()>=deadline:raise TimeoutError('Updater admission was not confirmed; previous application remains open')
            time.sleep(.05)
    except BaseException:
        if child.poll() is None:
            child.terminate();child.wait(timeout=5)
        raise


def _update_install(owner,path,forceExtensions=False):
    archive=Path(path).resolve(strict=True)
    folder=owner.root.resolve().parent
    if archive.suffix.lower()!='.zip' or archive.parent!=folder:
        raise ValueError('Choose the update ZIP beside the working Helene folder')
    destination=folder/('.helene-update-'+uuid.uuid4().hex)
    destination.mkdir()
    archives=[archive]
    receipt=archive.with_name(archive.name+'.download.json')
    if receipt.is_file():
        value=json.loads(receipt.read_text(encoding='utf-8'))
        rows=[value['archive'],*value['components']]
        cache=folder/'.helene-downloads'/archive.name.removesuffix('.zip')
        from . import component_downloads
        manifest=component_downloads.incoming(archive)
        pinned={component_downloads.cache_name(a):a for a in component_downloads.select(manifest,value['extensions'])} if manifest else {}
        def valid_component(row):
            candidate=Path(row['path']).resolve()
            if manifest:
                record=pinned.get(candidate.name)
                return candidate.parent==component_downloads.cache_dir(owner.root).resolve() and record is not None and row.get('sha256')==record['sha256'] and row.get('bytes')==record['size']
            return candidate.parent==cache and candidate.name in COMPONENTS
        if Path(rows[0]['path']).resolve()!=archive or any(not valid_component(r) for r in rows[1:]):
            raise ValueError('Download receipt paths differ')
        for row in rows:
            candidate=Path(row['path'])
            with candidate.open('rb') as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
            if actual!=row['sha256']:raise ValueError('Downloaded archive changed: '+candidate.name)
        archives=[Path(r['path']) for r in rows]
    else:
        # A manually downloaded base ZIP is a complete application. Optional
        # components already installed in the working root remain there.
        pass
    for item in archives:_extract(item,destination,lambda value:owner.event('package-progress',value))
    new_root=destination/'Helene'
    if not (new_root/'python-app.json').is_file() or not (new_root/'app/python_app.py').is_file():
        raise ValueError('Update ZIP must contain Helene Python application')
    from .python_platform import require_runtime
    require_runtime(new_root)
    interpreter=new_root/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
    if not interpreter.is_file():raise ValueError('Python application runtime is missing')
    import shutil
    import threading
    # Copy the helper outside the live installation before replacing its files.
    helper=destination/'python_source_updater.py'
    from .update_extensions import check
    check(owner.root, new_root, force=forceExtensions)
    from .code_transition import preflight
    source_plan = preflight(owner.root, new_root)
    if source_plan['state'] != 'ready':
        # All merge material lives in .source-updates, independent of this
        # disposable extraction. Keep the verified ZIP for the next click.
        shutil.rmtree(destination)
        return source_plan
    shutil.copy2(new_root/'app/localharness/python_source_updater.py',helper)
    argv=[str(interpreter),str(helper),'--old',str(owner.root),'--new',str(new_root),
          '--pid',str(os.getpid()),'--staging',str(destination)]
    if getattr(owner,'browser_mode',False):argv.append('--browser')
    # Verified input ZIPs remain a reusable cache, not disposable updater output.
    log=folder/('helene-update-'+destination.name.removeprefix('.helene-update-')+'.log')
    with log.open('ab') as output:
        child=subprocess.Popen(argv,cwd=destination,stdin=subprocess.DEVNULL,
            stdout=output,stderr=subprocess.STDOUT,
            creationflags=(0x00000200|0x00000008) if os.name=='nt' else 0,
            start_new_session=os.name!='nt')
    await_admission(child,destination,owner.root)
    def exit_old():
        owner.close()
        os._exit(0)
    timer=threading.Timer(2,exit_old);timer.daemon=True;timer.start()
    return {'dir':str(owner.root),'updater_pid':child.pid,'log':str(log),
            'old_install_retained':True,'staging':str(destination)}
