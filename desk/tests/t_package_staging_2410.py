"""Specific low-space immutable POSIX package-staging checks."""
from pathlib import Path
import errno,os,stat,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
import python_posix_packages as packages

@unittest.skipUnless(os.name=='posix','POSIX file-mode and hard-link staging')
class Staging(unittest.TestCase):
 def test_normalized_file_shares_owned_build_bytes(self):
  with tempfile.TemporaryDirectory() as t:
   src=Path(t)/'source';dst=Path(t)/'stage';src.write_bytes(b'seed');src.chmod(0o644);packages.stage_file(src,dst)
   self.assertEqual(src.stat().st_ino,dst.stat().st_ino)
 def test_noncanonical_mode_is_copied_before_rpm_normalization(self):
  with tempfile.TemporaryDirectory() as t:
   src=Path(t)/'source';dst=Path(t)/'stage';src.write_bytes(b'seed');src.chmod(0o600);packages.stage_file(src,dst);dst.chmod(0o644)
   self.assertNotEqual(src.stat().st_ino,dst.stat().st_ino);self.assertEqual(stat.S_IMODE(src.stat().st_mode),0o600);self.assertEqual(src.read_bytes(),b'seed')
 def test_cross_device_copies_but_unrelated_failure_is_not_hidden(self):
  with tempfile.TemporaryDirectory() as t:
   src=Path(t)/'source';src.write_bytes(b'seed');src.chmod(0o644)
   with patch.object(packages.os,'link',side_effect=OSError(errno.EXDEV,'different device')):packages.stage_file(src,Path(t)/'stage')
   self.assertEqual((Path(t)/'stage').read_bytes(),b'seed')
   with patch.object(packages.os,'link',side_effect=PermissionError(errno.EACCES,'denied')):
    with self.assertRaises(PermissionError):packages.stage_file(src,Path(t)/'other')

if __name__=='__main__':unittest.main()
