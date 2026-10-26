"""First archive launch: discover installed profiles and carry one safely."""
from pathlib import Path
import json

_APP=None


def run(root):
    from . import installed_packages
    root=Path(root).resolve()
    pending=root/'python-migration.json'
    if pending.is_file():
        note=json.loads(pending.read_text(encoding='utf-8'))
        if note.get('state')!='preserving':return True
    else:note={}
    identity=root/'installed-package.json'
    rows=[json.loads(identity.read_text(encoding='utf-8'))] if identity.is_file() else installed_packages.discover()
    if not rows:return True
    from PySide6 import QtCore,QtWidgets,QtWebEngineWidgets
    global _APP
    if QtWidgets.QApplication.instance() is None:
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    _APP=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class Copy(QtCore.QThread):
        progress=QtCore.Signal(dict)
        done=QtCore.Signal(dict)
        failed=QtCore.Signal(str)
        def __init__(self,package):super().__init__();self.package=package
        def run(self):
            import time
            last=0.0
            def report(value):
                nonlocal last
                now=time.monotonic()
                if now-last>=.1 or value.get('bytes')==value.get('total_bytes'):
                    last=now;self.progress.emit(value)
            try:self.done.emit(installed_packages.prepare(self.package,root,report))
            except Exception as exc:self.failed.emit(str(exc))

    class Dialog(QtWidgets.QDialog):
        def __init__(self):
            super().__init__();self.setWindowTitle('Hélène — переезд в переносимую папку')
            self.busy=False;self.open_app=False
            from .wizard_style import apply
            apply(self,root)
            box=QtWidgets.QVBoxLayout(self);box.setContentsMargins(32,28,32,28);box.setSpacing(18)
            brand=QtWidgets.QLabel('HÉLÈNE  /  ПЕРЕЕЗД');brand.setObjectName('brand');box.addWidget(brand)
            title=QtWidgets.QLabel('Всё твоё — с тобой');title.setObjectName('headline');box.addWidget(title)
            steps=QtWidgets.QLabel('01  Перенос профиля     ·     02  Проверка     ·     03  Запуск');steps.setObjectName('steps');box.addWidget(steps)
            message=QtWidgets.QLabel('Найдена установленная Hélène. Заберём в новую папку память, настройки и твой код. Когда приложение откроется, удалим прежний пакет — система может попросить пароль.')
            message.setWordWrap(True);box.addWidget(message)
            self.choice=QtWidgets.QComboBox();self.choice.setObjectName('installedProfile')
            for row in rows:self.choice.addItem(row['name']+' '+row['version']+' — '+(row['profile'] or 'профиль ещё не создан'))
            box.addWidget(self.choice)
            target=QtWidgets.QLabel('Новая папка: '+str(root));target.setWordWrap(True);box.addWidget(target)
            target.setObjectName('caption');target.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            self.status=QtWidgets.QLabel('Профиль сохранится в этой папке. Дальнейшие обновления выполняются из приложения.');self.status.setWordWrap(True);self.status.setObjectName('packageStatus');box.addWidget(self.status,1)
            self.bar=QtWidgets.QProgressBar();self.bar.setRange(0,1);self.bar.setValue(0);box.addWidget(self.bar)
            buttons=QtWidgets.QHBoxLayout();box.addLayout(buttons)
            self.start=QtWidgets.QPushButton('Продолжить перенос' if note else 'Перенести и открыть');self.start.setObjectName('migrateInstalled');self.start.clicked.connect(self.copy);buttons.addWidget(self.start)
            self.start.setProperty('primary',True);self.start.setDefault(True)
            self.cancel=QtWidgets.QPushButton('Позже');self.cancel.clicked.connect(self.reject);buttons.addWidget(self.cancel)
        def copy(self):
            self.busy=True;self.start.setEnabled(False);self.cancel.setEnabled(False);self.choice.setEnabled(False);self.bar.setRange(0,0)
            self.status.setText('Закрываю прежнее приложение и сохраняю профиль…')
            self.worker=Copy(rows[self.choice.currentIndex()]);self.worker.progress.connect(self.progress);self.worker.done.connect(self.copy_finished);self.worker.failed.connect(self.failed);self.worker.start()
        def progress(self,value):
            from .wizard_style import progress_text
            self.status.setText(progress_text(value))
            total=value.get('total_bytes',0)
            if total:self.bar.setRange(0,1000);self.bar.setValue(min(1000,int(value.get('bytes',0)*1000/total)))
            else:self.bar.setRange(0,0)
        def copy_finished(self,value):
            self.worker.wait();self.busy=False;self.open_app=True;self.accept()
        def failed(self,error):
            self.worker.wait();self.busy=False;self.start.setEnabled(True);self.cancel.setEnabled(True);self.bar.setRange(0,1);self.bar.setValue(0)
            self.status.setText('Перенос остановлен. Прежний пакет сохранён.\n'+error)
            self.start.setText('Повторить перенос')
        def reject(self):
            if not self.busy:super().reject()
        def closeEvent(self,event):
            if self.busy:event.ignore()
            else:event.accept()
    dialog=Dialog();dialog.exec();return dialog.open_app
