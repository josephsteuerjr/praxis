"""Native Qt consumer; run in the packaged Linux runtime with DISPLAY (or offscreen)."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_wizard as wizard
sys.path.insert(0,str(Path(__file__).resolve().parent))
import t_migration_wizard as engine_tests


@unittest.skipUnless(importlib.util.find_spec('PySide6'),'Native packaged PySide6 required')
class NativeWizard(unittest.TestCase):
    def test_actual_dialog_preview_copy_and_close(self):
        from PySide6 import QtCore,QtWidgets,QtWebEngineWidgets
        if QtWidgets.QApplication.instance() is None:
            QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as d:
            request=engine_tests.WizardEngine().fixture(Path(d));errors=[];state={'step':0,'ticks':0}
            def drive():
                state['ticks']+=1
                window=next((w for w in app.topLevelWidgets() if w.objectName()=='migrationWizard'),None)
                if window is None:return
                try:
                    if state['step']==0:
                        self.assertIn('этой программе',window.fresh.text())
                        hint=window.findChild(QtWidgets.QLabel,'freshDestinationHint')
                        self.assertIn(str(Path(request['destination']).resolve()),hint.text())
                        self.assertIn('только для переноса',hint.text())
                        self.assertFalse(window.start.isEnabled())
                        window.check.click();self.assertIn('офлайн',window.status.text())
                        window.source.setText(request['source']);window.destination.setText(request['destination']);window.home.setText(request['data_home'])
                        window.offline.setChecked(True);window.check.click()
                        self.assertTrue(window.start.isEnabled(),window.status.text())
                        self.assertFalse((Path(request['destination'])/'python-migration.json').exists())
                        window.start.click();self.assertTrue(window.busy);state['step']=1
                    elif state['step']==1 and not window.busy:
                        self.assertIn('Перенос завершён',window.status.text());self.assertTrue(window.open.isVisible())
                        self.assertEqual(json.loads((Path(request['destination'])/'python-migration.json').read_text())['state'],'prepared')
                        window.reject();state['step']=2
                    if state['ticks']>200:raise TimeoutError('Native migration did not finish in ten seconds')
                except BaseException as exc:
                    errors.append(exc)
                    if not window.busy:window.reject()
            timer=QtCore.QTimer();timer.timeout.connect(drive);timer.start(50)
            try:self.assertFalse(wizard.run(Path(request['destination'])))
            finally:timer.stop()
            if errors:raise errors[0]
            self.assertEqual(state['step'],2)


if __name__=='__main__':unittest.main(verbosity=2)
