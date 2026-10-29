"""Python desktop updates: download beside Helene, apply into its working folder."""
import sys
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
_waiting_updates = {}

def update_agent_request(owner):
    from .desktop_update_request import status
    return status(owner.current().tree)

def update_agent_confirm(owner,id,sourceMode='preserve'):
    from .desktop_update_request import request
    return request(owner.current().tree,'confirm',root=owner.root,identity=id,source_mode=sourceMode,by='window')


def cancel_waiting(owner):
    """Revoke only a pending review continuation, never interrupt a file switch."""
    target = os.path.normcase(str(owner.root.resolve()))
    with _update_guard:
        pending = _waiting_updates.get(target)
        if pending is not None:
            pending.set()
        return pending is not None


def update_cancel(owner):
    from . import desktop_update_request as jobs
    job = jobs.status(owner.current().tree)
    if job.get('state') in jobs.ACTIVE:
        jobs.request(owner.current().tree, 'cancel', root=owner.root, identity=job['id'], by='window')
        cancel_waiting(owner)
        return {'ok': True, 'id': job['id'], 'cancel_requested': True}
    if not cancel_waiting(owner):
        raise RuntimeError('Ожидание ревью уже завершено; переключение версии не прерывается.')
    return {'ok': True}


def update_progress(owner):
    from .update_progress import status
    value = status(owner.root)
    if not value:
        from .desktop_update_request import status as job_status
        job = job_status(owner.current().tree)
        if job.get('state') != 'none':
            return {'id':job['id'],'stage':job.get('phase',job['state']),
                    'label':'Обновление · '+job['state'],'error':job.get('error','')}
    return value


def update_start(owner, url, sha256=None, sourceMode=None):
    from .desktop_update_request import admit_download
    return admit_download(owner, url, sha256, sourceMode)


def update_resume(owner,id):
    from . import desktop_update_request as jobs
    return jobs.request(owner.current().tree,'resume',root=owner.root,identity=id)



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
    matched=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:[-.]([A-Za-z0-9.]+))?',text)
    if not matched:raise ValueError('Unrecognized release version')
    numeric=tuple(int(v) for v in matched.groups()[:3])
    suffix=matched[4]
    prerelease=tuple((0,int(v)) if v.isdecimal() else (1,v.lower()) for v in suffix.split('.')) if suffix else ()
    return (*numeric, 0 if suffix else 1, prerelease)


def update_check(owner,url):
    value=request_json(url)
    latest=value.get('version') or value.get('tag_name','')
    current=version(owner.root)
    assets=value.get('assets',[])
    from .python_platform import asset_name, target, distribution
    name=asset_name(latest.lstrip('vV'),target(owner.root),distribution(owner.root))
    candidates=[a for a in assets if a.get('name')==name]
    asset=candidates[0] if len(candidates)==1 else {}
    address=value.get('url') if value.get('version') else asset.get('browser_download_url',value.get('html_url',''))
    notes=value.get('notes') or value.get('body','')
    sha=str(value.get('sha256') or asset.get('digest','')).removeprefix('sha256:')
    if sha and not re.fullmatch('[a-fA-F0-9]{64}',sha):raise ValueError('Invalid update feed SHA256')
    if not sha and asset:
        for line in notes.splitlines():
            if asset['name'] in line:
                found=re.search(r'\b[0-9a-fA-F]{64}\b',line)
                if found:sha=found[0].lower();break
    return {'current':current,'latest':latest.lstrip('vV'),'newer':parts(latest)>parts(current),
            'url':address,'notes':next((s.strip() for s in notes.splitlines() if s.strip() and not s.startswith('#')),'')[:200],'sha256':sha}


