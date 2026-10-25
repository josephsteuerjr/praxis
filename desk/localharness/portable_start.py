"""An unpacked Windows archive is already the permanent application."""
import json,os,socket
from pathlib import Path

def previous_installations(root):
    root=Path(root).resolve();found=[]
    for variable,suffix in [('ProgramFiles','Helene'),('LOCALAPPDATA','Helene'),('LOCALAPPDATA','Programs/Helene')]:
        base=os.environ.get(variable)
        if not base:continue
        candidate=(Path(base)/suffix).resolve()
        if candidate!=root and (candidate/'helene.json').is_file() and candidate not in found:found.append(candidate)
    return found

def initialize(root):
    root=Path(root).resolve();path=root/'helene.json'
    if path.exists():return
    if (root/'python-migration.json').exists():raise RuntimeError('Finish the recorded migration before first startup')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    value={'mode':'local','tree':'profile/main','code':'tree','port':port,'python':'runtime/python.exe',
           'app':'app/deskapp.py','runner':'app/localharness/runner.py','setup_complete':False,
           'model':{'framework':'openai','base_url':'','model':'','key':''},'relay':{'enabled':False},
           'voice':{'enabled':False},'installed':{'service':False}}
    # Exclusive creation keeps two simultaneous first launches from replacing
    # the winner's configuration. No data/profile is copied from the runtime.
    try:
        with path.open('x',encoding='utf-8')as stream:json.dump(value,stream,indent=2)
    except FileExistsError:pass
