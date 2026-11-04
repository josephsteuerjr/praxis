from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
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
 def fixture(self,**changes):
  return SimpleNamespace(host=object(),tray=None,exiting=False,relaunch=False,exit_session=False,session_exit_intent=False,**changes)
 def test_linux_close_without_tray_owns_session_exit(self):
  app=self.fixture()
  with patch.object(sys,'platform','linux'):python_app.close_without_tray(app)
  self.assertTrue(app.exiting and app.exit_session and app.session_exit_intent)
 def test_observer_exit_relaunch_and_other_platform_do_not_acquire_exit(self):
  for change in ({'exiting':True},{'relaunch':True},{'tray':object()},{'host':None}):
   app=self.fixture()
   for key,value in change.items():setattr(app,key,value)
   with patch.object(sys,'platform','linux'):python_app.close_without_tray(app)
   self.assertFalse(app.exit_session)
  app=self.fixture()
  with patch.object(sys,'platform','darwin'):python_app.close_without_tray(app)
  self.assertFalse(app.exit_session)

if __name__=='__main__':unittest.main()
