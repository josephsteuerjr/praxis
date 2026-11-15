"""Native Qt consumer; run in the packaged Linux runtime with DISPLAY (or offscreen)."""
import importlib.util
import json
import os
from unittest import mock
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_wizard as wizard
sys.path.insert(0,str(Path(__file__).resolve().parent))
import t_migration_wizard as engine_tests

HELENE_TEST_NATIVE_INPUTS = ('DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR')


@unittest.skipUnless(importlib.util.find_spec('PySide6'),'Native packaged PySide6 required')
class NativeWizard(unittest.TestCase):
    def setUp(self):
        # Importing PySide6 itself adds environment options. Restore them along
        # with the optional headless fixture before the hermetic stand checks.
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            if not os.environ.get('XDG_RUNTIME_DIR'):
                runtime = tempfile.TemporaryDirectory(prefix='helene-qt-fixture-')
                self.addCleanup(runtime.cleanup)
                os.environ['XDG_RUNTIME_DIR'] = runtime.name

    def test_actual_dialog_preview_copy_and_close(self):
        for language in ('ru','en','fr','zh'):
            with self.subTest(language=language):
                self._check_language(language)

    def _check_language(self,language):
        from PySide6 import QtCore,QtGui,QtWidgets,QtWebEngineWidgets
        from localharness.ui_language import text
        if QtWidgets.QApplication.instance() is None:
            QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        if sys.platform=='win32' and os.environ.get('QT_QPA_PLATFORM')=='offscreen' and not getattr(app,'_fixture_fonts_ready',False):
            # The Windows offscreen plugin has no native font discovery.
            # Register actual OS fonts for this isolated visual fixture only.
            for name in ('segoeui.ttf','msyh.ttc'):
                font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
                if font.is_file():QtGui.QFontDatabase.addApplicationFont(str(font))
            font=QtGui.QFont();font.setFamilies(['Segoe UI','Microsoft YaHei'])
            app.setFont(font)
            app._fixture_fonts_ready=True
        with tempfile.TemporaryDirectory() as d:
            request=engine_tests.WizardEngine().fixture(Path(d));errors=[];state={'step':0,'ticks':0}
            for folder in ('source','destination'):
                path=Path(request[folder])/'helene.json'
                config=json.loads(path.read_text(encoding='utf-8'))
                config.update(lang=language,agent={'frame_language':'fr'})
                path.write_text(json.dumps(config),encoding='utf-8')
            def drive():
                state['ticks']+=1
                window=next((w for w in app.topLevelWidgets() if w.objectName()=='migrationWizard' and w.isVisible()),None)
                if window is None:return
                try:
                    if state['step']==0:
                        self.assertEqual(window.fresh.text(),text('native.qt.455da29b64',language))
                        hint=window.findChild(QtWidgets.QLabel,'freshDestinationHint')
                        self.assertIn(str(Path(request['destination']).resolve()),hint.text())
                        self.assertIn(text('native.qt.f655958952',language),hint.text())
                        self.assertTrue(all(label.textFormat()==QtCore.Qt.TextFormat.PlainText for label in window.findChildren(QtWidgets.QLabel)))
                        self.assertEqual(window.source.text(),'')
                        self.assertEqual(window.source.placeholderText(),text('native.qt.5effafb7b2',language))
                        self.assertFalse(window.start.isEnabled())
                        window.check.click();self.assertIn(text('native.qt.8d7ab8ee4d',language),window.status.text())
                        window.source.setText(request['source']);window.destination.setText(request['destination']);window.home.setText(request['data_home'])
                        window.offline.setChecked(True);window.check.click()
                        self.assertTrue(window.start.isEnabled(),window.status.text())
                        self.assertFalse((Path(request['destination'])/'python-migration.json').exists())
                        window.on_progress({'files':1234})
                        numbers=QtCore.QLocale({'ru':'ru_RU','en':'en_US','fr':'fr_FR','zh':'zh_CN'}[language])
                        self.assertIn(text('native.migration.files',language,{'count':numbers.toString(1234)}),window.status.text())
                        if os.environ.get('HELENE_NATIVE_SHOTS'):
                            directory=Path(os.environ['HELENE_NATIVE_SHOTS']);directory.mkdir(parents=True,exist_ok=True)
                            app.processEvents()
                            self.assertTrue(window.grab().save(str(directory/(language+'.png'))))
                            (directory/(language+'-layout.json')).write_text(json.dumps({
                                'window':[window.width(),window.height()],
                                'labels':[{'text':label.text(),'rect':list(label.geometry().getRect())}
                                          for label in window.findChildren(QtWidgets.QLabel)]},ensure_ascii=False),encoding='utf-8')
                        window.start.click();self.assertTrue(window.busy);state['step']=1
                    elif state['step']==1 and not window.busy:
                        self.assertIn(text('native.qt.dc34f612d3',language),window.status.text());self.assertTrue(window.open.isVisible())
                        config=json.loads((Path(request['destination'])/'helene.json').read_text(encoding='utf-8'))
                        self.assertEqual(config['lang'],language);self.assertEqual(config['agent']['frame_language'],'fr')
                        self.assertEqual(json.loads((Path(request['destination'])/'python-migration.json').read_text())['state'],'prepared')
                        window.reject();state['step']=2
                    if state['ticks']>200:raise TimeoutError('Native migration did not finish in ten seconds')
                except BaseException as exc:
                    import traceback
                    traceback.print_exc()
                    errors.append(exc)
                    if not window.busy:window.reject()
            timer=QtCore.QTimer();timer.timeout.connect(drive);timer.start(50)
            try:self.assertFalse(wizard.run(Path(request['destination'])))
            finally:timer.stop()
            if errors:raise errors[0]
            self.assertEqual(state['step'],2)


if __name__=='__main__':unittest.main(verbosity=2)