def release_endpoint(owner, selected_version='latest'):
    """Use the existing per-agent update.url; stable defaults stay unchanged."""
    from .essential_backup import load
    configured = str((load(owner.current().config).get('update') or {}).get('url') or '').strip()
    default = 'https://api.github.com/repos/josephsteuerjr/praxis/releases/latest'
    endpoint = secure_url(configured or default)
    if selected_version != 'latest':
        parts(selected_version)
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.hostname == 'api.github.com' and parsed.path.endswith('/releases/latest'):
            endpoint = urllib.parse.urlunsplit(parsed._replace(path=parsed.path.removesuffix('/latest')+'/tags/v'+selected_version.lstrip('vV')))
        else:
            raise ValueError('This configured feed exposes latest only; choose a version-specific feed explicitly')
    return endpoint


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
                from .update_io import count as count_read
                count_read(progress, 'download', len(chunk))
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
    from .python_platform import asset_name, target, distribution
    selected=target(owner.root)
    flavor=distribution(owner.root)
    ending=asset_name('',selected,flavor).removeprefix('Helene-')
    release=name[len('Helene-'):-len(ending)] if name.startswith('Helene-') and name.endswith(ending) else ''
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?',release) or name != asset_name(release,selected,flavor):
        raise ValueError('Expected an update archive for this application target')
    folder=owner.root.resolve().parent
    from .update_progress import Progress
    job = getattr(owner, 'update_job', None)
    operation=job['id'] if job else uuid.uuid4().hex
    report=Progress(folder/('.helene-update-'+operation+'.progress.json'),operation,owner.root,event=owner.event)
    report.phase('downloading')
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
    receipt.write_text(json.dumps({'archive':result,'components':components,'extensions':selected,
                                   'progress':str(report.path)},indent=2),encoding='utf-8')
    report.save()
    result['components']=components
    return result


def _extract(archive, destination, progress=None, prefix='Helene/'):
    from .parallel_files import extract
    return extract(archive,destination,progress,prefix)


def update_install(owner,path,forceExtensions=False,sourceMode=None):
    if forceExtensions:
        raise ValueError('Incompatible-extension override is obsolete; resolve compatibility before ordinary Update')
    target=os.path.normcase(str(owner.root.resolve()))
    with _update_guard:
        if target in _update_targets:
            raise RuntimeError('Python update is already preparing or handed off for this installation')
        _update_targets.add(target)
    try:
        result = _update_install(owner,path,forceExtensions,sourceMode)
        if isinstance(result, dict) and result.get('state') in {'needs_resolution', 'cancelled'}:
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
                if value.get('birth') is not None:
                    from .session_host import process_state
                    if process_state(value) != 'alive':
                        raise ValueError('Updater admission process birth is not current')
                if value.get('process_exit_allowed'):
                    from .session_host import process_state
                    job=json.loads(Path(value['job_path']).read_text(encoding='utf-8'))
                    if (process_state(value.get('durable_owner') or {})!='alive' or job.get('id')!=value.get('job_id')
                            or job.get('admission')!=value.get('durable_owner')):
                        raise ValueError('Legacy handoff has no current durable process owner')
                return value
            if child.poll() is not None:raise RuntimeError('Updater refused admission; previous application remains open')
            if time.monotonic()>=deadline:raise TimeoutError('Updater admission was not confirmed; previous application remains open')
            time.sleep(.05)
    except BaseException:
        if child.poll() is None:
            child.terminate();child.wait(timeout=5)
        raise


