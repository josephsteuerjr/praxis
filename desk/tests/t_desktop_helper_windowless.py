"""Supported helper probes never create a visible console on Windows."""
import sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_desktop_commands as commands,update_process
class Helpers(unittest.TestCase):
 def test_tailscale_probe_uses_existing_background_flags(self):
  with tempfile.TemporaryDirectory() as d:
   config=Path(d)/'config.json';config.write_text('{}',encoding='utf8')
   owner=SimpleNamespace(current=lambda:SimpleNamespace(config=config))
   with patch.object(Path,'is_file',return_value=True),patch.object(commands.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'100.64.0.1\n')) as run:
    self.assertEqual(commands.invoke(owner,'tailscale_ip',{}),'100.64.0.1')
   self.assertEqual(run.call_args.kwargs['creationflags'],update_process.background_flags())
   self.assertEqual(run.call_args.kwargs['timeout'],5)
if __name__=='__main__':unittest.main()
