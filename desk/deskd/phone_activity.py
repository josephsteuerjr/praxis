"""Read-only phone monitoring: display metadata, never Forge payload authority."""
from . import forge_reader

TASK=('id','kind','goal','status','max_active_workers')
UNIT=('id','parent_unit_id','depth','ancestry_unknown','role','model','subtask','status',
      'request_status','custody','current_disposition','stale_dispositions','started','created',
      'finished','observation','liveness','process_liveness')
EVENT=('seq','kind','at','tool','generation','iteration','call_id')

def activity(tree,limit=20,cursor=''):
    page=forge_reader.activity(tree,limit,cursor)
    def project(task):
        result={key:task[key] for key in TASK if key in task}
        result['agents']=[{key:unit[key] for key in UNIT if key in unit} for unit in task.get('agents',[])]
        return result
    return {**page,'active':[project(task) for task in page['active']],
            'recent':[project(task) for task in page['recent']]}

def events(tree,task_id,unit_id,offset=0,limit=50,generation='',after_seq=0):
    page=forge_reader.events(tree,task_id,unit_id,offset,limit,generation,after_seq)
    rows=[]
    for event in page['events']:
        row={key:event[key] for key in EVENT if key in event}
        receipt=event.get('receipt') or {}
        if receipt:row['receipt']={key:receipt[key] for key in ('handler','status') if key in receipt}
        rows.append(row)
    return {**page,'events':rows}
