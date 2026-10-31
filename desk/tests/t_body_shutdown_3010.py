"""Stop settles the body watchdog and its processes before programme retirement."""
import os,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'localharness'))
import body

class Shutdown(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='body-shutdown-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.live=body.Body(self.root,self.root/'data',{})
        self.live._write_state=Mock();self.live.probe=Mock(return_value=False)

    def test_stop_prevents_an_already_awake_watchdog_from_respawning(self):
        entered=threading.Event();release=threading.Event()
        child=Mock(proc=None,retry_at=0,started_at=0)
        def absent():entered.set();release.wait(6);return False
        child.alive.side_effect=absent
        self.live.children=[child]
        watcher=threading.Thread(target=self.live._watch);self.live.thread=watcher;watcher.start()
        errors=[]
        def stop():
            try:self.live.stop()
            except BaseException as exc:errors.append(exc)
        closer=None
        try:
            self.assertTrue(entered.wait(5),'watchdog never reached child observation')
            closer=threading.Thread(target=stop);closer.start()
            self.assertTrue(self.live.stopping.wait(2))
        finally:
            release.set()
            if closer:closer.join(7)
            watcher.join(7)
        self.assertFalse(watcher.is_alive());self.assertFalse(errors,errors)
        child.spawn.assert_not_called()

    def test_failed_child_stop_keeps_custody_and_can_be_retried(self):
        child=Mock();child.kill.side_effect=[RuntimeError('stop unconfirmed'),None]
        self.live.children=[child]
        with self.assertRaisesRegex(RuntimeError,'unconfirmed'):self.live.stop()
        self.live.stop()
        self.assertEqual(child.kill.call_count,2)

    @unittest.skipUnless(os.name=='nt','Windows kernel process custody')
    def test_stop_closes_descendant_file_lock_before_directory_retirement(self):
        marker=self.root/'child-ready';held=self.root/'held.txt'
        grandchild="from pathlib import Path;import sys,time;f=open(sys.argv[1],'wb');Path(sys.argv[2]).write_text('ready');time.sleep(60)"
        parent="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]);time.sleep(60)"
        child=body._Child('fixture',[sys.executable,'-c',parent,grandchild,str(held),str(marker)],
                          dict(os.environ),self.root,self.root/'child.log')
        self.live.children=[child]
        try:
            self.assertTrue(child.spawn(None))
            deadline=time.monotonic()+8
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.03)
            self.assertTrue(marker.exists(),'real descendant did not hold its fixture file')
            with self.assertRaises(PermissionError):held.unlink()
            self.live.stop()
            held.unlink()
            self.assertFalse(held.exists());self.assertFalse(child.alive())
        finally:child.kill()

if __name__=='__main__':unittest.main()
