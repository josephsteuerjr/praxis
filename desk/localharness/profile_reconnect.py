"""Reconnect preserved data with the small settings record saved before cleanup."""
import base64
import copy
import json
import os
from pathlib import Path
import socket

FORMAT='helene-profile-reconnect.v1'
SETTINGS={'agent-default.json','window-preferences.json','owner-stop.json'}


def record(data):
    return Path(data)/'memory/.control/installation-reconnect.json'


def discover():
    """Known data locations only; never search the owner's whole home tree."""
    from .portable_profile import default_data
    candidates={default_data().resolve()}
    if os.name=='nt' and os.environ.get('LOCALAPPDATA'):
        candidates.add((Path(os.environ['LOCALAPPDATA'])/'Programs/Helene/data').resolve())
    found=[path for path in candidates if record(path).is_file() and load(path) is not None]
    return found[0] if len(found)==1 else None


def load(data):
    data=Path(data).resolve();path=record(data)
    if not path.exists():return None
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(value,dict) or value.get('format')!=FORMAT
            or Path(value.get('data','')).resolve()!=data
            or not isinstance(value.get('config'),dict)
            or not isinstance(value.get('old_program'),str)
            or value['config'].get('mode','local')!='local'
            or not isinstance(value.get('settings',{}),dict)
            or set(value.get('settings',{}))-SETTINGS):
        raise ValueError('Сохранённые настройки не соответствуют выбранной папке данных')
    original=Path(value['old_program']).resolve()
    tree=Path(value['config'].get('tree') or 'data')
    if (tree if tree.is_absolute() else original/tree).resolve()!=data:
        raise ValueError('Сохранённые настройки указывают на другую папку данных')
    return value


def restore(program,data,progress=None):
    program,data=Path(program).resolve(),Path(data).resolve()
    saved=load(data)
    if saved is None:return False
    target=program/'helene.json'
    if target.exists():raise ValueError('Действующие настройки программы не перезаписываются')
    value=copy.deepcopy(saved['config'])
    from .standalone_relay import prepare_migration
    value=prepare_migration(saved['old_program'],program,[value],progress)[0]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    value.update(tree=str(data),code=str(program/'tree'),port=port,
        python=str(program/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')),
        app=str(program/'app/deskapp.py'),runner=str(program/'app/localharness/runner.py'))
    value['installed']=dict(value.get('installed') or {},service=False)
    # Resolve named data paths against their previous configuration directory.
    old=Path(saved['old_program'])
    for container,key in ((value.get('backup'),'dir'),(value.get('voice'),'tts_reference')):
        if isinstance(container,dict) and container.get(key):
            path=Path(container[key])
            if not path.is_absolute():container[key]=str((old/path).resolve())
    from .portable_profile import relative_config
    from .vision_policy import _write,_document
    decoded={name:base64.b64decode(body,validate=True) for name,body in saved.get('settings',{}).items()}
    # Apply the same private writer as llm.json, before any model keys enter temp.
    _write(target,_document(relative_config(value,target,program)),private=True)
    for name,body in decoded.items():
        if not (program/name).exists():_write(program/name,body,private=True)
    return True
