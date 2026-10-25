"""Package declarations include actual X11 executor command providers."""
from pathlib import Path
import importlib.util,unittest
P=Path(__file__).resolve().parents[1]/'installer/python_posix_packages.py'
s=importlib.util.spec_from_file_location('posix_packages',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Dependencies(unittest.TestCase):
 def test_debian_x11_observations_are_runtime_requirements(self):
  self.assertIn('x11-utils',m.DEBIAN)
 def test_fedora_exact_executables_are_runtime_requirements(self):
  for name in ('xprop','xdpyinfo','xwininfo'):
   self.assertIn('/usr/bin/'+name,m.FEDORA)
if __name__=='__main__':unittest.main(verbosity=2)
