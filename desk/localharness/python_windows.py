"""Owner-requested Windows integration; no ordinary native host dependency."""
import ctypes
import json
import os
from pathlib import Path
import subprocess


AUTOMATIC_ARGUMENTS='--launch-intent automatic'


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
    if not path.is_file():return None
    env=dict(os.environ,HELENE_LINK=str(path))
    raw=powershell("$ErrorActionPreference='Stop';[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:HELENE_LINK);@{target=$s.TargetPath;cwd=$s.WorkingDirectory;arguments=$s.Arguments}|ConvertTo-Json -Compress",env)
    value=json.loads(raw)
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
    path.parent.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,HELENE_LINK=str(path),HELENE_TARGET=str(target),HELENE_WORKDIR=str(cwd),HELENE_ARGUMENTS=arguments)
    powershell("$ErrorActionPreference='Stop';$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:HELENE_LINK);$s.TargetPath=$env:HELENE_TARGET;$s.WorkingDirectory=$env:HELENE_WORKDIR;$s.Arguments=$env:HELENE_ARGUMENTS;$s.IconLocation=$env:HELENE_WORKDIR+'\\app\\resources\\python-app.ico,0';$s.Save()",env)
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
    if command=='autostart_get':return shortcut_matches(shortcut_binding(startup),executable,owner.root,automatic)
    if command=='autostart_set':
        if type(args.get('on'))is not bool:raise ValueError('Autostart on must be a boolean')
        prior=shortcut_binding(startup)
        if prior and (not same_path(prior['target'],executable) or
                      prior['cwd'] and not same_path(prior['cwd'],owner.root) or
                      prior['arguments'] not in ('',automatic)):
            raise RuntimeError('Startup Helene.lnk belongs to another launch binding; it was not changed')
        if startup.exists() and prior is None:raise RuntimeError('Startup path is not a shortcut file')
        if args['on']:shortcut(startup,owner.root/'helene.exe',owner.root,AUTOMATIC_ARGUMENTS)
        else:
            startup.unlink(missing_ok=True)
            if startup.exists():raise RuntimeError('Autostart shortcut removal was not confirmed')
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