def _update_install(owner,path,forceExtensions=False,sourceMode=None):
    archive=Path(path).resolve(strict=True)
    folder=owner.root.resolve().parent
    if archive.suffix.lower()!='.zip' or archive.parent!=folder:
        raise ValueError('Choose the update ZIP beside the working Helene folder')
    job = getattr(owner, 'update_job', None)
    destination=Path(job['staging']) if job and job.get('staging') else folder/('.helene-update-'+(job['id'] if job else uuid.uuid4().hex))
    if destination.parent != folder or not destination.name.startswith('.helene-update-'):
        raise ValueError('Durable update staging identity differs')
    resumed = bool(job and job.get('staging') and (destination/'extraction.json').is_file())
    destination.mkdir(exist_ok=resumed)
    if job:
        from .desktop_update_request import checkpoint
        checkpoint(owner, job, staging=str(destination), phase='preparing' if resumed else 'unpacking')
    from .update_progress import Progress
    operation=destination.name.removeprefix('.helene-update-')
    progress=Progress(folder/(destination.name+'.progress.json'),operation,owner.root,event=owner.event)
    if resumed:
        from .package_notice import read
        state = read(destination / 'extraction.json')
        if state.get('archive_sha256') != job['archive_sha256']:
            raise ValueError('Retained extraction does not match the admitted package')
        return _prepare_handoff(owner, destination, state['verified'], progress, forceExtensions, sourceMode)
    archives=[archive]
    receipt=archive.with_name(archive.name+'.download.json')
    if receipt.is_file():
        value=json.loads(receipt.read_text(encoding='utf-8'))
        if value.get('progress'):
            previous=Path(value['progress']).resolve()
            if previous.parent!=folder or not previous.name.startswith('.helene-update-') or not previous.name.endswith('.progress.json'):
                raise ValueError('Download progress receipt differs')
            downloaded=Progress(previous,target=owner.root)
            if downloaded.value['stage'] not in ('completed','failed','needs_resolution'):
                progress.path.unlink()
                progress=downloaded
                progress.event=owner.event
                progress.pin()
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
    verified={}
    try:
        progress.phase('unpacking',staging=str(destination))
        for item in archives:
            verified.update(_extract(item,destination,progress))
        if job:
            from .package_notice import write
            write(destination / 'extraction.json', {'archive_sha256':job['archive_sha256'], 'verified':verified})
            checkpoint(owner, job, phase='staged')
        return _prepare_handoff(owner,destination,verified,progress,forceExtensions,sourceMode)
    except BaseException as exc:
        progress.phase('failed',error=str(exc),previous_application_open=True)
        raise


def _prepare_handoff(owner,destination,verified,progress,forceExtensions,sourceMode=None):
    new_root=destination/'Helene'
    _wait_pause(owner,progress)
    from . import source_admission
    if source_admission.activate(new_root):
        verified = source_admission.current_proofs(verified, new_root)
    # Ordinary Update belongs to the selected agent/session host. The migration
    # wizard is never a parallel owner of this work.
    job = getattr(owner, 'update_job', None)
    if job:
        from .desktop_update_request import checkpoint,bind_incoming
        bind_incoming(job,new_root)
        checkpoint(owner, job, phase='preparing', staging=str(destination))
    predicate = getattr(owner, 'update_cancelled', None)
    cancellation = _Cancellation(predicate) if predicate else None
    review_binding,interpreter=prepare_code(owner.root,new_root,progress,sourceMode,forceExtensions,cancelled=cancellation)
    _wait_pause(owner,progress)
    # No old window closes on preparation failure. The helper independently
    # reacquires the lease and binds the snapshot identity before admission.
    # Full mutable-byte validation follows old-owner exit, before any replacement.
    from python_migrate import installation_lock
    from .python_source_updater import prepare_install
    with installation_lock(owner.root):
        prepare_install(owner.root,new_root,verified=verified,progress=progress,
                        attached_data=getattr(owner,'migration_data',None))
        if review_binding is not None:
            from .package_notice import read
            final = read(new_root / 'code-transition-plan.json')
            if [(row['source'], row['directory']) for row in final['plans']] != review_binding:
                raise ValueError('Состав обновления изменился после ревью. Старая версия остаётся открытой.')
    return _launch_handoff(owner, destination, new_root, interpreter, progress)


def _wait_pause(owner,progress=None):
    from . import owner_stop
    from .desktop_update_request import status as job_status
    job=getattr(owner,'update_job',None)
    if not job:return
    marker=Path(job['tree'])/'memory/.control/autonomy-paused.json'
    announced=False
    while marker.exists() or job_status(job['tree']).get('state')=='paused':
        cancelled=getattr(owner,'update_cancelled',None)
        if owner_stop.stopped() or (cancelled and cancelled()):raise InterruptedError('Owner stopped paused update')
        if progress and not announced:
            progress.phase('paused',message='Задание сохранено до явного продолжения владельцем');announced=True
        time.sleep(.1)


class _Cancellation:
    """Event-shaped adapter over the durable owner intent and local Stop."""
    def __init__(self, predicate):
        self.event = threading.Event()
        self.predicate = predicate
    def is_set(self):
        return self.event.is_set() or self.predicate()
    def set(self):
        self.event.set()
    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while not self.is_set() and time.monotonic() < deadline:
            self.event.wait(min(.1, max(0, deadline-time.monotonic())))
        return self.is_set()


