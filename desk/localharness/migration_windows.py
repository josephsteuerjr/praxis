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


def close_python(old, *, force=False):
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
        except (OSError, RuntimeError, ValueError) as exc:
            request_error = exc
            if force:continue  # exact executable identity is rechecked by stop()
        try:
            env['HELENE_EXIT_WAIT']='3000' if force else '25000'
            powershell("$ErrorActionPreference='Stop';$p=Get-Process -Id ([int]$env:HELENE_WINDOW_PID) -ErrorAction SilentlyContinue;if($p -and -not $p.WaitForExit([int]$env:HELENE_EXIT_WAIT)){throw 'Python window exit unconfirmed'}",env)
        except Exception as exc:
            if force:continue
            raise RuntimeError('Python window exit unconfirmed; installation retained') from (request_error or exc)
    if owners and not force:
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
        system_action(old,'stop_service')
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            current=service(old)
            if current is None or current['State']=='Stopped':break
            time.sleep(.25)
        else:raise RuntimeError('Old service stop unconfirmed')
    close_python(old,force=True)
    # The legacy Update button launches the wizard while its window is alive.
    # Close only executables physically inside this exact old installation;
    # its Job owner then tears down its own runner/relay children.
    stop_owned_processes(old)
    deadline=time.monotonic()+15
    while True:
        try:quiescent(old);break
        except RuntimeError:
            if time.monotonic()>=deadline:raise
            time.sleep(.25)
    return {'service':before}


def stop_owned_processes(old):
    """Force only identified old executables/entries, including pre-control apps."""
    old=Path(old).resolve()
    raw=powershell("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();@(Get-CimInstance Win32_Process|Select-Object ProcessId,ExecutablePath,CommandLine)|ConvertTo-Json -Compress",dict(os.environ))
    rows=json.loads(raw or '[]')
    if isinstance(rows,dict):rows=[rows]
    scripts=[old/p for p in ('app/python_app.py','app/deskapp.py','deskapp.py','app/localharness/runner.py','localharness/runner.py')]
    for row in rows:
        if int(row['ProcessId']) in (os.getpid(),os.getppid()):continue
        executable=row.get('ExecutablePath');command=row.get('CommandLine') or ''
        if not executable:continue
        path=Path(executable).resolve()
        owned=path.is_relative_to(old) or (path.name.lower() in ('python.exe','pythonw.exe') and
            any(references_root(command,str(script)) for script in scripts))
        if not owned:continue
        env=dict(os.environ,HELENE_OLD_PID=str(row['ProcessId']),HELENE_OLD_EXE=executable,HELENE_OLD_COMMAND=command)
        # Recheck process generation immediately before Stop-Process. An exited
        # PID that Windows reused cannot authorize signalling its new owner.
        powershell("$ErrorActionPreference='Stop';$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$env:HELENE_OLD_PID);if($p -and $p.ExecutablePath -eq $env:HELENE_OLD_EXE -and $p.CommandLine -eq $env:HELENE_OLD_COMMAND){Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop}",env)


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
    system_action(old,'restore_service')
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


