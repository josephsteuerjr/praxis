"""Real Linux detached descendants, not mocked group census."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from command_process import CommandProcess


@unittest.skipUnless(sys.platform.startswith('linux'),'Linux subreaper contract')
class DetachedCustody(unittest.TestCase):
    def test_deferred_command_cannot_execute_before_durable_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'effect'
            proc=CommandProcess([sys.executable,'-B','-c',f'from pathlib import Path;Path({str(marker)!r}).write_text("effect")'],defer_start=True)
            try:
                time.sleep(.1);self.assertFalse(marker.exists())
                proc.admit();self.assertEqual(proc.wait(5),0);self.assertTrue(marker.exists())
            finally:proc.stop_tree()

    def test_setsid_descendant_holds_completion_and_stop_reaps_it(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'pid'
            code=("import os,time\nfrom pathlib import Path\npid=os.fork()\n"
                  f"if pid==0:\n os.setsid()\n p=Path({str(marker)!r})\n t=p.with_suffix('.tmp')\n t.write_text(str(os.getpid()))\n t.replace(p)\n time.sleep(60)\n")
            proc=CommandProcess([sys.executable,'-B','-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                end=time.monotonic()+4
                while not marker.exists() and time.monotonic()<end:time.sleep(.02)
                self.assertTrue(marker.exists())
                child=int(marker.read_text());time.sleep(.15)
                self.assertIsNone(proc.poll(),'detached child still owns the command')
                ok,note=proc.stop_tree();self.assertTrue(ok,note)
                self.assertIsNotNone(proc.poll())
                self.assertFalse(Path(f'/proc/{child}').exists(),'even zombie descendants must be reaped')
            finally:
                proc.stop_tree()

    def test_lost_helper_cannot_release_custody_as_terminal(self):
        proc=CommandProcess([sys.executable,'-B','-c','import time;time.sleep(.3)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(.1)
        os.kill(proc.pid,signal.SIGKILL)
        time.sleep(.4)
        self.assertIsNone(proc.poll())
        ok,note=proc.stop_tree();self.assertFalse(ok);self.assertIn('unknown',note)
        # Fixture reconciliation only: command has elapsed, helper is our child.
        proc._proc.wait()
        import shutil
        shutil.rmtree(proc._custody)


if __name__=='__main__':unittest.main()
