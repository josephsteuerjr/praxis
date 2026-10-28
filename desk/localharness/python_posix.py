"""Unprivileged desktop integration; never install a system service/broker."""
import hashlib
import os
from pathlib import Path
import sys


def startup_binding(root):
    root=Path(root).resolve()
    identity=hashlib.sha256(str(root).encode()).hexdigest()[:12]
    if sys.platform=='darwin':
        import plistlib
        from .python_platform import desktop_argv
        label='app.helene.python.'+identity
        value={'Label':label,'ProgramArguments':desktop_argv(root,['--root',str(root),'--launch-intent','automatic']),
               'RunAtLoad':True,'KeepAlive':False,'LimitLoadToSessionType':'Aqua',
               'ProcessType':'Interactive','WorkingDirectory':str(root),
               'StandardOutPath':str(root/'autostart.log'),'StandardErrorPath':str(root/'autostart.log')}
        return Path.home()/'Library/LaunchAgents'/(label+'.plist'),plistlib.dumps(value).decode('utf-8')
    name='helene-'+identity+'.desktop'
    base=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))
    # Desktop Exec values are not shell text. Escape the desktop-entry grammar,
    # including percent field codes, rather than interpolating a shell command.
    def quote(value):
        value=str(value)
        if any(c in value for c in '\n\r\0'):raise ValueError('Invalid desktop entry path')
        return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')+'"'
    argv=[root/'runtime/bin/python3',root/'app/python_app.py','--root',root,'--launch-intent','automatic']
    text='[Desktop Entry]\nType=Application\nName=Hélène\nExec='+ ' '.join(quote(x) for x in argv)+'\nTerminal=false\n'
    return base/'autostart'/name,text


def invoke(owner,command,args):
    if command in ('install_service','remove_service','broker_resume','service_state','admin_state'):
        raise RuntimeError('Privileged broker/system service is absent from the Python application')
    if command=='notify':
        owner.event('notification',{'title':str(args.get('title','')),'body':str(args.get('body',''))})
        return None
    if command in ('autostart_get','autostart_set'):
        if not (sys.platform.startswith('linux') or sys.platform=='darwin'):
            raise RuntimeError('Unsupported POSIX login integration')
        path,text=startup_binding(owner.root)
        if command=='autostart_get':return path.is_file() and path.read_text(encoding='utf-8')==text
        if type(args.get('on')) is not bool:raise ValueError('Autostart on must be boolean')
        if path.exists() and (path.is_symlink() or not path.is_file() or path.read_text(encoding='utf-8')!=text):
            raise RuntimeError('Autostart entry differs; existing bytes retained')
        if args['on']:
            if path.is_file():return None
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x',encoding='utf-8') as out:out.write(text)
        else:path.unlink(missing_ok=True)
        return None
    raise RuntimeError('This OS does not expose the requested desktop integration: '+command)
