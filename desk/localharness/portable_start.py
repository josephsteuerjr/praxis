"""An unpacked Windows archive is already the permanent application."""
import json,os,socket
from pathlib import Path

def previous_installations(root):
    root=Path(root).resolve();found=[]
    if os.name=='nt':
        import winreg
        # Per-user and machine registrations are separate installation scopes.
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive,r'Software\Microsoft\Windows\CurrentVersion\Uninstall',0,winreg.KEY_READ|view) as parent:
                        for index in range(winreg.QueryInfoKey(parent)[0]):
                            try:
                                with winreg.OpenKey(parent,winreg.EnumKey(parent,index)) as key:
                                    title=str(winreg.QueryValueEx(key,'DisplayName')[0]).lower()
                                    location=winreg.QueryValueEx(key,'InstallLocation')[0]
                                if not any(name in title for name in ('helene','hélène')):continue
                                candidate=Path(location).resolve()
                                if candidate!=root and (candidate/'helene.json').is_file() and candidate not in found:found.append(candidate)
                            except OSError:continue
                except OSError:continue
    for variable,suffix in [('ProgramFiles','Helene'),('LOCALAPPDATA','Helene'),('LOCALAPPDATA','Programs/Helene')]:
        base=os.environ.get(variable)
        if not base:continue
        candidate=(Path(base)/suffix).resolve()
        if candidate!=root and (candidate/'helene.json').is_file() and candidate not in found:found.append(candidate)
    return found

def initialize(root):
    root=Path(root).resolve();path=root/'helene.json'
    if path.exists():
        from .portable_profile import bind_program
        bind_program(root)
        if os.name=='nt':
            from .program_state import hidden
            hidden(root/'portable-program.json')
        return
    if (root/'portable-program.json').exists():
        raise RuntimeError('The saved profile pointer is missing; select your existing data folder before starting')
    if (root/'python-migration.json').exists():raise RuntimeError('Finish the recorded migration before first startup')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    from .portable_profile import default_data
    data=default_data()
    if data.exists() and any(data.iterdir()):
        raise RuntimeError('The default data folder already contains an agent; connect it explicitly in the migration window')
    data.mkdir(parents=True,exist_ok=True)
    value={'mode':'local','tree':str(data),'code':'tree','port':port,'python':'runtime/python.exe',
           'app':'app/deskapp.py','runner':'app/localharness/runner.py','setup_complete':False,
           'model':{'framework':'openai','base_url':'','model':'','key':''},'relay':{'enabled':False},
           'voice':{'enabled':False},'installed':{'service':False}}
    # Exclusive creation keeps two simultaneous first launches from replacing
    # the winner's configuration. No data/profile is copied from the runtime.
    try:
        with path.open('x',encoding='utf-8')as stream:json.dump(value,stream,indent=2)
    except FileExistsError:pass
