"""Retire the identified legacy Windows package after proving the new owner."""
import json
import os
from pathlib import Path
import subprocess
import time
from .python_windows import AUTOMATIC_ARGUMENTS,powershell,shortcut,shortcut_binding

def registered_uninstall(old):
    """Use the old installation's registered quiet command, never a name match."""
    if os.name!='nt':return None
    import ctypes,winreg
    from ctypes import wintypes
    old=Path(old).resolve();base=r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,base,0,winreg.KEY_READ|view) as parent:
                    names=[winreg.EnumKey(parent,i) for i in range(winreg.QueryInfoKey(parent)[0])]
                for name in names:
                    with winreg.OpenKey(hive,base+'\\'+name,0,winreg.KEY_READ|view) as key:
                        try:location=winreg.QueryValueEx(key,'InstallLocation')[0]
                        except FileNotFoundError:continue
                        if Path(location).resolve()!=old:continue
                        try:command=winreg.QueryValueEx(key,'QuietUninstallString')[0]
                        except FileNotFoundError:command=winreg.QueryValueEx(key,'UninstallString')[0]
                    shell=ctypes.WinDLL('shell32');kernel=ctypes.WinDLL('kernel32')
                    shell.CommandLineToArgvW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_int)]
                    shell.CommandLineToArgvW.restype=ctypes.POINTER(wintypes.LPWSTR)
                    count=ctypes.c_int();array=shell.CommandLineToArgvW(command,ctypes.byref(count))
                    if not array:raise ValueError('Invalid registered uninstaller command')
                    try:argv=[array[i] for i in range(count.value)]
                    finally:kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree(array)
                    executable=Path(argv[0]).resolve()
                    if executable.parent!=old or not executable.is_file():raise ValueError('Registered uninstaller leaves the selected old installation')
                    if executable.name.lower()=='helene-setup.exe':
                        if '--uninstall' not in argv or any(a not in ('--uninstall','--quiet') for a in argv[1:]):raise ValueError('Unsupported legacy removal switches')
                        if '--quiet' not in argv:argv.append('--quiet')
                    elif executable.name.lower()=='uninstall.exe':
                        if any(a.lower()!='/s' for a in argv[1:]):raise ValueError('Unsupported registered removal switches')
                        if len(argv)==1:argv.append('/S')
                    else:raise ValueError('Unrecognized registered Helene uninstaller')
                    return argv
            except FileNotFoundError:continue
    return None

def uninstall_registered(old,argv):
    """Called only after the new Python window and preserved profile are proven."""
    current=service()
    if current and not service_belongs_to(old,current):
        raise RuntimeError('The Helene service belongs to another installation; old package retained')
    result=subprocess.run(argv,cwd=old,capture_output=True,timeout=180,creationflags=0x08000000)
    if result.returncode:raise RuntimeError('The old uninstaller failed; the new application remains available')
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if registered_uninstall(old) is None and service(old) is None:
            return {'ok':True,'method':'registered_uninstaller','purge':False}
        time.sleep(.25)
    raise RuntimeError('Old installation removal has not yet been confirmed')


def request_python_exit(old, pid):
    """Ask this installation's existing authenticated window, never close-to-tray."""
    import urllib.request
    from urllib.parse import urlsplit
    path = old / f'.python-window-{pid}.json'
    if not path.is_file():
        raise RuntimeError('This running legacy window has no update exit surface; use its tray Exit, then retry')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value.get('schema') != 1 or value.get('pid') != pid
            or Path(value.get('root', '')).resolve() != old.resolve()):
        raise ValueError('Python window control identity differs from selected installation')
    origin = value.get('origin', '')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Invalid local Python window origin')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    body = json.dumps({'command': 'python_window', 'args': {'action': 'exit', 'relaunch': False}}).encode()
    request = urllib.request.Request(origin + '/__python__/invoke', data=body,
        headers={'Content-Type': 'application/json', 'X-Helene-Window': value['token']})
    with http.open(request, timeout=3) as response:
        return json.load(response)


def service(old=None):
    raw=powershell("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();$s=Get-CimInstance Win32_Service -Filter \"Name='Helene'\"; if($s){$s|Select-Object Name,State,PathName|ConvertTo-Json -Compress}",dict(os.environ))
    if not raw:return None
    value=json.loads(raw)
    return value if old is None or service_belongs_to(old,value) else None


def service_belongs_to(old,value):
    command=str(value.get('PathName','')).replace('/','\\').lower()
    root=str(old.resolve()).replace('/','\\').lower()
    # Exact quoted executable/config root component, not a product-name guess.
    return ('"'+root+'\\') in command or command.startswith(root+'\\')


