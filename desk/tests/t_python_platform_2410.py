"""Actual app-info and native bridge consume the same public platform vocabulary."""
from pathlib import Path
from types import SimpleNamespace
import json,sys,sysconfig,tempfile,unittest
from unittest.mock import patch
DESK=Path(__file__).resolve().parents[1];sys.path.insert(0,str(DESK))
import python_app
from localharness import python_platform,python_desktop_commands as commands

class PublicIdentity(unittest.TestCase):
 def test_empty_windows_query_uses_compiled_abi_and_keeps_unknown_targets_closed(self):
  for compiled,arch,target in [('win-amd64','x86_64','windows-x86_64'),('win-arm64','arm64',None),('unknown','',None)]:
   with self.subTest(compiled=compiled),patch.object(sys,'platform','win32'),patch.object(python_platform.platform,'machine',return_value=''),patch.object(sysconfig,'get_platform',return_value=compiled):
    self.assertEqual(python_platform.desktop_identity(),{'platform':'windows','arch':arch})
    if target:self.assertEqual(python_platform.runtime_target(),target)
    else:
     with self.assertRaises(RuntimeError):python_platform.runtime_target()

 def test_positive_unsupported_machine_is_never_overridden_by_compiled_fallback(self):
  with patch.object(sys,'platform','win32'),patch.object(python_platform.platform,'machine',return_value='arm64'),patch.object(sysconfig,'get_platform',return_value='win-amd64') as fallback:
   with self.assertRaises(RuntimeError):python_platform.runtime_target()
  fallback.assert_not_called()

 def test_app_info_and_bridge_share_actual_architecture(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'python-app.json').write_text(json.dumps({'format':'helene-python-app.v1','version':'1.9.4'}))
   agent=SimpleNamespace(config=root/'helene.json',tree=root/'data')
   owner=SimpleNamespace(root=root,current=lambda:agent)
   app=python_app.Application(root,root,program=root)
   try:
    for platform,machine,system,arch in [('darwin','arm64','macos','arm64'),('darwin','x86_64','macos','x86_64'),('linux','aarch64','linux','arm64'),('win32','AMD64','windows','x86_64')]:
     with self.subTest(platform=platform,machine=machine),patch.object(sys,'platform',platform),patch.object(python_platform.platform,'machine',return_value=machine),patch.object(commands,'load',return_value={}):
      info=commands.invoke(owner,'app_info',{})
      self.assertEqual((info['platform'],info['arch']),(system,arch))
      self.assertIn(json.dumps(system),app.bridge())
   finally:app.server.server_close()
 def test_ui_metadata_does_not_grant_unsupported_runtime(self):
  with patch.object(sys,'platform','darwin'),patch.object(python_platform.platform,'machine',return_value='x86_64'):
   self.assertEqual(python_platform.desktop_identity()['arch'],'x86_64')
   with self.assertRaises(RuntimeError):python_platform.runtime_target()

if __name__=='__main__':unittest.main()
