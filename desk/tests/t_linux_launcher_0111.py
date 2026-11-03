"""Exercise the emitted shell entry before Python and Qt are loaded."""
from pathlib import Path
import os,subprocess,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
from python_posix_bundle import launcher

@unittest.skipIf(os.name=='nt','POSIX shell entry consumer')
class Launcher(unittest.TestCase):
 def run_entry(self,*,target='linux-x86_64',libraries=True,previous=None):
  with tempfile.TemporaryDirectory() as raw:
   root=Path(raw)/'Hélène with spaces';(root/'runtime/bin').mkdir(parents=True)
   if libraries:(root/'runtime/system-libs').mkdir()
   (root/'runtime/lib/python-fixture/site-packages/PySide6/Qt/lib').mkdir(parents=True)
   python=root/'runtime/bin/python3'
   python.write_text('#!/bin/sh\nprintf "%s\\n" "${LD_LIBRARY_PATH-unset}" "$@"\n');python.chmod(0o755)
   entry=root/'helene';entry.write_text(launcher(target));entry.chmod(0o755)
   env={k:v for k,v in os.environ.items() if k!='LD_LIBRARY_PATH'}
   if previous is not None:env['LD_LIBRARY_PATH']=previous
   rows=subprocess.check_output([str(entry),'--argument','value with spaces'],env=env,cwd='/',text=True).splitlines()
   self.assertEqual(rows[1:],['-s',str(root/'app/python_app.py'),'--root',str(root),'--argument','value with spaces'])
   return rows[0],str(root/'runtime/system-libs')
 def test_linux_prefers_its_verified_libraries_and_retains_owner_tail(self):
  actual,bundled=self.run_entry(previous='/fixture/owner libraries:/fixture/second')
  paths=actual.split(':')
  self.assertTrue(paths[0].endswith('/runtime/lib/python-fixture/site-packages/PySide6/Qt/lib'))
  self.assertTrue(paths[1].endswith('/runtime/lib'))
  self.assertGreater(paths.index(bundled),1,'OS libraries precede the fallback closure')
  self.assertEqual(paths[-2:],['/fixture/owner libraries','/fixture/second'])
 def test_runtime_without_a_library_overlay_retains_ambient_lookup(self):
  self.assertEqual(self.run_entry(libraries=False)[0],'unset')
 def test_macos_does_not_inject_a_linux_loader_path(self):
  self.assertEqual(self.run_entry(target='macos-arm64',previous='/fixture/owner')[0],'/fixture/owner')

if __name__=='__main__':unittest.main()
