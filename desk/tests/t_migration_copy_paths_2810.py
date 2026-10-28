"""Windows may return an extended path after copy workers create a parent."""
from pathlib import Path
import os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import python_migrate


@unittest.skipUnless(os.name=='nt','Windows path representations')
class CopyPaths(unittest.TestCase):
    def test_extended_drive_and_unc_have_the_same_identity(self):
        for plain,extended in ((r'C:\Programs\Helene\app',r'\\?\C:\Programs\Helene\app'),
                               (r'\\server\share\Helene\app',r'\\?\UNC\server\share\Helene\app')):
            with self.subTest(plain=plain),patch.object(Path,'resolve',return_value=Path(plain)):
                expected=python_migrate._program_path_identity(Path('unused'))
            with patch.object(Path,'resolve',return_value=Path(extended)):
                actual=python_migrate._program_path_identity(Path('unused'))
            self.assertEqual(expected,actual)
            self.assertFalse(actual.is_relative_to(expected.parent.with_name('Other')))

    def test_extended_destination_after_preflight_is_still_inside(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'package';new=root/'Helene 2.2.13'
            package.mkdir();new.mkdir()
            (package/'early.css').write_bytes(b'css')
            (package/'later.py').write_bytes(b'python')
            original=Path.resolve;copy_started=False
            def resolve(path,*args,**kwargs):
                result=original(path,*args,**kwargs)
                if copy_started and path==new/'later.py':return Path('\\\\?\\'+str(result))
                return result
            def progress(event):
                nonlocal copy_started
                copy_started=True
            with patch.object(Path,'resolve',resolve):
                python_migrate.copy_program(package,new,progress)
            self.assertEqual((new/'later.py').read_bytes(),b'python')

    def test_resolved_escape_is_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'package';new=root/'new';outside=root/'outside'
            package.mkdir();new.mkdir();outside.mkdir()
            (package/'escape.py').write_bytes(b'cannot escape')
            original=Path.resolve
            def resolve(path,*args,**kwargs):
                if path==new/'escape.py':return Path('\\\\?\\'+str(outside/'escape.py'))
                return original(path,*args,**kwargs)
            with patch.object(Path,'resolve',resolve),self.assertRaisesRegex(ValueError,'Invalid program copy path'):
                python_migrate.copy_program(package,new,lambda event:None)
            self.assertFalse((outside/'escape.py').exists())


if __name__=='__main__':unittest.main()
