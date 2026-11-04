"""Physical-root transactions shared by Forge tasks, including overlapping roots.

The OS locks protect admission/metadata transactions, not model or command time.
Existing unit journals retain lifetime ownership. Independent sibling roots take
shared ancestor locks and can proceed concurrently; a parent root conflicts with
every descendant. No production permissions are invented here.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
import time


def physical(value: str | Path) -> Path:
    return Path(value).resolve()


def overlaps(left: str | Path, right: str | Path) -> bool:
    a,b=physical(left),physical(right)
    return a==b or a in b.parents or b in a.parents


@contextlib.contextmanager
def transaction(root, state_dir: Path, timeout: float = 60):
    if not root:
        yield
        return
    root=physical(root)
    directory=Path(state_dir)/'root-mutexes'
    directory.mkdir(parents=True,exist_ok=True)
    handles=[]
    deadline=time.monotonic()+max(.1,float(timeout))
    try:
        for current in [*reversed(root.parents),root]:
            digest=hashlib.sha256(os.path.normcase(str(current)).encode()).hexdigest()
            handle=(directory/(digest+'.lock')).open('a+b')
            handles.append(handle)
            exclusive=current==root
            while True:
                try:
                    if os.name=='nt':
                        # Windows has no shared msvcrt byte lock. Serialize this
                        # adapter conservatively; Linux is the canonical Forge.
                        import msvcrt
                        handle.seek(0)
                        if not handle.read(1):handle.write(b'\0');handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(),(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic()>=deadline:
                        raise TimeoutError(f'physical-root transaction unavailable: {root}')
                    time.sleep(.02)
        yield
    finally:
        for handle in reversed(handles):handle.close()


def foreign_writer_reason(forge, task_id: str, *, root=None) -> str:
    task=forge.get(task_id) or {}
    root=root or task.get('root')
    if not root:return ''
    # Workshop supplies a physical root without a Forge task identity.
    task={**task,'root':str(root)}
    for meta in forge.TASKS_DIR.glob('*/task.json'):
        other_id=meta.parent.name
        if other_id==task_id:continue
        other=forge._read_json(meta,{}) or {}
        if not other.get('root') or not tasks_overlap(task,other):continue
        for row in forge._units(other_id,'agents'):
            if row.get('role')!='worker':continue
            if row.get('custody_recovered'):continue
            uid=str(row['id'])
            # A queued reservation owns no tools/root. Unknown and a terminal
            # writer without release/tree-exit evidence still own it.
            active=row.get('status') in forge._LIVE_UNIT_STATES|{'unknown'}
            active=active or (row.get('status') in forge._TERMINAL_AGENT_STATES|{'lost'}
                and not forge._root_released(other_id,uid)
                and not forge._writer_ended(forge._unit_dir(other_id,'agents',uid)))
            if active:return f'overlapping root writer occupied: {other_id}/{uid}'
        for kind in ('processes','verifications'):
            for row in forge._units(other_id,kind):
                if row.get('custody_recovered'):continue
                if row.get('status') not in forge._TERMINAL_AGENT_STATES|{'passed','passed_with_skips','stale'}:
                    return f'overlapping root job occupied: {other_id}/{row["id"]}'
    return ''


def _remote(task):
    return task.get('scope') in {'host','windows'}

def _binding(task):
    value=task.get('root_binding') or {}
    if (value.get('schema')==1 and value.get('verified') is True
            and value.get('namespace') and value.get('root_id')
            and value.get('root_id') in value.get('ancestors',[])):
        return value
    return None

def tasks_overlap(left,right):
    if not _remote(left) and not _remote(right):return overlaps(left['root'],right['root'])
    if _remote(left)!=_remote(right) or left.get('scope')!=right.get('scope'):return False
    if left.get('scope')=='windows':
        a,b=left.get('device_id'),right.get('device_id')
        if a and b and a!=b:return False
    a,b=_binding(left),_binding(right)
    # An old backend cannot establish physical aliases. Retain a coarse custody
    # domain for its tasks instead of declaring unverified paths independent.
    if not a or not b:return True
    if a['namespace']!=b['namespace']:return False
    return a['root_id'] in b['ancestors'] or b['root_id'] in a['ancestors']

@contextlib.contextmanager
def _keys(keys,state_dir,timeout):
    directory=Path(state_dir)/'root-mutexes';directory.mkdir(parents=True,exist_ok=True)
    handles=[];deadline=time.monotonic()+max(.1,timeout)
    try:
        for key,exclusive in sorted(keys.items()):
            h=(directory/(hashlib.sha256(key.encode()).hexdigest()+'.lock')).open('a+b');handles.append(h)
            while True:
                try:
                    if os.name=='nt':
                        import msvcrt
                        h.seek(0);h.write(b'0');h.flush();h.seek(0)
                        msvcrt.locking(h.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(h.fileno(),(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic()>=deadline:raise TimeoutError('remote physical-root transaction unavailable')
                    time.sleep(.02)
        yield
    finally:
        for h in reversed(handles):h.close()

@contextlib.contextmanager
def task_transaction(task,state_dir,timeout=60):
    if not _remote(task):
        with transaction(task.get('root'),state_dir,timeout):yield
        return
    binding=_binding(task)
    # Gate orders all legacy vs bound admissions too. Shared gate lets verified
    # independent roots run in parallel. Unknown device identity is global.
    domain=str(task.get('scope'));gate='!remote:'+domain
    keys={gate:not bool(binding)}
    if binding:
        for ident in binding['ancestors']:
            keys[domain+':'+binding['namespace']+':'+ident]=(ident==binding['root_id'])
    with _keys(keys,state_dir,timeout):yield

def adopt_host_binding(forge,task_id):
    """Explicit upgrade only after old custody clears; keep exact task preimage."""
    task=forge.get(task_id) or {}
    if task.get('scope')!='host':return 'not a host task'
    # Hold the coarse legacy gate while resolving and migrating the namespace.
    unbound={**task,'root_binding':{}}
    with task_transaction(unbound,forge.STATE_DIR):
        with forge._task_mutation_lock(task_id):
            task=forge.get(task_id) or {}
            if any(r.get('status') not in forge._TERMINAL_AGENT_STATES|{'passed','passed_with_skips','stale'}
                   for kind in ('agents','processes','verifications') for r in forge._units(task_id,kind)):
                return 'root binding adoption refused: unsettled units'
            if any(r.get('role')=='worker' and not forge._root_released(task_id,r['id'])
                   and not forge._writer_ended(forge._unit_dir(task_id,'agents',r['id']))
                   for r in forge._units(task_id,'agents')):
                return 'root binding adoption refused: unreleased worker'
            response=forge.serverd_client.workspace_inspect_result(task['root'],'orientation')
            binding=_binding({'root_binding':response.get('root_binding')})
            if not response.get('ok') or not binding:return 'root binding adoption requires compatible backend'
            directory=forge._task_file(task_id).parent/'migrations';directory.mkdir(exist_ok=True)
            # A prepared receipt precedes task replacement. A crash after the
            # replacement is recoverable without mutating the adopted task again.
            if task.get('root_binding')==binding:
                for path in sorted(directory.glob('host-root-*.json')):
                    receipt=forge._read_json(path,{}) or {}
                    if receipt.get('state')!='prepared' or receipt.get('root_binding')!=binding:continue
                    preimage=path.with_suffix('.preimage')
                    if (not preimage.is_file() or hashlib.sha256(preimage.read_bytes()).hexdigest()!=receipt.get('source_sha256')):
                        return 'root binding receipt preimage damaged; adoption not certified'
                    current=forge._task_file(task_id).read_bytes()
                    receipt.update(state='committed',after_sha256=hashlib.sha256(current).hexdigest(),
                                   recovered_at=forge._now())
                    forge._atomic_json(path,receipt)
                return 'root binding already adopted'
            path=forge._task_file(task_id);raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
            preimage=directory/('host-root-'+digest+'.preimage')
            if preimage.exists():
                if preimage.read_bytes()!=raw:return 'root binding preimage damaged; adoption refused'
            else:
                with preimage.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            receipt_path=directory/('host-root-'+digest+'.json')
            receipt={'source_sha256':digest,'root_binding':binding,'state':'prepared',
                     'at':forge._now(),'reason':'explicit compatible backend adoption'}
            forge._atomic_json(receipt_path,receipt)
            task['root_binding']=binding;task['root']=binding['canonical'];forge._save_task(task)
            receipt.update(state='committed',after_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),committed_at=forge._now())
            forge._atomic_json(receipt_path,receipt)
            return 'root binding adopted'
