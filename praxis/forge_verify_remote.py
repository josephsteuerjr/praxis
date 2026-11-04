"""Per-check durable remote IDs and custody readback; no command replay."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time

TERMINAL={'done','failed','error','stopped','timed_out'}
MATRIX_TERMINAL=TERMINAL|{'passed','passed_with_skips','stale','stalled'}

def _job(directory,index,cid):
    return directory/'remote-checks'/f'{index+1:02d}-{cid}'

def run_check(index,check,root,directory,timeout,observation,*,stopped):
    import forge
    import forge_remote_run as remote
    task={**observation,'id':str(observation.get('task_id') or observation.get('id') or directory.name),
          'root':str(root),'scope':observation.get('scope') or 'host'}
    cid=str(check.get('id') or f'check-{index+1}')
    # Never interpret a check ID as a path, even from retained malformed metadata.
    safe=hashlib.sha256(cid.encode()).hexdigest()[:24]
    job=_job(directory,index,safe)
    if (job/'request.json').exists():
        request=forge._read_json(job/'request.json',{}) or {}
        if not request:return {'status':'unknown','exit':-1,'custody':{'group_exit_verified':False},'note':'remote check identity unreadable'}
    else:
        request=remote.start_at(forge,task,job,'check-'+safe,str(check.get('command') or ''),
                                str(check.get('cwd') or '.'),timeout)
    deadline=time.monotonic()+timeout if timeout else float('inf')
    value=request
    while value.get('status') not in TERMINAL or value.get('prelaunch_refused') is not True:
        value=remote.refresh(forge,job,task,stop=stopped() or time.monotonic()>=deadline)
        if not isinstance(value.get('status'),str) or value['status'] not in TERMINAL|remote.ACTIVE|{'unknown'}:
            value={**value,'status':'unknown','error':'unrecognized remote status; custody retained'}
        if value.get('status') in TERMINAL|{'unknown'}:break
        time.sleep(.05)
    raw=value.get('remote_result') or {}
    nested=raw.get('terminal') or raw.get('result') or {}
    code=raw.get('exit',nested.get('exit_code',raw.get('exit_code',-1)))
    status=value.get('status','unknown')
    proven=status in TERMINAL and (value.get('prelaunch_refused') is True or task['scope']!='host'
                or remote.tree_settled(raw,value.get('remote_id') or value.get('backend_operation_id') or ''))
    custody={'remote':True,'spawned':value.get('prelaunch_refused') is not True,
             'group_exit_verified':bool(proven),'remote_request':str(job/'request.json'),
             'backend_operation_id':value.get('remote_id') or value.get('backend_operation_id'),
             'tree_exit':raw.get('tree_exit'),'prelaunch_refused':value.get('prelaunch_refused') is True}
    return {'status':('passed' if status=='done' else status) if proven else 'unknown',
            'exit':int(code if code is not None else -1),'custody':custody,
            'body':str(raw.get('body') or raw.get('stdout_tail') or nested.get('stdout_tail') or raw.get('error') or ''),
            'remote_log':str(raw.get('log') or nested.get('stdout_log') or ''),
            'note':str(raw.get('custody_error') or value.get('error') or '')}

def result_status(request,result,scope=None):
    if not isinstance(request,dict) or not isinstance(result,dict):return 'unknown'
    status=result.get('status')
    if status is not None and not isinstance(status,str):return 'unknown'
    if scope and request.get('scope') and scope!=request['scope']:return 'unknown'
    scope=scope or request.get('scope')
    if scope is not None and not isinstance(scope,str):return 'unknown'
    checks=result.get('checks',[])
    if not isinstance(checks,list) or any(not isinstance(r,dict) for r in checks):return 'unknown'
    if any('custody' in r and not isinstance(r['custody'],dict) for r in checks):return 'unknown'
    if scope not in {'host','windows'} and any((r.get('custody') or {}).get('remote') for r in checks):
        return 'unknown'
    if scope not in {'host','windows'} or status not in MATRIX_TERMINAL:return status
    if not checks:return 'unknown'
    import forge_remote_run as remote
    for row in checks:
        custody=row.get('custody') or {}
        if custody.get('group_exit_verified') is not True:return 'unknown'
        if custody.get('spawned') is False:continue
        if scope=='host' and not remote.tree_settled({'tree_exit':custody.get('tree_exit')},
                                                    custody.get('backend_operation_id') or ''):return 'unknown'
    return status

def _read(path):
    try:
        value=json.loads(path.read_text());return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def _owner_snapshot(forge,directory,request):
    tid=str(request.get('task_id') or directory.parent.parent.name)
    if tid!=directory.parent.parent.name:return None
    owner=forge.get(tid) or {}
    scope=str(owner.get('scope') or 'self')
    if (not owner or owner.get('id') not in (None,tid) or request.get('root')!=owner.get('root')
            or (request.get('scope') and request['scope']!=scope)):return None
    if scope not in {'host','windows'} and any((directory/'remote-checks').glob('*/request.json')):return None
    return {**request,'id':tid,'scope':scope}

def stop_owned(forge,directory):
    """Write only private nonce stops / reconcile exact already-persisted RPC IDs."""
    request=forge._read_json(directory/'request.json',{}) or {}
    if request.get('verification_custody')!='check-tree-v1':return
    task=_owner_snapshot(forge,directory,request)
    if task is None:return
    if task['scope'] in {'host','windows'}:
        import forge_remote_run as remote
        for path in sorted((directory/'remote-checks').glob('*/request.json')):
            remote.refresh(forge,path.parent,task,stop=True)
    else:
        for path in sorted((directory/'check-custody').glob('*/request.json')):
            req=_read(path)
            if req.get('nonce'):
                target=path.parent/'stop.tmp';target.write_text(req['nonce']);target.replace(target.with_name('stop'))

def reconcile_dead_supervisor(forge,directory):
    request=forge._read_json(directory/'request.json',{}) or {}
    if request.get('verification_custody')!='check-tree-v1' or not forge._writer_ended(directory):return False
    task=_owner_snapshot(forge,directory,request)
    if task is None:return False
    rows=[];remote_scope=task['scope'] in {'host','windows'}
    import forge_remote_run as remote
    checks=request.get('checks') or []
    if not isinstance(checks,list) or any(not isinstance(check,dict) for check in checks):return False
    for index,check in enumerate(checks):
        cid=str(check.get('id') or f'check-{index+1}')
        if remote_scope:
            safe=hashlib.sha256(cid.encode()).hexdigest()[:24];job=_job(directory,index,safe)
            if (job/'request.json').exists():
                value=forge._read_json(job/'result.json',{}) or {}
                req=forge._read_json(job/'request.json',{}) or {}
                if not req or not (req.get('remote_id') or req.get('backend_operation_id')):return False
                value=remote.project_result(job,req,value,task['scope'])
                if remote.custody_status(req,value,task['scope']) not in TERMINAL:return False
                raw=value.get('remote_result') or {}
                custody={'remote':True,'group_exit_verified':True,'spawned':value.get('prelaunch_refused') is not True,
                         'backend_operation_id':req.get('remote_id') or req.get('backend_operation_id'),
                         'tree_exit':raw.get('tree_exit')}
            else:custody={'spawned':False,'group_exit_verified':True,'never_admitted':True}
        else:
            # Producer names are normalized identically by forge_verify._safe_id.
            from forge_verify import _safe_id
            job=directory/'check-custody'/f'{index+1:02d}-{_safe_id(cid,f"check-{index+1}")}'
            if (job/'request.json').exists():
                req=_read(job/'request.json');receipt=_read(job/'settled.json')
                if not req.get('nonce') or receipt.get('nonce')!=req['nonce'] or receipt.get('settled') is not True:return False
                custody={'group_exit_verified':True,'spawned':True,'receipt':receipt}
            else:custody={'spawned':False,'group_exit_verified':True,'never_admitted':True}
        rows.append({'id':cid,'status':'stopped','custody':custody})
    if not rows:return False
    tid=str(request.get('task_id') or directory.parent.parent.name)
    if tid!=directory.parent.parent.name:return False
    # Serialize publication only; RPC/command waits happened outside it. A
    # different controller's complete result always wins without replacement.
    with forge._mutation_lock(tid):
        current=forge._read_json(directory/'request.json',{}) or {}
        if current!=request or _owner_snapshot(forge,directory,current)!=task or not forge._writer_ended(directory):return False
        result_path=directory/'result.json'
        if result_path.exists():
            existing=forge._read_json(result_path,{}) or {}
            scope=(forge.get(tid) or {}).get('scope') or 'self'
            if result_status(current,existing,scope) not in MATRIX_TERMINAL:return False
        else:
            forge._atomic_json(result_path,{'status':'stopped','checks':rows,'finished':forge._now(),
                               'reconciled':'exact check custody after proven supervisor exit'})
        digest=hashlib.sha256(result_path.read_bytes()).hexdigest()
        prior=forge._read_json(directory/'stop.json',{}) or {}
        if prior.get('state')!='settled' or prior.get('result_sha256')!=digest:
            forge._atomic_json(directory/'stop.json',{'state':'settled','at':forge._now(),
                'result_sha256':digest,'note':'all admitted check trees settled; exact result retained'})
        return True
