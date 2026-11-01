"""One durable owner-consented update job, shared by agent and disposable UI.

The session Controller's existing watcher executes it. No UI thread owns the job,
no age timer revokes admitted semantic work, and crash recovery rejoins its exact
staging/worker identities rather than issuing another model or download request.
"""
from pathlib import Path
import hashlib
import os
import re
import time
import uuid
from .package_notice import read, write
from .config_transaction import transaction

ACTIVE = {'confirmed', 'running', 'handed_off', 'attention', 'paused'}


def path(tree):
    return Path(tree) / 'memory/.control/desktop-update-request.json'


def program():
    config = Path(os.environ.get('HELENE_CONFIG') or '').resolve()
    base = config.parent.parent.parent if config.parent.parent.name == 'agents' else config.parent
    if not (base / 'python-app.json').is_file():
        raise ValueError('Desktop installation is unavailable in this runner')
    return base


def status(tree):
    value=read(path(tree)) or {'state':'none'}
    from .update_supersession import blocked
    return {**value,'state':'superseded'} if blocked(value,protocol='desktop',current_tree=tree) else value


def incoming_reference(new,*,create=False):
    from .program_state import root
    new=Path(new).resolve();canonical=root(new,create=create)/'incoming-job.json'
    legacy=new.parent/('update-job-ref-'+hashlib.sha256(str(new).encode()).hexdigest()[:16]+'.json')
    return legacy if not create and not canonical.exists() and legacy.is_file() else canonical


def bind_incoming(value,new):
    write(incoming_reference(new,create=True),{'id':value['id'],'path':str(path(value['tree']).resolve()),
        'program':value['program'],'incoming':str(Path(new).resolve())})


def producers_pending(value):
    """Current-job custody only; a failed observation is never terminal."""
    from .session_host import process_state
    observations={}
    if value.get('helper'):
        observations['helper']=process_state(value['helper'])
        if observations['helper'] not in ('absent','changed'):return True,observations
    if value.get('staging'):
        staging=Path(value['staging']).resolve();program=Path(value['program']).resolve()
        from .program_state import staging_belongs
        if not staging_belongs(program,staging):
            return True,{**observations,'staging':'identity-unknown'}
        record=read(staging/'Helene/code-transition-plan.json')
        for row in record.get('plans',[]):
            directory=Path(row['directory']);operation=read(directory/'pipeline.json')
            producer=read(directory/'coordinator-process.json')
            if producer:
                observed=process_state(producer);observations[directory.name]=observed
                if observed not in ('absent','changed'):return True,observations
            if operation.get('stop_confirmed'):continue
            if operation.get('task_id'):return True,{**observations,'merger':'settlement-unknown'}
            if operation.get('review_id'):
                store='.source-update-reviews' if (operation.get('coordinator') or {}).get('review_store')=='isolated-v1' else '.self-review'
                review=read(Path(operation['tree'])/'memory'/store/operation['review_id']/'request.json')
                terminal={'done','error','cancelled'}
                if review.get('execution') not in terminal or any(c.get('execution') not in terminal for c in review.get('critics',[])):
                    return True,{**observations,'review':'settlement-unknown'}
    return False,observations


def settle_cancel(tree,value):
    if value.get('state')=='attention' and (value.get('cancel_requested') or cancelled(tree,value.get('id',''))):
        pending,observations=producers_pending(value)
        value['cancel_observation']=observations
        if not pending:
            value.update(state='cancelled',cancel_requested=True,
                cancel_settlement={'at':time.time(),'producer_states':observations})
    return value


def _admission_recovery(program):
    from .python_source_updater import active_update
    if active_update(Path(program).resolve()).is_file():
        raise RuntimeError('An interrupted update requires startup recovery before new work')


def _save(tree, value):
    from .update_supersession import blocked
    if value.get('state')!='superseded' and blocked(value,protocol='desktop',current_tree=tree):
        raise RuntimeError('Programme replaced; superseded work cannot write progress')
    target = path(tree)
    value['updated'] = time.time()
    write(target, value)
    # Retain prior requests by identity; the current pointer is not a history DB.
    write(target.parent / 'desktop-update-jobs' / (value['id'] + '.json'), value)
    return value


