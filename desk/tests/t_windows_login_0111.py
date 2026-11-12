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
  self.enterContext(patch.object(windows,'STARTUP_APPROVED_KEY',self.registry+'\\Approved'))
  self.enterContext(patch.dict(os.environ,APPDATA=str(self.base/'Roaming')))
  def remove():
   for suffix in ('\\Approved\\Run','\\Approved\\StartupFolder','\\Approved'):
    try:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,self.registry+suffix)
    except FileNotFoundError:pass
   try:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,self.registry)
   except FileNotFoundError:pass
  self.addCleanup(remove)
 def approval(self,location,name,state):
  import winreg
  value=state.to_bytes(4,'little')+b'\x12'*8
  with winreg.CreateKey(winreg.HKEY_CURRENT_USER,windows.STARTUP_APPROVED_KEY+'\\'+location) as key:
   winreg.SetValueEx(key,name,0,winreg.REG_BINARY,value)
  return value
 def test_windows_disabled_run_is_not_reported_enabled(self):
  windows.set_run(windows.run_command(self.root))
  for state in (3,7):
   with self.subTest(state=state):
    before=self.approval('Run',windows.RUN_NAME,state)
    self.assertFalse(windows.invoke(self.owner,'autostart_get',{}))
    self.assertEqual(windows.startup_approval('Run',windows.RUN_NAME),before)
 def test_explicit_enable_resets_only_own_disabled_run(self):
  windows.set_run(windows.run_command(self.root))
  other=self.approval('Run','AnotherApp',3)
  for state in (3,7):
   self.approval('Run',windows.RUN_NAME,state)
   windows.invoke(self.owner,'autostart_set',{'on':True})
   self.assertTrue(windows.invoke(self.owner,'autostart_get',{}))
   self.assertIsNone(windows.startup_approval('Run',windows.RUN_NAME))
  self.assertEqual(windows.startup_approval('Run','AnotherApp'),other)
 def test_disabled_automatic_shortcut_is_enabled_without_duplicate_run(self):
  link=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/Helene.lnk'
  windows.shortcut(link,self.root/'helene.exe',self.root,windows.AUTOMATIC_ARGUMENTS)
  self.approval('StartupFolder',link.name,3)
  before=link.read_bytes()
  self.assertFalse(windows.invoke(self.owner,'autostart_get',{}))
  windows.invoke(self.owner,'autostart_set',{'on':True})
  self.assertTrue(windows.invoke(self.owner,'autostart_get',{}))
  self.assertEqual(link.read_bytes(),before)
  self.assertIsNone(windows.run_binding())
 def test_update_retarget_preserves_windows_disabled_preference(self):
  old=self.base/'Old Helene';windows.set_run(windows.run_command(old))
  before=self.approval('Run',windows.RUN_NAME,3)
  windows.retarget_run([old],self.root)
  self.assertEqual(windows.run_binding(),windows.run_command(self.root))
  self.assertEqual(windows.startup_approval('Run',windows.RUN_NAME),before)
  self.assertFalse(windows.invoke(self.owner,'autostart_get',{}))
 def test_unknown_approval_is_not_reported_enabled_or_overwritten(self):
  windows.set_run(windows.run_command(self.root))
  before=self.approval('Run',windows.RUN_NAME,100)
  self.assertFalse(windows.invoke(self.owner,'autostart_get',{}))
  with self.assertRaisesRegex(RuntimeError,'Unknown Windows startup approval'):
   windows.invoke(self.owner,'autostart_set',{'on':True})
  self.assertEqual(windows.startup_approval('Run',windows.RUN_NAME),before)
 def test_malformed_approval_propagates_and_is_kept(self):
  import winreg
  windows.set_run(windows.run_command(self.root))
  with winreg.CreateKey(winreg.HKEY_CURRENT_USER,windows.STARTUP_APPROVED_KEY+'\\Run') as key:
   winreg.SetValueEx(key,windows.RUN_NAME,0,winreg.REG_SZ,'foreign')
  for command,args in [('autostart_get',{}),('autostart_set',{'on':True})]:
   with self.assertRaisesRegex(RuntimeError,'Invalid Windows startup approval'):
    windows.invoke(self.owner,command,args)
  with winreg.OpenKey(winreg.HKEY_CURRENT_USER,windows.STARTUP_APPROVED_KEY+'\\Run') as key:
   self.assertEqual(winreg.QueryValueEx(key,windows.RUN_NAME),('foreign',winreg.REG_SZ))
 def test_foreign_binding_refusal_preserves_approval(self):
  command='"C:\\Other\\app.exe"';windows.set_run(command)
  before=self.approval('Run',windows.RUN_NAME,3)
  with self.assertRaises(RuntimeError):windows.invoke(self.owner,'autostart_set',{'on':True})
  self.assertEqual(windows.run_binding(),command)
  self.assertEqual(windows.startup_approval('Run',windows.RUN_NAME),before)
 def test_approval_write_denial_is_not_reported_as_success(self):
  import winreg
  windows.set_run(windows.run_command(self.root))
  before=self.approval('Run',windows.RUN_NAME,3)
  with patch.object(winreg,'DeleteValue',side_effect=PermissionError('approval denied')):
   with self.assertRaises(PermissionError):windows.invoke(self.owner,'autostart_set',{'on':True})
  self.assertEqual(windows.startup_approval('Run',windows.RUN_NAME),before)
  self.assertFalse(windows.invoke(self.owner,'autostart_get',{}))
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

 def test_update_restores_captured_run_after_old_hooks_removed_and_is_one_shot(self):
  from localharness import login_migration as migration
  old=self.base/'Old';old.mkdir();(old/'helene.exe').write_bytes(b'not executed')
  for state in (2,3):
   with self.subTest(state=state):
    windows.set_run(windows.run_command(old));approval=self.approval('Run',windows.RUN_NAME,state)
    migration.capture(self.root,[old]);windows.set_run(None)
    migration.capture(self.root,[old])  # resumed cleanup must not lose the captured preference
    result=migration.commit(self.root)
    self.assertEqual(result['outcomes']['run'],'retargeted')
    self.assertEqual(windows.run_binding(),windows.run_command(self.root))
    self.assertEqual(windows.startup_approval('Run',windows.RUN_NAME),approval)
    self.assertEqual(windows.invoke(self.owner,'autostart_get',{}),state==2)
    windows.invoke(self.owner,'autostart_set',{'on':False});migration.commit(self.root)
    self.assertIsNone(windows.run_binding(),'later reads must not undo an explicit disable')

 def test_update_restores_legacy_startup_shortcut_and_preserves_disabled_state(self):
  from localharness import login_migration as migration
  old=self.base/'Old';old.mkdir();(old/'helene.exe').write_bytes(b'not executed')
  link=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/Helene.lnk'
  windows.shortcut(link,old/'helene.exe',old,'')
  approval=self.approval('StartupFolder',link.name,3)
  migration.capture(self.root,[old]);link.unlink();migration.commit(self.root)
  self.assertTrue(windows.shortcut_matches(windows.shortcut_binding(link),self.root/'helene.exe',self.root,windows.AUTOMATIC_ARGUMENTS))
  self.assertEqual(windows.startup_approval('StartupFolder',link.name),approval)
  self.assertFalse(windows.invoke(self.owner,'autostart_get',{}))

 def test_update_does_not_enable_absent_or_overwrite_replaced_login(self):
  from localharness import login_migration as migration
  old=self.base/'Old';migration.capture(self.root,[old]);migration.commit(self.root)
  self.assertIsNone(windows.run_binding())
  windows.set_run(windows.run_command(old));migration.capture(self.root,[old])
  foreign='"C:\\Other\\app.exe"';windows.set_run(foreign)
  self.assertEqual(migration.commit(self.root)['outcomes']['run'],'changed-elsewhere')
  self.assertEqual(windows.run_binding(),foreign)

if __name__=='__main__':unittest.main()