def install_shortcuts(new,folder=None,*,desktop_only=False):
    """Fresh per-user install has no SCM, registry uninstall or all-users effects."""
    if os.name!='nt':return []
    from deskd.local_files import _known_folder
    if desktop_only:
        folder=Path(folder) if folder else (_known_folder('B4BFCC3A-DB2C-424C-B029-7FE99A87C641') or Path.home()/'Desktop')
        link=folder/'Helene.lnk'
        prior=shortcut_binding(link)
        if prior and not link_belongs(prior,new):
            raise RuntimeError('Ярлык Hélène на рабочем столе относится к другой программе')
        shortcut(link,new/'helene.exe',new)
        return [str(link)]
    if folder is None:
        folder=(_known_folder('A77F5D77-2E2B-44C3-A6A2-ABA601054A51') or Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs')/'Helene'
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    for name,args in [('Helene.lnk',''),('Stop Helene.lnk','--panic --notify')]:
        shortcut(folder/name,new/'helene.exe',new,args)
    return [str(folder/'Helene.lnk'),str(folder/'Stop Helene.lnk')]


def admin():
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def run_privileged(script,env):
    """Only an OS operation is elevated, never the wizard/new agent/profile.

    Windows owns its consent/administrator-credential dialog. Passwords never
    pass through this process. Explicit values survive another admin identity.
    """
    import base64
    allowed={'HELENE_OLD_ROOT','HELENE_SYSTEM_ACTION','HELENE_RETIRE_MANIFEST','HELENE_RETIRE_SHA256','HELENE_LINK'}
    assignments='\n'.join('$env:'+name+"='"+str(value).replace("'","''")+"'"
                          for name,value in env.items() if name in allowed)
    encoded=base64.b64encode((assignments+'\n'+script).encode('utf-16le')).decode('ascii')
    exe=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    # Start-Process returns only after the small privileged operation completes.
    # The calling user's wizard and subsequent agent keep their original token.
    launch="$ErrorActionPreference='Stop';try{$p=Start-Process -FilePath $env:HELENE_ADMIN_EXE -ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand',$env:HELENE_ADMIN_CODE) -Verb RunAs -WindowStyle Hidden -Wait -PassThru;exit $p.ExitCode}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}"
    result=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-Command',launch],
        env=dict(os.environ,HELENE_ADMIN_EXE=str(exe),HELENE_ADMIN_CODE=encoded),
        capture_output=True,timeout=180,creationflags=0x08000000)
    if result.returncode:
        raise RuntimeError('Windows не разрешил операцию со старой программой. Подтверди запрос администратора; данные сохранены.')


