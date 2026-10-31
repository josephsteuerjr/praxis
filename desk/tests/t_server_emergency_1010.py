"""Real process groups and durable server stop, only inside copied Linux."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK/'server'),str(DESK/'localharness')]
import emergency_stop as stop
import serverboot
import owner_stop
import emergency


@unittest.skipIf(os.name=='nt','Real process lifecycle runs only in copied Linux')
class ServerStop(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tree=Path(self.tmp.name)/'data';self.tree.mkdir()
        self.pending=self.tree/'memory/.control/panic.json';self.pending.parent.mkdir(parents=True)
        stop.prepare(self.tree)
    def test_stop_is_persistent_and_cannot_turn_into_a_resume_or_command(self):
        self.pending.write_text(json.dumps({'via':'telegram','command':'echo never-execute','resume':True}))
        self.assertTrue(stop.consume(self.tree));self.assertFalse(self.pending.exists())
        before=stop.latch(self.tree).read_bytes()
        stop.prepare(self.tree)
        self.pending.write_text('{bad json')
        self.assertTrue(stop.consume(self.tree))
        self.assertEqual(before,stop.latch(self.tree).read_bytes())
        with patch.dict(os.environ,HELENE_SUPERVISOR='serverboot',HELENE_TREE=str(self.tree)):
            self.assertEqual(owner_stop.seed_file(),stop.latch(self.tree))
            self.assertTrue(owner_stop.stopped())
    def test_real_channel_or_agent_request_reaches_supervisor_without_root_write(self):
        import threading
        done=threading.Event()
        def observe():
            while not done.wait(.01):
                if self.pending.exists():stop.consume(self.tree);return
        thread=threading.Thread(target=observe);thread.start()
        try:
            with patch.dict(os.environ,HELENE_SUPERVISOR='serverboot',HELENE_TREE=str(self.tree)):
                reply=emergency.request(self.tree.parent/'helene.json',via='phone')
                self.assertTrue(reply['latched'])
                with self.assertRaises(RuntimeError):owner_stop.resume()
        finally:done.set();thread.join(2)
    def test_child_stop_terminates_its_real_grandchild_group(self):
        pidfile=self.tree/'grandchild.pid'
        script='import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); ready=Path(sys.argv[1]); pending=ready.with_suffix(".publishing"); pending.write_text(str(p.pid)); pending.replace(ready); time.sleep(60)'
        child=serverboot.Child('runner','fixture',[sys.executable,'-c',script,str(pidfile)],dict(os.environ),self.tree,self.tree/'fixture.log')
        child.spawn()
        try:
            # Wait for actual group readiness before measuring Stop. Cold
            # interpreter startup under the all-core gate is not the Stop
            # deadline; the three-second post-Stop contract below is retained.
            deadline=time.monotonic()+10
            while not pidfile.exists() and time.monotonic()<deadline:
                self.assertIsNone(child.proc.poll(),'Fixture parent exited before publishing its grandchild')
                time.sleep(.01)
            self.assertTrue(pidfile.is_file(),'Fixture grandchild did not become ready before the startup deadline')
            pid=int(pidfile.read_text())
            self.assertEqual(os.getpgid(pid),child.proc.pid)
            child.stop()
            deadline=time.monotonic()+3
            def working():
                try:return Path('/proc/'+str(pid)+'/stat').read_text().split()[2] not in {'Z','X'}
                except FileNotFoundError:return False
            while working() and time.monotonic()<deadline:time.sleep(.01)
            self.assertFalse(working())
            self.assertIsNotNone(child.proc.poll())
        finally:child.stop()


if __name__=='__main__':unittest.main()
