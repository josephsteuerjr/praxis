"""Owner-requested Windows integration; no ordinary native host dependency."""
import ctypes
import json
import os
from pathlib import Path
import subprocess


def powershell(script,env=None):
    exe=Path(os.environ.get('SYSTEMROOT','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-Command',script],
                          env=env,capture_output=True,timeout=30,creationflags=0x08000000)
    if result.returncode:raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
    return result.stdout.decode('utf-8-sig').strip()


def shortcut(path,target,cwd,arguments=''):
    path.parent.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,HELENE_LINK=str(path),HELENE_TARGET=str(target),HELENE_WORKDIR=str(cwd),HELENE_ARGUMENTS=arguments)
    powershell("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:HELENE_LINK);$s.TargetPath=$env:HELENE_TARGET;$s.WorkingDirectory=$env:HELENE_WORKDIR;$s.Arguments=$env:HELENE_ARGUMENTS;$s.IconLocation=$env:HELENE_WORKDIR+'\\app\\resources\\python-app.ico,0';$s.Save()",env)
    if not path.is_file():raise RuntimeError('Shortcut was not created')


def invoke(owner,command,args):
    if command in ('install_service','remove_service','broker_resume','service_state','admin_state'):
        raise RuntimeError('Брокер и привилегированная служба недоступны в Python-поставке Hélène.')
    if os.name!='nt':raise RuntimeError('Windows integration unavailable on this platform')
    startup=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/Helene.lnk'
    if command=='autostart_get':return startup.is_file()
    if command=='autostart_set':
        if args['on']:shortcut(startup,owner.root/'helene.exe',owner.root)
        else:startup.unlink(missing_ok=True)
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
