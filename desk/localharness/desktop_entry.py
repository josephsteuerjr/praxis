"""Portable Linux GUI entry: one owner, no terminal dependency, retained logs."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import runpy
import subprocess
import sys
import time
import traceback


def quote(value):
    value=str(value)
    if any(c in value for c in '\n\r\0'):raise ValueError('Invalid desktop path')
    # String-value decoding precedes Exec quoting in the desktop-entry grammar.
    return '"'+value.replace('\\','\\\\\\\\').replace('"','\\\\"').replace('`','\\\\`').replace('$','\\\\$').replace('%','%%')+'"'


def desktop_text(root):
    root=Path(root).resolve()
    args=[root/'Start Helene.sh']
    def value(path):return str(path).replace('\\','\\\\').replace('\n','\\n').replace('\r','\\r')
    return ('[Desktop Entry]\nType=Application\nName=Hélène\nComment=Personal agent desktop\n'
        'Exec='+' '.join(quote(a) for a in args)+'\nPath='+value(root)+'\n'
        'Icon='+value(root/'app/resources/python-app.png')+'\nTerminal=false\nCategories=Utility;\nStartupNotify=false\n')


def install_desktop(root):
    root=Path(root).resolve()
    home=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')
    entry=home/'applications'/('helene-'+hashlib.sha256(str(root).encode()).hexdigest()[:12]+'.desktop')
    entry.parent.mkdir(parents=True,exist_ok=True)
    text=desktop_text(root)
    temporary=entry.with_suffix('.tmp');temporary.write_text(text,encoding='utf-8');temporary.chmod(0o755);temporary.replace(entry)
    return entry


def log_path(root):
    home=Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'helene/logs'
    home.mkdir(parents=True,exist_ok=True)
    return home/('launch-'+hashlib.sha256(str(root).encode()).hexdigest()[:12]+'-'+str(time.time_ns())+'.log')


def worker(root, arguments, log):
    # Keep the descriptor across the startup recovery exec, but normal Popen
    # close_fds prevents this UI lease from leaking to model/native children.
    with (root/'.desktop-gui.lock').open('a+b') as lease:
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('Hélène is already open for this program; no second window started',flush=True)
            return 0
        os.set_inheritable(lease.fileno(),True)
        lease.seek(0);lease.truncate()
        lease.write(json.dumps({'pid':os.getpid(),'root':str(root),'log':str(log)}).encode());lease.flush()
        print(json.dumps({'stage':'starting','pid':os.getpid(),'root':str(root),'argv':arguments,
            'cwd':str(Path.cwd()),'session':{k:os.environ.get(k,'') for k in ('XDG_SESSION_TYPE','XDG_CURRENT_DESKTOP','DISPLAY','WAYLAND_DISPLAY')},
            'python':sys.executable}),flush=True)
        try:
            sys.path.insert(0,str(root/'app'))
            sys.argv=[str(root/'app/python_app.py'),'--root',str(root),*arguments]
            runpy.run_path(str(root/'app/python_app.py'),run_name='__main__')
            return 0
        except SystemExit as exc:
            if exc.code in (None,0):return 0
            print('Application exited with status '+str(exc.code),flush=True)
            try:
                from PySide6 import QtWidgets
                app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
                QtWidgets.QMessageBox.critical(None,'Hélène — запуск остановлен',
                    'Не удалось открыть приложение. Диагностика сохранена:\n'+str(log)+'\n\n'+str(root/'startup-error.log'))
            except BaseException:traceback.print_exc()
            return 1
        except BaseException:
            traceback.print_exc()
            try:
                from PySide6 import QtWidgets
                app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
                QtWidgets.QMessageBox.critical(None,'Hélène — запуск остановлен',
                    'Не удалось открыть приложение. Диагностика сохранена:\n'+str(log)+'\n\n'+traceback.format_exc()[-1800:])
            except BaseException:traceback.print_exc()
            return 1


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--diagnostic',action='store_true')
    parser.add_argument('--install-desktop',action='store_true')
    parser.add_argument('--notify',action='store_true')
    parser.add_argument('--log',type=Path)
    args,arguments=parser.parse_known_args(argv)
    root=args.root.resolve()
    if not args.worker and not args.diagnostic and not args.install_desktop:
        try:install_desktop(root)
        except OSError:pass  # menu policy never prevents opening the actual window
    if args.install_desktop:
        installed=install_desktop(root)
        print(installed)
        if args.notify:
            from PySide6 import QtWidgets
            app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            QtWidgets.QMessageBox.information(None,'Hélène','Hélène добавлена в меню приложений.\n'+str(installed))
        return 0
    if args.worker or args.diagnostic:return worker(root,arguments,args.log or Path('/dev/stderr'))
    log=log_path(root)
    command=[str(root/'runtime/bin/python3'),'-s',str(Path(__file__).resolve()),'--root',str(root),'--worker','--log',str(log),*arguments]
    with log.open('xb') as output:
        child=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                               start_new_session=True,close_fds=True)
    # Admission or early failure is bounded; the GUI continues independently of
    # a file manager/terminal. There is no browser or file-manager fallback.
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        lease=root/'.desktop-gui.lock'
        if lease.is_file():
            try:
                if json.loads(lease.read_bytes()).get('pid')==child.pid:return 0
            except (ValueError,OSError):pass
        if child.poll() is not None:return child.returncode
        time.sleep(.03)
    print('Hélène is starting; diagnostics: '+str(log),file=sys.stderr)
    return 0


if __name__=='__main__':raise SystemExit(main())
