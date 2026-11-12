"""Keep the existing per-user login binding through programme retirement.

Capture before old hooks are removed; apply only at the admitted new owner.
Explorer's StartupApproved records are never enabled/reset by an update.
"""
from pathlib import Path
import json,os

from . import python_windows as windows
from .package_notice import write
from .program_state import directory


def _path(program, *, create=False):
    return directory(program, 'login-migration', create=create)/'preference.json'


def _shortcut():
    return Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/Helene.lnk'


def _record(program):
    try:value=json.loads(_path(program).read_text(encoding='utf-8'))
    except FileNotFoundError:return {}
    if (not isinstance(value,dict) or value.get('schema')!=1
            or value.get('program')!=str(Path(program).resolve())
            or value.get('state') not in ('pending','applied','owner-changed')):
        raise ValueError('Invalid login migration receipt; retained for recovery')
    return value


def owner_changed(program):
    """An explicit setting after capture supersedes the old update preference."""
    if os.name!='nt':return
    value=_record(program)
    if value.get('state')=='pending':
        value['state']='owner-changed';write(_path(program),value)


def _belongs(binding, root):
    if not binding:return False
    if binding['cwd'] and not windows.same_path(binding['cwd'],root):return False
    if windows.same_path(binding['target'],Path(root)/'helene.exe'):
        return binding['arguments'] in ('',windows.AUTOMATIC_ARGUMENTS)
    target=windows.run_root('"'+binding['target']+'" '+binding['arguments'])
    return target is not None and windows.same_path(target,root)


def capture(program, roots):
    if os.name != 'nt':return
    roots=[Path(root).resolve() for root in roots if Path(root).resolve()!=Path(program).resolve()]
    if not roots:return
    path=_path(program)
    previous=_record(program)
    if previous.get('state')=='pending':return  # a resumed cleanup must keep the pre-removal evidence
    command=windows.run_binding()
    run_root=windows.run_root(command) if command else None
    value={'schema':1, 'program':str(Path(program).resolve()), 'state':'pending'}
    if run_root is not None and any(windows.same_path(run_root,root) for root in roots):
        value['run']=command
    link=_shortcut();binding=windows.shortcut_binding(link)
    if any(_belongs(binding,root) for root in roots):
        value['shortcut']=binding
    # An absent/foreign binding is not permission to opt into login startup.
    if 'run' in value or 'shortcut' in value:
        write(_path(program,create=True),value)


def commit(program):
    if os.name != 'nt':return {'state':'not-applicable'}
    path=_path(program);value=_record(program)
    if value.get('state')!='pending':return {'state':'unchanged'}
    if value.get('schema')!=1 or value.get('program')!=str(Path(program).resolve()):
        raise ValueError('Login migration belongs to another programme')
    outcomes={}
    if 'run' in value:
        current=windows.run_binding();desired=windows.run_command(program)
        if current in (None,value['run'],desired):
            windows.set_run(desired);outcomes['run']='retargeted'
        else:outcomes['run']='changed-elsewhere'
    if 'shortcut' in value:
        link=_shortcut();current=windows.shortcut_binding(link)
        executable,arguments=windows.launch_binding(program,windows.AUTOMATIC_ARGUMENTS)
        if (current==value['shortcut'] or current is None and not link.exists()
                or windows.shortcut_matches(current,executable,program,arguments)):
            windows.shortcut(link,executable,program,arguments);outcomes['shortcut']='retargeted'
        else:outcomes['shortcut']='changed-elsewhere'
    value.update(state='applied',outcomes=outcomes)
    write(path,value)
    return value
