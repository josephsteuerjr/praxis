"""Restart the requested owner, preserving Stop and using a windowed Windows entry."""
import ctypes,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
DESK=Path(__file__).resolve().parents[1];sys.path.insert(0,str(DESK))
import python_app,python_tray
from localharness.python_platform import desktop_argv


class Restart(unittest.TestCase):
    def test_windows_gui_reentry_never_uses_console_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(sys,'platform','win32'):
            root=Path(tmp)
            command=desktop_argv(root,['--root',str(root),'--launch-intent','manual'])
            self.assertEqual(Path(command[0]),root/'runtime/pythonw.exe')
            self.assertEqual(command[2:],['--root',str(root),'--launch-intent','manual'])

    def test_window_reconnect_detaches_but_full_restart_closes_the_session_without_owner_exit(self):
        for full in (False,True):
            with self.subTest(full=full),tempfile.TemporaryDirectory() as tmp:
                app=python_app.Application(Path(tmp),Path(tmp))
                host=Mock();app.host=host
                app.invoke('restart_self',{'full':full})
                app.invoke('python_window',{'action':'exit'})
                self.assertTrue(app.relaunch)
                app.close()
                if full:host.shutdown.assert_called_once_with(owner_exit=False)
                else:host.close.assert_called_once_with();host.shutdown.assert_not_called()

    def test_tray_restores_minimized_placement_then_foreground_without_sticky_topmost(self):
        for state in ('Minimized','Normal','Maximized'):
            with self.subTest(state=state):
                api=SimpleNamespace(ShowWindow=Mock(),SetForegroundWindow=Mock(return_value=1))
                native=Mock();native.Handle.ToInt64.return_value=1234;native.WindowState.ToString.return_value=state
                with patch.object(ctypes,'WinDLL',return_value=api,create=True):
                    self.assertTrue(python_tray.restore_window(native))
                native.Show.assert_called_once();native.BringToFront.assert_called_once();native.Activate.assert_called_once()
                api.SetForegroundWindow.assert_called_once_with(1234)
                if state=='Minimized':api.ShowWindow.assert_called_once_with(1234,9)
                else:api.ShowWindow.assert_not_called()
                self.assertNotIn('TopMost',native.__dict__)

if __name__=='__main__':unittest.main()
