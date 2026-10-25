"""Linux graphical entry for a fresh app or an offline profile migration.

Loaded before the controller: never swaps a live application's data. The same
profile_paths engine serves the CLI and this window; it does not retire source.
"""
from pathlib import Path
import json

_QT_APPLICATION = None  # keep the shared native application alive for pywebview


def saved_request(destination):
    """Recover only the input selection, never silently resume a copy."""
    path=Path(destination)/'python-migration.json'
    if not path.is_file():return None
    note=json.loads(path.read_text(encoding='utf-8'))
    if note.get('state')!='preserving':return None
    return {'source':note['old_root'],'destination':str(Path(destination).resolve()),
            'data_home':note['data_home'],'aliases':note.get('path_aliases') or {},'resume':True}


def preview(request):
    from .profile_paths import preview_offline
    return preview_offline(request['source'],request['destination'],request['data_home'],
                           request.get('aliases',{}),resume=request.get('resume',False))


def execute(request, progress):
    from .profile_paths import prepare_offline
    return prepare_offline(request['source'],request['destination'],request['data_home'],
                           request.get('aliases',{}),resume=request.get('resume',False),progress=progress)


def run(root):
    """Return True only when the owner explicitly chooses to open this app."""
    from PySide6 import QtCore,QtWidgets,QtWebEngineWidgets
    # The subsequent regular app uses QtWebEngine in this same process.
    global _QT_APPLICATION
    if QtWidgets.QApplication.instance() is None:
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    root=Path(root).resolve()

    class Worker(QtCore.QThread):
        progress=QtCore.Signal(dict)
        finished_copy=QtCore.Signal(dict)
        failed=QtCore.Signal(str)
        def __init__(self,request):
            super().__init__();self.request=request
        def run(self):
            import time
            last=0.0
            def report(value):
                nonlocal last
                now=time.monotonic()
                if now-last>=0.1:
                    last=now;self.progress.emit(value)
            try:self.finished_copy.emit(execute(self.request,report))
            except Exception as exc:self.failed.emit(str(exc))

    class Wizard(QtWidgets.QDialog):
        def __init__(self):
            super().__init__();self.setWindowTitle('Hélène — перенос профиля')
            self.resize(760,660);self.setMinimumWidth(540)
            self.open_app=False;self.worker=None;self.checked=None;self.busy=False
            outer=QtWidgets.QVBoxLayout(self);outer.setSpacing(12)
            title=QtWidgets.QLabel('Добро пожаловать в Hélène')
            title.setStyleSheet('font-size:24px;font-weight:600');outer.addWidget(title)
            intro=QtWidgets.QLabel('Создай нового агента или перенеси полную офлайн-копию прежней установки. Исходная папка останется на месте.')
            intro.setWordWrap(True);outer.addWidget(intro)
            self.setStyleSheet('QWidget {background:#f4efe6;color:#302c26;} QLineEdit,QPlainTextEdit {background:#fffdf8;color:#302c26;border:1px solid #bcb3a3;border-radius:4px;padding:6px;} QPushButton {background:#e6ddd0;color:#302c26;border:1px solid #a99f8d;border-radius:5px;padding:8px 12px;} QPushButton:hover {background:#d9cebd;} QPushButton:disabled {color:#8a8378;border-color:#cdc5b8;} QProgressBar {border:1px solid #bcb3a3;text-align:center;color:#302c26;} QProgressBar::chunk {background:#79886b;} QScrollArea {border:1px solid #bcb3a3;}')
            self.fresh=QtWidgets.QPushButton('Новый агент в этой программе → настроить')
            self.fresh.clicked.connect(self.new_agent);outer.addWidget(self.fresh)
            fresh_hint=QtWidgets.QLabel('Откроется текущая программа: '+str(root)+'\nПапки ниже используются только для переноса, не для создания нового агента.')
            fresh_hint.setObjectName('freshDestinationHint');fresh_hint.setWordWrap(True);outer.addWidget(fresh_hint)
            scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll,1)
            body=QtWidgets.QWidget();scroll.setWidget(body);form=QtWidgets.QVBoxLayout(body)
            form.addWidget(QtWidgets.QLabel('Перенос существующего профиля — выбор папок'))
            self.inputs=[]
            self.source=self.path_field(form,'1. Офлайн-копия прежней установки','Папка с helene.json')
            self.destination=self.path_field(form,'2. Новая распакованная программа',str(root))
            self.home=self.path_field(form,'3. Папка для сохранённых данных и кода',str(root.parent/(root.name+'-data')))
            self.offline=QtWidgets.QCheckBox('Источник — полная офлайн-копия, не папка работающего агента')
            form.addWidget(self.offline);self.offline.stateChanged.connect(self.invalidate)
            self.resume=QtWidgets.QCheckBox('Продолжить прерванный перенос в эту же папку')
            form.addWidget(self.resume);self.resume.stateChanged.connect(self.invalidate)
            self.mapping_toggle=QtWidgets.QCheckBox('Исходные пути относятся к другой системе (например, Windows)')
            form.addWidget(self.mapping_toggle)
            self.mapping=QtWidgets.QPlainTextEdit();self.mapping.setPlaceholderText('{"C:\\\\Users\\\\Name\\\\Helene": "/home/me/Offline copy"}')
            self.mapping.setMaximumHeight(95);self.mapping.setVisible(False);form.addWidget(self.mapping)
            self.mapping_toggle.toggled.connect(self.mapping.setVisible)
            self.mapping_toggle.toggled.connect(self.invalidate);self.mapping.textChanged.connect(self.invalidate)
            hint=QtWidgets.QLabel('Карта путей: прежний абсолютный корень → его папка внутри офлайн-копии. Ключи модели и сессии сохраняются локально. Голосовые окружения другой ОС не переносятся.')
            hint.setWordWrap(True);form.addWidget(hint)
            self.report=QtWidgets.QPlainTextEdit();self.report.setReadOnly(True);self.report.setMinimumHeight(110)
            self.report.setPlaceholderText('Проверка покажет выбранные данные и код до начала копирования.');form.addWidget(self.report)
            self.status=QtWidgets.QLabel('Ничего ещё не изменено.');self.status.setWordWrap(True);outer.addWidget(self.status)
            self.bar=QtWidgets.QProgressBar();self.bar.setRange(0,1);self.bar.setValue(0);outer.addWidget(self.bar)
            row=QtWidgets.QHBoxLayout();outer.addLayout(row)
            self.check=QtWidgets.QPushButton('Проверить');self.check.clicked.connect(self.check_request);row.addWidget(self.check)
            self.start=QtWidgets.QPushButton('Начать перенос');self.start.setEnabled(False);self.start.clicked.connect(self.start_copy);row.addWidget(self.start)
            self.open=QtWidgets.QPushButton('Открыть приложение');self.open.setVisible(False);self.open.clicked.connect(self.open_current);row.addWidget(self.open)
            self.close_button=QtWidgets.QPushButton('Закрыть');self.close_button.clicked.connect(self.reject);row.addWidget(self.close_button)
            self.setObjectName('migrationWizard')
            for name,widget in {'source':self.source,'destination':self.destination,'dataHome':self.home,'offline':self.offline,'resume':self.resume,'preview':self.check,'start':self.start,'open':self.open,'status':self.status,'report':self.report,'fresh':self.fresh}.items():widget.setObjectName(name)
            try:
                pending=saved_request(root)
                if pending:
                    self.source.setText(pending['source']);self.home.setText(pending['data_home']);self.resume.setChecked(True)
                    if pending['aliases']:
                        self.mapping_toggle.setChecked(True);self.mapping.setPlainText(json.dumps(pending['aliases'],ensure_ascii=False,indent=2))
                    self.status.setText('Найден прерванный перенос. Проверь пути и явно продолжи его.');self.fresh.setEnabled(False)
            except Exception as exc:self.status.setText('Не удалось прочитать журнал переноса: '+str(exc));self.fresh.setEnabled(False)

        def path_field(self,layout,label,value):
            layout.addWidget(QtWidgets.QLabel(label));row=QtWidgets.QHBoxLayout();layout.addLayout(row)
            entry=QtWidgets.QLineEdit();entry.setText(value if not value.startswith('Папка с') else '')
            entry.setPlaceholderText(value);entry.textChanged.connect(self.invalidate);row.addWidget(entry,1)
            pick=QtWidgets.QPushButton('Выбрать…');row.addWidget(pick)
            def choose():
                value=QtWidgets.QFileDialog.getExistingDirectory(self,label,entry.text())
                if value:entry.setText(value)
            pick.clicked.connect(choose);self.inputs.extend([entry,pick]);return entry

        def invalidate(self,*_):
            self.checked=None
            if hasattr(self,'start'):self.start.setEnabled(False)
            if hasattr(self,'open'):self.open.setVisible(False)

        def request(self):
            if not self.offline.isChecked():raise ValueError('Сначала подтверди, что источник — офлайн-копия.')
            values=[x.text().strip() for x in (self.source,self.destination,self.home)]
            if not all(values):raise ValueError('Выбери все три папки.')
            return dict(zip(('source','destination','data_home'),values),
                        aliases=json.loads(self.mapping.toPlainText() or '{}') if self.mapping_toggle.isChecked() else {},resume=self.resume.isChecked())

        def check_request(self):
            self.invalidate()
            try:
                request=self.request();result=preview(request)
                lines=['Продолжить прерванную копию' if result['resume'] else 'Готово к переносу',
                       'Источник: '+result['source'],'Программа: '+result['destination'],'Данные: '+result['data_home']]
                for agent in result['agents']:
                    lines.extend(['','Профиль: '+agent['config'],'Память: '+agent['tree'],'Код: '+agent['code']])
                lines.extend(['','Источник не удаляется. Агент не запускается автоматически.'])
                self.report.setPlainText('\n'.join(lines));self.checked=request;self.start.setEnabled(True)
                self.start.setText('Продолжить перенос' if result['resume'] else 'Начать перенос');self.status.setText('Проверка пройдена. Начало — только по твоей кнопке.')
            except Exception as exc:self.status.setText('Проверка: '+str(exc))

        def start_copy(self):
            if self.checked is None:return
            self.busy=True;self.set_controls(False);self.bar.setRange(0,0);self.status.setText('Сохраняю резервную копию и данные…')
            self.worker=Worker(dict(self.checked));self.worker.progress.connect(self.on_progress)
            self.worker.finished_copy.connect(self.done_copy);self.worker.failed.connect(self.failed_copy);self.worker.start()

        def set_controls(self,enabled):
            for item in [*self.inputs,self.offline,self.resume,self.mapping_toggle,self.mapping,self.check,self.fresh,self.close_button]:item.setEnabled(enabled)
            self.start.setEnabled(enabled and self.checked is not None)

        def on_progress(self,value):
            detail=(f"{value['bytes']/1048576:.1f} МБ" if 'bytes' in value else f"{value.get('files',0)} файлов")
            self.status.setText('Сохраняю данные: '+detail)

        def done_copy(self,note):
            self.worker.wait()
            self.busy=False;self.set_controls(True);self.checked=None;self.start.setEnabled(False);self.bar.setRange(0,1);self.bar.setValue(1)
            target=Path(note['new_root']).resolve();self.status.setText('Перенос завершён. Источник сохранён. Резервная копия: '+note['backup'])
            self.report.appendPlainText('\nПодготовленная программа: '+str(target)+'\nАгент ещё не запускался.')
            self.fresh.setEnabled(False);self.open.setVisible(target==root)
            if target!=root:self.report.appendPlainText('Открой helene в выбранной папке программы.')

        def failed_copy(self,error):
            self.worker.wait()
            self.busy=False;self.set_controls(True);self.checked=None;self.start.setEnabled(False);self.bar.setRange(0,1);self.bar.setValue(0)
            self.status.setText('Перенос остановлен: '+error+'\nИсточник сохранён. Если появился журнал, отметь продолжение и снова проверь пути.')

        def new_agent(self):
            from .essential_backup import load
            value=load(root/'helene.json')
            if value.get('setup_complete') is not False or (root/'python-migration.json').exists():
                self.status.setText('Это уже настроенная установка. Выбери отдельную свежую программу для нового агента.');return
            self.open_current()

        def open_current(self):self.open_app=True;self.accept()
        def reject(self):
            if self.busy:self.status.setText('Копирование ещё идёт. Закрытие прервёт перенос; дождись завершения.');return
            super().reject()
        def closeEvent(self,event):
            if self.busy:event.ignore();self.reject()
            else:event.accept()

    _QT_APPLICATION=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window=Wizard();window.exec()
    return window.open_app
