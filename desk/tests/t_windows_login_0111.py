"""Native per-user registration with an isolated registry key and profile."""
from pathlib import Path
from types import SimpleNamespace
import os,sys,tempfile,unittest,uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_windows as windows

@unittest.skipUnless(os.name=='nt','Actual Windows login registry and shell links')
class Login(unittest.TestCase):
 def setUp(self):
  import winreg
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
  self.root=self.base/'Мама программа';self.root.mkdir();(self.root/'helene.exe').write_bytes(b'not executed')
  self.owner=SimpleNamespace(root=self.root)
  self.registry=r'Software\Helene-QA-'+uuid.uuid4().hex
  self.enterContext(patch.object(windows,'RUN_KEY',self.registry))
  self.enterContext(patch.dict(os.environ,APPDATA=str(self.base/'Roaming')))
  def remove():
   try:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,self.registry)
   except FileNotFoundError:pass
  self.addCleanup(remove)
 def test_enable_readback_disable(self):
  self.assertFalse(windows.invoke(self.owner,'autostart_get',{}))
  windows.invoke(self.owner,'autostart_set',{'on':True});self.assertTrue(windows.invoke(self.owner,'autostart_get',{}))
  windows.invoke(self.owner,'autostart_set',{'on':False});self.assertIsNone(windows.run_binding())
 def test_native_user_registration_does_not_launch_powershell(self):
  with patch.object(windows,'powershell',side_effect=AssertionError('No script interpreter for login registration')):
   windows.invoke(self.owner,'autostart_set',{'on':True});self.assertTrue(windows.invoke(self.owner,'autostart_get',{}))
 def test_moved_helene_binding_is_retargeted(self):
  old=r'"D:\Users\Мама\Рабочий стол\Helene\helene.exe" --launch-intent automatic'
  windows.set_run(old);self.assertTrue(windows.owned_run(old))
  windows.invoke(self.owner,'autostart_set',{'on':True});self.assertEqual(windows.run_binding(),windows.run_command(self.root))
 def test_foreign_registry_value_is_retained(self):
  previous=r'"C:\Other application\other.exe" --launch-intent automatic';windows.set_run(previous)
  with self.assertRaises(RuntimeError):windows.invoke(self.owner,'autostart_set',{'on':True})
  self.assertEqual(windows.run_binding(),previous)
 def test_python_entry_keeps_quoted_unicode_program_and_arguments(self):
  (self.root/'helene.exe').unlink();python=self.root/'runtime/pythonw.exe';python.parent.mkdir();python.write_bytes(b'not executed')
  command=windows.run_command(self.root);self.assertTrue(windows.owned_run(command))
  windows.invoke(self.owner,'autostart_set',{'on':True});self.assertTrue(windows.invoke(self.owner,'autostart_get',{}))
 def test_legacy_manual_shortcut_is_upgraded_to_automatic_intent(self):
  link=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/Helene.lnk'
  windows.shortcut(link,self.root/'helene.exe',self.root,'')
  windows.invoke(self.owner,'autostart_set',{'on':True})
  self.assertFalse(link.exists());self.assertTrue(windows.invoke(self.owner,'autostart_get',{}))
 def test_retiring_captured_program_retargets_enabled_login(self):
  old=self.base/'Прежняя папка';windows.set_run(windows.run_command(old))
  windows.retarget_run([old],self.root);self.assertEqual(windows.run_binding(),windows.run_command(self.root))
 def test_retirement_does_not_change_an_unrelated_login_preference(self):
  old=self.base/'Other Helene';command=windows.run_command(old);windows.set_run(command)
  windows.retarget_run([self.base/'Retiring Helene'],self.root);self.assertEqual(windows.run_binding(),command)
 def test_removing_captured_program_drops_its_login_registration(self):
  windows.set_run(windows.run_command(self.root));windows.retarget_run([self.root],None);self.assertIsNone(windows.run_binding())

if __name__=='__main__':unittest.main()