def new_source_mode(mode=None,*,root=None):
    if mode not in (None,'preserve','clean'):
        raise ValueError('Choose preserve or clean source mode')
    if root is not None:
        from .authored_baseline import mode as eligible
        return eligible(root,mode)
    if mode=='preserve':raise ValueError('Preserve mode requires an identified programme and verified source baseline')
    return 'clean'


def request(tree, action, *, source_mode=None, version='latest', reason='', owner_words='',
            owner_spoke=False, root=None, by='agent', identity=''):
    from .python_updates import version as installed_version, parts
    if action == 'status':
        return status(tree)
    target = Path(root).resolve() if root is not None else program()
    if source_mode is not None and source_mode not in ('preserve', 'clean'):
        raise ValueError('Expected preserve or clean source mode')
    with transaction(path(tree)):
        previous = settle_cancel(tree,status(tree))
        if previous.get('state')=='cancelled':_save(tree,previous)
        if action == 'plan':
            _admission_recovery(target)
            if previous.get('state') in ACTIVE:
                raise ValueError('An update is already admitted; inspect or stop that exact job')
            if version != 'latest':
                parts(version)
            if owner_words and not owner_spoke:
                raise ValueError('Only a direct owner turn can consent to an update')
            mode = new_source_mode(source_mode,root=target)
            words = ' '.join(str(owner_words).split())
            # The mind interprets the direct owner's words. Code records exact
            # provenance and an explicitly selected mode, never a keyword vote.
            admitted = bool(words)
            value = {'format': 'helene-desktop-update-request.v2', 'id': uuid.uuid4().hex,
                'created': time.time(), 'program': str(target), 'tree': str(Path(tree).resolve()),
                'from_version': installed_version(target), 'version': version, 'source_mode': mode,
                'reason': str(reason), 'owner_words': words, 'phase': 'requested',
                'confirmed_by': 'owner-words' if admitted else '', 'state': 'confirmed' if admitted else 'awaiting'}
        elif action in ('confirm', 'decline'):
            if previous.get('state') != 'awaiting' or (identity and previous.get('id') != identity):
                raise ValueError('No matching pending update proposal')
            if previous.get('program') != str(target) or previous.get('from_version') != installed_version(target):
                raise ValueError('Installation changed; request a new update')
            if by != 'window' and (not owner_spoke or len(str(owner_words).strip()) < 2):
                raise ValueError('Direct owner confirmation is required')
            value = {**previous, 'state': 'confirmed' if action == 'confirm' else 'declined',
                     'confirmed_by': by, 'owner_words': str(owner_words), 'confirmed_at': time.time()}
            if action == 'confirm':
                mode=new_source_mode(previous.get('source_mode'),root=target)
                if source_mode is not None and new_source_mode(source_mode,root=target)!=mode:
                    raise ValueError('Source policy changed; request a new update')
                value['source_mode'] = mode
        elif action == 'cancel':
            if previous.get('id') != identity or previous.get('state') not in ACTIVE:
                raise ValueError('No matching active job')
            write(path(tree).parent / 'desktop-update-jobs' / (identity + '.cancel.json'),
                  {'id': identity, 'reason': reason or 'Owner stopped update', 'at': time.time()})
            value = {**previous, 'cancel_requested': True}
            settle_cancel(tree,value)
        elif action == 'resume':
            if previous.get('cancel_requested') or cancelled(tree,previous.get('id','')):
                raise ValueError('Cancelled jobs cannot be revived; preserve their result and request new work')
            if previous.get('id') != identity or previous.get('state') not in ('attention', 'paused'):
                raise ValueError('No matching resumable job')
            if previous.get('helper'):
                from .session_host import process_state
                if process_state(previous['helper']) not in ('absent','changed'):
                    raise ValueError('The admitted mechanical producer is live or unknown; inspect it instead of replaying')
            value = {**previous, 'state': 'confirmed', 'resume_requested': time.time()}
        else:
            raise ValueError('Expected status, plan, confirm, decline, cancel or resume')
        return _save(tree, value)


