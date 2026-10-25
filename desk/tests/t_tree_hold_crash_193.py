"""Actual Win32 contender and crashed-holder recovery, temporary state only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
HARNESS=Path(__file__).resolve().parents[1]/'localharness'
if sys.platform=='win32':
    sys.path.insert(0,str(HARNESS))
    import desktop_supervisor as ds
HOLDER="""import sys,time;from pathlib import Path
sys.path.insert(0,sys.argv[1])
from desktop_supervisor import _tree_hold
with _tree_hold(Path(sys.argv[2])):
 Path(sys.argv[3]).write_text('held')
 while True:time.sleep(.05)
"""
@unittest.skipUnless(sys.platform=='win32','Win32 lock')
class Hold(unittest.TestCase):
    def test_contender_and_crash_stale_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);tree=root/'data';tree.mkdir();ready=root/'ready'
            pause=tree/'memory/.control/autonomy-paused.json';pause.parent.mkdir(parents=True);pause.write_text('{"fixture":true}')
            child=subprocess.Popen([sys.executable,'-B','-c',HOLDER,str(HARNESS),str(tree),str(ready)],cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                end=time.monotonic()+15
                while not ready.exists():
                    if child.poll() is not None:self.fail(str(child.communicate()))
                    if time.monotonic()>end:self.fail('holder readiness timeout')
                    time.sleep(.02)
                with self.assertRaisesRegex(RuntimeError,'desktop tree owned'):
                    with ds._tree_hold(tree):self.fail('contender admitted')
                child.kill();child.communicate(timeout=10)
                lock=tree/'.desktop-owner.lock';self.assertEqual(lock.read_bytes(),b'active\n')
                with ds._tree_hold(tree) as clean:clean()
                self.assertEqual(lock.read_bytes(),b'clean\n');self.assertEqual(pause.read_text(),'{"fixture":true}')
            finally:
                if child.poll() is None:child.kill()
                child.communicate(timeout=10)
if __name__=='__main__':unittest.main(verbosity=2)
