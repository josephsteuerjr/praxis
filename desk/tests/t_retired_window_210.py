import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from localharness import migration_windows as migration
from localharness.desktop_supervisor import _tree_hold

class Retirement(unittest.TestCase):
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
