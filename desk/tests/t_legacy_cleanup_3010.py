"""Legacy cleanup is exact-target and empty-only, without a new uninstaller."""
import os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_windows as migration
from deskd import local_files

class Cleanup(unittest.TestCase):
    def test_empty_local_placeholder_removed_nonempty_preserved(self):
        for populated,expected in ((False,'empty-removed'),(True,'nonempty-retained')):
            with self.subTest(populated=populated),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);target=base/'Helene';target.mkdir()
                if populated:(target/'unknown-owner-file').write_bytes(b'owner data')
                winos=SimpleNamespace(**{**vars(os),'name':'nt'})
                with patch.object(migration,'os',winos),patch.object(local_files,'_known_folder',return_value=base):
                    self.assertEqual(migration.remove_empty_legacy_local()['state'],expected)
                self.assertEqual(target.exists(),populated)
                if populated:self.assertEqual((target/'unknown-owner-file').read_bytes(),b'owner data')

    def test_unpin_happens_while_owned_shortcuts_still_exist_and_leaves_other_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';old.mkdir();foreign=root/'other';foreign.mkdir()
            own=root/'own.lnk';other=root/'other.lnk';own.write_bytes(b'shortcut');other.write_bytes(b'other')
            bindings={own:{'target':str(old/'helene.exe')},other:{'target':str(foreign/'helene.exe')}}
            seen=[]
            def unpin(path):self.assertTrue(path.exists());seen.append(path)
            with patch.object(migration,'shortcut_binding',side_effect=bindings.get),patch.object(migration,'unpin_shortcut',side_effect=unpin):
                self.assertEqual(migration.unpin_shortcuts(old,[root]),[str(own)])
            self.assertEqual(seen,[own]);self.assertTrue(own.exists());self.assertTrue(other.exists())

    def test_linked_local_placeholder_is_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);target=base/'Helene';outside=base/'outside';outside.mkdir()
            try:target.symlink_to(outside,target_is_directory=True)
            except OSError:self.skipTest('Creating this fixture directory link is unavailable')
            winos=SimpleNamespace(**{**vars(os),'name':'nt'})
            with patch.object(migration,'os',winos),patch.object(local_files,'_known_folder',return_value=base):
                self.assertEqual(migration.remove_empty_legacy_local()['state'],'linked-retained')
            self.assertTrue(target.is_symlink());self.assertTrue(outside.exists())

if __name__=='__main__':unittest.main()
