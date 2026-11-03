"""The desktop observer does not own the durable agent session."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import python_app

class WindowSessionLifetime(unittest.TestCase):
    def test_observer_close_detaches_but_explicit_exit_shuts_down(self):
        for action, expected in ((None,False),({'action':'close'},False),({'action':'exit'},True),({'action':'exit','observer_only':True},False)):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as d:
                root=Path(d);app=python_app.Application(root,root)
                app.host=Mock();app.serve()
                if action is not None:app.invoke('python_window',action)
                app.close()
                self.assertEqual(app.host.shutdown.called,expected)
                self.assertEqual(app.host.close.called,not expected)
    def test_relaunch_exit_detaches_current_observer(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);app=python_app.Application(root,root);app.host=Mock();app.serve()
            app.relaunch=True
            app.invoke('python_window',{'action':'exit'})
            app.close();app.host.close.assert_called_once();app.host.shutdown.assert_not_called()

if __name__=='__main__':unittest.main()
