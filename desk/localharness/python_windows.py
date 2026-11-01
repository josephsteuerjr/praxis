"""Owner-requested Windows integration; no ordinary native host dependency."""
import ctypes
import json
import os
from pathlib import Path
import subprocess


AUTOMATIC_ARGUMENTS='--launch-intent automatic'
RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME='Helene'


def run_binding():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
            value,kind=winreg.QueryValueEx(key,RUN_NAME)
    except FileNotFoundError:return None
    if kind!=winreg.REG_SZ or not isinstance(value,str):raise RuntimeError('Invalid Hélène login entry')
    return value


def run_command(root):
    executable,arguments=launch_binding(root,AUTOMATIC_ARGUMENTS)
    return '"'+str(executable)+'" '+arguments


def run_root(command):
    """Only canonical Hélène entry arguments permit retargeting a moved app."""
    from ctypes import wintypes as w
    shell=ctypes.WinDLL('shell32');kernel=ctypes.WinDLL('kernel32')
    shell.CommandLineToArgvW.argtypes=[w.LPCWSTR,ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype=ctypes.POINTER(w.LPWSTR)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    count=ctypes.c_int();pointer=shell.CommandLineToArgvW(command,ctypes.byref(count))
    if not pointer:return None
    try:args=[pointer[i] for i in range(count.value)]
    finally:kernel.LocalFree(ctypes.cast(pointer,ctypes.c_void_p))
    if len(args)==3 and Path(args[0]).name.casefold()=='helene.exe' and args[1:]==['--launch-intent','automatic']:
        return Path(args[0]).parent
    if len(args)==7 and args[1]=='-s' and args[3]=='--root' and args[-2:]==['--launch-intent','automatic']:
        root=Path(args[4])
        if same_path(args[0],root/'runtime/pythonw.exe') and same_path(args[2],root/'app/python_app.py'):return root
    return None


def owned_run(command):return run_root(command) is not None


def retarget_run(roots,current):
    """An accepted update keeps the owner's existing login preference."""
    command=run_binding()
    if command is None:return
    previous=run_root(command)
    if previous is not None and any(same_path(previous,root) for root in roots):
        set_run(run_command(current) if current is not None else None)


def set_run(command):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_QUERY_VALUE|winreg.KEY_SET_VALUE) as key:
        if command is None:
            try:winreg.DeleteValue(key,RUN_NAME)
            except FileNotFoundError:pass
        else:winreg.SetValueEx(key,RUN_NAME,0,winreg.REG_SZ,command)
    if run_binding()!=command:raise RuntimeError('Login entry did not match after saving')


def powershell(script,env=None):
    exe=Path(os.environ.get('SYSTEMROOT','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    # Windows PowerShell 5.1 otherwise writes redirected paths in the OEM code
    # page. Every owned result/error stream has one explicit Unicode contract.
    script="[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);$OutputEncoding=[Console]::OutputEncoding;\n"+script
    result=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-Command',script],
                          env=env,capture_output=True,timeout=30,creationflags=0x08000000)
    if result.returncode:raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
    return result.stdout.decode('utf-8-sig').strip()


def shortcut_binding(path):
    from .windows_shortcuts import read
    value=read(path)
    if value is None:return None
    if not isinstance(value,dict) or any(not isinstance(value.get(key),str) for key in ('target','cwd','arguments')):
        raise RuntimeError('Invalid shortcut binding')
    return value


def same_path(actual,expected):
    return bool(actual) and os.path.normcase(os.path.abspath(actual))==os.path.normcase(os.path.abspath(expected))


def shortcut_matches(binding,target,cwd,arguments):
    return bool(binding and same_path(binding['target'],target) and same_path(binding['cwd'],cwd) and binding['arguments']==arguments)


def shortcut(path,target,cwd,arguments=''):
    if Path(target).name.lower()=='helene.exe' and not Path(target).exists() and (Path(cwd)/'runtime/pythonw.exe').is_file():
        target,arguments=launch_binding(cwd,arguments)
    from .windows_shortcuts import write
    write(path,target,cwd,arguments,Path(cwd)/'app/resources/python-app.ico')
    if not shortcut_matches(shortcut_binding(path),target,cwd,arguments):
        raise RuntimeError('Shortcut target, working directory or arguments did not match after saving')


def launch_binding(root,arguments=''):
    root=Path(root)
    if (root/'helene.exe').is_file():return root/'helene.exe',arguments
    return root/'runtime/pythonw.exe',subprocess.list2cmdline(['-s',str(root/'app/python_app.py'),'--root',str(root)])+(' '+arguments if arguments else '')


def invoke(owner,command,args):
    if command in ('install_service','remove_service','broker_resume','service_state','admin_state'):
        raise RuntimeError('Брокер и привилегированная служба недоступны в Python-поставке Hélène.')
    if os.name!='nt':raise RuntimeError('Windows integration unavailable on this platform')
    startup=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/Helene.lnk'
    executable,automatic=launch_binding(owner.root,AUTOMATIC_ARGUMENTS)
    if command=='autostart_get':
        return run_binding()==run_command(owner.root) or shortcut_matches(shortcut_binding(startup),executable,owner.root,automatic)
    if command=='autostart_set':
        if type(args.get('on'))is not bool:raise ValueError('Autostart on must be a boolean')
        prior=shortcut_binding(startup)
        if prior and (not same_path(prior['target'],executable) or
                      prior['cwd'] and not same_path(prior['cwd'],owner.root) or
                      prior['arguments'] not in ('',automatic)):
            raise RuntimeError('Startup Helene.lnk belongs to another launch binding; it was not changed')
        if startup.exists() and prior is None:raise RuntimeError('Startup path is not a shortcut file')
        current=run_binding();expected=run_command(owner.root)
        if current is not None and current!=expected and not (args['on'] and owned_run(current)):
            raise RuntimeError('Login entry belongs to another launch binding; it was not changed')
        if args['on']:
            # Existing valid shortcuts remain supported. New registrations use
            # Windows' per-user Run API, without WScript or an elevated helper.
            if prior is None:set_run(expected)
            elif prior['arguments']!=automatic:
                set_run(expected)
                try:startup.unlink()
                except OSError:
                    set_run(current)
                    raise
        else:
            startup.unlink(missing_ok=True)
            if startup.exists():raise RuntimeError('Autostart shortcut removal was not confirmed')
            if current is not None:set_run(None)
        return None
    if command in ('firewall_allow','firewall_clear'):
        port=args['port']
        if type(port)is not int or not 1<=port<=65535:raise ValueError('Invalid port')
        binary=Path(os.environ['SYSTEMROOT'])/'System32/netsh.exe'
        rule='Helene Python '+str(port)
        argv=['advfirewall','firewall','delete','rule','name='+rule]
        if command=='firewall_allow':argv=['advfirewall','firewall','add','rule','name='+rule,'dir=in','action=allow','protocol=TCP','localport='+str(port)]
        result=ctypes.windll.shell32.ShellExecuteW(None,'runas',str(binary),subprocess.list2cmdline(argv),None,0)
        if result<=32:raise RuntimeError('Firewall elevation refused')
        return 'Запрос правила передан Windows.'
    if command=='open_privacy_pane':
        kinds={'microphone':'ms-settings:privacy-microphone','camera':'ms-settings:privacy-webcam','notifications':'ms-settings:notifications'}
        os.startfile(kinds[args['kind']]);return None
    if command=='notify':
        # The window owns presentation; never launch a notification subprocess.
        owner.event('notification',{'title':str(args.get('title','')),'body':str(args.get('body',''))})
        return None
    raise ValueError('Unknown Windows operation')
