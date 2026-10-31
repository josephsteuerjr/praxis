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

def uninstall_registered(old,argv,*,progress=None):
    """Let the selected native uninstaller own shutdown and programme removal."""
    current=service()
    if current and not service_belongs_to(old,current):
        raise RuntimeError('The Helene service belongs to another installation; old package retained')
    import tempfile
    # The launcher can spend minutes waiting for its elevated data-ACL worker.
    # Keep emitting truthful phase/elapsed progress instead of freezing the UI.
    with tempfile.TemporaryFile() as output:
        child=subprocess.Popen(argv,cwd=old,stdout=output,stderr=subprocess.STDOUT,creationflags=0x08000000)
        started=time.monotonic()
        while child.poll() is None:
            elapsed=time.monotonic()-started
            if progress:progress({'stage':'uninstalling_old','seconds':elapsed,
                'detail':'Штатный деинсталлятор завершает работу · данные сохраняются'})
            if elapsed>=600:raise RuntimeError('Native uninstall is still running; data attachment postponed (pid '+str(child.pid)+')')
            time.sleep(.5)
        if child.returncode:raise RuntimeError('The old uninstaller failed; existing data has not been attached to a new owner')
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if registered_uninstall(old) is None and service(old) is None:
            return {'ok':True,'method':'registered_uninstaller','purge':False}
        time.sleep(.25)
    raise RuntimeError('Old installation removal has not yet been confirmed')


def uninstaller_for(old):
    """Exact registered command, or the native setup left in this installation."""
    old=Path(old).resolve()
    command=registered_uninstall(old)
    if command:return command
    for name,args in (('helene-setup.exe',['--uninstall','--quiet']),('uninstall.exe',['/S'])):
        entry=old/name
        if entry.is_file():
            if entry.is_symlink() or entry.resolve().parent!=old:
                raise ValueError('Uninstaller must belong to the selected installation')
            return [str(entry),*args]
    return None


