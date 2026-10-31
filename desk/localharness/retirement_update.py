"""Sealed completion custody for an updater that may die after launching NEW.

The original updater and the session-owned recovery worker share one lease.
Recovery never rediscovers deletion targets or treats elapsed wall time as health.
"""
from contextlib import contextmanager
from pathlib import Path
import copy,datetime as dt,hashlib,os,time
from .package_notice import read,write

FORMAT='helene-retirement-update.v1'


def prepared_material(value):
    result=copy.deepcopy(value)
    for key in ('retirement','_retirement_started'):result.pop(key,None)
    return result


def _self():
    import psutil
    return {'pid':os.getpid(),'birth':psutil.Process().create_time()}


def _file(path,*,content=True):
    from .first_start import plain
    path=plain(path)
    if not path.is_file():raise ValueError('Update scratch is not a regular file')
    stamp=path.stat()
    result={'path':str(path),'file':[stamp.st_dev,stamp.st_ino]}
    if content:result['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _job(args,value):
    if not getattr(args,'job',None):return None
    from . import desktop_update_request as jobs
    from .first_start import plain
    target=plain(args.job);record=read(target)
    if (record.get('id')!=args.job_id or record.get('program')!=str(Path(args.old).resolve())
            or jobs.path(record.get('tree','')).resolve()!=target
            or Path(record['tree']).resolve() not in [Path(p).resolve() for p in value['note']['data_paths']]):
        raise ValueError('Update completion belongs to another durable job')
    return {key:record[key] for key in ('id','program','tree','kind')}|{'path':str(target)}


@contextmanager
def owner(args,value):
    """Admit exact post-trial work before NEW opens, hold through all cleanup."""
    from . import portable_retirement as retirement,portable_switch as switch
    from .first_start import Places,_write
    from .python_source_updater import prepared_install_path
    program=Path(args.new).resolve();journal=read(retirement.path(program))
    retirement.validate(program,journal,Places.current())
    if journal['mode']!='update':raise ValueError('Update owner received an archive retirement')
    paths=switch.scratch_paths(args,value)
    # The prepared value refers to the seal, so its material excludes that one
    # recursive reference. All selection, payload, note and scratch facts remain.
    context={'format':FORMAT,'prepared':prepared_material(value),'producer':_self(),
        'args':{'old':str(Path(args.old).resolve()),'new':str(program),'staging':str(Path(args.staging).resolve()),
                'admission_name':args.admission_name},'job':_job(args,value),
        'files':[_file(p,content=p.name!='update.log') for p in paths[:-1] if p.exists()]}
    with retirement.lease(program,journal['id'],timeout_ms=250) as acquired:
        if not acquired:raise RuntimeError('Another helper owns this retirement')
        existing=journal.get('completion')
        if existing and existing!=context:raise ValueError('Update completion custody was already admitted')
        # The admitted transaction finalizes its own exact job/work. Supersede
        # unrelated captured old jobs, never the producer performing this switch.
        if journal.get('supersession'):
            from . import update_supersession
            current_directories={str(Path(row['directory']).resolve()) for row in read(program/'code-transition-plan.json').get('plans',[])}
            job=context.get('job')
            journal['supersession']['records']=[row for row in journal['supersession']['records']
                if not (row['protocol']=='source' and str(Path(row['directory']).resolve()) in current_directories
                        and row['program']==str(Path(args.old).resolve()))
                and not (job and row['protocol']=='desktop' and all(row.get(key)==job[key] for key in ('id','program','tree')))]
            journal['supersession']['sha256']=update_supersession.fingerprint(journal['supersession'])
        journal['completion']=context;journal['owner']=context['producer']
        _write(retirement.path(program),journal,private=True);retirement.seal(program,journal)
        value['retirement']={'id':journal['id'],'sha256':retirement.authority(journal)}
        write(prepared_install_path(program),value)
        yield
        finished=read(retirement.path(program))
        if retirement.authority(finished)!=retirement.authority(journal):raise ValueError('Retirement completion authority changed')
        _require_removed(program,finished)
        finished['finalized_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        _write(retirement.path(program),finished,private=True)


def _require_removed(program,journal):
    targets={row['root'] for row in journal['targets']}
    if (journal.get('state')!='completed' or set(journal.get('removed',[]))!=targets
            or any(Path(root).exists() for root in targets)):
        raise ValueError('Captured former programmes have not all been retired')


def cancelled(journal):
    job=(journal.get('completion') or {}).get('job')
    if not job:return False
    from . import desktop_update_request as jobs
    current=read(job['path'])
    return (jobs.cancelled(job['tree'],job['id']) or
        (all(current.get(key)==job[key] for key in ('id','program','tree','kind')) and
         (current.get('cancel_requested') is True or current.get('state') in ('cancelled','superseded'))))


def _context(program,journal):
    from . import portable_switch as switch,portable_retirement as retirement
    from .first_start import plain
    program=plain(program);context=journal.get('completion') or {}
    if context.get('format')!=FORMAT:raise ValueError('Update completion custody is missing')
    value=copy.deepcopy(context['prepared']);arguments=context['args']
    value['retirement']={'id':journal['id'],'sha256':retirement.authority(journal)}
    old=plain(arguments['old'])
    switch.identity(old,program,value)
    if (plain(arguments['new'])!=program or plain(arguments['staging'])!=program.parent
            or switch.scratch_scope(old,program)!=value.get('scratch')):
        raise ValueError('Recovery scratch leaves its admitted staging directory')
    from .program_state import _identity
    import re
    _identity(value['note']['id'])
    if not re.fullmatch(r'updater-admitted(?:-[0-9a-f]{32})?\.json',arguments['admission_name']):
        raise ValueError('Recovery updater admission filename differs')
    allowed={program.parent/name for name in ('python_source_updater.py','update.log',arguments['admission_name'])}
    seen=set()
    for row in context['files']:
        target=plain(row['path'])
        if target not in allowed or target in seen:raise ValueError('Recovery scratch target was not admitted')
        seen.add(target)
        if target.exists() and _file(target,content='sha256' in row)!=row:
            raise ValueError('Recovery scratch file was replaced')
    from .python_source_updater import prepared_install_path
    prepared=prepared_install_path(program)
    if prepared.exists():
        saved=read(prepared)
        if prepared_material(saved)!=context['prepared'] or saved.get('retirement')!=value['retirement']:
            raise ValueError('Prepared update was replaced before recovery')
    if read(program/'python-migration.json').get('id')!=value['note']['id']:
        raise ValueError('Migration receipt changed before completion')
    job=context.get('job')
    if job:
        from . import desktop_update_request as jobs
        if (plain(job['path'])!=jobs.path(job['tree']).resolve() or job['program']!=str(old)
                or plain(job['tree']) not in [plain(p) for p in value['note']['data_paths']]):
            raise ValueError('Recovered durable job leaves admitted data')
    return context,value


def finalize(program,journal,*,backend=None,observe=None):
    """Only after all old targets are gone; safe after any partial finalization."""
    from . import portable_retirement as retirement,portable_switch as switch,session_host,owner_stop,session_exit
    from .first_start import Places,plain,_write,receipt
    from .python_source_updater import prepared_install_path
    program=plain(program)
    if owner_stop.stopped() or session_exit.requested(program):raise InterruptedError('Owner stopped completion')
    retirement.validate(program,journal,Places.current());_require_removed(program,journal)
    context,value=_context(program,journal)
    state=(observe or session_host.process_state)(context['producer'])
    if state not in ('absent','changed'):raise RuntimeError('Previous updater exit is not confirmed')
    if journal.get('finalized_at'):return journal
    if backend is None:
        from .windows_first_start import Backend
        backend=Backend(program)
    backend.excluded=[program];backend.process_excluded=[program]
    switch.cleanup_work(program,value)
    for row in context['files']:
        target=plain(row['path'])
        if target.exists():
            # Recheck immediately before each effect, including inode identity.
            if _file(target,content='sha256' in row)!=row:raise ValueError('Recovery scratch changed before cleanup')
            backend.roots=[target];backend.remove(target)
    prepared=prepared_install_path(program)
    if prepared.exists():
        saved=read(prepared)
        if prepared_material(saved)!=context['prepared']:raise ValueError('Prepared update changed before cleanup')
        backend.roots=[prepared];backend.remove(prepared)
    completed=read(receipt(program));completed['update']={'id':value['note']['id'],
        'confirmed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'healthy_seconds':switch.TRIAL_SECONDS}
    _write(receipt(program),completed,private=True)
    note=read(program/'python-migration.json')
    if note.get('id')!=value['note']['id']:raise ValueError('Migration receipt changed before completion')
    note.update(state='completed',healthy_seconds=switch.TRIAL_SECONDS);write(program/'python-migration.json',note)
    job=context.get('job')
    if job:
        from . import desktop_update_request as jobs
        from .config_transaction import transaction
        # Complete the task only after filesystem work. This keeps a new update
        # from being admitted while its predecessor still writes receipts.
        with transaction(Path(job['path'])):
            current=read(job['path'])
            if all(current.get(key)==job[key] for key in ('id','program','tree','kind')):
                was_cancelled=cancelled(journal)
                current.update(state='cancelled' if was_cancelled else 'completed',phase='completed',terminal_at=time.time(),
                    result={'installed_root':str(program),'effect':'committed','healthy_seconds':switch.TRIAL_SECONDS})
                jobs._save(job['tree'],current)
    journal['finalized_at']=dt.datetime.now(dt.timezone.utc).isoformat()
    _write(retirement.path(program),journal,private=True)
    return journal