def system_action(old,action,files=None):
    """Bound SCM/registry/tasks or an exact verified program-file deletion list."""
    if os.name!='nt':return
    if action not in ('stop_service','cleanup','remove_files','restore_service'):raise ValueError('Unknown privileged action')
    import hashlib,uuid
    request=None
    env=dict(os.environ,HELENE_OLD_ROOT=str(Path(old).resolve()),HELENE_SYSTEM_ACTION=action)
    if action=='remove_files':
        from .package_notice import write
        folder=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')/'Helene/retirement-requests'
        request=folder/(uuid.uuid4().hex+'.json')
        write(request,{'old':str(Path(old).resolve()),'files':files or {}})
        env.update(HELENE_RETIRE_MANIFEST=str(request),HELENE_RETIRE_SHA256=hashlib.sha256(request.read_bytes()).hexdigest())
    script=r'''
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($env:HELENE_OLD_ROOT).TrimEnd('\')
function FileDigest([string]$path){$stream=[IO.File]::OpenRead($path);$sha=[Security.Cryptography.SHA256]::Create();try{return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose();$stream.Dispose()}}
function Bound([string]$cmd){return $cmd -and ($cmd.StartsWith('"'+$root+'\',[StringComparison]::OrdinalIgnoreCase) -or $cmd.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase))}
$service=Get-CimInstance Win32_Service -Filter "Name='Helene'"
$ours=$service -and (Bound $service.PathName)
switch($env:HELENE_SYSTEM_ACTION){
 'stop_service'{if($ours){$s=Get-Service Helene;if($s.Status -ne 'Stopped'){$s.Stop();$s.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30))}}}
 'restore_service'{if(!$ours){throw 'Previous service binding changed'};Start-Service Helene}
 'cleanup'{
  if($ours){if($service.State -ne 'Stopped'){throw 'Previous service restarted'};& "$env:SystemRoot\System32\sc.exe" delete Helene|Out-Null;if($LASTEXITCODE -notin @(0,1060)){throw 'Service removal failed'}}
  foreach($h in @([Microsoft.Win32.Registry]::LocalMachine)){
   foreach($view in @([Microsoft.Win32.RegistryView]::Registry64,[Microsoft.Win32.RegistryView]::Registry32)){
    $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine,$view)
    $parent=$base.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall',$true)
    if($parent){foreach($name in $parent.GetSubKeyNames()){$key=$parent.OpenSubKey($name);$location=$key.GetValue('InstallLocation');$key.Close();if($location -and [IO.Path]::GetFullPath($location).TrimEnd('\').Equals($root,[StringComparison]::OrdinalIgnoreCase)){$parent.DeleteSubKeyTree($name)}};$parent.Close()};$base.Close()
   }
  }
  Get-ScheduledTask -ErrorAction SilentlyContinue|ForEach-Object {$t=$_;foreach($a in $t.Actions){if(Bound $a.Execute){Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false -ErrorAction Stop;break}}}
 }
 'remove_files'{
  if((FileDigest $env:HELENE_RETIRE_MANIFEST) -ne $env:HELENE_RETIRE_SHA256){throw 'Retirement manifest changed'}
  $manifest=Get-Content -LiteralPath $env:HELENE_RETIRE_MANIFEST -Raw -Encoding UTF8|ConvertFrom-Json
  if(!$manifest.old.Equals($root,[StringComparison]::OrdinalIgnoreCase)){throw 'Retirement root changed'}
  $files=$manifest.files
  foreach($entry in $files.PSObject.Properties){
   $path=[IO.Path]::GetFullPath((Join-Path $root $entry.Name))
   if(!$path.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Retirement path leaves installation'}
   if(Test-Path -LiteralPath $path -PathType Leaf){
    $check=$path;while($check.Length -ge $root.Length){if(((Get-Item -LiteralPath $check -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Linked retirement path'};if($check.Equals($root,[StringComparison]::OrdinalIgnoreCase)){break};$check=[IO.Path]::GetDirectoryName($check)}
    if((FileDigest $path) -ne $entry.Value){throw 'Old program file changed'};Remove-Item -LiteralPath $path -Force
   }
  }
 }
}
'''
    # Service elevation is explicit and narrow. Without a service, ordinary
    # per-user cleanup/process shutdown does not trigger a password request.
    try:
        if admin():return powershell(script,env)
        if action in ('stop_service','restore_service') or (action=='cleanup' and service(old)):
            return run_privileged(script,env)
        try:return powershell(script,env)
        except RuntimeError:return run_privileged(script,env)
    finally:
        if request:request.unlink(missing_ok=True)


def link_belongs(binding,root):
    target=binding.get('target','');root=Path(root).resolve()
    return bool(target and (Path(target).resolve().is_relative_to(root) or
        (references_root(binding.get('arguments',''),str(root/'app/python_app.py')) and
         references_root(binding.get('arguments',''),str(root)))))