def uninstall_before_attach(old,command,data_paths,*,progress=None,cancelled=None):
    """No preliminary stop, Python file walk, deletion or code backup."""
    old=Path(old).resolve()
    expected={'helene-setup.exe':('--uninstall','--quiet'),'uninstall.exe':('/s',)}
    entry=Path(command[0]).resolve() if command else old
    switches=[str(arg).lower() for arg in command[1:]]
    if (entry.parent!=old or entry.name.lower() not in expected
            or len(switches)!=len(expected[entry.name.lower()]) or set(switches)!=set(expected[entry.name.lower()])):
        raise ValueError('Unsupported data-preserving uninstaller command')
    # Native uninstallers retain data/agents/backups without --purge. Reject
    # a nonstandard in-program data directory before they can remove it.
    for raw in data_paths:
        data=Path(raw).resolve()
        if not data.is_dir():raise ValueError('Existing data folder is missing')
        if data==old or (data.is_relative_to(old) and data.relative_to(old).parts[0].lower() not in ('data','agents','backups')):
            raise ValueError('The old uninstaller cannot preserve this in-program data directory; move the data outside the programme first')
    captured=owned_processes(old)
    from .session_host import process_state
    existing=[row for row in captured
              if Path(row.get('ExecutablePath') or '').name.lower() in ('helene-setup.exe','uninstall.exe','icacls.exe')
              and process_state({'pid':row['ProcessId'],'birth':row['Birth']}) not in ('absent','changed')]
    # The native uninstaller may delete its shortcuts without notifying the
    # shell. Unpin while those exact shortcut identities still exist.
    unpinned=unpin_shortcuts(old) if os.name=='nt' else []
    # An outer UAC launcher may already have returned. Joining its still-live
    # generation must not start a second uninstaller over the same data ACLs.
    receipt=({'ok':True,'method':'existing_native_uninstaller','purge':False,
              'joined_existing':[{'pid':row['ProcessId'],'birth':row['Birth']} for row in existing]}
             if existing else uninstall_registered(old,command,progress=progress))
    # NSIS/UAC can return from its outer launcher and remove SCM/registry
    # entries while an elevated helene-setup/icacls worker still owns the data.
    # Observe new generations too, not only those present before invocation.
    deadline=time.monotonic()+600
    observed={(int(row['ProcessId']),row['Birth']):row for row in captured}
    original_generations=set(observed)
    stopping=set()
    while True:
        if cancelled and cancelled.is_set():raise InterruptedError('Owner stopped waiting for legacy uninstall')
        try:
            from .session_host import process_state
            current=owned_processes(old)
            observed.update({(int(row['ProcessId']),row['Birth']):row for row in current})
            alive=[row for row in observed.values()
                   if process_state({'pid':row['ProcessId'],'birth':row['Birth']}) not in ('absent','changed')]
            uninstallers=[row for row in alive if (
                Path(row.get('ExecutablePath') or '').name.lower() in ('helene-setup.exe','uninstall.exe','icacls.exe')
                or ((int(row['ProcessId']),row['Birth']) not in original_generations
                    and Path(row.get('ExecutablePath') or '').name.lower() in ('cmd.exe','powershell.exe','pwsh.exe','conhost.exe')))]
            if progress and alive:
                progress({'stage':'uninstalling_old','processes':[{'pid':row['ProcessId'],
                    'name':Path(row.get('ExecutablePath') or '').name} for row in alive],
                    'detail':'Ожидаю завершения штатного удаления и освобождения данных'})
            if alive and not uninstallers:
                # The native uninstaller has finished. Close only its remaining
                # captured generations; never interrupt an ACL/uninstall worker.
                for row in alive:
                    identity=(int(row['ProcessId']),row['Birth'])
                    if identity not in stopping:
                        stop_generation(row);stopping.add(identity)
                raise RuntimeError('Waiting for old application descendants to exit')
            if alive:raise RuntimeError('Waiting for the native uninstaller and its data-permission workers')
            quiescent(old)
            if service(old):raise RuntimeError('The old uninstaller left its service registered')
            if any((old/name).exists() for name in ('helene.exe','app/python_app.py','app/deskapp.py','runtime/python.exe','helene-svc.exe')):
                raise RuntimeError('The old uninstaller left executable programme files')
            if any(not Path(data).is_dir() for data in data_paths):
                raise RuntimeError('The old uninstaller did not retain the selected data')
            # Older native uninstallers remove only normal product/agent
            # shortcut names and leave their emergency-stop variants behind.
            # Remove remaining links by exact old target, never by display name.
            shortcuts=remove_shortcuts(old)
            return dict(receipt,method='native_uninstaller',old_processes_confirmed_dead=True,
                        shortcuts_unpinned=unpinned,shortcuts_removed=shortcuts,
                        empty_local=remove_empty_legacy_local())
        except RuntimeError:
            if time.monotonic()>=deadline:raise
            time.sleep(.2)


def request_python_exit(old, pid, *, observer_only=False):
    """Ask this installation's existing authenticated window, never close-to-tray."""
    import urllib.request
    from urllib.parse import urlsplit
    from .program_state import window_control
    path = window_control(old,pid)
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
    body = json.dumps({'command': 'python_window', 'args': {'action': 'exit', 'relaunch': False,
                      'observer_only': bool(observer_only),'owner_exit':False}}).encode()
    request = urllib.request.Request(origin + '/__python__/invoke', data=body,
        headers={'Content-Type': 'application/json', 'X-Helene-Window': value['token']})
    with http.open(request, timeout=3) as response:
        return json.load(response)


def service(old=None):
    """Use the native SCM query, without a WMI/PowerShell enumeration."""
    import psutil
    try:record=psutil.win_service_get('Helene').as_dict()
    except psutil.NoSuchProcess:return None
    value={'Name':record['name'],'State':'Stopped' if record['status']=='stopped' else 'Running',
           'PathName':record['binpath'],'ProcessId':record.get('pid')}
    return value if old is None or service_belongs_to(old,value) else None


