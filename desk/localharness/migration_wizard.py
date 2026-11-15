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
    from .ui_language import for_root,text
    language=for_root(root)
    ui_text=lambda key,values=None:text(key,language,values)
    numbers=QtCore.QLocale({'ru':'ru_RU','en':'en_US','fr':'fr_FR','zh':'zh_CN'}[language])
    def plain_label(value):
        widget=QtWidgets.QLabel(value)
        widget.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        return widget

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
            super().__init__();self.setWindowTitle(ui_text('native.qt.cce6f1af63'))
            self.resize(760,660);self.setMinimumWidth(540)
            self.open_app=False;self.worker=None;self.checked=None;self.busy=False
            outer=QtWidgets.QVBoxLayout(self);outer.setContentsMargins(24,24,24,24);outer.setSpacing(12)
            title=plain_label(ui_text('native.qt.303a00c7e0'))
            title.setStyleSheet('font-size:24px;font-weight:400');outer.addWidget(title)
            intro=plain_label(ui_text('native.qt.6041176bbb'))
            intro.setWordWrap(True);outer.addWidget(intro)
            self.setStyleSheet('QWidget {background:#f8eee0;color:#1d1a17;font-size:14px;} QLineEdit,QPlainTextEdit {background:#fff9f0;border:1px solid #c7b8a5;border-radius:8px;padding:8px 12px;} QPushButton {background:#fff9f0;border:1px solid #c7b8a5;border-radius:8px;padding:8px 12px;min-height:20px;} QPushButton:hover {background:#eedcc6;} QPushButton:focus,QLineEdit:focus,QPlainTextEdit:focus {border-color:#9a4a1e;} QPushButton:disabled {color:#8a8378;border-color:#d4c7b6;} QCheckBox {spacing:8px;} QProgressBar {border:1px solid #c7b8a5;border-radius:4px;text-align:center;} QProgressBar::chunk {background:#e0915a;} QScrollArea {border:1px solid #c7b8a5;border-radius:8px;}')
            self.fresh=QtWidgets.QPushButton(ui_text('native.qt.455da29b64'))
            self.fresh.clicked.connect(self.new_agent);outer.addWidget(self.fresh)
            fresh_hint=plain_label(ui_text('native.qt.af9c0eea39')+str(root)+ui_text('native.qt.f655958952'))
            fresh_hint.setObjectName('freshDestinationHint');fresh_hint.setWordWrap(True);outer.addWidget(fresh_hint)
            scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll,1)
            body=QtWidgets.QWidget();scroll.setWidget(body);form=QtWidgets.QVBoxLayout(body);form.setContentsMargins(16,16,16,16);form.setSpacing(8)
            form.addWidget(plain_label(ui_text('native.qt.b22dab3873')))
            self.inputs=[]
            self.source=self.path_field(form,ui_text('native.qt.faf4a6e16c'),ui_text('native.qt.5effafb7b2'),placeholder=True)
            self.destination=self.path_field(form,ui_text('native.qt.6f23e1b527'),str(root))
            self.home=self.path_field(form,ui_text('native.qt.740fbc74b9'),str(root.parent/(root.name+'-data')))
            self.offline=QtWidgets.QCheckBox(ui_text('native.qt.52cc00b2b9'))
            form.addWidget(self.offline);self.offline.stateChanged.connect(self.invalidate)
            self.resume=QtWidgets.QCheckBox(ui_text('native.qt.75a3fdd91d'))
            form.addWidget(self.resume);self.resume.stateChanged.connect(self.invalidate)
            self.mapping_toggle=QtWidgets.QCheckBox(ui_text('native.qt.074daf2732'))
            form.addWidget(self.mapping_toggle)
            self.mapping=QtWidgets.QPlainTextEdit();self.mapping.setPlaceholderText('{"C:\\\\Users\\\\Name\\\\Helene": "/home/me/Offline copy"}')
            self.mapping.setMaximumHeight(95);self.mapping.setVisible(False);form.addWidget(self.mapping)
            self.mapping_toggle.toggled.connect(self.mapping.setVisible)
            self.mapping_toggle.toggled.connect(self.invalidate);self.mapping.textChanged.connect(self.invalidate)
            hint=plain_label(ui_text('native.qt.54bdc6dc5d'))
            hint.setWordWrap(True);form.addWidget(hint)
            self.report=QtWidgets.QPlainTextEdit();self.report.setReadOnly(True);self.report.setMinimumHeight(110)
            self.report.setPlaceholderText(ui_text('native.qt.bd7d04ac69'));form.addWidget(self.report)
            self.status=plain_label(ui_text('native.qt.4d28b2f527'));self.status.setWordWrap(True);outer.addWidget(self.status)
            self.bar=QtWidgets.QProgressBar();self.bar.setRange(0,1);self.bar.setValue(0);outer.addWidget(self.bar)
            row=QtWidgets.QGridLayout();row.setSpacing(8);row.setColumnStretch(0,1);row.setColumnStretch(1,1);outer.addLayout(row)
            self.check=QtWidgets.QPushButton(ui_text('native.qt.e4424a6df6'));self.check.clicked.connect(self.check_request);row.addWidget(self.check,0,0)
            self.start=QtWidgets.QPushButton(ui_text('native.qt.f007c39121'));self.start.setEnabled(False);self.start.clicked.connect(self.start_copy);row.addWidget(self.start,0,1)
            self.open=QtWidgets.QPushButton(ui_text('native.qt.be432c828c'));self.open.setVisible(False);self.open.clicked.connect(self.open_current);row.addWidget(self.open,1,0)
            self.close_button=QtWidgets.QPushButton(ui_text('native.qt.a7a4033657'));self.close_button.clicked.connect(self.reject);row.addWidget(self.close_button,1,1)
            self.setObjectName('migrationWizard')
            for name,widget in {'source':self.source,'destination':self.destination,'dataHome':self.home,'offline':self.offline,'resume':self.resume,'preview':self.check,'start':self.start,'open':self.open,'status':self.status,'report':self.report,'fresh':self.fresh}.items():widget.setObjectName(name)
            try:
                pending=saved_request(root)
                if pending:
                    self.source.setText(pending['source']);self.home.setText(pending['data_home']);self.resume.setChecked(True)
                    if pending['aliases']:
                        self.mapping_toggle.setChecked(True);self.mapping.setPlainText(json.dumps(pending['aliases'],ensure_ascii=False,indent=2))
                    self.status.setText(ui_text('native.qt.60a720a5b2'));self.fresh.setEnabled(False)
            except Exception as exc:self.status.setText(ui_text('native.qt.ac2f5750ce')+str(exc));self.fresh.setEnabled(False)

        def path_field(self,layout,label,value,placeholder=False):
            layout.addWidget(plain_label(label));row=QtWidgets.QHBoxLayout();layout.addLayout(row)
            entry=QtWidgets.QLineEdit();entry.setText('' if placeholder else value)
            entry.setPlaceholderText(value);entry.textChanged.connect(self.invalidate);row.addWidget(entry,1)
            pick=QtWidgets.QPushButton(ui_text('native.qt.a413d0a1c6'));row.addWidget(pick)
            def choose():
                value=QtWidgets.QFileDialog.getExistingDirectory(self,label,entry.text())
                if value:entry.setText(value)
            pick.clicked.connect(choose);self.inputs.extend([entry,pick]);return entry

        def invalidate(self,*_):
            self.checked=None
            if hasattr(self,'start'):self.start.setEnabled(False)
            if hasattr(self,'open'):self.open.setVisible(False)

        def request(self):
            if not self.offline.isChecked():raise ValueError(ui_text('native.qt.8d7ab8ee4d'))
            values=[x.text().strip() for x in (self.source,self.destination,self.home)]
            if not all(values):raise ValueError(ui_text('native.qt.48b00cdd0a'))
            return dict(zip(('source','destination','data_home'),values),
                        aliases=json.loads(self.mapping.toPlainText() or '{}') if self.mapping_toggle.isChecked() else {},resume=self.resume.isChecked())

        def check_request(self):
            self.invalidate()
            try:
                request=self.request();result=preview(request)
                lines=[ui_text('native.qt.be231ee9a3') if result['resume'] else ui_text('native.qt.43ec2638ae'),
                       ui_text('native.qt.f30d1d4879')+result['source'],ui_text('native.qt.bd012083e9')+result['destination'],ui_text('native.qt.683ea92ad0')+result['data_home']]
                for agent in result['agents']:
                    lines.extend(['',ui_text('native.qt.f107b5dcc0')+agent['config'],ui_text('native.qt.e33333f360')+agent['tree'],ui_text('native.qt.0f0e770492')+agent['code']])
                lines.extend(['',ui_text('native.qt.e5da425cea')])
                self.report.setPlainText('\n'.join(lines));self.checked=request;self.start.setEnabled(True)
                self.start.setText(ui_text('native.qt.38959f9e7c') if result['resume'] else ui_text('native.qt.f007c39121'));self.status.setText(ui_text('native.qt.613fc1fa92'))
            except Exception as exc:self.status.setText(ui_text('native.qt.49084e8f3a')+str(exc))

        def start_copy(self):
            if self.checked is None:return
            self.busy=True;self.set_controls(False);self.bar.setRange(0,0);self.status.setText(ui_text('native.qt.680902f9df'))
            self.worker=Worker(dict(self.checked));self.worker.progress.connect(self.on_progress)
            self.worker.finished_copy.connect(self.done_copy);self.worker.failed.connect(self.failed_copy);self.worker.start()

        def set_controls(self,enabled):
            for item in [*self.inputs,self.offline,self.resume,self.mapping_toggle,self.mapping,self.check,self.fresh,self.close_button]:item.setEnabled(enabled)
            self.start.setEnabled(enabled and self.checked is not None)

        def on_progress(self,value):
            detail=(ui_text('native.migration.mib',{'count':numbers.toString(float(value['bytes'])/1048576,'f',1)})
                    if 'bytes' in value else ui_text('native.migration.files',{'count':numbers.toString(int(value.get('files',0)))}))
            self.status.setText(ui_text('native.qt.49d95cf8a6')+detail)

        def done_copy(self,note):
            self.worker.wait()
            self.busy=False;self.set_controls(True);self.checked=None;self.start.setEnabled(False);self.bar.setRange(0,1);self.bar.setValue(1)
            target=Path(note['new_root']).resolve();self.status.setText(ui_text('native.qt.dc34f612d3')+note['backup'])
            self.report.appendPlainText(ui_text('native.qt.d5441e4b1f')+str(target)+ui_text('native.qt.e8debc7ba7'))
            self.fresh.setEnabled(False);self.open.setVisible(target==root)
            if target!=root:self.report.appendPlainText(ui_text('native.qt.1d45296257'))

        def failed_copy(self,error):
            self.worker.wait()
            self.busy=False;self.set_controls(True);self.checked=None;self.start.setEnabled(False);self.bar.setRange(0,1);self.bar.setValue(0)
            self.status.setText(ui_text('native.qt.3e15e07052')+error+ui_text('native.qt.de85f04d6d'))

        def new_agent(self):
            from .essential_backup import load
            value=load(root/'helene.json')
            if value.get('setup_complete') is not False or (root/'python-migration.json').exists():
                self.status.setText(ui_text('native.qt.325c54c782'));return
            self.open_current()

        def open_current(self):self.open_app=True;self.accept()
        def reject(self):
            if self.busy:self.status.setText(ui_text('native.qt.cfbf854123'));return
            super().reject()
        def closeEvent(self,event):
            if self.busy:event.ignore();self.reject()
            else:event.accept()

    _QT_APPLICATION=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window=Wizard();window.exec()
    return window.open_app