def close_python(old):
    if os.name!='nt':return
    env=dict(os.environ,HELENE_OLD_ROOT=str(old.resolve()))
    raw=powershell("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); @((Get-CimInstance Win32_Process)|Where-Object {$_.ExecutablePath -and ($_.ExecutablePath.Equals($env:HELENE_OLD_ROOT+'\\runtime\\python.exe',[StringComparison]::OrdinalIgnoreCase) -or $_.ExecutablePath.Equals($env:HELENE_OLD_ROOT+'\\runtime\\pythonw.exe',[StringComparison]::OrdinalIgnoreCase))}|Select-Object ProcessId,CommandLine)|ConvertTo-Json -Compress",env)
    rows=json.loads(raw or '[]')
    if isinstance(rows,dict):rows=[rows]
    owners=[row for row in rows if references_root(row.get('CommandLine',''),str(old/'app/python_app.py'))]
    for row in owners:
        env['HELENE_WINDOW_PID']=str(row['ProcessId'])
        # A disconnect may race successful exit. Observe the selected process
        # independently, while an absent/invalid descriptor never authorizes kill.
        request_error = None
        try:
            request_python_exit(old, int(row['ProcessId']))
        except OSError as exc:
            request_error = exc
        try:
            powershell("$ErrorActionPreference='Stop';$p=Get-Process -Id ([int]$env:HELENE_WINDOW_PID) -ErrorAction SilentlyContinue;if($p -and -not $p.WaitForExit(25000)){throw 'Python window exit unconfirmed'}",env)
        except Exception as exc:
            raise RuntimeError('Python window exit unconfirmed; installation retained') from (request_error or exc)
    if owners:
        from .app_migration import discover
        for agent in discover(old)['agents']:
            value=agent['value']
            if value.get('mode')=='remote':continue
            record=Path(agent['tree'])/'.desktop-owner.lock'
            if not owner_lease_available(record):
                raise RuntimeError('Previous Python owner still holds its data lease; previous installation retained')


def owner_lease_available(path):
    """Probe the real lease without deleting or rewriting its diagnostic record."""
    if not path.exists():return True
    if os.name!='nt':
        import fcntl
        with path.open('rb') as reader:
            try:fcntl.flock(reader.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return False
            fcntl.flock(reader.fileno(),fcntl.LOCK_UN);return True
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt
    kernel=c.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW;create.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE];create.restype=w.HANDLE
    handle=create(str(path),0x80000000,0,None,3,0x00200080,None)
    if handle==c.c_void_p(-1).value:
        code=c.get_last_error()
        if code in (32,33):return False
        raise c.WinError(code)
    try:descriptor=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except Exception:
        kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle(handle);raise
    with os.fdopen(descriptor,'rb') as reader:
        if os.fstat(reader.fileno()).st_file_attributes&0x400:raise ValueError('Owner lease is a reparse point')
        return True


def stop(old):
    if os.name!='nt':return {'service':None}
    before=service(old)
    if before and before['State']!='Stopped':
        sc=Path(os.environ['SYSTEMROOT'])/'System32/sc.exe'
        result=subprocess.run([str(sc),'stop','Helene'],capture_output=True,timeout=20,creationflags=0x08000000)
        if result.returncode not in (0,1062):raise RuntimeError('Old service Stop refused')
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            current=service(old)
            if current is None or current['State']=='Stopped':break
            time.sleep(.25)
        else:raise RuntimeError('Old service stop unconfirmed')
    close_python(old)
    # The legacy Update button launches the wizard while its window is alive.
    # Close only executables physically inside this exact old installation;
    # its Job owner then tears down its own runner/relay children.
    env=dict(os.environ,HELENE_OLD_ROOT=str(old.resolve()))
    powershell("$ErrorActionPreference='Stop';Get-CimInstance Win32_Process|Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:HELENE_OLD_ROOT+'\\',[StringComparison]::OrdinalIgnoreCase)}|ForEach-Object {Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop}",env)
    deadline=time.monotonic()+15
    while True:
        try:quiescent(old);break
        except RuntimeError:
            if time.monotonic()>=deadline:raise
            time.sleep(.25)
    return {'service':before}


def quiescent(old):
    """Find only command lines carrying the exact old installation; never kill by name."""
    if os.name!='nt':return True
    env=dict(os.environ,HELENE_OLD_ROOT=str(old.resolve()),HELENE_MIGRATOR_PID=str(os.getpid()),HELENE_WIZARD_PID=str(os.getppid()))
    raw=powershell("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); @((Get-CimInstance Win32_Process)|Where-Object {$_.ProcessId -ne [int]$env:HELENE_MIGRATOR_PID -and $_.ProcessId -ne [int]$env:HELENE_WIZARD_PID -and $_.CommandLine}|Select-Object ProcessId,CommandLine)|ConvertTo-Json -Compress",env)
    rows=json.loads(raw or '[]')
    if isinstance(rows,dict):rows=[rows]
    if any(references_root(row.get('CommandLine',''),str(old.resolve())) for row in rows):
        raise RuntimeError('Close the old Hélène window before switching; old processes still reference its directory')
    return True


def references_root(command,root):
    import re
    root=root.replace('/','\\').rstrip('\\')
    command=command.replace('/','\\')
    return re.search(r'(?:^|[\s\"])'+re.escape(root)+r'(?=$|[\\\s\"])',command,re.IGNORECASE) is not None