def cancelled(tree, identity):
    return (path(tree).parent / 'desktop-update-jobs' / (identity + '.cancel.json')).exists()


def checkpoint(owner, value, **changes):
    tree = Path(value['tree'])
    with transaction(path(tree)):
        current = status(tree)
        if current.get('id') != value['id']:
            raise RuntimeError('Update job identity changed; no effects admitted')
        from .update_supersession import blocked
        if blocked(value,protocol='desktop',current_tree=tree):raise RuntimeError('Programme replaced; this update is superseded')
        value.update(changes)
        if cancelled(tree, value['id']):
            value['cancel_requested'] = True
        _save(tree, value)
    return value


def admit_install(owner, path, forceExtensions=False, sourceMode=None, browser_mode=None):
    """UI returns only after durable admission bound to this process generation."""
    from .python_updates import version
    from .session_host import control_path, process_state
    from . import owner_stop
    if owner_stop.stopped():
        raise RuntimeError('Owner Stop forbids update admission')
    if forceExtensions:
        raise ValueError('Incompatible-extension override is obsolete; use ordinary Update after resolving compatibility')
    archive = Path(path).resolve(strict=True)
    _admission_recovery(owner.root)
    if archive.suffix.lower() != '.zip' or not archive.is_file():
        raise ValueError('Choose a verified Hélène update ZIP')
    mode = new_source_mode(sourceMode,root=owner.root)
    if mode not in ('preserve', 'clean'):
        raise ValueError('Choose preserve or clean source mode')
    descriptor = read(control_path(owner.root))
    if not getattr(owner, 'session_host', False) or descriptor.get('pid') != os.getpid() or process_state(descriptor) != 'alive':
        raise RuntimeError('Update requires a confirmed persistent session owner')
    tree = owner.current().tree
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    with transaction(globals()['path'](tree)):
        previous = settle_cancel(tree,status(tree))
        if previous.get('state')=='cancelled':_save(tree,previous)
        if previous.get('state') in ACTIVE:
            if (previous.get('archive') == str(archive) and previous.get('archive_sha256') == digest
                    and previous.get('source_mode') == mode):
                return {'state': 'admitted', 'job': previous, 'reused': True}
            raise ValueError('This agent already owns an update job; inspect or stop it first')
        value = {'format': 'helene-desktop-update-request.v2', 'id': uuid.uuid4().hex,
            'kind': 'install', 'created': time.time(), 'program': str(owner.root.resolve()),
            'tree': str(tree.resolve()), 'agent_id': owner.current().id, 'from_version': version(owner.root),
            'archive': str(archive), 'archive_sha256': digest, 'source_mode': mode,
            'force_extensions': bool(forceExtensions), 'state': 'confirmed', 'phase': 'downloaded',
            'browser_mode': bool(getattr(owner,'browser_mode',False) if browser_mode is None else browser_mode),
            'confirmed_by': 'window', 'admission': {k: descriptor[k] for k in ('pid', 'birth', 'generation')}}
        _save(tree, value)
    return {'state': 'admitted', 'job': value, 'observer_may_close': True}