def service_belongs_to(old,value):
    command=str(value.get('PathName','')).replace('/','\\').lower()
    root=str(old.resolve()).replace('/','\\').lower()
    # Exact quoted executable/config root component, not a product-name guess.
    return ('"'+root+'\\') in command or command.startswith(root+'\\')


def close_python(old, *, force=False):
    """Ask an identified Python window to exit, then observe its native PID."""
    if os.name!='nt':return
    import psutil
    old=Path(old).resolve()
    interpreters={old/'runtime/python.exe',old/'runtime/pythonw.exe'}
    owners=[row for row in process_snapshot() if row.get('ExecutablePath')
            and Path(row['ExecutablePath']).resolve() in interpreters
            and references_root(row.get('CommandLine',''),str(old/'app/python_app.py'))]
    for row in owners:
        request_error=None
        try:request_python_exit(old,int(row['ProcessId']))
        except (OSError,RuntimeError,ValueError) as exc:
            request_error=exc
            if force:continue
        try:
            process=psutil.Process(int(row['ProcessId']))
            if process.create_time()!=row['Birth']:continue
            process.wait(timeout=3 if force else 25)
        except psutil.NoSuchProcess:pass
        except (psutil.Error,OSError) as exc:
            if force:continue
            raise RuntimeError('Python window exit unconfirmed; installation retained') from (request_error or exc)
    if owners and not force:
        from .app_migration import discover
        for agent in discover(old)['agents']:
            if agent['value'].get('mode')!='remote' and not owner_lease_available(Path(agent['tree'])/'.desktop-owner.lock'):
                raise RuntimeError('Previous Python owner still holds its data lease')


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


def process_snapshot():
    """Native process generations; never execute/inspect an old Python module."""
    import psutil
    rows=[]
    if hasattr(psutil.process_iter,'cache_clear'):psutil.process_iter.cache_clear()
    for process in psutil.process_iter(['pid','ppid','exe','cmdline','create_time','status'],ad_value=None):
        try:
            value=process.info
            if value['status'] in (psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD):continue
            rows.append({'ProcessId':value['pid'],'ParentProcessId':value['ppid'],
                         'ExecutablePath':value['exe'],'CommandLine':subprocess.list2cmdline(value['cmdline'] or []),
                         'Birth':value['create_time']})
        except psutil.NoSuchProcess:continue
    return rows


def stop_generation(row):
    """Recheck birth, image and entry before signalling exactly this generation."""
    import psutil
    try:
        process=psutil.Process(int(row['ProcessId']))
        if (process.create_time()!=row['Birth'] or process.exe()!=row.get('ExecutablePath')
                or subprocess.list2cmdline(process.cmdline())!=(row.get('CommandLine') or '')):
            return False
        process.terminate();process.wait(timeout=10)
        return True
    except psutil.NoSuchProcess:return True
    except (psutil.AccessDenied,psutil.TimeoutExpired) as exc:
        raise RuntimeError('Windows did not confirm termination of old process '+str(row['ProcessId'])) from exc


def owned_processes(old):
    """Read-only scoped guardians and captured external descendants, in order."""
    old=Path(old).resolve();rows=process_snapshot();owned_ids=set()
    scripts=[old/p for p in ('app/python_app.py','app/deskapp.py','deskapp.py','app/localharness/runner.py','localharness/runner.py')]
    for row in rows:
        if int(row['ProcessId']) in (os.getpid(),os.getppid()):continue
        executable=row.get('ExecutablePath');command=row.get('CommandLine') or ''
        if not executable:continue
        path=Path(executable).resolve()
        if path.is_relative_to(old) or (path.name.lower() in ('python.exe','pythonw.exe') and
                any(references_root(command,str(script)) for script in scripts)):
            owned_ids.add(int(row['ProcessId']))
    while True:
        generations={int(row['ProcessId']):row.get('Birth') for row in rows}
        children=set()
        for row in rows:
            parent=int(row.get('ParentProcessId') or -1)
            if parent not in owned_ids:continue
            if row.get('Birth') is None or generations.get(parent) is None:
                raise RuntimeError('Old child process generation is unknown; new owner not admitted')
            # An orphan can retain a reused parent PID. Its earlier birth does
            # not make it a child of this newly observed supervisor generation.
            if float(row['Birth'])>=float(generations[parent]):children.add(int(row['ProcessId']))
        added=children-owned_ids-{os.getpid(),os.getppid()}
        if not added:break
        owned_ids.update(added)
    parents={int(row['ProcessId']):int(row.get('ParentProcessId') or -1) for row in rows}
    def depth(row):
        level=0;parent=parents.get(int(row['ProcessId']));seen=set()
        while parent in owned_ids and parent not in seen:
            seen.add(parent);level+=1;parent=parents.get(parent)
        return level
    result=[row for row in sorted(rows,key=depth) if int(row['ProcessId']) in owned_ids]
    for row in result:
        if not row.get('Birth'):raise RuntimeError('Old process generation is unknown; new owner not admitted')
    return result


