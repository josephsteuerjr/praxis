"""Bounded macOS/Linux handoff; stop only the selected installation's writers."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
import time

from .package_notice import read,write


def _inside(value, root, *, cwd=None):
    try:
        path=Path(value)
        if not path.is_absolute():
            if cwd is None:return False
            path=Path(cwd)/path
        path=path.resolve()
        return path==root or path.is_relative_to(root)
    except (OSError,ValueError,TypeError):return False


def _entry_inside(args,root,cwd):
    args=iter(args)
    for arg in args:
        if arg in ('-c','--eval','-e','-m','-p','--print'):return False
        if arg in ('-W','-X','-r','--require','--loader','--rcfile'):
            next(args,None);continue
        if arg.startswith('-'):continue
        path=Path(arg)
        # Only the entry script identifies an externally hosted interpreter.
        # Later data/status filenames do not grant authority to stop it.
        if not path.is_absolute():
            base=cwd() if callable(cwd) else cwd
            if not base:return False
            path=Path(base)/path
        return path.is_file() and path.resolve()!=root and _inside(path,root)
    return False


def _command_inside(args,root,cwd=None):
    if not args:return False
    executable=args[0].lstrip('-:@+!')
    if _inside(executable,root):return True
    name=Path(executable).name.lower()
    return (name.startswith(('python','node')) or name in {'sh','bash'}) and _entry_inside(args[1:],root,cwd)


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
            if _inside(info['exe'],old) or script_owner and _entry_inside((info['cmdline'] or [])[1:],old,process.cwd):
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
                    value=plistlib.loads(path.read_bytes());args=list(value.get('ProgramArguments') or [])
                    if value.get('Program'):args=[value['Program'],*args[1:]]
                    if not _command_inside(args,old,value.get('WorkingDirectory')):continue
                    label=value['Label'];target=domain+'/'+label
                    state=subprocess.run(['launchctl','print',target],capture_output=True,text=True,timeout=10)
                    found.append({'kind':'launchctl','path':str(path),'target':target,'elevated':elevated,'running':state.returncode==0})
                except (OSError,ValueError,KeyError):continue
    else:
        for directory,user in ((Path.home()/'.config/systemd/user',True),(Path('/etc/systemd/system'),False)):
            for path in directory.glob('*.service'):
                if path.is_symlink():continue
                try:
                    lines=[line.strip() for line in path.read_text().splitlines()]
                    commands=[line.partition('=')[2] for line in lines if line.startswith('ExecStart=')]
                    cwd=next((line.partition('=')[2] for line in lines if line.startswith('WorkingDirectory=')),None)
                    if not any(_command_inside(shlex.split(command),old,cwd) for command in commands):continue
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
                # Qt/guardians can exec between discovery and shutdown. Rebind
                # to the same birth identity and current selected-root scope.
                current=next(((p,row) for p,row in _processes(old)
                              if p.pid==process.pid and row['create_time']==info['create_time']),None)
                if current is None:continue
                process,info=current
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


def remove_program(old,new,note,*,before_start=False,progress=None,cancelled=None):
    """Retire stopped program/code files, preserving data, models and links."""
    def check():
        if cancelled and cancelled.is_set():raise InterruptedError('Migration retirement cancelled')
    check()
    old,new=Path(old).resolve(),Path(new).resolve()
    if old==new or new.is_relative_to(old) or old.is_relative_to(new):raise ValueError('Unsafe retirement')
    if before_start:
        if note.get('source_policy')!='clean' or not note.get('old_quiescent'):raise ValueError('Early retirement requires a quiescent clean migration')
        quiescent(old)
        from .migration_windows import owner_lease_available
        if any(not owner_lease_available(Path(p)/'.desktop-owner.lock') for p in note['data_paths']):raise RuntimeError('Data ownership is still held')
    elif not note.get('startup_proof'):raise ValueError('Unsafe retirement')
    protected=[Path(p).resolve() for p in note.get('data_paths',[])+note.get('old_data_paths',[])]
    protected += [old/name for name in ('data','profile','agents','backups','archives','memory','models')]
    clean=note.get('source_policy')=='clean'
    if note.get('normal_python_update'):
        from .python_archive_update import validate_retirement
        validate_retirement(old,new,note)
        clean=True  # the old copy may retire only after its preserved successor
    if not clean:
        protected += [Path(row['old']).resolve() for row in note.get('source_paths',[]) if row.get('old')]
        protected += [old/name for name in ('tree','code','code-backup')]
    def retained(path):return any(path==p or path.is_relative_to(p) for p in protected)
    def linked(path):
        info=path.lstat()
        return path.is_symlink() or bool(getattr(info,'st_file_attributes',0)&0x400)
    roots={old/name for name in ('app','runtime','resources','bin','Helene.app','Migrate Helene.app')}
    if clean:
        roots.update(old/name for name in ('tree','code','code-backup','components','voice','voice-xtts','relay-product'))
        roots.update(Path(row['old']).resolve() for row in note.get('source_paths',[])
                     if row.get('old') and Path(row['old']).resolve()!=old and Path(row['old']).resolve().is_relative_to(old))
    names=set()
    declared={}
    if note.get('normal_python_update'):
        declared={**note.get('old_runtime_code_inventory',{}),**read(old/'python-package-files.json').get('files',{})}
        names.update(declared)
    for folder in sorted(roots):
        check()
        if not folder.is_dir() or linked(folder) or retained(folder):continue
        for directory,dirs,entries in os.walk(folder,followlinks=False):
            check();here=Path(directory)
            dirs[:]=[name for name in dirs if name.lower() not in ('models','model','memory','.secrets')
                     and not retained(here/name) and not linked(here/name)]
            names.update((here/name).relative_to(old).as_posix() for name in entries if not linked(here/name))
    entries=('python-app.json','python-package-files.json','Start Helene.command','Migrate Helene.command','Start Helene.sh',
             'helene','helene-body','helene-bridge','helene.exe','Helene.exe','helene-setup.exe','helene-body.exe',
             'helene-bridge.exe','helene-svc.exe','helene-host.exe')
    names.update(name for name in entries if (old/name).is_file())
    files={}
    for name in sorted(names):
        check();path=old/name
        if not path.exists() and not path.is_symlink():continue
        resolved=path.resolve()
        if not resolved.is_relative_to(old) or linked(path) or any(p.is_symlink() for p in path.parents if p.is_relative_to(old)):raise ValueError('Linked program path')
        if path.name in ('helene.json','helene.json.bak','owner-stop.json','agent-default.json','window-preferences.json',
                         '.env','auth.json','credentials.json'):continue
        parts=path.relative_to(old).parts
        if retained(resolved) or any(part.lower() in ('memory','.secrets') for part in parts):continue
        if any(part.lower() in ('models','model') for part in parts):
            from .python_archive_update import declared_runtime_file
            if not note.get('normal_python_update') or not declared_runtime_file(old,name,declared):continue
        if path.is_file():files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    write(new/'old-program-retirement.json',{'old':str(old),'new':str(new),'files':files,
        'protected':[str(p) for p in protected],'policy':'quiescent-clean-program-only;no-code-backup' if clean else 'legacy-retirement'})
    if progress:progress({'stage':'removing_old','files':0,'total_files':len(files)})
    # Resolve each parent through held directory descriptors. A directory
    # exchanged for a link after inventory must never redirect deletion.
    root_fd=os.open(old,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for number,(name,digest) in enumerate(files.items(),1):
            check();parent=os.dup(root_fd)
            try:
                parts=Path(name).parts
                for part in parts[:-1]:
                    next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                    os.close(parent);parent=next_fd
                fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
                with os.fdopen(fd,'rb') as source:
                    opened=os.fstat(source.fileno())
                    if not stat.S_ISREG(opened.st_mode) or hashlib.file_digest(source,'sha256').hexdigest()!=digest:
                        raise RuntimeError('Old program changed')
                    current=os.stat(parts[-1],dir_fd=parent,follow_symlinks=False)
                    if (current.st_dev,current.st_ino)!=(opened.st_dev,opened.st_ino):
                        raise RuntimeError('Old program changed')
                    check();os.unlink(parts[-1],dir_fd=parent)
            except OSError as exc:
                raise RuntimeError('Old program path changed or became linked') from exc
            finally:os.close(parent)
            if progress and number==len(files):progress({'stage':'removing_old','files':number,'total_files':len(files)})
    finally:os.close(root_fd)
    return {'ok':True,'removed_files':len(files),'purge':False,'data_retained':True}



def registered_uninstall(old):
    return None


def retire(old,new):
    return cleanup_installation(old,new)
