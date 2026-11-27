"""Server-native frozen review and A/L/B/M candidates; never applies live code.

Review evidence and author disposition are independent records. A critic's
findings never become a hidden permission gate for ordinary Praxis work.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
import threading

from catalog_families import PreflightRefusal
import forge
import forge_runtime as runtime

EXCLUDED={'.git','memory','soul','workspace','secrets','.secrets','credentials','sessions','logs',
          'backups','node_modules','target','__pycache__','.venv','venv','.state','.pytest_cache','.mypy_cache','.ruff_cache'}
SIDES={'A','L','B','M','candidate','evidence'}
# A private-key header counts only with key material after it (64 base64 characters,
# wrapped lines and escaped newlines allowed). Credential-floor tests carry the bare
# header as a fixture; matching it refused every complete-source review (20.11).
SECRET=re.compile(rb'(?:sk-[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{32,}'
                  rb'|-----BEGIN [A-Z ]*PRIVATE KEY-----(?:(?:\s|\\[nr])*[A-Za-z0-9+/=]){64})')
_OBSERVED={}
_TICK_LOCK=threading.Lock()


class _InvalidReviewInput(ValueError):
    """Deterministic validation failure; not by itself proof of no effect."""


@contextmanager
def _preflight(*, enabled=True):
    """Translate only explicit validation failures in a pre-write region."""
    try:
        yield
    except _InvalidReviewInput as exc:
        if not enabled:
            raise
        raise PreflightRefusal(str(exc)) from exc


@contextmanager
def _admission_preflight(task_id, refusal=''):
    # Early fences precede migration; once migration can write, even a
    # deterministic validation error cannot establish an effect-free refusal.
    write_possible=False

    def mark_policy_write():
        nonlocal write_possible
        write_possible=True

    try:
        reason=forge._admission_reason(task_id,before_policy_write=mark_policy_write)
        if reason:raise _InvalidReviewInput(refusal or reason)
        yield
    except _InvalidReviewInput as exc:
        if write_possible:
            raise
        raise PreflightRefusal(str(exc)) from exc


def digest(raw):return hashlib.sha256(raw).hexdigest()
def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def _root_identity(root):
    p=Path(root)
    if p.is_symlink():raise _InvalidReviewInput('review source root was redirected')
    stat=p.stat()
    return {'device':stat.st_dev,'inode':stat.st_ino}


def _input_root(task_id,root):
    source=Path(root)
    if source.is_symlink():raise _InvalidReviewInput('linked review root is not a source snapshot')
    source=source.resolve()
    if any(part in {'secrets','.secrets','credentials','sessions'} for part in source.parts):
        raise _InvalidReviewInput('private source root excluded')
    protected=any(source.is_relative_to((forge.BASE/name).resolve()) for name in ('memory','soul'))
    if protected:
        own=(forge._task_dir(task_id)/'reviews').resolve()
        relative=source.relative_to(own) if source.is_relative_to(own) else None
        if relative is None or len(relative.parts)!=2 or relative.parts[1] not in SIDES:
            raise _InvalidReviewInput('private/runtime memory is not a review source')
        directory,record=_record(task_id,relative.parts[0])
        if record['kind']=='merge':validate_merge(directory,record['input_sha256'])
        else:validate_snapshot(directory,record['subject_sha256'])
    if not source.is_dir():raise _InvalidReviewInput('review source root is missing or not a directory')
    _root_identity(source)
    return source


def inside(root,path):
    base=Path(root).resolve()
    value=base/str(path)
    if value.is_symlink() or not value.resolve().is_relative_to(base):raise _InvalidReviewInput('review path escaped snapshot')
    return value


def inventory(root,paths=None):
    root=Path(root).resolve()
    if not root.is_dir():raise _InvalidReviewInput('review source root is missing')
    selected=set(paths) if paths is not None else None
    rows={}
    for directory,dirs,files in os.walk(root,followlinks=False):
        if any((Path(directory)/n).is_symlink() for n in dirs if n not in EXCLUDED):
            raise _InvalidReviewInput('linked source directory cannot be silently omitted')
        dirs[:]=sorted(n for n in dirs if n not in EXCLUDED and not (Path(directory)/n).is_symlink())
        for name in sorted(files):
            p=Path(directory)/name
            rel=p.relative_to(root).as_posix()
            if selected is not None and rel not in selected:continue
            if name.startswith('.env') or name.lower().endswith(('.key','.pem','.session')):continue
            if p.is_symlink():raise _InvalidReviewInput(f'symlink in review input: {rel}')
            data=p.read_bytes()
            if SECRET.search(data):raise _InvalidReviewInput(f'possible credential in review source: {rel}')
            rows[rel]={'sha256':digest(data),'size':len(data),'mode':p.stat().st_mode&0o777,
                       'exists':True}
    if selected is not None:
        for name in selected:
            inside(root,name)
            if any(part in EXCLUDED for part in Path(name).parts):raise _InvalidReviewInput('private review input excluded')
            if name not in rows:rows[name]={'exists':False,'sha256':None,'size':0,'mode':None}
    return rows


def _task_directory(task_id):
    task=forge._task_dir(task_id)
    if task.parent!=forge.TASKS_DIR or task.is_symlink() or not task.resolve().is_relative_to(forge.TASKS_DIR.resolve()):
        raise _InvalidReviewInput('review task root redirected')
    if not forge.get(task_id):raise _InvalidReviewInput(f'нет coding-задачи {task_id}')
    if (task/'reviews').is_symlink():raise _InvalidReviewInput('review directory redirected')
    return task


@contextmanager
def _review_lock(task_id):
    # Root-mutex files and lock leases are bookkeeping for existing tasks;
    # unlike admission-policy migration, they don't change review/task state.
    with _preflight():
        _task_directory(task_id)
    with forge._mutation_lock(task_id,create_task_dir=False):
        yield


def _directory(task_id,review_id):
    if not isinstance(review_id,str) or not re.fullmatch(r'(?:review|merge)-[0-9a-f]{16}',review_id):raise _InvalidReviewInput('invalid review id')
    return inside(_task_directory(task_id)/'reviews',review_id)


def _record(task_id,review_id):
    directory=_directory(task_id,review_id)
    record=runtime._read(directory/'record.json')
    if not isinstance(record,dict) or record.get('task_id')!=task_id or record.get('id')!=review_id:
        raise _InvalidReviewInput('review record identity mismatch')
    if record.get('kind')!=review_id.split('-',1)[0]:
        raise _InvalidReviewInput('review record kind does not match its id')
    return directory,record


def _require_kind(record,kind):
    if record['kind']!=kind:raise _InvalidReviewInput(f'expected a {kind} id')


def _review_manifests(roots,paths=None):
    return {side:inventory(root,None if side=='evidence' else paths) for side,root in roots.items()}


def _freeze(directory,roots,manifests):
    # After mkdir even an explicit validation failure can leave partial bytes.
    directory.mkdir(parents=True)
    for side,rows in manifests.items():
        if side not in SIDES:raise ValueError('unsupported review side')
        (directory/side).mkdir(parents=True,exist_ok=True)
        for name,row in rows.items():
            if not row['exists']:continue
            src=inside(roots[side],name)
            data=src.read_bytes()
            if digest(data)!=row['sha256']:raise ValueError('review input changed during freeze')
            dest=inside(directory/side,name)
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(data);dest.chmod(row['mode'])


def validate_snapshot(directory,expected):
    directory=Path(directory)
    raw=(directory/'subject.json').read_bytes()
    if digest(raw)!=expected:raise _InvalidReviewInput('review subject identity changed')
    subject=json.loads(raw)
    for side,rows in subject['manifests'].items():
        for name,row in rows.items():
            p=inside(directory/side,name)
            if row['exists']:
                if not p.is_file() or digest(p.read_bytes())!=row['sha256'] or p.stat().st_mode&0o777!=row['mode']:
                    raise _InvalidReviewInput(f'frozen review source changed: {side}/{name}')
            elif p.exists():raise _InvalidReviewInput('frozen absence changed')
    return subject


def _prepare_inputs(task_id,root,previous_review_id,merge_id,review_id):
    directory=_directory(task_id,review_id)
    roots={'candidate':_input_root(task_id,root)}
    prior=None
    verification={'state':'not_run','qualified':False}
    verification_source=None
    if previous_review_id:
        prior=status(task_id,previous_review_id)
        verification_source=previous_review_id
    if merge_id:
        merged,operation=_record(task_id,merge_id)
        if not operation.get('finished'):raise _InvalidReviewInput('merger must finish an exact candidate before review')
        validate_merge(merged,operation['input_sha256'])
        if candidate_signature(merged)!=operation['candidate_sha256']:raise _InvalidReviewInput('merged candidate changed')
        roots={side:merged/side for side in ('A','L','B','M')}
        verification_source=merge_id
    if verification_source:
        import forge_battery
        tested=forge_battery.status(task_id,verification_source)
        current=roots.get('M',roots.get('candidate'))
        if tested['qualified'] and tested['candidate_sha256']==digest(encoded(inventory(current))):
            proof,_=_record(task_id,verification_source)
            roots['evidence']=proof/'verification/evidence'
            verification={k:tested.get(k) for k in ('state','qualified','candidate_sha256','report')}
            verification['source_review_id']=verification_source
    return directory,roots,prior,verification


def prepare(task_id,*,criteria='',previous_review_id='',paths=None,merge_id=''):
    task,root,error=forge._task_root(task_id)
    if error:raise PreflightRefusal(error)
    with _review_lock(task_id):
        review_id='review-'+uuid.uuid4().hex[:16]
        with _preflight():
            for identifier,kind in ((previous_review_id,'review'),(merge_id,'merge')):
                if identifier:_require_kind(_record(task_id,identifier)[1],kind)
        with _admission_preflight(task_id):
            directory,roots,prior,verification=_prepare_inputs(task_id,root,previous_review_id,merge_id,review_id)
            manifests=_review_manifests(roots,paths)
        _freeze(directory,roots,manifests)
        subject={'schema':1,'task_id':task_id,'id':review_id,'criteria':criteria,
                 'scope':'selected paths' if paths is not None else 'complete nonprivate source roots',
                 'manifests':manifests,'previous_review':prior,'merge_id':merge_id,
                 'verification':verification}
        raw=encoded(subject)
        (directory/'subject.json').write_bytes(raw)
        record={'schema':1,'id':review_id,'task_id':task_id,'subject_sha256':digest(raw),
                'created':forge._now(),'criteria':criteria,'units':[],'review_task_id':'',
                'kind':'review','previous_review_id':previous_review_id,'merge_id':merge_id,
                'sources':{s:str(p) for s,p in roots.items()},'selected_paths':paths,
                'source_identities':{s:_root_identity(p) for s,p in roots.items()}}
        runtime.atomic_json(directory/'record.json',record)
    return status(task_id,review_id)


def start(task_id,review_id,*,critics=1,max_iters=0,model=''):
    if type(critics) is not int or not 1<=critics<=8:raise PreflightRefusal('explicit critic count must be 1..8')
    with _preflight():
        _require_kind(_record(task_id,review_id)[1],'review')
    with _review_lock(task_id):
        with _preflight():
            directory,record=_record(task_id,review_id)
            _require_kind(record,'review')
        with _admission_preflight(task_id,'task control forbids review start'):
            validate_snapshot(directory,record['subject_sha256'])
        if record['units']:return status(task_id,review_id)
        tid='code-'+uuid.uuid4().hex[:8]
        script=str(Path(__file__).with_name('forge_review_worker.py'))
        bound=forge.self_review_task(tid,str(directory),record['subject_sha256'],script,max_active_workers=critics)
        bound.update(coordinator_task_id=task_id,coordinator_review_id=review_id)
        forge._save_task(bound)
        # Publish the bound review task before spawn. A crash never invents a
        # fresh set of critics; readback retains unknown/partial admission.
        record['review_task_id']=tid
        record['units']=[{'id':'agent-'+uuid.uuid4().hex[:8],'status':'reserved'} for _ in range(critics)]
        runtime.atomic_json(directory/'record.json',record)
    for index,unit in enumerate(record['units']):
        result=forge.agent(tid,'spawn',agent_id=unit['id'],role='reviewer',max_iters=max_iters,
                           model=model,brief=('Integration review. ' if index==0 else 'Adversarial independent review. ')
                           +'Review frozen subject '+record['subject_sha256'])
        # After reservation, do not translate even validation exceptions.
        with forge._mutation_lock(task_id,create_task_dir=False):
            _,current=_record(task_id,review_id)
            if current['review_task_id']!=tid:raise ValueError('review reservation identity changed')
            actual=next(row for row in current['units'] if row['id']==unit['id'])
            actual['admission']=result
            actual['status']='admitted' if (forge._unit_dir(tid,'agents',unit['id'])/'request.json').exists() else 'unknown'
            runtime.atomic_json(directory/'record.json',current)
    return status(task_id,review_id)


def status(task_id,review_id):
    directory,record=_record(task_id,review_id)
    if record['kind']=='merge':
        stale=stale_merge_inputs(record)
        return {**record,'candidate_sha256':candidate_signature(directory),'stale_inputs':stale,
                'current_eligibility':'stale' if stale else 'current'}
    subject=validate_snapshot(directory,record['subject_sha256'])
    verdicts=[]
    expected={f'{side}/{name}' for side,rows in subject['manifests'].items() for name,row in rows.items() if row['exists']}
    for unit in record['units']:
        where=forge._unit_dir(record['review_task_id'],'agents',unit['id'])
        result=runtime._read(where/'result.json',{})
        execution=result.get('status')
        if not execution:
            execution=forge._unit_state(where).get('status','unknown')
        value={'unit_id':unit['id'],'execution':execution,
               'result_sha256':forge._result_identity(where/'result.json'),'verdict':'insufficient'}
        if result.get('status')=='done':
            try:
                report=json.loads(result.get('result',''))
                coverage=set(report.get('coverage',[]))
                reads=runtime._read(where/'review-reads.json',{})
                read_complete={name for name,row in reads.items() if row.get('complete')}
                if report.get('subject_sha256')!=record['subject_sha256']:raise ValueError('critic subject mismatch')
                if report.get('verdict') not in {'clear','findings','insufficient'}:raise ValueError('invalid critic verdict')
                if report.get('verdict')=='clear' and (expected-coverage or expected-read_complete):
                    raise ValueError('clear report lacks complete source read coverage')
                value.update(report=report,verdict=report['verdict'])
            except (ValueError,TypeError) as exc:value['error']=str(exc)
        verdicts.append(value)
    outcome=('findings' if any(v['verdict']=='findings' for v in verdicts) else
             'clear' if verdicts and all(v['verdict']=='clear' for v in verdicts) else 'insufficient')
    stale=[]
    for side,source in record.get('sources',{}).items():
        try:
            if _root_identity(source)!=record.get('source_identities',{}).get(side):
                stale.append(side);continue
            if inventory(source,None if side=='evidence' else record.get('selected_paths'))!=subject['manifests'][side]:stale.append(side)
        except (OSError,ValueError):stale.append(side)
    if record.get('merge_id'):
        _,merge_record=_record(task_id,record['merge_id'])
        stale.extend('merge/'+s for s in stale_merge_inputs(merge_record))
    if subject.get('verification',{}).get('qualified'):
        import forge_battery
        if not forge_battery.status(task_id,subject['verification']['source_review_id'])['qualified']:
            stale.append('verification')
    return {**record,'verdict':outcome,'critics':verdicts,'scope':subject['scope'],
            'expected_files':len(expected),'stale_sources':stale,'current_eligibility':'stale' if stale else 'current',
            'verification':subject.get('verification',{'state':'not_run','qualified':False}),
            'tested_and_reviewed':bool(subject.get('verification',{}).get('qualified') and outcome=='clear' and not stale),
            'author_disposition':runtime._read(directory/'author-disposition.json',None)}


def reviews(task_id):
    """Name every review and merge of a task, newest first; ``status`` needs no prior id."""
    folder=_task_directory(task_id)/'reviews'
    rows=[];incomplete=[]
    for directory in (sorted(folder.iterdir()) if folder.is_dir() else []):
        if not directory.is_dir() or not re.fullmatch(r'(?:review|merge)-[0-9a-f]{16}',directory.name):continue
        if not (directory/'record.json').exists():
            incomplete.append(directory.name);continue
        try:
            value=status(task_id,directory.name)
            row={'id':directory.name,'kind':value.get('kind'),'created':value.get('created'),
                 'verdict':value.get('verdict'),'finished':value.get('finished'),
                 'current_eligibility':value.get('current_eligibility')}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            row={'id':directory.name,'error':f'{type(exc).__name__}: {exc}'}
        rows.append({k:v for k,v in row.items() if v is not None})
    rows.sort(key=lambda row:str(row.get('created','')),reverse=True)
    return {'task_id':task_id,'reviews':rows,'incomplete_directories':incomplete,
            'hint':'pass review_id for the full record of one review or merge'}


def _decision_signature(run_id, call_id, task_id, review_id, value):
    if not all(isinstance(part, str) and part for part in (run_id, call_id)):
        return None
    return digest(encoded(["praxis.review-decision.v1", run_id, call_id, task_id, review_id,
                           value["subject_sha256"], value["decision"], value["reason"],
                           value["critic_evidence_sha256"]]))


def decision_receipt(run_id, call_id, args):
    """Prove only the disposition written by this exact durable tool call."""
    try:
        task_id, review_id = args["task_id"], args["review_id"]
        directory, record = _record(task_id, review_id)
        path = directory / "author-disposition.json"
        if path.is_symlink() or not path.is_file():
            return None
        value = runtime._read(path)
        if (not isinstance(value, dict) or not isinstance(value.get("source_call_sha256"), str)
                or record["subject_sha256"] != args["expected_subject_sha256"]
                or value["subject_sha256"] != record["subject_sha256"]
                or value["decision"] != args["decision"] or value["reason"] != args["reason"]):
            return None
        signature = _decision_signature(run_id, call_id, task_id, review_id, value)
        if not signature or value["source_call_sha256"] != signature:
            return None
        return dict(forge_task_id=task_id, review_id=review_id, decision=value["decision"],
                    subject_sha256=value["subject_sha256"], source_call_sha256=signature)
    except (KeyError, OSError, ValueError, TypeError):
        return None


def decide(task_id,review_id,expected_subject_sha256,decision,reason,*,execution=None):
    if decision not in ('accept','defer','reject','override') or not isinstance(reason,str) or not reason.strip():
        raise PreflightRefusal('explicit decision and reason required')
    with _review_lock(task_id):
        with _preflight():
            directory,record=_record(task_id,review_id)
            _require_kind(record,'review')
            evidence=status(task_id,review_id)
        if record['subject_sha256']!=expected_subject_sha256:raise PreflightRefusal('review subject changed')
        value={'schema':1,'subject_sha256':expected_subject_sha256,'decision':decision,'reason':reason,
               'critic_evidence_sha256':digest(encoded(evidence['critics'])),'at':forge._now()}
        path=directory/'author-disposition.json'
        if path.exists():
            prior=runtime._read(path)
            if any(prior[k]!=value[k] for k in value if k!='at'):raise PreflightRefusal('author decision already bound; retain original')
            return prior
        if execution and execution.get("tool") == "coding_review":
            signature = _decision_signature(execution.get("run_id"), execution.get("call_id"),
                                            task_id, review_id, value)
            if signature:
                value["source_call_sha256"] = signature
        runtime.atomic_json(path,value)
        forge._event(task_id,'review_author_disposition',review_id=review_id,**value)
    return value


def candidate_signature(directory):return digest(encoded(inventory(Path(directory)/'M')))


def stale_merge_inputs(record):
    stale=[]
    for side,source in record.get('sources',{}).items():
        try:
            if _root_identity(source)!=record.get('source_identities',{}).get(side):
                stale.append(side);continue
            if inventory(source)!=record['inputs'][side]:stale.append(side)
        except (OSError,ValueError):stale.append(side)
    return stale


def validate_merge(directory,expected,*,current=False):
    record=runtime._read(Path(directory)/'record.json')
    if record.get('input_sha256')!=expected:raise _InvalidReviewInput('merge input identity changed')
    for side in ('A','L','B'):
        if inventory(Path(directory)/side)!=record['inputs'][side]:raise _InvalidReviewInput('frozen merge input changed')
    if current and stale_merge_inputs(record):raise _InvalidReviewInput('original A/L/B inputs changed; prepare a new bound candidate')
    return record


def prepare_merge(task_id,base,local,incoming,*,criteria=''):
    sources={'A':base,'L':local,'B':incoming}
    if any(not isinstance(p,(str,Path)) or not str(p).strip() for p in sources.values()):
        raise PreflightRefusal('nonblank base, local and incoming roots are required')
    with _review_lock(task_id):
        review_id='merge-'+uuid.uuid4().hex[:16]
        with _preflight():
            directory=_directory(task_id,review_id)
            roots={s:_input_root(task_id,p) for s,p in sources.items()}
        with _admission_preflight(task_id,'task control forbids merge'):
            manifests=_review_manifests(roots)
        _freeze(directory,roots,manifests)
        (directory/'M').mkdir()
        conflicts=[]
        all_names=set().union(*(set(rows) for rows in manifests.values()))
        for name in sorted(all_names):
            a,l,b=(manifests[s].get(name,{'exists':False,'sha256':None,'mode':None}) for s in ('A','L','B'))
            key=lambda r:(r['exists'],r.get('sha256'),r.get('mode'))
            selected='B' if key(l)==key(a) else 'L' if key(b)==key(a) or key(l)==key(b) else None
            if selected is None:
                conflicts.append(name);selected='L'
            row=manifests[selected].get(name,{})
            if row.get('exists'):
                p=inside(directory/'M',name);p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes(inside(directory/selected,name).read_bytes());p.chmod(row['mode'])
        record={'schema':1,'kind':'merge','id':review_id,'plan_id':digest(review_id.encode()),'task_id':task_id,'inputs':manifests,
                'sources':{'A':str(Path(base).resolve()),'L':str(Path(local).resolve()),'B':str(Path(incoming).resolve())},
                'source_identities':{s:_root_identity(p) for s,p in roots.items()},
                'input_sha256':digest(encoded(manifests)),'criteria':criteria,'conflicts':conflicts,
                'resolved':[],'finished':False,'created':forge._now(),'merge_task_id':'','unit_id':''}
        runtime.atomic_json(directory/'record.json',record)
    return status(task_id,review_id)


def rework_merge(task_id,merge_id,criteria):
    if not isinstance(criteria,str) or not criteria.strip():raise PreflightRefusal('concrete repair/review findings required')
    with _preflight():
        previous,old=_record(task_id,merge_id)
        _require_kind(old,'merge')
        validate_merge(previous,old['input_sha256'])
    value=prepare_merge(task_id,previous/'A',previous/'L',previous/'B',criteria=criteria)
    directory,record=_record(task_id,value['id'])
    # New immutable attempt preserves original A/L/B and starts from reviewed M.
    import shutil
    if (directory/'M').exists():shutil.rmtree(directory/'M')
    if (previous/'M').exists():shutil.copytree(previous/'M',directory/'M')
    record.update(previous_merge_id=merge_id,previous_candidate_sha256=candidate_signature(previous),
                  resolved=list(old['resolved']),repairs=list(old.get('repairs',[])),sources=old['sources'],
                  source_identities=old['source_identities'])
    runtime.atomic_json(directory/'record.json',record)
    return status(task_id,record['id'])


def start_merge(task_id,merge_id,*,max_iters=0,model=''):
    with _review_lock(task_id):
        with _preflight():
            directory,record=_record(task_id,merge_id)
            _require_kind(record,'merge')
            if record['merge_task_id']:return status(task_id,merge_id)
        with _admission_preflight(task_id,'task control forbids merger start'):
            validate_merge(directory,record['input_sha256'],current=True)
        tid='code-'+uuid.uuid4().hex[:8]
        script=str(Path(__file__).with_name('forge_review_worker.py'))
        bound=forge.source_update_task(tid,str(directory),record['plan_id'],record['input_sha256'],script,max_active_workers=1)
        bound.update(coordinator_task_id=task_id,coordinator_review_id=merge_id)
        forge._save_task(bound)
        record['merge_task_id']=tid;record['unit_id']='agent-'+uuid.uuid4().hex[:8]
        runtime.atomic_json(directory/'record.json',record)
    forge.agent(tid,'spawn',agent_id=record['unit_id'],role='worker',model=model,max_iters=max_iters,
                brief='Resolve isolated A/L/B/M candidate. '+record['criteria'])
    return status(task_id,merge_id)


def command(task_id,action='status',review_id='',criteria='',previous_review_id='',paths=None,
            merge_id='',base='',local='',incoming='',critics=1,max_iters=0,model='',
            expected_subject_sha256='',decision='',reason='',verification_id='',expected_candidate_sha256='',*,_execution=None):
    if action=='prepare':value=prepare(task_id,criteria=criteria,previous_review_id=previous_review_id,paths=paths,merge_id=merge_id)
    elif action=='start':value=start(task_id,review_id,critics=critics,max_iters=max_iters,model=model)
    elif action in ('','status'):
        with _preflight():
            value=status(task_id,review_id) if review_id else reviews(task_id)
    elif action=='decide':value=decide(task_id,review_id,expected_subject_sha256,decision,reason,execution=_execution)
    elif action=='prepare_merge':value=prepare_merge(task_id,base,local,incoming,criteria=criteria)
    elif action=='start_merge':value=start_merge(task_id,review_id,max_iters=max_iters,model=model)
    elif action=='rework_merge':value=rework_merge(task_id,review_id,criteria)
    elif action=='verification_plan':
        import forge_battery
        value=forge_battery.prepare(task_id,review_id)
    elif action=='bind_verification':
        import forge_battery
        value=forge_battery.bind(task_id,review_id,verification_id,expected_candidate_sha256)
    else:raise PreflightRefusal('unsupported review action')
    return json.dumps(value,ensure_ascii=False)


# The dispatcher supplies execution provenance itself; it is not tool input.
_INTERNAL_COMMAND_SIGNATURE=inspect.signature(command)
_COMMAND_SIGNATURE=_INTERNAL_COMMAND_SIGNATURE.replace(parameters=[
    parameter for parameter in _INTERNAL_COMMAND_SIGNATURE.parameters.values()
    if parameter.name != '_execution'
])


def validate_call(arguments):
    """Bind and type-check dispatcher input without consulting Forge state."""
    # Providers may pad known optional fields with null. Required fields and
    # unknown keywords still have to be validated, even if supplied as null.
    supplied={key:value for key,value in arguments.items()
              if value is not None or key not in _COMMAND_SIGNATURE.parameters or key=='task_id'}
    bound=_COMMAND_SIGNATURE.bind(**supplied)
    for key,value in bound.arguments.items():
        if key in ('critics','max_iters'):
            if type(value) is not int:raise ValueError(f'{key} must be an integer')
        elif key=='paths':
            if not isinstance(value,list) or any(not isinstance(path,str) or '\x00' in path for path in value):
                raise ValueError('paths must be an array of path strings')
        elif not isinstance(value,str) or '\x00' in value:
            raise ValueError(f'{key} must be a string without NUL characters')
    if not bound.arguments['task_id'].strip():raise ValueError('task_id must not be blank')
    return supplied


def tick():
    """Deliver real critic outcomes to the parent coordinator, never invent work."""
    from core import events
    if not _TICK_LOCK.acquire(blocking=False):return 0
    emitted=0
    try:
        for path in forge.TASKS_DIR.glob('*/reviews/review-*/record.json'):
            try:
                record=runtime._read(path)
                if not record.get('units') or not record.get('review_task_id'):continue
                files=[path,*[forge._unit_dir(record['review_task_id'],'agents',u['id'])/name
                              for u in record['units'] for name in ('request.json','result.json')]]
                fingerprint=tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else (str(p),None,None) for p in files)
                if _OBSERVED.get(path)==fingerprint:continue
                value=status(record['task_id'],record['id'])
                if any(c['execution'] not in {'done','error','failed','stalled','stopped','timed_out','lost','unknown'} for c in value['critics']):continue
                outcome={'review_id':record['id'],'subject_sha256':record['subject_sha256'],
                         'verdict':value['verdict'],'critics':value['critics']}
                outcome_sha=digest(encoded(outcome))
                marker=path.parent/'completion-events'/(outcome_sha+'.json')
                if not marker.exists():
                    parent=forge.get(record['task_id']) or {}
                    payload={'schema':'praxis.forge-review-result.v1','task_id':record['task_id'],
                        'review_id':record['id'],'subject_sha256':record['subject_sha256'],
                        'outcome_sha256':outcome_sha,'verdict':value['verdict'],
                        'goal':parent.get('goal',''),'origin_chat':parent.get('origin_chat',''),
                        'run_context':parent.get('run_context'),'priority':parent.get('priority','normal'),
                        'review_task_id':record['review_task_id'],'status':'observed'}
                    result=events.emit('forge_review_result','forge_review',payload=payload,
                        dedup_key='forge-review:'+record['task_id']+':'+record['id']+':'+outcome_sha)
                    if result is None:continue
                    runtime.atomic_json(marker,{'schema':1,'outcome':outcome,'event_key':result['dedup_key']})
                    emitted+=1
                _OBSERVED[path]=fingerprint
            except (OSError,ValueError,TypeError,KeyError):
                # A damaged result is visible to status/readers, never a clear
                # verdict or a reason to launch a replacement critic.
                continue
        return emitted
    finally:_TICK_LOCK.release()