def stop_owned_processes(old):
    """Portable fallback only: stop guardians before their captured children."""
    for row in owned_processes(old):
        stop_generation(row)


def quiescent(old):
    """Prove no selected-root references remain, using native process APIs."""
    if os.name!='nt':return True
    exempt={os.getpid(),os.getppid()}
    if any(int(row['ProcessId']) not in exempt and references_root(row.get('CommandLine',''),str(Path(old).resolve()))
           for row in process_snapshot()):
        raise RuntimeError('Old processes still reference the selected installation; new owner not admitted')
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
    allowed={'HELENE_OLD_ROOT','HELENE_SYSTEM_ACTION','HELENE_RETIRE_MANIFEST','HELENE_RETIRE_SHA256','HELENE_LINK',
             'HELENE_FIRST_ROOTS','HELENE_FIRST_DATA','HELENE_FIRST_PYTHON','HELENE_FIRST_ARGUMENTS'}
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
        import tempfile
        folder=Path(tempfile.gettempdir())/'Helene-retirement-requests'
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
    if target and Path(target).resolve().is_relative_to(root):return True
    arguments=binding.get('arguments','')
    launchers=('helene.exe','helene-host.exe','helene-setup.exe','Start Helene.vbs',
        'Helene setup and recovery.vbs','Migrate Helene.vbs','app/python_app.py','app/windows_start.py')
    if any(references_root(arguments,str(root/name)) for name in launchers):return True
    cwd=binding.get('cwd')
    return bool(cwd and Path(cwd).resolve()==root and any(references_root(arguments,name) for name in launchers))


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


def owned_shortcuts(old,folders=None):
    for folder in shortcut_folders() if folders is None else folders:
        for path in Path(folder).rglob('*.lnk') if Path(folder).is_dir() else []:
            binding=shortcut_binding(path)
            if not binding or not link_belongs(binding,old):continue
            yield path


def unpin_shortcuts(old,folders=None):
    unpinned=[]
    for path in owned_shortcuts(old,folders):
        unpin_shortcut(path)
        unpinned.append(str(path))
    return unpinned


def remove_shortcuts(old,folders=None):
    removed=[]
    for path in owned_shortcuts(old,folders):
        unpin_shortcut(path)  # failure keeps the identity and is reported
        try:path.unlink()
        except PermissionError:
            env=dict(os.environ,HELENE_LINK=str(path))
            run_privileged("$ErrorActionPreference='Stop';Remove-Item -LiteralPath $env:HELENE_LINK -Force",env)
        removed.append(str(path))
    return removed


