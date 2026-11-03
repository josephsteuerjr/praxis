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
    def test_signaled_member_is_confirmed_while_its_original_handle_prevents_pid_reuse(self):
        import desktop_process
        child=desktop_process.JobChild.spawn([sys.executable,'-I','-S','-c','pass'],
            cwd=self.root,env=dict(os.environ),log=self.root/'signaled.log',stopped=lambda:False)
        try:
            self.assertEqual(desktop_process._Wait(int(child.handle),8000),desktop_process.WAIT_OBJECT_0)
            child.job.seal();child.job.pending.add(child.pid);child.job.capture()
            self.assertFalse(child.job.pending)
        finally:child.stop()

    @unittest.skipUnless(os.name=='nt','Windows kernel process custody')
    def test_open_error_87_settles_a_valid_pid_but_zero_never_reaches_the_api(self):
        import ctypes,desktop_process
        job=desktop_process.ChildJob()
        try:
            job.seal();job.pending.add(os.getpid())
            # API fault injection, not a claim that a freed PID stays unused.
            with patch.object(desktop_process,'_OpenProcess',return_value=None) as opened, \
                    patch.object(ctypes,'get_last_error',return_value=87), \
                    patch.object(desktop_process,'_InJob') as membership:
                job.capture()
            opened.assert_called_once_with(0x00100000|0x1000,False,os.getpid())
            membership.assert_not_called();self.assertFalse(job.pending)
            job.pending.add(0)
            with patch.object(desktop_process,'_OpenProcess') as opened:
                with self.assertRaisesRegex(desktop_process.CustodyFault,'Invalid Job process identifier'):job.capture()
            opened.assert_not_called()
        finally:job.pending.clear();job.close()

    @unittest.skipUnless(os.name=='nt','Windows kernel process custody')
    def test_lost_initial_census_stays_fenced_even_when_first_termination_also_fails(self):
        import desktop_process
        witness=object()
        job=Mock(handle=witness,capture_complete=False)
        job.capture.side_effect=OSError('census unavailable')
        job.terminate.side_effect=[OSError('termination failed'),None]
        child=desktop_process.JobChild(witness,0,job)
        with self.assertRaisesRegex(OSError,'termination failed'):child.request_stop()
        self.assertTrue(getattr(child,'_uncaptured_stop',False))
        job.capture.side_effect=None
        with self.assertRaises(desktop_process.CustodyFault):child.request_stop()
        self.assertEqual(job.terminate.call_count,2,'A retained Job may receive another termination request')
        self.assertEqual(job.capture.call_count,1,'A later census cannot erase missing initial custody')
        job.wait_empty.assert_not_called();job.close.assert_not_called()

    @unittest.skipUnless(os.name=='nt','Windows kernel process custody')
    def test_final_sealed_census_is_empty_while_the_terminated_leader_handle_is_retained(self):
        import desktop_process
        child=desktop_process.JobChild.spawn([sys.executable,'-I','-S','-c','import time;time.sleep(30)'],
            cwd=self.root,env=dict(os.environ),log=self.root/'census.log',stopped=lambda:False)
        try:
            child.request_stop();child.job.wait_empty();child.job.capture()
            self.assertFalse(child.job.pending);self.assertFalse(child.job.members)
            self.assertEqual(child.job.active(),0)
        finally:child.stop()

    @unittest.skipUnless(os.name=='nt','Windows kernel process custody')
    def test_unconfirmed_member_wait_keeps_custody_even_with_empty_accounting(self):
        import desktop_process
        held=self.root/'held.txt';marker=self.root/'ready'
        code="from pathlib import Path;import sys,time;f=open(sys.argv[1],'wb');Path(sys.argv[2]).write_text('ready');time.sleep(60)"
        child=body._Child('fixture',[sys.executable,'-c',code,str(held),str(marker)],
                          dict(os.environ),self.root,self.root/'child.log')
        try:
            self.assertTrue(child.spawn(None));custody=child.custody
            deadline=time.monotonic()+8
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.03)
            self.assertTrue(marker.exists())
            real_wait=desktop_process._Wait
            def wait(handle,timeout):
                if int(handle)==int(custody.handle):return real_wait(handle,timeout)
                return desktop_process.WAIT_TIMEOUT
            with patch.object(desktop_process,'_Wait',side_effect=wait),patch.object(custody.job,'active',return_value=0):
                with self.assertRaises(desktop_process.CustodyFault):child.kill()
            self.assertIs(child.custody,custody);self.assertIs(child.proc,custody)
            self.assertIsNotNone(custody.job.handle)
            self.assertTrue(custody.job.members)
        finally:child.kill()
        held.unlink();self.assertFalse(held.exists())

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
