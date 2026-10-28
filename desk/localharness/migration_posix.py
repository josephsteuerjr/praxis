"""Bounded macOS/Linux handoff; stop only the selected installation's writers."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import time

from .package_notice import write


def _inside(value, root):
    try:
        path=Path(value).resolve()
        return path==root or path.is_relative_to(root)
    except (OSError,ValueError,TypeError):return False


def _processes(old):
    import psutil
    old=Path(old).resolve();ancestors={os.getpid()}
    result=[]
    for process in psutil.process_iter(['pid','exe','cmdline','create_time','status']):
        try:
            info=process.info
            if process.pid in ancestors or info['status'] in (psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD):continue
            executable=Path(info['exe'] or '').name.lower()
            script_owner=(executable.startswith(('python','node')) or executable in {'sh','bash'})
            if _inside(info['exe'],old) or script_owner and any(_inside(arg,old) and Path(arg).resolve()!=old for arg in (info['cmdline'] or [])[1:] if not arg.startswith('-')):
                result.append((process,info))
        except (psutil.NoSuchProcess,psutil.AccessDenied):continue
    return result


def _wait_closed(process,timeout):
    """Non-child zombies retain a PID until their parent reaps, but no files."""
    import psutil
    deadline=time.monotonic()+timeout
    while True:
        try:
            if not process.is_running() or process.status() in (psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD):return True
        except psutil.NoSuchProcess:return True
        if time.monotonic()>=deadline:return False
        time.sleep(.05)


def _run(args, *, elevated=False):
    if elevated:
        if sys.platform=='darwin':
            command=shlex.join([str(x) for x in args])
            args=['osascript','-e','do shell script '+json.dumps(command,ensure_ascii=False)+' with administrator privileges']
        else:
            if not shutil.which('pkexec'):raise RuntimeError('Для остановки системной службы нужен pkexec; останови службу и повтори установку')
            args=['pkexec',*args]
    done=subprocess.run(args,capture_output=True,text=True,timeout=90)
    if done.returncode:raise RuntimeError((done.stderr or done.stdout).strip() or 'Service operation refused')
    return done.stdout


def service(old):
    """Discover only a service whose executable arguments bind it to this root."""
    old=Path(old).resolve();found=[]
    if sys.platform=='darwin':
        for directory,domain,elevated in ((Path.home()/'Library/LaunchAgents',f'gui/{os.getuid()}',False),
                (Path('/Library/LaunchAgents'),f'gui/{os.getuid()}',True),(Path('/Library/LaunchDaemons'),'system',True)):
            for path in directory.glob('*.plist'):
                try:
                    value=plistlib.loads(path.read_bytes());args=[value.get('Program',''),*value.get('ProgramArguments',[])]
                    if not any(_inside(arg,old) for arg in args if arg):continue
                    label=value['Label'];target=domain+'/'+label
                    state=subprocess.run(['launchctl','print',target],capture_output=True,text=True,timeout=10)
                    found.append({'kind':'launchctl','path':str(path),'target':target,'elevated':elevated,'running':state.returncode==0})
                except (OSError,ValueError,KeyError):continue
    else:
        for directory,user in ((Path.home()/'.config/systemd/user',True),(Path('/etc/systemd/system'),False)):
            for path in directory.glob('*.service'):
                if path.is_symlink():continue
                try:
                    text=path.read_text();commands=[line.partition('=')[2] for line in text.splitlines() if line.startswith('ExecStart=')]
                    if not any(_inside(arg.lstrip('-:@+!'),old) for command in commands for arg in shlex.split(command)):continue
                    command=['systemctl',*(['--user'] if user else [])]
                    active=subprocess.run([*command,'is-active',path.name],capture_output=True,timeout=10)
                    found.append({'kind':'systemd','path':str(path),'unit':path.name,'command':command,'elevated':not user,'running':active.returncode==0})
                except (OSError,ValueError):continue
    return {'State':'Running' if any(row['running'] for row in found) else 'Stopped','services':found} if found else None


def stop(old):
    before=service(old)
    for row in (before or {}).get('services',[]):
        if not row['running']:continue
        command=[*row['command'],'stop',row['unit']] if row['kind']=='systemd' else ['launchctl','bootout',row['target']]
        _run(command,elevated=row['elevated'])
    # Try the authenticated window exit before terminating legacy processes.
    from .migration_windows import request_python_exit
    for process,info in _processes(old):
        if any(Path(arg).name=='python_app.py' for arg in info['cmdline'] or []):
            try:request_python_exit(Path(old),process.pid)
            except (OSError,ValueError,RuntimeError):pass
    import psutil
    for process,info in _processes(old):
        try:
            if _wait_closed(process,0):continue
            if process.create_time()!=info['create_time'] or process.cmdline()!=info['cmdline']:
                if _wait_closed(process,0):continue
                raise RuntimeError('Old process identity changed')
            process.terminate()
            if not _wait_closed(process,8):
                process.kill()
                if not _wait_closed(process,5):raise RuntimeError('Прежний процесс не завершился: '+str(process.pid))
        except psutil.NoSuchProcess:pass
    quiescent(old)
    return {'service':before}


def close_python(old):
    return stop(old)


def quiescent(old):
    if _processes(old):raise RuntimeError('Прежняя Hélène ещё использует папку; установка сохранена для повтора')
    from .app_migration import discover
    if (Path(old)/'helene.json').is_file():
        for row in discover(Path(old))['agents']:
            if row['value'].get('mode')!='remote' and not owner_lease_available(Path(row['tree'])/'.desktop-owner.lock'):
                raise RuntimeError('Прежняя Hélène ещё держит папку данных')
    return True


def owner_lease_available(path):
    import fcntl
    if not Path(path).exists():return True
    with Path(path).open('rb') as stream:
        try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return False
        fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    return True


def restore_service(old,before):
    if not before:return False
    current=service(old)
    def identity(rows):return [{k:v for k,v in row.items() if k!='running'} for row in rows]
    if current is None or identity(current['services'])!=identity(before['services']):
        raise RuntimeError('Previous service registration changed')
    for row in before['services']:
        if row['running']:
            command=[*row['command'],'start',row['unit']] if row['kind']=='systemd' else ['launchctl','bootstrap',row['target'].rsplit('/',1)[0],row['path']]
            _run(command,elevated=row['elevated'])
    return any(row['running'] for row in before['services'])


def cleanup_installation(old,new):
    before=service(old)
    for row in (before or {}).get('services',[]):
        if row['running']:raise RuntimeError('Old service restarted; retirement refused')
        if row['kind']=='systemd':_run([*row['command'],'disable',row['unit']],elevated=row['elevated'])
        else:_run(['launchctl','disable',row['target']],elevated=row['elevated'])
    removed=[]
    for folder in (Path.home()/'Desktop',Path.home()/'.local/share/applications',Path.home()/'.config/autostart'):
        for path in folder.glob('*.desktop'):
            if path.is_symlink():continue
            if desktop_binding(path,old):
                path.unlink();removed.append(str(path))
        for path in folder.glob('*.app'):
            if path.is_symlink():
                if _inside(path.resolve(),Path(old).resolve()):path.unlink();removed.append(str(path))
            elif desktop_binding(path,old):
                for relative in ('Contents/MacOS/Helene','Contents/Info.plist'):(path/relative).unlink()
                for relative in ('Contents/MacOS','Contents',''):
                    try:(path/relative).rmdir()
                    except OSError:pass
                removed.append(str(path))
    return {'shortcuts_removed':removed,'services_disabled':bool(before),'legacy_uninstaller_used':False}


def desktop_binding(path,root):
    """Recognize an executable binding, never an unrelated app's data argument."""
    path,root=Path(path),Path(root).resolve()
    try:
        if path.suffix=='.app':
            if path.is_symlink():return False
            value=plistlib.loads((path/'Contents/Info.plist').read_bytes())
            if value.get('CFBundleIdentifier')!='ai.helene.desktop-shortcut':return False
            script=(path/'Contents/MacOS/Helene').read_text()
            commands=[line[5:] for line in script.splitlines() if line.startswith('exec ')]
        else:
            commands=[line.partition('=')[2] for line in path.read_text().splitlines() if line.startswith(('Exec=','TryExec='))]
        for command in commands:
            args=shlex.split(command)
            if not args:continue
            if _inside(args[0],root):return True
            if Path(args[0]).name.startswith(('python','node')) or Path(args[0]).name in ('sh','bash'):
                if any(_inside(arg,root) and Path(arg).resolve()!=root for arg in args[1:] if not arg.startswith('-')):return True
    except (OSError,ValueError,plistlib.InvalidFileException):pass
    return False


