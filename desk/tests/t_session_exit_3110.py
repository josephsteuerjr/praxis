"""Tray Exit must settle its private owner even when update cancellation fails."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import session_host as session, python_controller, python_updates
from localharness import desktop_supervisor as supervisor
from localharness import session_exit


class _Owner:
    def __init__(self, root, event):
        self.root = root
        self.selected = 'main'
        self.stop = threading.Event()
        self.exiting = False
        self.starts = 0
        self.lock = threading.RLock()
        self.calls = 0
        self.active = 0
        self.maximum = 0
        self.guard = threading.Lock()

    def _default(self):return 'main'
    @contextmanager
    def selection(self, identity):yield
    def start(self):self.starts += 1
    def close(self):
        with self.guard:
            self.calls += 1
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        try:
            self.stop.set()
            time.sleep(.03)
        finally:
            with self.guard:self.active -= 1


class ExitLatch(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='session-exit-private-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _host(self):
        host = session.Host(self.root, controller_factory=_Owner, control_tree=self.root/'data')
        self.addCleanup(host.server.server_close)
        host.publish = Mock()
        return host

    def _exit(self, host, arguments=None):
        try:return host.dispatch('exit', arguments or {})
        except (OSError, RuntimeError, ValueError) as error:return error

    def test_cancellation_exception_cannot_skip_owner_close_or_exit_latches(self):
        host = self._host()
        observed = []
        def cancel(reason):
            observed.append((host.stop.is_set(), host.owner.stop.is_set(), host.owner.exiting))
            raise RuntimeError('synthetic cancellation bookkeeping failure')
        host.cancel_owned_jobs = Mock(side_effect=cancel)
        with patch.object(session.agents, 'find', return_value=object()):self._exit(host)
        self.assertTrue(host.stop.is_set(), 'Tray Exit must latch host shutdown despite cancellation errors')
        self.assertTrue(host.owner.stop.is_set(), 'Tray Exit must latch the supervisor stop event')
        self.assertTrue(host.owner.exiting)
        self.assertEqual(observed, [(True, True, True)], 'Exit latches must precede cancellation bookkeeping')
        self.assertGreaterEqual(host.owner.calls, 1)

    def test_repeated_concurrent_exit_never_runs_overlapping_owner_teardown(self):
        host = self._host()
        host.cancel_owned_jobs = Mock(return_value=(False, {}))
        with patch.object(session.agents, 'find', return_value=object()):
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda _index:self._exit(host), range(6)))
            self._exit(host)
        self.assertTrue(host.stop.is_set())
        self.assertTrue(host.owner.stop.is_set())
        self.assertGreaterEqual(host.owner.calls, 1)
        self.assertEqual(host.owner.maximum, 1, 'Concurrent Exit must share teardown custody')

    def test_controller_cancellation_exception_still_stops_captured_helpers(self):
        controller = python_controller.Controller(self.root, lambda *args:None)
        class Child:
            handle = object()
        child = Child()
        controller.helpers = [child]
        def settle(children):
            for captured in children.values():captured.handle = None
        with patch.object(python_updates, 'cancel_waiting', side_effect=RuntimeError('synthetic waiting-job error')), \
             patch.object(supervisor, 'stop_children', side_effect=settle):
            try:controller.close()
            except RuntimeError:pass
        self.assertTrue(controller.stop.is_set())
        self.assertIsNone(child.handle, 'Cancellation bookkeeping cannot retain the captured child handle')

    def test_exit_is_installation_level_despite_deleted_agent_and_stale_job(self):
        host = self._host()
        host.cancel_owned_jobs = Mock(return_value=(False, {}))
        with patch.object(session.agents, 'find', side_effect=AssertionError('Exit cannot require an agent')):
            outcome = self._exit(host, {'agent':'deleted-agent', 'session_job':'stale-job'})
        self.assertNotIsInstance(outcome, Exception)
        self.assertTrue(host.stop.is_set())
        self.assertTrue(host.owner.stop.is_set())
        self.assertGreaterEqual(host.owner.calls, 1)

    def test_owner_tray_exit_records_durable_programme_intent(self):
        host = self._host()
        host.cancel_owned_jobs = Mock(return_value=(False, {}))
        self._exit(host)
        self.assertTrue(session_exit.requested(self.root))
        record = json.loads(session_exit.path(self.root).read_text(encoding='utf-8-sig'))
        self.assertEqual(record['program'], str(self.root.resolve()))
        self.assertEqual(record['generation'], host.descriptor['generation'])
        self.assertEqual(record['pid'], host.descriptor['pid'])
        self.assertEqual(record['birth'], host.descriptor['birth'])

    def test_mechanical_handoff_exit_closes_without_recording_owner_intent(self):
        host = self._host()
        host.cancel_owned_jobs = Mock(return_value=(False, {}))
        self._exit(host, {'owner_exit':False})
        self.assertTrue(host.stop.is_set())
        self.assertTrue(host.owner.stop.is_set())
        self.assertGreaterEqual(host.owner.calls, 1)
        self.assertFalse(session_exit.path(self.root).exists())
        self.assertFalse(session_exit.requested(self.root))

    def test_mechanical_handoff_never_erases_a_previous_owner_exit_intent(self):
        host = self._host()
        host.cancel_owned_jobs = Mock(return_value=(False, {}))
        session_exit.record(self.root, host.descriptor)
        before = session_exit.path(self.root).read_bytes()
        self._exit(host, {'owner_exit':False})
        self.assertEqual(session_exit.path(self.root).read_bytes(), before)
        self.assertTrue(session_exit.requested(self.root))

    def test_host_cannot_restart_a_supervisor_after_exit_is_latched(self):
        host = self._host()
        host.cancel_owned_jobs = Mock(return_value=(False, {}))
        with patch.object(session.agents, 'find', return_value=object()):
            self._exit(host)
            try:host.dispatch('start', {})
            except (OSError, RuntimeError, ValueError):pass
        self.assertEqual(host.owner.starts, 0)
        self.assertTrue(host.owner.stop.is_set())
        self.assertTrue(host.owner.exiting)
        self.assertTrue(session_exit.requested(self.root))

    def test_controller_cannot_restart_after_installation_exit_intent(self):
        controller = python_controller.Controller(self.root, lambda *args:None)
        controller.exiting = True
        with patch.object(python_controller.agents, 'read_config', return_value={'mode':'remote'}), \
             patch.object(controller, 'sync_publishers') as publishers:
            with self.assertRaises(RuntimeError):controller.start()
        publishers.assert_not_called()
        self.assertIsNone(controller.thread)

    def test_session_client_mechanical_exit_transmits_no_owner_intent(self):
        client = object.__new__(session.SessionClient)
        client.descriptor = {'pid':42, 'birth':1}
        client._request = Mock(return_value={'stop_requested':True, 'stopped':True})
        client.close = Mock()
        with patch.object(session, 'process_state', return_value='absent'):
            result = client.shutdown(owner_exit=False)
        client._request.assert_called_once_with('exit', {'owner_exit':False}, timeout=45)
        client.close.assert_called_once()
        self.assertTrue(result['session_exited'])


_HOLDER = r'''
import ctypes,json,os,sys,time
from ctypes import wintypes as w
from pathlib import Path
import psutil
held,ready=map(Path,sys.argv[1:3])
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
k.CreateFileW.restype=w.HANDLE
handle=k.CreateFileW(str(held),0xC0000000,0,None,4,0x80,None)
if handle in (None,ctypes.c_void_p(-1).value):raise ctypes.WinError(ctypes.get_last_error())
ready.write_text(json.dumps({'pid':os.getpid(),'birth':psutil.Process().create_time()}))
while True:time.sleep(.1)
'''


_PRIVATE_HOST = r'''
import json,os,sys,threading,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import session_host as session,python_updates,windows_first_start,windows_hooks
from localharness.python_controller import Controller
from localharness import desktop_supervisor as supervisor
from localharness.package_notice import write
import psutil
root,data,evidence,holder=map(Path,sys.argv[2:6]);mode=sys.argv[6]
def forbidden(*args,**kwargs):raise AssertionError('No host OS registrations or inventory authority')
windows_first_start.Backend=forbidden
windows_first_start.restart_holders=forbidden
windows_first_start.terminate=forbidden
windows_hooks.retire=forbidden
psutil.process_iter=forbidden
def owner_stopped():
    if mode=='monitor-error' and (evidence/'raise-monitor-error').is_file():
        raise RuntimeError('synthetic owner-stop monitor failure')
    return False
session.owner_stop.stopped=owner_stopped
def cancel_waiting(owner):
    if mode=='waiting-error':raise RuntimeError('synthetic waiting-job bookkeeping failure')
python_updates.cancel_waiting=cancel_waiting
class PrivateController(Controller):
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear()
        self.captured=[]
        def own():
            children={}
            try:
                while not self.stop.is_set():
                    for role in ('body','bridge'):
                        child=children.get(role)
                        if child is not None and child.handle is not None and child.poll() is None:continue
                        if self.stop.is_set():break
                        try:
                            child=supervisor.JobChild.spawn(
                                [sys.executable,'-B','-s',str(holder),str(root/(role+'-held.lock')),
                                 str(evidence/('ready-'+role+'.json'))],
                                cwd=root,env=dict(os.environ),log=evidence/(role+'.log'),
                                stopped=self.stop.is_set)
                        except InterruptedError:
                            if self.stop.is_set():break
                            raise
                        children[role]=child
                        self.captured.append({'role':role,'pid':child.pid,
                                              'birth':psutil.Process(child.pid).create_time()})
                        write(evidence/'captured.json',{'children':self.captured})
                    self.stop.wait(.02)
            finally:
                supervisor.stop_children(children)
                write(evidence/'settled.json',{'children':self.captured,
                    'handles_closed':all(child.handle is None for child in children.values()),
                    'jobs_closed':all(not child.job.handle for child in children.values())})
        self.thread=threading.Thread(target=own,name='private-respawning-supervisor')
        self.thread.start()
    def status(self):return {'agents':{},'fixture':True}
host=session.Host(root,controller_factory=PrivateController,control_tree=data)
def cancel_jobs(reason):
    if mode=='cancel-error':raise RuntimeError('synthetic owned-job bookkeeping failure')
    return False,{}
host.cancel_owned_jobs=cancel_jobs
try:host.run()
finally:
    write(evidence/'host-final.json',{'host_stop':host.stop.is_set(),
        'owner_stop':host.owner.stop.is_set(),
        'supervisor_alive':bool(host.owner.thread and host.owner.thread.is_alive())})
'''


@unittest.skipUnless(os.name=='nt', 'Windows private process and exclusive-handle custody')
class PrivateProcessExit(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='session-exit-process-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home/'private programme'
        self.data = self.home/'private control data'
        self.evidence = self.home/'evidence'
        for path in (self.root, self.data, self.evidence):path.mkdir()
        self._write(self.root/'python-app.json', {'version':'private-fixture'})
        self._write(self.root/'helene.json', {
            'tree':str(self.data), 'code':'tree', 'mode':'local', 'setup_complete':False})
        self.holder = self.home/'private_holder.py'
        self.holder.write_text(_HOLDER, encoding='utf-8')
        self.process = None
        self.descriptor = None
        self.addCleanup(self._cleanup_processes)

    def _write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def _read(self, path):
        try:return json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):return {}

    def _wait(self, condition, message, timeout=10):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            if condition():return
            if self.process and self.process.poll() is not None:
                self.fail(message+'\n'+self._log())
            time.sleep(.025)
        self.fail(message+'\n'+self._log())

    def _log(self):
        path = self.evidence/'host.log'
        return path.read_text(encoding='utf-8', errors='replace')[-7000:] if path.exists() else ''

    def _start(self, mode):
        with (self.evidence/'host.log').open('wb') as output:
            self.process = subprocess.Popen(
                [sys.executable, '-B', '-s', '-c', _PRIVATE_HOST,
                 str(Path(__file__).resolve().parents[1]), str(self.root), str(self.data),
                 str(self.evidence), str(self.holder), mode],
                cwd=self.home, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        descriptor_path = self.data/'memory/.control/desktop-session.json'
        self._wait(lambda:self._read(descriptor_path).get('pid')==self.process.pid
                   and self._read(descriptor_path).get('state')=='running',
                   'Private real Host did not publish its own running PID')
        self.descriptor = self._read(descriptor_path)
        import psutil
        self.assertEqual(psutil.Process(self.process.pid).create_time(), self.descriptor['birth'])
        session._rpc(self.descriptor, 'status', timeout=4)
        for role in ('body', 'bridge'):
            self._wait(lambda role=role:self._read(self.evidence/('ready-'+role+'.json')).get('pid'),
                       'Private '+role+' did not acquire its exclusive handle')
        captured = self._read(self.evidence/'captured.json')['children']
        self.assertEqual({row['role'] for row in captured}, {'body', 'bridge'})
        for role in ('body', 'bridge'):
            with self.assertRaises(PermissionError):(self.root/(role+'-held.lock')).unlink()

    def _respawn_one_captured_child(self):
        import psutil
        original = self._read(self.evidence/'ready-body.json')
        child = psutil.Process(original['pid'])
        self.assertEqual(child.create_time(), original['birth'])
        child.terminate()
        self._wait(lambda:self._read(self.evidence/'ready-body.json').get('pid') not in (None,original['pid']),
                   'Private watchdog did not reproduce the child-respawn path')
        self.assertGreater(len(self._read(self.evidence/'captured.json')['children']), 2)

    def _request_tray_exit(self):
        # This is the tray's authenticated Exit operation, not observer detach,
        # closing a WebView, or terminating a process from the test.
        try:return session._rpc(self.descriptor, 'exit', timeout=6)
        except (OSError, RuntimeError, ValueError) as error:
            # urllib's HTTPError owns its response stream even on a failed
            # request; disposing it grants no process-stop success.
            if callable(getattr(error, 'close', None)):error.close()
            return str(error)

    def _assert_fully_exited(self):
        try:self.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            raise AssertionError('Tray Exit left the private Python owner alive\n'+self._log()) from None
        self.assertIn(session.process_state(self.descriptor), ('absent', 'changed'))
        settled = self._read(self.evidence/'settled.json')
        self.assertTrue(settled.get('handles_closed'), self._log())
        self.assertTrue(settled.get('jobs_closed'), self._log())
        captured = self._read(self.evidence/'captured.json')['children']
        self.assertEqual(settled['children'], captured)
        import psutil
        for row in captured:
            try:remaining = psutil.Process(row['pid'])
            except psutil.NoSuchProcess:continue
            self.assertNotEqual(remaining.create_time(), row['birth'], 'Captured private child still lives')
        final = self._read(self.evidence/'host-final.json')
        self.assertTrue(final.get('owner_stop'), final)
        self.assertFalse(final.get('supervisor_alive'), final)
        from localharness.phone_tunnel.platform_support import acquire_file_lock, release_file_lock
        lease = acquire_file_lock(self.data/'memory/.control/desktop-session.lock', timeout_sec=.5)
        release_file_lock(lease)
        moved = self.home/'released programme directory'
        self.root.rename(moved)
        for role in ('body', 'bridge'):(moved/(role+'-held.lock')).unlink()
        self.assertFalse(self.root.exists())
        captured_bytes = (self.evidence/'captured.json').read_bytes()
        time.sleep(.15)
        self.assertIsNotNone(self.process.poll())
        self.assertEqual((self.evidence/'captured.json').read_bytes(), captured_bytes)
        print('PRIVATE_EXIT_PROOF '+json.dumps({
            'case': self.id().split('.')[-1],
            'host': {key:self.descriptor[key] for key in ('pid', 'birth', 'generation')},
            'host_state': session.process_state(self.descriptor),
            'children': captured,
            'captured_same_birth_children_absent': True,
            'process_handles_closed': settled['handles_closed'],
            'job_handles_closed': settled['jobs_closed'],
            'session_lease_reacquired': True,
            'exclusive_files_released': ['body-held.lock', 'bridge-held.lock'],
            'programme_directory_renamed': True,
            'late_capture_writes': False,
            'supervisor_alive': final['supervisor_alive'],
        }, sort_keys=True), flush=True)

    def _cleanup_processes(self):
        if self.process and self.process.poll() is None:
            # Popen holds the exact private process handle, never an inventory
            # match or an owner application's recycled PID.
            self.process.terminate()
            try:self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        import psutil
        for row in self._read(self.evidence/'captured.json').get('children', []):
            try:
                child = psutil.Process(row['pid'])
                if child.create_time()!=row['birth']:continue
                child.terminate()
                child.wait(timeout=5)
            except (psutil.NoSuchProcess, psutil.AccessDenied):pass

    def test_tray_exit_cancellation_error_settles_respawned_children_handles_and_directory(self):
        self._start('cancel-error')
        self._respawn_one_captured_child()
        self._request_tray_exit()
        self._assert_fully_exited()

    def test_concurrent_tray_exit_waiting_error_still_releases_all_captured_handles(self):
        self._start('waiting-error')
        self._respawn_one_captured_child()
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _index:self._request_tray_exit(), range(6)))
        self._assert_fully_exited()

    def test_run_loop_exception_always_tears_down_its_respawning_supervisor(self):
        self._start('monitor-error')
        (self.evidence/'raise-monitor-error').write_text('raise only in this private Host')
        self._assert_fully_exited()


if __name__=='__main__':unittest.main()
