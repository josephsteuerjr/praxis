"""Native hand and snapshot custody; expensive work uses canonical owned search."""
import json
from pathlib import Path
import re
import sys
import threading
import time

from code_navigation import OPS

# Cursor retention bounds only disposable metadata, never evidence collection.
CURSOR_SECONDS=3600
CURSOR_SLOTS=128
_SNAPSHOTS={}
_LOCK=threading.Lock()


def query(op, root='home', path='', name='', module='', limit=40, cursor='', *, home=None):
    import forge
    import run_context
    import search_execution
    home=Path(home or forge.BASE).resolve()
    def refused(error, **more):return dict(error=error,complete=False,coverage={'seen':0,'parsed':0,'failed':[],'skipped':[],'budget':None,'complete':False},**more)
    if op not in OPS:return refused('invalid_op')
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200:return refused('limit_must_be_1_to_200')
    if not all(isinstance(v,str) for v in (root,path,name,module,cursor)):return refused('string_arguments_required')
    if op in ('definition','references','callers','callees') and not name:return refused('name_required')
    if name and not all(part.isidentifier() for part in name.split('.')):return refused('name_must_be_dotted_identifier')
    if module and not all(part.isidentifier() for part in module.split('.')):return refused('module_must_be_dotted_identifier')
    kind='external'
    if root=='home':
        directory=home;kind='home'
    elif root.startswith('task:'):
        task,directory,error=forge._task_root(root[5:])
        if task and task.get('scope') in ('host','windows'):return refused('unsupported_remote_task')
        if error or directory is None:return refused('task_root_unavailable',reason=error)
        kind='task'
    else:
        directory=Path(root)
        if not directory.is_absolute():return refused('absolute_root_required')
    try:
        directory=directory.resolve()
        if not directory.is_dir():return refused('root_not_directory')
        narrow=(directory/path).resolve()
        if Path(path).is_absolute() or not narrow.is_relative_to(directory):return refused('path_outside_root')
        if not narrow.exists():return refused('stale_cursor' if cursor else 'path_unavailable',changed_paths=[path])
    except (OSError,RuntimeError):return refused('root_unavailable')
    canonical=narrow.relative_to(directory).as_posix()
    if canonical=='.':canonical=''
    request={'op':op,'root':{'kind':kind,'path':str(directory)},'path':canonical,
             'name':name,'module':module,'limit':limit,'offset':0,
             'cache_dir':str(home/'memory'/'.cache'/'code-query') if kind in ('home','task') else ''}
    key=json.dumps([str(directory),op,canonical,name,module],ensure_ascii=False)
    if cursor:
        if not re.fullmatch(r'[0-9a-f]{16}:[0-9]+',cursor):return refused('invalid_cursor')
        snap,raw_offset=cursor.split(':')
        with _LOCK:
            saved=_SNAPSHOTS.get((key,snap))
        if not saved or time.monotonic()-saved[0]>CURSOR_SECONDS:return refused('cursor_expired_or_query_changed',cursor_retention_s=CURSOR_SECONDS)
        request.update(previous=saved[1],offset=int(raw_offset))
    if run_context.current_run() is None:
        # Standalone inspection/test context has no run to own a child.
        from code_navigation import execute
        result=execute(request)
    else:
        result=search_execution.call([sys.executable,'-B',str(Path(__file__).with_name('code_navigation.py'))],request)
        if result is None:return refused('owned_search_failed')
    snapshot=result.get('snapshot');manifest=result.pop('_manifest',None)
    if manifest is not None and snapshot:
        with _LOCK:
            now=time.monotonic()
            for k,v in list(_SNAPSHOTS.items()):
                if now-v[0]>CURSOR_SECONDS:del _SNAPSHOTS[k]
            _SNAPSHOTS[(key,snapshot)]=(now,manifest)
            while len(_SNAPSHOTS)>CURSOR_SLOTS:
                del _SNAPSHOTS[min(_SNAPSHOTS,key=lambda k:_SNAPSHOTS[k][0])]
    result['cursor_retention']={'seconds':CURSOR_SECONDS,'snapshots':CURSOR_SLOTS,'storage':'parent process RAM; restart expires cursors'}
    coverage=result.setdefault('coverage',{})
    for key,value in {'seen':None,'parsed':0 if result.get('error')=='stale_cursor' else None,
                      'failed':[],'skipped':[],'budget':None,'complete':False}.items():
        coverage.setdefault(key,value)
    if result.get('error'):
        coverage['complete']=False
        coverage['note']='Collection aborted; null counts were not established. No result rows are claimed.'
    return result