def remove_empty_legacy_local():
    """Remove only the legacy empty placeholder; never walk data or links."""
    if os.name!='nt':return {'state':'not-windows'}
    import errno
    from deskd.local_files import _known_folder
    base=_known_folder('F1B32785-6FBA-4FCF-9D55-7B8E7F157091')
    if base is None:
        raw=os.environ.get('LOCALAPPDATA')
        if not raw:return {'state':'unavailable'}
        base=Path(raw)
    target=Path(base)/'Helene'
    from .program_state import _plain
    try:_plain(target)
    except ValueError:return {'state':'linked-retained'}
    try:target.rmdir()
    except FileNotFoundError:return {'state':'absent'}
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY,errno.EEXIST) or getattr(exc,'winerror',None)==145:
            return {'state':'nonempty-retained'}
        raise
    return {'state':'empty-removed'}


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
    return {'shortcuts_removed':remove_shortcuts(old),'legacy_uninstaller_used':False,
            'empty_local':remove_empty_legacy_local()}


def has_system_registration(old):
    """Read-only discovery; no elevation for installations with no system hooks."""
    env=dict(os.environ,HELENE_OLD_ROOT=str(Path(old).resolve()))
    raw=powershell(r'''$r=$env:HELENE_OLD_ROOT;$found=$false;foreach($p in @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')){foreach($v in Get-ItemProperty $p -ErrorAction SilentlyContinue){if($v.InstallLocation -and $v.InstallLocation.TrimEnd('\').Equals($r,[StringComparison]::OrdinalIgnoreCase)){$found=$true}}};foreach($t in Get-ScheduledTask -ErrorAction SilentlyContinue){foreach($a in $t.Actions){if($a.Execute -and $a.Execute.Trim('"').StartsWith($r+'\',[StringComparison]::OrdinalIgnoreCase)){$found=$true}}};if($found){'yes'}''',env)
    return raw=='yes'


