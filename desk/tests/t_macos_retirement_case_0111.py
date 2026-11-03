"""Real case-insensitive filesystem retirement, no live installations."""
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_posix

@unittest.skipUnless(sys.platform=='darwin','Native Mac filesystem semantics')
class CaseAliases(unittest.TestCase):
    def test_one_actual_entry_is_retired_once_despite_case_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();old=root/'old';new=root/'new';old.mkdir();new.mkdir()
            (old/'helene.exe').write_bytes(b'synthetic previous launcher')
            if not (old/'Helene.exe').exists():self.skipTest('This filesystem is case sensitive')
            data=root/'profile';data.mkdir();(data/'owner.txt').write_bytes(b'retain exact')
            note={'startup_proof':{'synthetic':True},'data_paths':[str(data)],'old_data_paths':[]}
            result=migration_posix.remove_program(old,new,note)
            self.assertEqual(result['removed_files'],1);self.assertFalse((old/'helene.exe').exists())
            self.assertEqual((data/'owner.txt').read_bytes(),b'retain exact')

if __name__=='__main__':unittest.main()