def install_shortcuts(new,folder=None,*,desktop_only=False):
    new=Path(new).resolve();desktop=Path(folder) if folder else Path.home()/'Desktop';desktop.mkdir(parents=True,exist_ok=True)
    if sys.platform=='darwin':
        target=desktop/'Helene.app';contents=target/'Contents';binary=contents/'MacOS/Helene'
        if target.exists() and not desktop_binding(target,new):raise ValueError('Desktop Hélène item belongs to another installation; retained')
        binary.parent.mkdir(parents=True,exist_ok=True)
        binary.write_text('#!/bin/sh\nexec '+shlex.join([str(new/'runtime/bin/python3'),'-s',str(new/'app/python_app.py'),'--root',str(new)])+'\n')
        binary.chmod(0o755)
        (contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'Helene','CFBundleName':'Hélène','CFBundleIdentifier':'ai.helene.desktop-shortcut','CFBundlePackageType':'APPL'}))
    else:
        target=desktop/'Helene.desktop'
        if target.exists() and not desktop_binding(target,new):raise ValueError('Desktop Hélène item belongs to another installation; retained')
        def quoted(value):return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')+'"'
        target.write_text('[Desktop Entry]\nType=Application\nName=Hélène\nExec='+ ' '.join(quoted(v) for v in (new/'runtime/bin/python3','-s',new/'app/python_app.py','--root',new))+'\nTerminal=false\n')
        target.chmod(0o755)
    return [str(target)]


