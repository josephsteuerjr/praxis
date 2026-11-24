from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness.linux_desktop import configure
import python_app

class Desktop(unittest.TestCase):
 def test_vmware_xwayland_uses_software_without_disabling_sandbox(self):
  env={'DISPLAY':':0','WAYLAND_DISPLAY':'wayland-0'};configure(env,virtual=True)
  self.assertEqual(env['QT_QPA_PLATFORM'],'xcb')
  self.assertEqual(env['QTWEBENGINE_CHROMIUM_FLAGS'],'--disable-gpu')
  self.assertNotIn('QTWEBENGINE_DISABLE_SANDBOX',env)
 def test_owner_rendering_choices_are_preserved(self):
  env={'DISPLAY':':1','QT_QPA_PLATFORM':'owner-platform','QT_QUICK_BACKEND':'owner-backend','QTWEBENGINE_CHROMIUM_FLAGS':'--owner-flag'}
  previous=dict(env);configure(env,virtual=True)
  for key,value in previous.items():self.assertEqual(env[key],value)
 def test_physical_machine_keeps_default_gpu_and_no_display_does_not_invent_one(self):
  env={};configure(env,virtual=False)
  self.assertNotIn('QT_QPA_PLATFORM',env)
  self.assertNotIn('QTWEBENGINE_CHROMIUM_FLAGS',env)
 def test_linux_window_manager_close_minimizes_and_vetoes_destruction(self):
  app=SimpleNamespace(window=Mock(),host=object(),exiting=False,relaunch=False)
  self.assertIs(python_app.linux_window_closing(app),False)
  app.window.minimize.assert_called_once_with()
  self.assertFalse(app.exiting)

 def test_exit_relaunch_and_setup_can_close_the_window(self):
  for change in ({'exiting':True},{'relaunch':True},{'host':None}):
   with self.subTest(change=change):
    app=SimpleNamespace(**({'window':Mock(),'host':object(),'exiting':False,'relaunch':False}|change))
    self.assertIs(python_app.linux_window_closing(app),True)
    app.window.minimize.assert_not_called()

if __name__=='__main__':unittest.main()