def prepare_code(old,new_root,progress,sourceMode=None,forceExtensions=False,cancelled=None,require_review=False,resolution_owner='agent'):
    """The incoming hand is temporarily attached to the existing agent."""
    from .legacy_update_tool import provide
    from .agent_source_update import AgentResolutionUnavailable
    from . import update_source
    from .package_notice import write
    try:
        try:
            return _prepare_code(old,new_root,progress,sourceMode,forceExtensions,cancelled,require_review,resolution_owner)
        except AgentResolutionUnavailable:
            if sourceMode=='clean':raise
            progress.phase('agent_tools')
            with provide(old,new_root):
                return _prepare_code(old,new_root,progress,sourceMode,forceExtensions,cancelled,require_review,resolution_owner)
    except AgentResolutionUnavailable:
        write(Path(new_root)/'source-resolution-unavailable.json',update_source.identity(old,new_root))
        raise


def _prepare_code(old,new_root,progress,sourceMode=None,forceExtensions=False,cancelled=None,require_review=False,resolution_owner='agent'):
    """Shared native-migration/update merge and incoming-panel preparation."""
    old,new_root=Path(old).resolve(),Path(new_root).resolve()
    if forceExtensions:
        raise ValueError('Incompatible-extension override is obsolete')
    from . import update_source
    resolution_runtime=os.environ.get('HELENE_SOURCE_UPDATE_RUNTIME')
    if resolution_runtime:
        from .source_update_host import verified_runtime
        verified_runtime(Path(resolution_runtime))
    update_source.select(old,new_root,sourceMode or 'preserve',by='window',require_review=require_review,
        resolution_owner=resolution_owner,resolution_runtime=resolution_runtime)
    if not (new_root/'python-app.json').is_file() or not (new_root/'app/python_app.py').is_file():
        raise ValueError('Update ZIP must contain Helene Python application')
    from .python_platform import require_runtime
    require_runtime(new_root,old if (old/'python-app.json').is_file() else None)
    from .update_process import interpreter_for
    interpreter=interpreter_for(new_root)
    if not interpreter.is_file():raise ValueError('Python application runtime is missing')
    from .update_extensions import check
    progress.phase('extensions')
    check(old, new_root, force=forceExtensions)
    from .code_transition import preflight
    progress.phase('code')
    from .agent_source_update import AgentResolutionUnavailable
    try:
        source_plan = ({'state':'ready','plans':[]} if sourceMode=='clean'
                       else preflight(old, new_root, notify=False))
    except AgentResolutionUnavailable:
        from .package_notice import write
        write(new_root/'source-resolution-unavailable.json',update_source.identity(old,new_root))
        raise
    review_binding = None
    if source_plan['state'] != 'ready':
        from .source_update_host import review
        from . import update_profile, code_transition
        def selected():
            value = update_profile.selection(old)
            value['settings'].pop('window-preferences.json', None)
            return value
        before_review = selected()
        cancelled = cancelled or threading.Event()
        target = os.path.normcase(str(old.resolve()))
        with _update_guard:
            _waiting_updates[target] = cancelled
        try:
            from .agent_source_update import AgentResolutionPending
            from .package_notice import read
            while True:
                progress.phase('waiting_agent',message='Агент совмещает правки в отдельной копии. После проверки обновление продолжится.')
                try:
                    review(old, new_root, source_plan['plans'], progress, cancelled)
                    break
                except AgentResolutionPending:
                    if cancelled.is_set():raise RuntimeError('Owner cancelled update review')
                    while any(read(Path(row['directory'])/'pipeline.json').get('state')=='waiting_agent'
                              for row in source_plan['plans']):
                        if cancelled.wait(.5):raise RuntimeError('Owner cancelled the update before agent resolution')
        except BaseException:
            from . import source_update
            from .package_notice import read,write
            for row in source_plan['plans']:
                directory=Path(row['directory']);operation=read(directory/'pipeline.json')
                if operation.get('resolution_owner')!='agent':continue
                if not cancelled.is_set():
                    continue  # a blocked executor/failed check retains this exact job
                source_update._cancel_marker(directory,'Owner cancelled selected-agent update preparation')
                operation.update(state='cancelled',cancel_requested=True)
                source_update._persist(directory,operation)
                request=read(source_update.path(operation['tree']))
                if request.get('id')==directory.name:
                    request['state']='cancelled';write(source_update.path(operation['tree']),request)
            raise
        finally:
            with _update_guard:
                if _waiting_updates.get(target) is cancelled:
                    _waiting_updates.pop(target, None)
        if cancelled.is_set():
            raise RuntimeError('Update review was cancelled; previous application remains open')
        code_transition.validate(new_root)
        if selected() != before_review:
            raise ValueError('Settings or selected source changed during update review; retry Update')
        source_plan = preflight(old, new_root, notify=False)
        if source_plan['state'] != 'ready':
            raise RuntimeError(source_plan['message'])
        review_binding = [(row['source'], row['directory']) for row in source_plan['plans']]
        # The isolated material is still pinned to the original click. No
        # download, extraction, or new merge request is needed after approval.
        check(old, new_root, force=forceExtensions)
    return review_binding,interpreter




