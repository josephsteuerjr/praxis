"""A foreground script may edit as its own verified Forge command, never another job.

The inherited nonce is not logged or placed in argv. Durable intent stores its hash.
Admission additionally proves live custodian identity and caller descent under /proc.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys

ENV='PRAXIS_EDIT_CONTEXT'


def issue(task_id,unit_id,parent_request_path,base):
    token=secrets.token_hex(32)
    context={'task_id':task_id,'unit_id':unit_id,'token':token}
    env=dict(os.environ);env[ENV]=json.dumps(context)
    # The base beside this code is the default. Naming it would point candidate code in a worktree at live memory.
    if Path(base).resolve()!=Path(__file__).resolve().parent:env['PRAXIS_BASE']=str(base)
    # Scripts import the public bridge without relying on agent being imported.
    directory=str(Path(__file__).resolve().parent/'pyhands')
    env['PYTHONPATH']=os.pathsep.join(filter(None,[directory,env.get('PYTHONPATH','')]))
    binding={'token_sha256':hashlib.sha256(token.encode()).hexdigest(),
             'parent_request_path':str(parent_request_path or '')}
    if not sys.platform.startswith('linux'):
        env.pop(ENV,None)
        binding={}
    return env,binding


def inherited():
    raw=os.environ.get(ENV)
    if raw is None:return None
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('Invalid script edit context')
    return value


def _descends_from(pid):
    import hand_bridge
    forwarded=hand_bridge.peer()
    current=int(forwarded['pid']) if forwarded else os.getpid()
    seen=set()
    while current>1 and current not in seen:
        if current==pid:return True
        seen.add(current)
        try:
            fields=(Path('/proc')/str(current)/'stat').read_text().rsplit(')',1)[1].split()
            current=int(fields[1])
        except (OSError,ValueError,IndexError):return False
    return False


def validate(forge,task_id,context):
    if not isinstance(context,dict) or context.get('task_id')!=task_id:
        raise ValueError('Script context belongs to a different task')
    unit=context.get('unit_id');token=context.get('token')
    if not isinstance(unit,str) or not unit.startswith('proc-') or '/' in unit or '\\' in unit or not isinstance(token,str):
        raise ValueError('Invalid script command identity')
    directory=forge._unit_dir(task_id,'processes',unit)
    request=forge._read_json(directory/'request.json',{}) or {}
    if not isinstance(request,dict):raise ValueError('Invalid command request')
    binding=request.get('script_edit_context') or {}
    if not isinstance(binding,dict):raise ValueError('Invalid command binding')
    if (request.get('id')!=unit or request.get('task_id')!=task_id
            or request.get('command_custody')!='foreground-group-v1'
            or not hmac.compare_digest(str(binding.get('token_sha256','')),hashlib.sha256(token.encode()).hexdigest())):
        raise ValueError('Script command binding mismatch')
    if any((directory/name).exists() for name in ('result.json','stop.json','stop-request.json')):
        raise ValueError('Script command is stopped or terminal')
    pid=int(request.get('supervisor_pid') or 0);born=str(request.get('supervisor_started_at') or '')
    if not pid or not born or not forge._pid_alive(pid,born) or not _descends_from(pid):
        raise ValueError('Script caller is not a descendant of its live command custodian')
    parent=str(binding.get('parent_request_path') or '')
    parent_id,_=forge._worker_parent(task_id,parent)
    if parent_id!=str(request.get('parent_unit_id') or ''):
        raise ValueError('Script worker ancestry mismatch')
    return parent,unit
