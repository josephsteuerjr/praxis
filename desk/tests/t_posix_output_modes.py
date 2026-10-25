"""POSIX packages and extraction never publish NTFS world-writable code."""
import json,os,stat,sys,tempfile,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
from localharness import python_updates
from python_posix_bundle import normalize_output_modes, prepare_qt_process
import python_posix_packages as packages

@unittest.skipIf(os.name=='nt','POSIX file modes require the native target')
class Modes(unittest.TestCase):
 def test_owned_output_keeps_execution_but_removes_other_writers(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);d=root/'app';d.mkdir();d.chmod(0o777)
   a=d/'run';a.write_text('code');a.chmod(0o777)
   b=d/'data';b.write_text('data');b.chmod(0o666)
   normalize_output_modes(root)
   self.assertEqual(stat.S_IMODE(d.stat().st_mode),0o755)
   self.assertEqual(stat.S_IMODE(a.stat().st_mode),0o755)
   self.assertEqual(stat.S_IMODE(b.stat().st_mode),0o644)
 def test_package_normalization_does_not_mutate_source_through_hardlinks(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);seed=root/'seed';seed.mkdir();(seed/'app').mkdir();(seed/'app').chmod(0o777)
   (seed/'python-app.json').write_text(json.dumps({'target':'linux-x86_64','version':'2.0.0'}))
   for n in ('LICENSE','NOTICE','THIRD-PARTY.md'):(seed/n).write_text('fixture')
   a=seed/'app/main.py';a.write_text('pass');a.chmod(0o777)
   packages.stage(seed,root/'package')
   self.assertEqual(stat.S_IMODE(a.stat().st_mode),0o777)
   got=root/'package/usr/lib/helene-python/seed/app/main.py'
   self.assertEqual(stat.S_IMODE(got.stat().st_mode),0o755)
   self.assertEqual(got.read_bytes(),a.read_bytes())
 def test_complete_payload_normalized_under_permissive_umask(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);seed=root/'seed';seed.mkdir();seed.chmod(0o777)
   (seed/'python-app.json').write_text(json.dumps({'target':'linux-x86_64','version':'2.0.0'}))
   for n in ('LICENSE','NOTICE','THIRD-PARTY.md'):
    f=seed/n;f.write_text('fixture');f.chmod(0o666)
   before=os.umask(0)
   try:packages.stage(seed,root/'package')
   finally:os.umask(before)
   payload=root/'package'
   for f in (payload,*payload.rglob('*')):
    self.assertEqual(stat.S_IMODE(f.stat().st_mode)&0o022,0,str(f))
   self.assertEqual(stat.S_IMODE(seed.stat().st_mode),0o777)
   self.assertEqual(stat.S_IMODE((seed/'NOTICE').stat().st_mode),0o666)
   late=seed/'python-package-files.json';late.write_text('{}');late.chmod(0o666)
   normalize_output_modes(seed)
   self.assertEqual(stat.S_IMODE(seed.stat().st_mode),0o755)
   self.assertEqual(stat.S_IMODE(late.stat().st_mode),0o644)
 def test_qt_process_permission_is_prepared_in_owned_runtime(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);helper=root/'runtime/lib/python3.14/site-packages/PySide6/Qt/libexec/QtWebEngineProcess'
   helper.parent.mkdir(parents=True);helper.write_bytes(b'\x7fELFfixture');helper.chmod(0o644)
   prepare_qt_process(root)
   self.assertEqual(stat.S_IMODE(helper.stat().st_mode),0o755)
   helper.write_bytes(b'not-an-ELF')
   with self.assertRaises(ValueError):prepare_qt_process(root)
 def test_zip_preserves_executable_and_clamps_writable_input(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);archive=root/'input.zip'
   with zipfile.ZipFile(archive,'w') as z:
    for name,mode in (('run',0o777),('data',0o666)):
     i=zipfile.ZipInfo('Helene/'+name);i.external_attr=(stat.S_IFREG|mode)<<16;z.writestr(i,b'payload')
   python_updates._extract(archive,root/'out')
   self.assertEqual(stat.S_IMODE((root/'out/Helene/run').stat().st_mode),0o755)
   self.assertEqual(stat.S_IMODE((root/'out/Helene/data').stat().st_mode),0o644)

if __name__=='__main__':unittest.main(verbosity=2)