def admit_download(owner, url, sha256=None, sourceMode=None, browser_mode=None):
    """The ordinary Update click admits BEFORE network/extraction/model work."""
    from .python_updates import version, secure_url
    from .session_host import control_path, process_state
    from . import owner_stop
    if owner_stop.stopped():
        raise RuntimeError('Owner Stop forbids update admission')
    secure_url(url)
    if not str(url).startswith('https://'):
        raise ValueError('Update archives require HTTPS')
    if sha256 and not re.fullmatch('[a-fA-F0-9]{64}', sha256):
        raise ValueError('Invalid update SHA256')
    mode = new_source_mode(sourceMode,root=owner.root)
    if mode not in ('preserve','clean'):
        raise ValueError('Choose preserve or clean source mode')
    descriptor=read(control_path(owner.root))
    _admission_recovery(owner.root)
    if not getattr(owner,'session_host',False) or descriptor.get('pid')!=os.getpid() or process_state(descriptor)!='alive':
        raise RuntimeError('Update requires a confirmed persistent session owner')
    tree=owner.current().tree
    with transaction(path(tree)):
        previous=settle_cancel(tree,status(tree))
        if previous.get('state')=='cancelled':_save(tree,previous)
        if previous.get('state') in ACTIVE:
            if previous.get('download_url')==url and previous.get('source_mode')==mode and previous.get('download_sha256')==(sha256 or ''):
                return {'state':'admitted','job':previous,'reused':True,'observer_may_close':True}
            raise ValueError('This agent already owns an update job')
        value={'format':'helene-desktop-update-request.v2','id':uuid.uuid4().hex,'kind':'download',
            'created':time.time(),'program':str(owner.root.resolve()),'tree':str(tree.resolve()),
            'agent_id':owner.current().id,'from_version':version(owner.root),'source_mode':mode,
            'browser_mode':bool(getattr(owner,'browser_mode',False) if browser_mode is None else browser_mode),
            'download_url':url,'download_sha256':sha256 or '', 'state':'confirmed','phase':'requested',
            'confirmed_by':'window','admission':{key:descriptor[key] for key in ('pid','birth','generation')}}
        _save(tree,value)
    return {'state':'admitted','job':value,'observer_may_close':True}


def admit_restore(owner, confirmed=False, browser_mode=None):
    """Admit the same clean installation work before any slow local staging."""
    from . import upstream_recovery
    from .python_updates import version
    from .session_host import control_path, process_state
    if confirmed is not True:
        raise ValueError('Подтверди возврат к upstream-коду')
    upstream_recovery._require_owner(owner)
    if not upstream_recovery.modern(owner.root):
        raise ValueError('This restore admission requires the current portable session')
    descriptor=read(control_path(owner.root))
    if (not getattr(owner,'session_host',False) or descriptor.get('pid')!=os.getpid()
            or process_state(descriptor)!='alive'):
        raise RuntimeError('Restore requires a confirmed persistent session owner')
    _admission_recovery(owner.root)
    agent=owner.current();tree=Path(agent.tree).resolve()
    if not getattr(agent,'enabled',True) or getattr(agent,'conflict',None):
        raise ValueError('Selected agent is not available for update work')
    root=str(Path(owner.root).resolve());installed=version(owner.root)
    with transaction(path(tree)):
        previous=settle_cancel(tree,status(tree))
        if previous.get('state')=='cancelled':_save(tree,previous)
        if previous.get('state') in ACTIVE:
            if (previous.get('program')==root and previous.get('tree')==str(tree)
                    and previous.get('kind')=='install' and previous.get('operation')=='restore_upstream'
                    and previous.get('source_mode')=='clean' and previous.get('from_version')==installed):
                value=previous;reused=True
            else:
                raise ValueError('This agent already owns an update job; inspect or stop it first')
        else:
            value={'format':'helene-desktop-update-request.v2','id':uuid.uuid4().hex,
                'kind':'install','operation':'restore_upstream','created':time.time(),
                'program':root,'tree':str(tree),'agent_id':agent.id,'from_version':installed,
                'source_mode':'clean','state':'confirmed','phase':'requested','confirmed_by':'window',
                'browser_mode':bool(getattr(owner,'browser_mode',False) if browser_mode is None else browser_mode),
                'admission':{key:descriptor[key] for key in ('pid','birth','generation')}}
            _save(tree,value);reused=False
    return {'state':'admitted','job':value,'reused':reused,'operation_id':value['id'],
        'status':str(path(tree)),'observer_may_close':True}