def _discard_extraction(owner, destination):
    """Retire only this invocation's unused extraction; merge evidence stays."""
    import shutil
    from .update_io import remove_created_file
    destination = Path(destination)
    if (destination.resolve() != owner.root.resolve().parent / destination.name
            or not destination.name.startswith('.helene-update-')):
        raise ValueError('Unexpected update extraction directory')
    def readonly(function, path, failure):
        if function is not os.unlink or not isinstance(failure[1], PermissionError):
            raise failure[1]
        remove_created_file(path)
    shutil.rmtree(destination, onerror=readonly)


def _launch_handoff(owner, destination, new_root, interpreter, progress):
    import shutil
    from .update_process import background_flags
    folder = owner.root.resolve().parent
    # Copy the helper outside the live installation before replacing its files.
    helper = destination / 'python_source_updater.py'
    shutil.copy2(new_root/'app/localharness/python_source_updater.py',helper)
    argv=[str(interpreter),str(helper),'--old',str(owner.root),'--new',str(new_root),
          '--pid',str(os.getpid()),'--staging',str(destination)]
    job = getattr(owner, 'update_job', None)
    if job:
        from .desktop_update_request import path as job_path, checkpoint
        write_path = job_path(job['tree'])
        argv.extend(['--job',str(write_path),'--job-id',job['id']])
    if getattr(owner,'browser_mode',False):argv.append('--browser')
    # Verified input ZIPs remain a reusable cache, not disposable updater output.
    log=folder/('helene-update-'+destination.name.removeprefix('.helene-update-')+'.log')
    with log.open('ab') as output:
        child=subprocess.Popen(argv,cwd=destination,stdin=subprocess.DEVNULL,
            stdout=output,stderr=subprocess.STDOUT,
            creationflags=background_flags(),
            start_new_session=os.name!='nt')
    admitted = await_admission(child,destination,owner.root)
    if job:
        checkpoint(owner, job, phase='handoff', helper=admitted)
    predicate = getattr(owner, 'update_cancelled', None)
    def exit_old():
        if predicate and predicate():
            return
        if getattr(owner, 'session_host', False):
            from .session_host import retire_observers
            retire_observers(owner.root)
        # The calling Controller owns the supervisor's data leases. Release
        # them through its normal checked teardown before an external migration
        # census (which excludes this PID) can require those leases to be free.
        owner.close()
        if getattr(owner,'bootstrap',False):
            from . import migration_lifecycle
            migration_lifecycle.stop(owner.root)
            migration_lifecycle.quiescent(owner.root)
        os._exit(0)
    timer=threading.Timer(2,exit_old);timer.daemon=True;timer.start()
    return {'dir':str(owner.root),'updater_pid':child.pid,'log':str(log),
            'old_install_retained':True,'staging':str(destination),'progress':str(progress.path),
            'operation_id':progress.value['id']}
