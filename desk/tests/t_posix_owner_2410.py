"""Real Linux subtree consumers, disposable HOME only. Never run on Windows."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wait_file(path, timeout=8):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if path.is_file():return path.read_text()
        time.sleep(.02)
    raise AssertionError('fixture did not publish '+str(path))


def dead(pid):
    import psutil
    try:return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:return True


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux kernel consumer')
class LinuxCustody(unittest.TestCase):
    def setUp(self):
        from localharness.posix_process import PosixChild,tree_hold
        self.Child,self.hold=PosixChild,tree_hold
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.old_home=os.environ.get('HOME');os.environ['HOME']=str(self.root)
    def tearDown(self):
        if self.old_home is None:os.environ.pop('HOME',None)
        else:os.environ['HOME']=self.old_home
        self.temp.cleanup()
    def spawn(self, code, stopped=lambda:False):
        return self.Child.spawn([sys.executable,'-c',code],cwd=self.root,
            env=dict(os.environ,HELENE_TREE=str(self.root)),log=self.root/'log',stopped=stopped)
    def test_import_policy_and_durable_stop(self):
        from localharness import desktop_supervisor, owner_stop
        self.assertTrue(callable(desktop_supervisor.Coordinator))
        owner_stop.latch_client('fixture')
        self.assertTrue(owner_stop.stopped())
        owner_stop.seed_file().unlink()
        self.assertFalse(owner_stop.stopped())
    def test_refused_before_start(self):
        with self.assertRaisesRegex(RuntimeError,'Stop'):
            self.spawn("open('bad','w').write('bad')",lambda:True)
        self.assertFalse((self.root/'bad').exists())
    def test_lease_is_exclusive_not_stale_word(self):
        (self.root/'.desktop-owner.lock').write_text('active\n')
        with self.hold(self.root) as clean:
            with self.assertRaises(BlockingIOError):
                with self.hold(self.root):pass
            clean()
        with self.hold(self.root) as clean:clean()
    def test_leader_exit_retires_detached_grandchild(self):
        code="import os,subprocess,sys,time;subprocess.Popen([sys.executable,'-c',\"import os,time;open('grand','w').write(str(os.getpid()));time.sleep(60)\"],start_new_session=True);time.sleep(.2)"
        with self.hold(self.root) as clean:
            child=self.spawn(code);pid=int(wait_file(self.root/'grand'))
            end=time.monotonic()+10
            while child.poll() is None:
                self.assertLess(time.monotonic(),end);time.sleep(.02)
            self.assertTrue(dead(pid));clean()
    def test_stop_retires_live_descendant(self):
        child=self.spawn("import os,time;open('pid','w').write(str(os.getpid()));time.sleep(60)")
        pid=int(wait_file(self.root/'pid'));child.stop()
        self.assertTrue(dead(pid));self.assertIsNone(child.handle)
    def test_closed_admission_pipe_still_waits_for_real_cleanup(self):
        child=self.spawn("import os,time;open('pid','w').write(str(os.getpid()));time.sleep(30)")
        pid=int(wait_file(self.root/'pid'))
        child.process.stdin.close()  # the failed-admission transport state
        child.stop()
        self.assertTrue(dead(pid));self.assertIsNone(child.handle)

    def test_released_app_can_handoff_detached_updater(self):
        code="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',\"import time;time.sleep(.8);open('handoff','w').write('ok')\"],start_new_session=True);time.sleep(.2)"
        child=self.spawn(code)
        guardian=child.process
        child.release()
        self.assertEqual(wait_file(self.root/'handoff'),'ok')
        self.assertEqual(guardian.wait(8),0)
    def test_owner_kill_retains_lease_until_orphans_dead(self):
        # Same-user detached grandchild ignores TERM, so lease must remain for
        # guardian's escalation interval even after GUI PID/its fd disappear.
        script=self.root/'owner.py'
        desk=Path(__file__).resolve().parents[1]
        script.write_text('import sys,os,time\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(desk))+')\nfrom localharness.posix_process import PosixChild,tree_hold\nr=Path('+repr(str(self.root))+')\nwith tree_hold(r):\n c=PosixChild.spawn([sys.executable,"-c",'+repr("import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',\"import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);open('orphan','w').write(str(os.getpid()));time.sleep(60)\"],start_new_session=True);time.sleep(60)")+'],cwd=r,env=dict(os.environ,HELENE_TREE=str(r)),log=r/"owner-log",stopped=lambda:False)\n (r/"owner-ready").write_text(str(c.pid))\n time.sleep(60)\n')
        owner=subprocess.Popen([sys.executable,str(script)],cwd=self.root)
        try:
            wait_file(self.root/'owner-ready');pid=int(wait_file(self.root/'orphan'))
            owner.kill();owner.wait(5);time.sleep(.15)
            with self.assertRaises(BlockingIOError):
                with self.hold(self.root):pass
            end=time.monotonic()+10
            while not dead(pid):
                self.assertLess(time.monotonic(),end);time.sleep(.03)
            # Guardian closes its last lease description after terminal cleanup.
            while True:
                try:
                    with self.hold(self.root) as clean:clean()
                    break
                except BlockingIOError:
                    self.assertLess(time.monotonic(),end);time.sleep(.03)
        finally:
            if owner.poll() is None:owner.kill();owner.wait()


if __name__=='__main__':unittest.main(verbosity=2)
