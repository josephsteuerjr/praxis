"""Append-only task-wide observations; unknown provider spend is never zero."""
from __future__ import annotations
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path


@contextmanager
def _lock(directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'.usage.lock').open('a+b') as handle:
        if os.name=='nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):handle.write(b'\0');handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        yield


def rows(directory):
    path=Path(directory)/'usage-ledger.jsonl'
    if not path.exists():return []
    result=[]
    for line in path.read_text(encoding='utf-8').splitlines():
        value=json.loads(line)
        if value.get('schema')!=1 or not value.get('event_id'):raise ValueError('corrupt Forge usage ledger')
        result.append(value)
    return result


def record(directory, *, unit_id, generation, call_id, kind, usage=None, **evidence):
    directory=Path(directory)
    identity=[directory.name,unit_id,generation,call_id,kind]
    digest=hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()
    value={'schema':1,'event_id':digest,'task_id':directory.name,'unit_id':unit_id,
           'generation':generation,'call_id':call_id,'kind':kind,'usage':usage,
           'at':dt.datetime.now(dt.timezone.utc).isoformat(),**evidence}
    with _lock(directory):
        prior=next((r for r in rows(directory) if r['event_id']==digest),None)
        if prior:
            if any(prior.get(k)!=value.get(k) for k in value if k!='at'):
                raise ValueError('Forge usage identity bound to different observation')
            return prior
        with (directory/'usage-ledger.jsonl').open('ab') as stream:
            stream.write(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()+b'\n')
            stream.flush();os.fsync(stream.fileno())
    return value


def summary(directory):
    events=rows(directory)
    calls={}
    total_in=total_out=0
    unknown_usage=0
    for event in events:
        key=(event['unit_id'],event['generation'],event['call_id'])
        if event['kind']=='model_start':calls.setdefault(key,'unknown')
        elif event['kind']=='model_result':
            calls[key]='observed'
            usage=event.get('usage')
            if not isinstance(usage,dict) or not usage:unknown_usage+=1
            else:
                total_in+=int(usage.get('in',usage.get('input_tokens',0)) or 0)
                total_out+=int(usage.get('out',usage.get('output_tokens',0)) or 0)
        elif event['kind']=='transport_unknown':unknown_usage+=1
    return {'schema':1,'events':len(events),'logical_calls':len(calls),
            'unknown_calls':sum(v=='unknown' for v in calls.values()),
            'unknown_usage_observations':unknown_usage,'observed_input_tokens':total_in,
            'observed_output_tokens':total_out,'cost':None,'cost_status':'unknown',
            'tree_caps':{'max_total_units':None,'max_depth':None,'max_children':None,
                         'max_tree_iters':None,'max_tree_tokens':None,'max_tree_cost':None}}
