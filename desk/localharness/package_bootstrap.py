"""Debian's unprivileged seed entry. Working updates use the Python updater."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid


def identity(root):
    root=Path(root)
    meta=json.loads((root/'python-app.json').read_text(encoding='utf-8'))
    inventory=root/'python-package-files.json'
    return {'version':meta['version'], 'inventory':hashlib.sha256(inventory.read_bytes()).hexdigest() if inventory.is_file() else ''}


def transition(seed, target):
    from .python_updates import parts
    incoming,current=identity(seed),identity(target)
    # A newer Python-owned update takes precedence over an older OS seed.
    needed=parts(incoming['version'])>parts(current['version']) or (
        parts(incoming['version'])==parts(current['version']) and incoming['inventory']!=current['inventory'])
    return {'seed':incoming, 'running':current, 'needed':needed,
            'program':str(target), 'seed_path':str(seed)}


def clone_seed(seed, stage, current=None):
    """Reuse verified identical *program* bytes from the user's own runtime.

    Every update write is a rename. Mutable configuration, agent source/data
    and the seed's root-owned inodes are never hard-linked into the update.
    """
    seed,stage=Path(seed),Path(stage)
    inventory=json.loads((seed/'python-package-files.json').read_text(encoding='utf-8'))['files']
    def copy(source, destination):
        relative=Path(source).relative_to(seed).as_posix()
        prior=Path(current)/relative if current else None
        expected=inventory.get(relative)
        if (expected and prior and prior.is_file() and not prior.is_symlink()
                and prior.stat().st_uid==os.getuid() and prior.stat().st_size==Path(source).stat().st_size
                and prior.stat().st_mode & 0o777==Path(source).stat().st_mode & 0o777):
            with prior.open('rb') as stream:matches=hashlib.file_digest(stream,'sha256').hexdigest()==expected
            if matches:
                try:os.link(prior,destination);return str(destination)
                except OSError:pass
        return shutil.copy2(source,destination)
    shutil.copytree(seed,stage,symlinks=False,copy_function=copy)


def offer(value):
    from PySide6 import QtWidgets
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    box=QtWidgets.QMessageBox()
    box.setWindowTitle('Hélène — обновление программы')
    box.setText('Установлен пакет '+value['seed']['version']+'. Рабочая программа: '+value['running']['version']+'.')
    box.setInformativeText('Обновить рабочую программу через Python? Профиль и авторский код сохранятся; конфликт кода остановит переход с отчётом. Неудачный запуск откатится.')
    box.setDetailedText('Рабочая папка: '+value['program']+'\nПакет: '+value['seed_path'])
    update=box.addButton('Обновить рабочую программу',QtWidgets.QMessageBox.AcceptRole)
    previous=box.addButton('Открыть прежнюю версию',QtWidgets.QMessageBox.RejectRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.exec()
    if box.clickedButton()==update:return 'update'
    if box.clickedButton()==previous:return 'previous'
    return 'cancel'


def launch_update(seed, target):
    from .python_updates import await_admission
    from .python_platform import require_runtime
    require_runtime(seed,target)
    from contextlib import ExitStack
    from .app_migration import discover
    from .desktop_supervisor import _tree_hold
    with ExitStack() as holds:
        for tree in sorted({Path(row['tree']) for row in discover(target)['agents']}):
            holds.enter_context(_tree_hold(tree))
    staging=target.parent/('.helene-update-'+uuid.uuid4().hex)
    staging.mkdir()
    incoming=staging/'Helene'
    clone_seed(seed,incoming,target)
    helper=staging/'python_source_updater.py'
    shutil.copy2(incoming/'app/localharness/python_source_updater.py',helper)
    log=target.parent/('helene-update-'+staging.name.removeprefix('.helene-update-')+'.log')
    with log.open('ab') as output:
        child=subprocess.Popen([str(incoming/'runtime/bin/python3'),'-s',str(helper),
            '--old',str(target),'--new',str(incoming),'--pid','0','--staging',str(staging)],
            cwd=staging,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    await_admission(child,staging,target)
    # Keep the handoff visible until startup proof or rollback. A background
    # failure must not look like a successful seed transition.
    from PySide6 import QtWidgets
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    progress=QtWidgets.QProgressDialog('Обновляю рабочую программу. Журнал: '+str(log),'',0,0)
    progress.setWindowTitle('Hélène — обновление');progress.setCancelButton(None);progress.show()
    try:
        import time
        while child.poll() is None:
            app.processEvents();time.sleep(.05)
    finally:progress.close()
    if child.returncode:
        detail=log.read_text(encoding='utf-8',errors='replace')[-5000:]
        raise RuntimeError('Python update failed; previous program retained. Log: '+str(log)+'\n'+detail)
    return {'updater_pid':child.pid,'log':str(log),'staging':str(staging)}


def main(seed, arguments):
    seed=Path(seed).resolve()
    home=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')/'helene-python'
    home.mkdir(parents=True,exist_ok=True)
    target=home/'program'
    try:
        with (home/'.seed.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if not target.exists():
                stage=home/('.seed-'+uuid.uuid4().hex)
                try:
                    clone_seed(seed,stage)
                    (stage/'bootstrap-seed.json').write_text(json.dumps({'seed':str(seed),**identity(seed)}),encoding='utf-8')
                    stage.replace(target)
                except BaseException:
                    if stage.exists():shutil.rmtree(stage)
                    raise
            if not (target/'app/python_app.py').is_file() or not (target/'runtime/bin/python3').is_file():
                raise RuntimeError('Existing working program is incomplete; retained: '+str(target))
            if not os.access(target,os.W_OK) or not os.access(home,os.W_OK):
                raise PermissionError('Program and update directory must be writable by this user')
            state=transition(seed,target)
            if '--seed-status' in arguments:
                print(json.dumps(state));return 0
            if state['needed']:
                choice=offer(state)
                if choice=='cancel':return 0
                if choice=='update':
                    result=launch_update(seed,target)
                    print(json.dumps(result));return 0
    except Exception as exc:
        message='Обновление Hélène: '+str(exc)+'\nПрофиль сохранён. Проверь журнал в '+str(home)
        (home/'bootstrap-error.log').write_text(message,encoding='utf-8')
        print(message,file=sys.stderr)
        from PySide6 import QtWidgets
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        QtWidgets.QMessageBox.critical(None,'Hélène — переход остановлен',message)
        return 1
    python=target/'runtime/bin/python3'
    os.execv(str(python),[str(python),'-s',str(seed/'app/localharness/desktop_entry.py'),'--root',str(target),*arguments])
