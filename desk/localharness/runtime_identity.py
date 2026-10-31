"""Once-per-worker startup observations, separate from current on-disk bytes."""
import hashlib,os,time
from pathlib import Path
from .package_notice import read


def capture(program,code):
    # Self-contained: the same observation is embedded in the temporary old
    # runner's hand, without importing/starting another incoming executor.
    import psutil,hashlib,os,time,json
    from pathlib import Path
    program,code=Path(program).resolve(),Path(code).resolve()
    try:version=json.loads((program/'python-app.json').read_text(encoding='utf-8')).get('version','unknown')
    except (OSError,ValueError,AttributeError):version='unknown'
    sources={}
    for name,path in (('agent.py',code/'agent.py'),('runner.py',program/'app/localharness/runner.py')):
        try:sources[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:pass
    return {'schema':1,'pid':os.getpid(),'birth':psutil.Process().create_time(),
        'observed_at':time.time(),'program':str(program),'code':str(code),
        'version_at_start':version,'source_at_start':sources,
        'scope':'startup source observation; existing Python modules require worker restart to change'}


def current(tree):
    from .session_host import process_state
    row=read(Path(tree)/'memory/.control/desk_inbox/.reader.json')
    context=row.get('code_context') or read(Path(tree)/'memory/.control/source-update-hand-ready.json').get('worker_context') or {}
    if not context:return {'available':False,'reason':'Executing worker has not published a startup identity'}
    return {**context,'available':context.get('pid')==row.get('pid') and process_state(context)=='alive'}