def restore_service(old, before):
    """Restore only the service whose exact binding we stopped, before retirement."""
    if not before or before['State']=='Stopped':return False
    current=service(old)
    if not current:raise RuntimeError('Previous service registration disappeared; cannot restore automatically')
    sc=Path(os.environ['SYSTEMROOT'])/'System32/sc.exe'
    got=subprocess.run([str(sc),'start','Helene'],capture_output=True,timeout=30,creationflags=0x08000000)
    if got.returncode not in (0,1056):raise RuntimeError('Previous service restart refused; retained installation needs manual start')
    return True


def retire(old,new):
    """Only integrations bound to old; user data/code deletion is never done here."""
    result={'service_removed':False,'registry_removed':[],'shortcuts_replaced':[]}
    if os.name!='nt':return result
    current=service(old)
    if current:
        if current['State']!='Stopped':raise RuntimeError('Old service restarted; retirement refused')
        sc=Path(os.environ['SYSTEMROOT'])/'System32/sc.exe'
        got=subprocess.run([str(sc),'delete','Helene'],capture_output=True,timeout=20,creationflags=0x08000000)
        if got.returncode not in (0,1060):raise RuntimeError('SCM deletion refused; old files retained')
        result['service_removed']=True
    import winreg
    uninstall=r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,uninstall,0,winreg.KEY_READ|view) as parent:
                    names=[winreg.EnumKey(parent,i) for i in range(winreg.QueryInfoKey(parent)[0])]
                for name in names:
                    path=uninstall+'\\'+name
                    with winreg.OpenKey(hive,path,0,winreg.KEY_READ|view) as key:
                        try:location=winreg.QueryValueEx(key,'InstallLocation')[0]
                        except FileNotFoundError:continue
                    if Path(location).resolve()==old.resolve():
                        winreg.DeleteKeyEx(hive,path,view,0);result['registry_removed'].append(name)
            except FileNotFoundError:pass
    # A scheduled session host is removed only when its executable belongs to
    # this old installation; a name alone never grants removal authority.
    env=dict(os.environ,HELENE_OLD_ROOT=str(old.resolve()))
    result['tasks_removed']=json.loads(powershell("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();$removed=@();Get-ScheduledTask -ErrorAction SilentlyContinue|ForEach-Object {$t=$_;foreach($a in $t.Actions){if($a.Execute -and $a.Execute.StartsWith($env:HELENE_OLD_ROOT+'\\',[StringComparison]::OrdinalIgnoreCase)){Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false -ErrorAction Stop;$removed+=($t.TaskPath+$t.TaskName);break}}};ConvertTo-Json -InputObject @($removed) -Compress",env) or '[]')
    from deskd.local_files import _known_folder
    roots=[_known_folder('A77F5D77-2E2B-44C3-A6A2-ABA601054A51') or Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs',
           _known_folder('B4BFCC3A-DB2C-424C-B029-7FE99A87C641') or Path.home()/'Desktop',
           _known_folder('0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8') or Path(os.environ['PROGRAMDATA'])/'Microsoft/Windows/Start Menu/Programs',
           _known_folder('C4AA340D-F20F-4863-AFEF-F87EF2E6BA25') or Path(os.environ.get('PUBLIC',str(Path.home())))/'Desktop',
           Path(os.environ['APPDATA'])/'Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar',
           Path(os.environ['APPDATA'])/'Microsoft/Internet Explorer/Quick Launch/User Pinned/StartMenu']
    result['shortcuts_replaced']=retarget_shortcuts(old,new,roots)
    if not result['shortcuts_replaced']:
        shortcut(roots[0]/'Helene Python.lnk',new/'helene.exe',new)
    return result


def retarget_shortcuts(old,new,roots):
    replaced=[]
    startup_roots={folder.resolve()/'Startup' for folder in (roots[0],roots[2])}
    for folder in roots:
        for link in folder.rglob('*.lnk') if folder.is_dir() else []:
            prior=shortcut_binding(link)
            if prior is None:continue
            target=prior['target']
            if target and Path(target).resolve().is_relative_to(old.resolve()):
                tokens=prior['arguments'].split()
                automatic=link.parent.resolve() in startup_roots or prior['arguments']==AUTOMATIC_ARGUMENTS or '--launch-intent=automatic' in tokens
                arguments='--panic --notify' if '--panic' in tokens else AUTOMATIC_ARGUMENTS if automatic else ''
                shortcut(link,new/'helene.exe',new,arguments)
                replaced.append(str(link))
    return replaced


def install_shortcuts(new,folder=None):
    """Fresh per-user install has no SCM, registry uninstall or all-users effects."""
    if os.name!='nt':return []
    from deskd.local_files import _known_folder
    if folder is None:
        folder=(_known_folder('A77F5D77-2E2B-44C3-A6A2-ABA601054A51') or Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs')/'Helene'
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    for name,args in [('Helene.lnk',''),('Stop Helene.lnk','--panic --notify')]:
        shortcut(folder/name,new/'helene.exe',new,args)
    return [str(folder/'Helene.lnk'),str(folder/'Stop Helene.lnk')]
