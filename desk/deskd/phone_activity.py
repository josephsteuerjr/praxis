"""Paired-phone observations and exact content refs; never mutation authority."""
import re
from . import forge_reader

TASK=('id','kind','goal','status','max_active_workers')
UNIT=('id','parent_unit_id','depth','ancestry_unknown','role','model','subtask','status',
      'request_status','custody','current_disposition','stale_dispositions','started','created',
      'finished','observation','liveness','process_liveness')
EVENT=('seq','kind','at','tool','generation','iteration','call_id')


def reference(value):
    if not isinstance(value,dict) or not isinstance(value.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',value['sha256']):
        return None
    kind=value.get('kind','payload')
    if kind not in ('payload','result','request'):return None
    result={'sha256':value['sha256'],'kind':kind}
    if isinstance(value.get('size_bytes'),int) and value['size_bytes']>=0:result['size_bytes']=value['size_bytes']
    return result

def activity(tree,limit=20,cursor=''):
    return project(forge_reader.activity(tree,limit,cursor))


def project(page):
    def task_view(task):
        result={key:task[key] for key in TASK if key in task}
        result['agents']=[]
        for unit in task.get('agents',[]):
            row={key:unit[key] for key in UNIT if key in unit}
            for key in ('request_ref','result_ref'):
                ref=reference(unit.get(key))
                if ref:row[key]=ref
            refs=unit.get('payload_refs')
            if isinstance(refs,dict):row['payload_refs']={key:ref for key,value in refs.items()
                if key in ('diff_ref','files_ref','checks_ref','review_ref') and (ref:=reference(value))}
            result['agents'].append(row)
        return result
    return {**page,'active':[task_view(task) for task in page['active']],
            'recent':[task_view(task) for task in page['recent']]}

def events(tree,task_id,unit_id,offset=0,limit=50,generation='',after_seq=0):
    page=forge_reader.events(tree,task_id,unit_id,offset,limit,generation,after_seq)
    rows=[]
    for event in page['events']:
        row={key:event[key] for key in EVENT if key in event}
        for key in ('input_ref','output_ref'):
            ref=reference(event.get(key))
            if ref:row[key]=ref
        for key in ('model','model_requested','summary','reason','attempt','retry','partial_not_dispatched'):
            value=event.get(key)
            if isinstance(value,str):row[key]=value[:2000]
            elif isinstance(value,(int,bool)):row[key]=value
        receipt=event.get('receipt') or {}
        if isinstance(receipt, dict) and receipt:
            # Device holders can already read these payloads. Keep observed commands
            # separate from requested input, with bounded display-only values.
            row['receipt']={}
            for key in ('handler','kind','status','command','argv','requested_argv','cwd','pid','exit_code','error'):
                value=receipt.get(key)
                if isinstance(value,(int,float,bool)):row['receipt'][key]=value
                elif isinstance(value,str):row['receipt'][key]=value[:8000]
                elif key in ('argv','requested_argv') and isinstance(value,list):
                    row['receipt'][key]=[str(v)[:2000] for v in value[:100]]
        metadata=event.get('metadata')
        if isinstance(metadata,dict):
            outcome=metadata.get('outcome')
            if outcome is not None:row['outcome']=outcome if outcome in ('ok','confirmed','failed','refused','partial','in_doubt','unknown') else 'unknown'
            if isinstance(metadata.get('target'),str):row['target']=metadata['target'][:300]
        if isinstance(event.get('error'),str):row['error']=event['error'][:2000]
        rows.append(row)
    return {**page,'events':rows}
