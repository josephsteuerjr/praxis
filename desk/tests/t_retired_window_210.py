import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from localharness import migration_windows as migration
from localharness.desktop_supervisor import _tree_hold

class Retirement(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows registry command parsing')
    def test_registered_command_uses_exact_root_and_refuses_purge(self):
        import uuid,winreg
        with tempfile.TemporaryDirectory() as temp:
            old=Path(temp);exe=old/'helene-setup.exe';exe.write_bytes(b'not executable; discovery only')
            keypath=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\HeleneTest-'+uuid.uuid4().hex
            try:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,keypath,0,winreg.KEY_WRITE|winreg.KEY_WOW64_64KEY) as key:
                    winreg.SetValueEx(key,'InstallLocation',0,winreg.REG_SZ,str(old))
                    winreg.SetValueEx(key,'QuietUninstallString',0,winreg.REG_SZ,f'"{exe}" --uninstall --quiet')
                self.assertEqual(migration.registered_uninstall(old),[str(exe),'--uninstall','--quiet'])
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,keypath,0,winreg.KEY_WRITE|winreg.KEY_WOW64_64KEY) as key:
                    winreg.SetValueEx(key,'QuietUninstallString',0,winreg.REG_SZ,f'"{exe}" --uninstall --quiet --purge')
                with self.assertRaisesRegex(ValueError,'switches'):migration.registered_uninstall(old)
            finally:winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER,keypath,winreg.KEY_WOW64_64KEY,0)

    def test_registered_uninstaller_cannot_remove_another_installations_service(self):
        with tempfile.TemporaryDirectory() as temp:
            old=Path(temp)/'old';other=Path(temp)/'other'
            current={'PathName':f'"{other}/helene-service.exe"','State':'Running'}
            with patch.object(migration,'service',return_value=current),patch.object(migration.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'another installation'):
                    migration.uninstall_registered(old,[str(old/'helene-setup.exe'),'--uninstall','--quiet'])
            run.assert_not_called()

    def test_registered_uninstall_runs_after_new_window_proof_and_before_new_shortcuts(self):
        import python_migrate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';new=root/'new';old.mkdir();new.mkdir()
            events=[];command=[str(old/'helene-setup.exe'),'--uninstall','--quiet']
            note={'id':'proof','state':'new_app_started','startup_proof':{'stopped':True}}
            with patch.object(migration,'registered_uninstall',return_value=command),patch.object(migration,'uninstall_registered',side_effect=lambda *a:events.append('uninstall') or {'ok':True}),patch.object(migration,'retire',side_effect=lambda *a:events.append('shortcuts') or {}):
                result=python_migrate.finish_retirement(old,new,new,note,lambda v:None)
            self.assertEqual(events,['uninstall','shortcuts']);self.assertEqual(result['state'],'completed')
    def test_uninstaller_failure_keeps_new_receipt_pending(self):
        import python_migrate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);note={'id':'proof','state':'new_app_started','startup_proof':{'stopped':True}}
            with patch.object(migration,'registered_uninstall',return_value=['old.exe']),patch.object(migration,'uninstall_registered',side_effect=RuntimeError('not removed')),patch.object(migration,'retire') as retire:
                with self.assertRaises(RuntimeError):python_migrate.finish_retirement(root,root,root,note,lambda v:None)
            retire.assert_not_called();self.assertEqual(note['state'],'new_app_started')
    def test_stale_active_word_is_not_a_live_owner_and_is_not_erased(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);record=root/'.desktop-owner.lock';record.write_bytes(b'active\n')
            self.assertTrue(migration.owner_lease_available(record))
            self.assertEqual(record.read_bytes(),b'active\n')
            with _tree_hold(root):self.assertFalse(migration.owner_lease_available(record))
            self.assertTrue(migration.owner_lease_available(record))
            self.assertEqual(record.read_bytes(),b'active\n')
    @unittest.skipUnless(os.name=='nt','Windows process discovery')
    def test_pythonw_owner_uses_authenticated_exit_without_service(self):
        with tempfile.TemporaryDirectory() as temp:
            old=Path(temp);(old/'tree').mkdir();(old/'tree/.desktop-owner.lock').write_bytes(b'active\n')
            row={'ProcessId':123,'CommandLine':f'"{old}/runtime/pythonw.exe" -s "{old}/app/python_app.py" --root "{old}"'}
            def ps(script,env):
                if 'Win32_Process' in script:
                    self.assertIn('pythonw.exe',script);return json.dumps([row])
                return ''
            with patch.object(migration,'powershell',side_effect=ps),patch.object(migration,'request_python_exit') as exit_,patch('localharness.app_migration.discover',return_value={'agents':[{'tree':str(old/'tree'),'value':{}}]}):
                migration.close_python(old);exit_.assert_called_once_with(old,123)
    def test_pinned_shortcut_is_retargeted_and_unrelated_binding_is_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';new=root/'new';folders=[root/str(i) for i in range(6)]
            for folder in folders:folder.mkdir()
            old_link=folders[4]/'Pinned.lnk';old_link.touch();foreign=folders[4]/'Other.lnk';foreign.touch()
            def binding(path):return {'target':str(old/'helene.exe') if path==old_link else str(root/'other/helene.exe'),'arguments':'','cwd':''}
            with patch.object(migration,'shortcut_binding',side_effect=binding),patch.object(migration,'shortcut') as make:
                self.assertEqual(migration.retarget_shortcuts(old,new,folders),[str(old_link)])
                make.assert_called_once_with(old_link,new/'helene.exe',new,'')

if __name__=='__main__':unittest.main()
