"""Read-only observations of the existing update journal and battery receipts.

UI counters never grant acceptance. No inventory scan, model call or writes.
"""
import re,time
from pathlib import Path
from .package_notice import read


def activity(tree):
    from . import source_update as pipeline
    from .session_host import process_state
    tree=Path(tree).resolve();request=read(pipeline.path(tree))
    if not request:return None
    if Path(request.get('tree') or '.').resolve()!=tree:return None
    directory=pipeline.locate(request);operation=read(directory/'pipeline.json')
    if operation.get('id')!=request['id'] or operation.get('input_sha256')!=request.get('input_sha256'):return None
    installed=pipeline.applied_status(tree) if request.get('installed_receipt') else None
    if not installed and Path(operation.get('tree') or '.').resolve()!=tree:return None
    state=operation.get('state','unknown')
    if state in ('cancelled','superseded') and not installed:return None
    producer=read(directory/'coordinator-process.json')
    live=not installed and bool(producer) and process_state(producer)=='alive'
    result={'id':request['id'],'state':state,'version':request.get('version',''),
        'live':live,'review_id':operation.get('review_id'),
        'error':operation.get('verification_error') or operation.get('finish_error') or operation.get('error',''),
        'role':('agent' if state in ('waiting_agent','needs_attention') else 'updater'),
        'next':('Исправить замечания и завершить кандидат' if state in ('waiting_agent','needs_attention')
            else 'Проверки, независимое ревью и переход выполняет обновлятор'),
        'composition':operation.get('composition_sha256','')}
    if installed:result.update(state='completed',role='none',live=False,next='Обновление установлено, запуск подтверждён',error='')
    control=(operation.get('coordinator') or {}).get('job_control') or {}
    if not installed and control.get('tree') and control.get('path'):
        from .desktop_update_request import path as job_path
        if Path(control['path']).resolve()==job_path(control['tree']).resolve():
            parent=read(control['path'])
            if parent.get('id')==control.get('id') and parent.get('program')==request.get('program'):
                result['parent_state']=parent.get('state')
                if parent.get('state') in ('completed','paused','cancelled','superseded'):
                    result.update(state=parent['state'],role='none',live=False,
                        next='Обновление завершено' if parent['state']=='completed' else 'Продолжение остановлено владельцем')
    pointer=read(directory/'test-battery/current.json');key=pointer.get('key','')
    if (re.fullmatch('[0-9a-f]{64}',key) and pointer.get('input_sha256')==operation['input_sha256']
            and pointer.get('composition_sha256')==operation.get('composition_sha256')
            and pointer.get('candidate_sha256')==operation.get('candidate_sha256')):
        progress=read(directory/'test-battery'/key/'progress.json')
        if progress:
            fresh=-5<=time.time()-float(progress.get('updated_at') or 0)<30
            result['tests']={**{k:progress.get(k) for k in ('state','updated_at','slots','completed','total',
                'completed_lanes','total_lanes','tests_completed','tests_failed','tests_total_known','copied_bytes','copied_files')},
                'live':live and fresh and state in ('verification_requested','review_requested','queued','merging'),
                'active':[{k:row.get(k) for k in ('slot','phase','lane','kind','file','started_at','current_test',
                    'tests_completed','tests_failed','tests_collected')}
                    for row in progress.get('active',[])[:12] if isinstance(row,dict)]}
    return result
