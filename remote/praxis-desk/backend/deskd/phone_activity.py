"""Read-only phone monitoring: display metadata, never Forge payload authority."""
from . import forge_reader

TASK=('id','kind','goal','status','max_active_workers')
UNIT=('id','parent_unit_id','depth','ancestry_unknown','role','model','subtask','status',
      'request_status','custody','current_disposition','stale_dispositions','started','created',
      'finished','observation','liveness','process_liveness','request_ref','result_ref','payload_refs')
EVENT=('seq','kind','at','tool','generation','iteration','call_id','input_ref','output_ref')

def activity(tree,limit=20,cursor=''):
    from . import forge_snapshot
    return project(forge_snapshot.activity(tree,limit,cursor))


def project(page):
    def task_view(task):
        result={key:task[key] for key in TASK if key in task}
        result['agents']=[{key:unit[key] for key in UNIT if key in unit} for unit in task.get('agents',[])]
        return result
    return {**page,'active':[task_view(task) for task in page['active']],
            'recent':[task_view(task) for task in page['recent']]}

def events(tree,task_id,unit_id,offset=0,limit=50,generation='',after_seq=0):
    page=forge_reader.events(tree,task_id,unit_id,offset,limit,generation,after_seq)
    rows=[]
    for event in page['events']:
        row={key:event[key] for key in EVENT if key in event}
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