def remove_program(old,new,note):
    """Delete only verified program files, keeping attached data and old source."""
    old,new=Path(old).resolve(),Path(new).resolve()
    if old==new or new.is_relative_to(old) or not note.get('startup_proof'):raise ValueError('Unsafe retirement')
    protected=[Path(p).resolve() for p in note.get('data_paths',[])+note.get('old_data_paths',[])]+[Path(row['old']).resolve() for row in note.get('source_paths',[]) if row.get('old')]
    protected += [old/name for name in ('data','profile','agents','backups','archives','memory','models','tree','code','code-backup')]
    names=set()
    for directory in ('app','runtime','resources','bin','Helene.app','Migrate Helene.app'):
        folder=old/directory
        if folder.is_dir() and not folder.is_symlink():names.update(p.relative_to(old).as_posix() for p in folder.rglob('*') if p.is_file())
    names.update(n for n in ('python-app.json','python-package-files.json','Start Helene.command','Migrate Helene.command','Start Helene.sh','helene','helene-body','helene-bridge') if (old/n).is_file())
    files={}
    for name in sorted(names):
        path=old/name;resolved=path.resolve()
        if not resolved.is_relative_to(old) or path.is_symlink() or any(p.is_symlink() for p in path.parents if p.is_relative_to(old)):raise ValueError('Linked program path')
        if any(resolved==p or resolved.is_relative_to(p) for p in protected):continue
        if any(part.lower() in ('models','model','memory','.secrets') for part in path.relative_to(old).parts):continue
        files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    write(new/'old-program-retirement.json',{'old':str(old),'new':str(new),'files':files,'protected':[str(p) for p in protected]})
    for name,digest in files.items():
        path=old/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('Old program changed')
        path.unlink()
    return {'ok':True,'removed_files':len(files),'purge':False,'data_retained':True}


def registered_uninstall(old):
    return None


def retire(old,new):
    return cleanup_installation(old,new)