def remove_program(old,new,note,*,before_start=False,progress=None,cancelled=None):
    """Retire stopped program/code files while preserving data and model roots.

    Older packages lack a passport: only known program directories/entries are
    admitted. No recursive deletion of the installation or data is performed.
    """
    import hashlib,stat,time
    old,new=Path(old).resolve(),Path(new).resolve()
    if old==new or new.is_relative_to(old) or old.is_relative_to(new):
        raise ValueError('Unsafe old-program retirement')
    def check():
        if cancelled and cancelled.is_set():raise InterruptedError('Owner stopped old-program retirement')
    check()
    if before_start:
        if note.get('source_policy')!='clean' or not note.get('old_quiescent'):
            raise ValueError('Early retirement requires a quiescent clean migration')
        quiescent(old)
        if any(not owner_lease_available(Path(p)/'.desktop-owner.lock') for p in note['data_paths']):
            raise RuntimeError('Old data ownership is still held; program retained')
    elif not note.get('startup_proof'):raise ValueError('Unsafe old-program retirement')
    if not any((old/p).exists() for p in ('helene.exe','Helene.exe','helene-setup.exe','helene-body.exe',
            'helene-bridge.exe','helene-svc.exe','helene-host.exe','app/python_app.py','python-app.json','helene.json','runtime','app')):
        return {'ok':True,'previous_program_already_absent':True,'purge':False}
    protected=[Path(p).resolve() for p in note['data_paths']+note.get('old_data_paths',[])]
    protected += [old/name for name in ('data','profile','agents','backups','archives','memory','models')]
    def retained(path):
        return any(path==p or path.is_relative_to(p) for p in protected)
    def linked(path):
        info=path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0)&0x400)
    def signature(path):
        info=path.lstat()
        return {'size':info.st_size,'mtime_ns':info.st_mtime_ns,'inode':info.st_ino}
    last=0.0
    def publish(**counts):
        nonlocal last
        if progress and (counts.get('force') or time.monotonic()-last>=.25):
            counts.pop('force',None);progress({'stage':'removing_old',**counts});last=time.monotonic()
    names=set()
    inventory=old/'python-package-files.json'
    declared=json.loads(inventory.read_text(encoding='utf-8')).get('files',{}) if inventory.is_file() else {}
    if note.get('normal_python_update'):
        declared={**note.get('old_runtime_code_inventory',{}),**declared}
    names.update(declared)
    program_roots={old/name for name in ('app','runtime','resources','components','bin','tree','code','code-backup','voice','voice-xtts','relay-product')}
    program_roots.update(Path(row['old']).resolve() for row in note.get('source_paths',[])
        if Path(row['old']).resolve()!=old and Path(row['old']).resolve().is_relative_to(old))
    for folder in sorted(program_roots):
        if folder.is_dir() and not linked(folder) and not retained(folder):
            for directory,dirs,entries in os.walk(folder,followlinks=False):
                check();here=Path(directory)
                dirs[:]=[n for n in dirs if n.lower() not in ('models','model','memory','.secrets')
                    and not retained(here/n) and not linked(here/n)]
                names.update((here/n).relative_to(old).as_posix() for n in entries if not linked(here/n))
                publish(inventoried_files=len(names),files=0)
    names.update(name for name in ('helene.exe','Helene.exe','helene-body.exe','helene-bridge.exe','helene-setup.exe','helene-svc.exe','helene-service.exe',
        'helene-host.exe','helene-relay.exe','helene-relay','uninstall.exe','python-app.json','python-package-files.json',
        'Start Helene.vbs','Helene setup and recovery.vbs','Migrate Helene.vbs') if (old/name).is_file())
    files={};seen=set()
    for name in sorted(names):
        check()
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid old program inventory')
        path=old/relative;resolved=path.resolve()
        identity=os.path.normcase(str(resolved))
        if identity in seen:continue
        seen.add(identity)
        if not resolved.is_relative_to(old) or resolved==old:raise ValueError('Old program inventory leaves installation')
        if retained(resolved):continue
        if path.name in ('helene.json','helene.json.bak','owner-stop.json','agent-default.json','window-preferences.json',
                '.env','auth.json','credentials.json') or not path.is_file():continue
        if any(part.lower() in ('memory','.secrets') for part in relative.parts):continue
        if any(part.lower() in ('models','model') for part in relative.parts):
            from .python_archive_update import declared_runtime_file
            if not note.get('normal_python_update') or not declared_runtime_file(old,name,declared):continue
        if path.is_symlink() or any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in [path,*path.parents] if p.is_relative_to(old)):
            raise ValueError('Linked program path cannot be retired')
        files[name]=signature(path)
    # Save the exact deletion list before the first mutation.
    from .package_notice import write
    write(new/'old-program-retirement.json',{'old':str(old),'new':str(new),'files':files,'protected':[str(p) for p in protected],
        'policy':'quiescent-clean-program-only;no-code-backup','inventory':'file-metadata'})
    remaining={};removed=0;removed_bytes=0
    publish(files=0,total_files=len(files),force=True)
    for name,expected in files.items():
        check()
        path=old/name
        if (any(linked(p) for p in (path,*path.parents) if p.is_relative_to(old))
                or signature(path)!=expected):raise RuntimeError('Old program changed before retirement')
        try:path.unlink();removed+=1;removed_bytes+=expected['size']
        except PermissionError:
            info=path.lstat()
            if not info.st_mode&stat.S_IWRITE and info.st_nlink==1:
                path.chmod(info.st_mode|stat.S_IWRITE)
                try:
                    path.unlink();removed+=1;removed_bytes+=expected['size']
                    publish(files=removed,total_files=len(files),bytes=removed_bytes);continue
                except PermissionError:pass
            # Elevated deletion still receives an exact digest-bound list;
            # ordinary per-user retirement need not read gigabytes twice.
            with path.open('rb') as stream:remaining[name]=hashlib.file_digest(stream,'sha256').hexdigest()
        publish(files=removed,total_files=len(files),bytes=removed_bytes)
    if remaining:system_action(old,'remove_files',remaining)
    if any((old/name).exists() for name in files):raise RuntimeError('Some old program files remain')
    publish(files=len(files),total_files=len(files),bytes=sum(v['size'] for v in files.values()),force=True)
    launchers=[name for name in ('helene.exe','Helene.exe','helene-setup.exe','app/python_app.py') if (old/name).is_file()]
    return {'ok':not launchers,'removed_files':len(files),'retained_program_entries':launchers,'purge':False,'data_retained':True}