def unpin_shortcut(path):
    """Microsoft's uninstall API removes a pin before its shortcut is deleted.

    https://learn.microsoft.com/windows/win32/api/shobjidl/nn-shobjidl-istartmenupinnedlist
    """
    import ctypes as c,uuid
    from ctypes import wintypes as w
    guid=lambda text:(c.c_ubyte*16).from_buffer_copy(uuid.UUID(text).bytes_le)
    ole=c.WinDLL('ole32');shell=c.WinDLL('shell32')
    ole.CoInitializeEx.argtypes=[c.c_void_p,w.DWORD];ole.CoInitializeEx.restype=c.c_long
    initialized=ole.CoInitializeEx(None,2)
    if initialized<0 and initialized!=-2147417850:raise OSError('COM initialization failed')
    item=c.c_void_p();pins=c.c_void_p()
    release=lambda p:c.WINFUNCTYPE(w.ULONG,c.c_void_p)(c.cast(p,c.POINTER(c.POINTER(c.c_void_p))).contents[2])(p)
    try:
        shell.SHCreateItemFromParsingName.argtypes=[w.LPCWSTR,c.c_void_p,c.c_void_p,c.POINTER(c.c_void_p)]
        shell.SHCreateItemFromParsingName.restype=c.c_long
        hr=shell.SHCreateItemFromParsingName(str(path),None,c.byref(guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')),c.byref(item))
        if hr<0:raise OSError('Cannot resolve shortcut for unpinning: '+str(hr))
        ole.CoCreateInstance.argtypes=[c.c_void_p,c.c_void_p,w.DWORD,c.c_void_p,c.POINTER(c.c_void_p)]
        ole.CoCreateInstance.restype=c.c_long
        hr=ole.CoCreateInstance(c.byref(guid('a2a9545d-a0c2-42b4-9708-a0b2badd77c8')),None,1,
            c.byref(guid('4cd19ada-25a5-4a32-b3b7-347bee5be36b')),c.byref(pins))
        if hr<0:raise OSError('Windows shortcut unpinning is unavailable: '+str(hr))
        remove=c.WINFUNCTYPE(c.c_long,c.c_void_p,c.c_void_p)(c.cast(pins,c.POINTER(c.POINTER(c.c_void_p))).contents[3])
        hr=remove(pins,item)
        if hr<0:raise OSError('Windows refused to unpin the old shortcut: '+str(hr))
    finally:
        if pins:release(pins)
        if item:release(item)
        if initialized>=0:ole.CoUninitialize()


def shortcut_folders():
    from deskd.local_files import _known_folder
    return [
        _known_folder('B4BFCC3A-DB2C-424C-B029-7FE99A87C641') or Path.home()/'Desktop',
        _known_folder('A77F5D77-2E2B-44C3-A6A2-ABA601054A51') or Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs',
        _known_folder('C4AA340D-F20F-4863-AFEF-F87EF2E6BA25') or Path(os.environ['PUBLIC'])/'Desktop',
        _known_folder('0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8') or Path(os.environ['PROGRAMDATA'])/'Microsoft/Windows/Start Menu/Programs',
        Path(os.environ['APPDATA'])/'Microsoft/Internet Explorer/Quick Launch/User Pinned']


def remove_shortcuts(old,folders=None):
    removed=[]
    for folder in folders or shortcut_folders():
        for path in Path(folder).rglob('*.lnk') if Path(folder).is_dir() else []:
            binding=shortcut_binding(path)
            if not binding or not link_belongs(binding,old):continue
            unpin_shortcut(path)  # failure keeps the identity and is reported
            try:path.unlink()
            except PermissionError:
                env=dict(os.environ,HELENE_LINK=str(path))
                run_privileged("$ErrorActionPreference='Stop';Remove-Item -LiteralPath $env:HELENE_LINK -Force",env)
            removed.append(str(path))
    return removed


def cleanup_installation(old,new):
    """Our own cleanup, independent of the legacy uninstaller's bugs."""
    if os.name!='nt':return {}
    old,new=Path(old).resolve(),Path(new).resolve()
    # Per-user registry is always handled with the wizard user's identity.
    import winreg
    for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
        base=r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,base,0,winreg.KEY_READ|view) as key:
                names=[winreg.EnumKey(key,i) for i in range(winreg.QueryInfoKey(key)[0])]
            for name in names:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,base+'\\'+name,0,winreg.KEY_READ|view) as key:
                    try:location=winreg.QueryValueEx(key,'InstallLocation')[0]
                    except FileNotFoundError:continue
                if Path(location).resolve()==old:winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER,base+'\\'+name,view,0)
        except FileNotFoundError:pass
    # Avoid asking for admin just to enumerate empty HKLM/task registrations.
    if service(old) or has_system_registration(old):system_action(old,'cleanup')
    return {'shortcuts_removed':remove_shortcuts(old),'legacy_uninstaller_used':False}


