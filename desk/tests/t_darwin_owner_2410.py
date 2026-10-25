"""Protocol/fanout tests everywhere; kernel consumers run ONLY on native Darwin."""
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import darwin_process as darwin
from localharness import desktop_supervisor as supervisor


class Protocol(unittest.TestCase):
    def simulated_spawn(self, interrupt):
        stopped = threading.Event()
        frames = []
        process = mock.Mock(returncode=0)
        process.wait.return_value = 0
        messages = iter([{'state': 'ready'}, {'state': 'prepared', 'pid': 99,
                         'birth': 12.0, 'pgid': 99}, {'state': 'started', 'pid': 99}])
        class Child(darwin.DarwinChild):
            def _send(self, value): frames.append(value)
            def _message(self, seconds=0):
                value = next(messages)
                if interrupt and value['state'] == 'prepared': stopped.set()
                return value
        with mock.patch.object(darwin.sys, 'platform', 'darwin'), \
             mock.patch.object(darwin.subprocess, 'Popen', return_value=process):
            if interrupt:
                with self.assertRaisesRegex(RuntimeError, 'Stop before target release'):
                    Child.spawn(['sentinel'], cwd='.', env={}, log='unused', stopped=stopped.is_set)
                process.stdin.close.assert_called_once()
            else:
                child = Child.spawn(['sentinel'], cwd='.', env={}, log='unused', stopped=stopped.is_set)
                self.assertEqual(child.pid, 99)
        return frames

    def test_stop_after_measurement_never_releases_target(self):
        frames = self.simulated_spawn(True)
        self.assertNotIn({'action': 'release-target'}, frames)
        self.assertIn({'action': 'start'}, frames)

    def test_ordinary_prepared_target_gets_explicit_release(self):
        self.assertIn({'action': 'release-target'}, self.simulated_spawn(False))

    def children(self):
        events = []
        class Child:
            def __init__(self, name, fails): self.name,self.fails,self.handle=name,fails,object()
            def request_stop(self): events.append('request-'+self.name)
            def stop(self):
                events.append('wait-'+self.name)
                if self.fails: raise darwin.CustodyFault('known identity unreadable')
                self.handle = None
        return events, Child('uncertain', True), Child('independent', False)

    def test_all_requests_precede_wait_and_fault_retains_handle(self):
        events, bad, good = self.children()
        with self.assertRaises(supervisor.CustodyFault) as caught:
            supervisor.stop_children({'bad':bad,'good':good})
        self.assertEqual(events[:2], ['request-uncertain','request-independent'])
        self.assertIn('wait-independent', events)
        self.assertIsNone(good.handle)
        self.assertEqual(caught.exception.children, (bad,))

    def test_actual_controller_close_preserves_uncertain_helper(self):
        from localharness.python_controller import Controller
        events,bad,good=self.children()
        ctrl=Controller.__new__(Controller)
        ctrl.lock=threading.RLock();ctrl.stop=threading.Event()
        ctrl.helpers=[bad,good];ctrl.thread=None;ctrl.error=None;ctrl.publishers={}
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
            ctrl.close()
        self.assertTrue(ctrl.stop.is_set())
        self.assertEqual(ctrl.helpers,[bad]);self.assertIsNone(good.handle)
        self.assertIn('wait-independent',events)

    def test_background_admission_fault_retained_and_retry_refused_until_cleanup(self):
        from localharness.python_controller import Controller
        events,bad,good=self.children()
        ctrl=Controller.__new__(Controller)
        ctrl.lock=threading.RLock();ctrl.stop=threading.Event();ctrl.root=Path('.')
        ctrl.helpers=[];ctrl.thread=None;ctrl.error=None;ctrl.publishers={}
        fault=supervisor.CustodyFault('admission cleanup unconfirmed');fault.children=(bad,)
        with mock.patch.object(supervisor.JobChild,'spawn',side_effect=fault) as spawn:
            with self.assertRaises(supervisor.CustodyFault):ctrl.background(['x'],Path('unused'))
            self.assertEqual(ctrl.helpers,[bad]);self.assertTrue(bad.custody_uncertain)
            with self.assertRaisesRegex(supervisor.CustodyFault,'Earlier helper'):
                ctrl.background(['x'],Path('unused'))
            self.assertEqual(spawn.call_count,1)
        ctrl.helpers.append(good)
        with self.assertRaisesRegex(RuntimeError,'unconfirmed'):ctrl.close()
        self.assertIn('request-independent',events);self.assertIsNone(good.handle)
        self.assertEqual(ctrl.helpers,[bad])
        bad.fails=False
        ctrl.close()
        self.assertEqual(ctrl.helpers,[])

    def test_real_closed_owner_pipe_still_reads_terminal_receipt(self):
        process=mock.Mock(returncode=0)
        process.stdin=io.BytesIO();process.stdin.close()
        child=darwin.DarwinChild(process)
        # Real closed transport + actual request_stop/stop/poll implementation;
        # only the guardian's incoming terminal frame is modeled on this host.
        terminal={'state':'empty','code':-15,'evidence':{'scope':'fixture'}}
        with mock.patch.object(child,'_message',return_value=terminal) as incoming:
            supervisor.stop_children({'admission-timeout':child})
        self.assertIsNone(child.handle);incoming.assert_called_once()
        process.wait.assert_called_once_with(5)

    def test_log_enospc_does_not_interrupt_cleanup_reporting(self):
        log=mock.Mock();log.open.side_effect=OSError(28,'No space left on device')
        with mock.patch.object(darwin._posix,'_emit') as send:
            darwin.report(log,{'state':'empty','code':0})
        self.assertEqual(send.call_args.args[0]['state'],'empty')
        self.assertIn('log_error',send.call_args.args[0])

    def test_signal_denial_is_not_retried_as_a_different_identity(self):
        import psutil
        census=darwin.Census(50,123.,50,'test-marker')
        process=mock.Mock();process.create_time.return_value=124.
        with mock.patch.object(psutil,'Process',return_value=process):
            census.signal_known(50,signal.SIGTERM)
        process.send_signal.assert_not_called()


