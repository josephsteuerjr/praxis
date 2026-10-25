"""The native builder consumes selected edition/body and explicit relay source."""
from pathlib import Path
from unittest.mock import patch
import importlib.util,tempfile,unittest
s=importlib.util.spec_from_file_location('native_build',Path(__file__).resolve().parents[1]/'installer/build_posix_native.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class NativeInputs(unittest.TestCase):
 def test_selected_body_and_explicit_headless_relay(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);edition=p/'selected';relay=p/'modern-relay';target=p/'target';calls=[]
   for source in (edition/'body',relay):
    source.mkdir(parents=True)
    for name in ('Cargo.toml','Cargo.lock'):(source/name).write_text('selected-input')
   def cargo(argv,*,cwd,env,check):
    calls.append((argv,cwd));(target/'release').mkdir(parents=True,exist_ok=True)
    for name in ('praxis-body','praxis-bridge','praxis-relay-headless'):(target/'release'/name).write_bytes(b'exact-native-fixture')
   with patch.object(m.sys,'platform','linux'),patch.object(m.subprocess,'run',side_effect=cargo):
    receipt=m.build(edition,relay,p/'out',target_dir=target,jobs=4)
   self.assertEqual([cwd for argv,cwd in calls],[edition/'body',relay])
   self.assertIn('praxis-relay-headless',calls[1][0]);self.assertIn('--locked',calls[0][0])
   self.assertEqual(set(receipt['builds'][0]['binaries']),{'helene-body','helene-bridge'})
   self.assertTrue((p/'out/native-build.json').is_file())
if __name__=='__main__':unittest.main(verbosity=2)