def _run(owner, value):
    from . import owner_stop, python_updates as updater
    from .session_host import process_state
    tree = Path(value['tree'])
    def stopped():
        return (getattr(owner,'exiting',False) or owner.stop.is_set()
                or owner_stop.stopped() or cancelled(tree, value['id']))
    if stopped():
        checkpoint(owner, value, state='cancelled', cancel_requested=True)
        return
    prior = value.get('execution_owner')
    current_owner = {'pid': os.getpid(), 'birth': __import__('psutil').Process().create_time()}
    if value.get('state') == 'running' and prior != current_owner:
        observed = process_state(prior or {})
        if observed not in ('absent', 'changed'):
            raise RuntimeError('Earlier update producer is live or unknown; rejoin it before another execution')
        if value.get('phase') not in ('downloaded', 'staged', 'preparing'):
            raise RuntimeError('Interrupted effect outcome requires observation; it is not replayed automatically')
    checkpoint(owner, value, state='running', execution_owner=current_owner)
    owner.update_job = value
    owner.update_cancelled = stopped
    if value.get('operation')=='restore_upstream':
        try:
            if value.get('kind')!='install' or value.get('source_mode')!='clean':
                raise ValueError('Restore job is not an admitted clean installation')
            from .upstream_recovery import restore_owned
            result=restore_owned(owner,operation=value['id'])
            checkpoint(owner,value,state='handed_off',result=result,phase='handoff')
        finally:
            owner.update_job=None
            owner.update_cancelled=None
        return
    if not value.get('archive'):
        checkpoint(owner, value, phase='downloading')
        if value.get('download_url'):
            download_url, download_sha = value['download_url'], value.get('download_sha256')
        else:
            tag = value['version']
            url = updater.release_endpoint(owner, tag)
            release = updater.update_check(owner, url)
            if not release['newer']:
                raise ValueError('Requested release is not newer than the installed version')
            download_url, download_sha = release['url'], release['sha256']
        downloaded = updater.update_download(owner, download_url, download_sha)
        checkpoint(owner, value, phase='downloaded', archive=downloaded['path'], archive_sha256=downloaded['sha256'])
    with Path(value['archive']).open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != value['archive_sha256']:
            raise ValueError('Admitted archive changed')
    if stopped():
        raise InterruptedError('Owner stopped update before preparation')
    owner.update_job = value
    owner.update_cancelled = stopped
    try:
        result = updater.update_install(owner, value['archive'], forceExtensions=value.get('force_extensions', False),
                                        sourceMode=value['source_mode'])
        checkpoint(owner, value, state='handed_off', result=result, phase='handoff')
    finally:
        owner.update_job = None
        owner.update_cancelled = None


def consume(owner):
    """Reconcile existing work under the session host; never revive cancellation."""
    from . import agents, owner_stop, python_updates as updater
    if (getattr(owner,'exiting',False) or owner.stop.is_set()
            or owner_stop.stopped() or getattr(owner,'bootstrap',False)):
        return
    for agent in agents.roster(owner.root):
        if not agent.enabled or agent.conflict:
            continue
        value = status(agent.tree)
        if value.get('state')=='attention' and (value.get('cancel_requested') or cancelled(agent.tree,value.get('id',''))):
            prior=dict(value);settle_cancel(agent.tree,value)
            if value!=prior:_save(agent.tree,value)
        if value.get('kind')=='migration' or value.get('state') not in ('confirmed', 'running'):
            continue
        if value.get('program') != str(owner.root.resolve()) or value.get('tree') != str(agent.tree.resolve()):
            continue
        if value.get('from_version') != updater.version(owner.root):
            checkpoint(owner, value, state='superseded', error='Installation changed; prior work retained')
            continue
        try:
            with owner.selection(agent.id):
                _run(owner, value)
        except Exception as exc:
            pending,observation=producers_pending(value)
            state = 'cancelled' if cancelled(agent.tree,value['id']) and not pending else 'attention'
            checkpoint(owner, value, state=state, error=f'{type(exc).__name__}: {exc}')
        finally:
            owner.update_job = None
            owner.update_cancelled = None
        owner.event('desktop-update-request', {'id': value['id'], 'state': value['state'], 'source_mode': value['source_mode']})


def watch(owner, stop):
    while not stop.wait(.5):
        try:
            consume(owner)
        except Exception as exc:
            owner.event('desktop-update-request-error', {'error': str(exc)})