def has_system_registration(old):
    """Read-only discovery; no elevation for installations with no system hooks."""
    env=dict(os.environ,HELENE_OLD_ROOT=str(Path(old).resolve()))
    raw=powershell(r'''$r=$env:HELENE_OLD_ROOT;$found=$false;foreach($p in @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')){foreach($v in Get-ItemProperty $p -ErrorAction SilentlyContinue){if($v.InstallLocation -and $v.InstallLocation.TrimEnd('\').Equals($r,[StringComparison]::OrdinalIgnoreCase)){$found=$true}}};foreach($t in Get-ScheduledTask -ErrorAction SilentlyContinue){foreach($a in $t.Actions){if($a.Execute -and $a.Execute.Trim('"').StartsWith($r+'\',[StringComparison]::OrdinalIgnoreCase)){$found=$true}}};if($found){'yes'}''',env)
    return raw=='yes'


def remove_program(old,new,note):
    """Delete an explicit program inventory, with data/source roots protected.

    Older packages lack a passport: only known program directories/entries are
    admitted. No recursive deletion of the installation or data is performed.
    """
    import hashlib
    old,new=Path(old).resolve(),Path(new).resolve()
    if old==new or new.is_relative_to(old) or not note.get('startup_proof'):
        raise ValueError('Unsafe old-program retirement')
    if not any((old/p).exists() for p in ('helene.exe','Helene.exe','helene-setup.exe','app/python_app.py','python-app.json')):
        return {'ok':True,'previous_program_already_absent':True,'purge':False}
    protected=[Path(p).resolve() for p in note['data_paths']+note.get('old_data_paths',[])]
    protected += [Path(row['old']).resolve() for row in note.get('source_paths',[]) if Path(row['old']).resolve()!=old]
    protected += [old/name for name in ('data','profile','agents','backups','archives','memory','models','code','code-backup')]
    names=set()
    inventory=old/'python-package-files.json'
    if inventory.is_file():names.update(json.loads(inventory.read_text(encoding='utf-8')).get('files',{}))
    for name in ('app','runtime','resources','components','bin'):
        folder=old/name
        if folder.is_dir() and not folder.is_symlink():
            names.update(p.relative_to(old).as_posix() for p in folder.rglob('*') if p.is_file())
    names.update(name for name in ('helene.exe','Helene.exe','helene-setup.exe','helene-service.exe',
        'helene-host.exe','uninstall.exe','python-app.json','python-package-files.json','Start Helene.vbs','Migrate Helene.vbs') if (old/name).is_file())
    files={};seen=set()
    for name in sorted(names):
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid old program inventory')
        path=old/relative;resolved=path.resolve()
        identity=os.path.normcase(str(resolved))
        if identity in seen:continue
        seen.add(identity)
        if not resolved.is_relative_to(old) or resolved==old:raise ValueError('Old program inventory leaves installation')
        if any(resolved==p or resolved.is_relative_to(p) for p in protected):continue
        if path.name in ('helene.json','helene.json.bak','owner-stop.json') or not path.is_file():continue
        if any(part.lower() in ('models','model','memory','.secrets') for part in relative.parts):continue
        if path.is_symlink() or any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in [path,*path.parents] if p.is_relative_to(old)):
            raise ValueError('Linked program path cannot be retired')
        files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    # Save the exact deletion list before the first mutation.
    from .package_notice import write
    write(new/'old-program-retirement.json',{'old':str(old),'new':str(new),'files':files,'protected':[str(p) for p in protected]})
    remaining={}
    for name,digest in files.items():
        path=old/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('Old program changed before retirement')
        try:path.unlink()
        except PermissionError:remaining[name]=digest
    if remaining:system_action(old,'remove_files',remaining)
    if any((old/name).exists() for name in files):raise RuntimeError('Some old program files remain')
    launchers=[name for name in ('helene.exe','Helene.exe','helene-setup.exe','app/python_app.py') if (old/name).is_file()]
    return {'ok':not launchers,'removed_files':len(files),'retained_program_entries':launchers,'purge':False,'data_retained':True}