def wait_file(path, timeout=12):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if path.is_file(): return path.read_text()
        time.sleep(.03)
    raise AssertionError('fixture did not publish '+str(path))


def dead(pid):
    import psutil
    try:return psutil.Process(pid).status()==psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:return True


@unittest.skipUnless(sys.platform=='darwin','native Darwin kernel consumer; no cross-platform PASS')
class NativeDarwin(unittest.TestCase):
    def setUp(self):
        from localharness.posix_process import tree_hold
        self.hold=tree_hold
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.old_home=os.environ.get('HOME');os.environ['HOME']=str(self.root)
        self.children=[]
    def tearDown(self):
        try:
            for child in self.children:
                if child.handle is not None:child.stop()
        finally:
            if self.old_home is None:os.environ.pop('HOME',None)
            else:os.environ['HOME']=self.old_home
            self.temp.cleanup()
    def spawn(self,code,stopped=lambda:False):
        child=darwin.DarwinChild.spawn([sys.executable,'-I','-c',code],cwd=self.root,
            env=dict(os.environ,HELENE_TREE=str(self.root)),log=self.root/'log',stopped=stopped)
        self.children.append(child);return child
    def test_refusal_executes_no_target_instruction(self):
        with self.assertRaisesRegex(RuntimeError,'Stop'):
            self.spawn("open('bad','w').write('bad')",lambda:True)
        self.assertFalse((self.root/'bad').exists())
    def test_live_child_stop_and_unrelated_process_survives(self):
        unrelated=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)'],start_new_session=True)
        try:
            child=self.spawn("import os,time;open('pid','w').write(str(os.getpid()));time.sleep(30)")
            pid=int(wait_file(self.root/'pid'));child.stop()
            self.assertTrue(dead(pid));self.assertIsNone(child.handle)
            self.assertIsNone(unrelated.poll())
            self.assertIn('residual',child.cleanup_evidence)
        finally:
            unrelated.terminate();unrelated.wait(10)
    def test_leader_exit_retires_marked_detached_grandchild(self):
        code="import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-c',\"import os,time;open('grand','w').write(str(os.getpid()));time.sleep(30)\"],start_new_session=True);time.sleep(.3)"
        with self.hold(self.root) as clean:
            child=self.spawn(code);pid=int(wait_file(self.root/'grand'))
            end=time.monotonic()+15
            while child.poll() is None:
                self.assertLess(time.monotonic(),end);time.sleep(.03)
            self.assertTrue(dead(pid));self.assertEqual(child.cleanup_evidence['known_alive'],[])
            clean()
    def test_released_replacement_can_handoff(self):
        code="import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-c',\"import time;time.sleep(.8);open('handoff','w').write('ok')\"],start_new_session=True);time.sleep(.2)"
        child=self.spawn(code);guardian=child.process;child.release()
        self.assertEqual(wait_file(self.root/'handoff'),'ok');self.assertEqual(guardian.wait(10),0)
        self.assertEqual(child.cleanup_evidence['scope'],'explicit-lifetime-transfer')
    def test_owner_kill_keeps_lease_until_observed_orphan_ends(self):
        desk=Path(__file__).resolve().parents[1]
        payload="import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-c',\"import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);open('orphan','w').write(str(os.getpid()));time.sleep(30)\"],start_new_session=True);time.sleep(30)"
        script=self.root/'owner.py'
        script.write_text('import sys,os,time\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(desk))+')\nfrom localharness.darwin_process import DarwinChild\nfrom localharness.posix_process import tree_hold\nr=Path('+repr(str(self.root))+')\nwith tree_hold(r):\n c=DarwinChild.spawn([sys.executable,"-I","-c",'+repr(payload)+'],cwd=r,env=dict(os.environ,HELENE_TREE=str(r)),log=r/"owner-log",stopped=lambda:False)\n (r/"ready").write_text(str(c.pid))\n time.sleep(30)\n')
        owner=subprocess.Popen([sys.executable,'-I',str(script)],cwd=self.root)
        try:
            wait_file(self.root/'ready');pid=int(wait_file(self.root/'orphan'))
            owner.kill();owner.wait(10);time.sleep(.1)
            with self.assertRaises(BlockingIOError):
                with self.hold(self.root):pass
            end=time.monotonic()+15
            while not dead(pid):
                self.assertLess(time.monotonic(),end);time.sleep(.03)
            while True:
                try:
                    with self.hold(self.root) as clean:clean()
                    break
                except BlockingIOError:
                    self.assertLess(time.monotonic(),end);time.sleep(.03)
        finally:
            if owner.poll() is None:owner.kill();owner.wait(10)


if __name__=='__main__':unittest.main(verbosity=2)
