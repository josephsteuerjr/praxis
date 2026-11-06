"""Prepared archive launch uses real authenticated ownership, without a model."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_app
import windows_start
from localharness import clean_startup, clean_update, session_host, owner_stop
from localharness import migration_lifecycle, python_source_updater, source_merge, portable_profile
from localharness.package_notice import read, write


HOST_SCRIPT = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from localharness import session_host as session
from localharness.python_controller import Controller
from localharness.package_notice import write
session.owner_stop.stopped=lambda:False
class NoModel(Controller):
    def start(self): pass
    def close(self): write(self.root/'fixture-closed.json', {'closed':True})
    def status(self): return {'agents':{},'fixture':True}
    def readiness(self, **kwargs): return {'fixture':True}
    def channel(self): return {}
session.Host(Path(sys.argv[2]), controller_factory=NoModel).run()
'''


class PreparedStartup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='helene-clean-startup-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.old, self.new, self.data = (self.base/name for name in ('old', 'new', 'data'))
        for root in (self.old, self.new):
            (root/'app/static').mkdir(parents=True)
            (root/'app/static/index.html').write_text('<html>fixture</html>', encoding='utf8')
            (root/'app/python_app.py').write_text('# fixture', encoding='utf8')
            (root/'tree').mkdir()
            write(root/'python-app.json', {'version':'2.5.1'})
        (self.data/'memory').mkdir(parents=True)
        (self.data/'memory/history').write_bytes(b'owner-history')
        write(self.old/'helene.json', {'tree':str(self.data), 'code':'tree', 'setup_complete':True,
              'autonomy':{'paused':True}, 'model':{'name':'fixture-only'}})
        self.processes = []
        self.addCleanup(self.stop_processes)
        self.enter(patch.dict(os.environ))
        self.enter(patch('localharness.bootstrap_ready.signal'))
        self.enter(patch('localharness.bootstrap_ready.attach'))
        self.enter(patch.object(owner_stop, 'stopped', return_value=False))
        self.enter(patch.object(portable_profile, 'default_data', return_value=self.base/'permanent/data'))
        self.enter(patch.object(source_merge, 'inventory', side_effect=AssertionError('No programme inventory')))
        self.value = clean_update.prepare(self.old, self.new)
        self.operation = self.value['note']['id']

    def enter(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def stop_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
            if process.stderr: process.stderr.close()

    def start_host(self, root):
        process = subprocess.Popen([sys.executable, '-B', '-c', HOST_SCRIPT,
            str(Path(__file__).resolve().parents[1]), str(root)], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.processes.append(process)
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            value = read(session_host.control_path(root))
            if value.get('pid') == process.pid and value.get('state') == 'running':
                session_host._rpc(value, 'status')
                return process, value
            if process.poll() is not None:
                self.fail(process.stderr.read().decode('utf8', 'replace'))
            time.sleep(.02)
        self.fail('Controlled session did not publish its authenticated endpoint')

    def spawn(self, argv, **kwargs):
        process, _descriptor = self.start_host(Path(argv[-1]))
        fixture = types.SimpleNamespace(handle=object(), pid=process.pid, poll=process.poll)
        fixture.release = lambda: setattr(fixture, 'handle', None)
        fixture.stop = lambda: process.terminate()
        return fixture

    @contextmanager
    def window_boundary(self):
        class Window:
            def __init__(self, *args):
                self.origin = 'http://127.0.0.1:39401'
                self.event = Mock()
                self.relaunch = False
                self.stopping = threading.Event(); self.stopping.set()
                self.host = None
            def serve(self): pass
            def publish_control(self): pass
            def close(self):
                if self.host: self.host.close()
        from localharness.desktop_supervisor import JobChild
        with ExitStack() as context:
            context.enter_context(patch.object(python_app, 'Application', Window))
            context.enter_context(patch.object(JobChild, 'spawn', side_effect=self.spawn))
            context.enter_context(patch('webbrowser.open'))
            # Skip unrelated host desktop integration. Ownership transport,
            # preparation, launch fences, profile binding and entry are real.
            context.enter_context(patch('localharness.linux_desktop.configure'))
            yield

    def launch(self, *, windows=False):
        with self.window_boundary():
            if windows:
                def ordinary(root, arguments):
                    return python_app._main(['--root', str(root), *arguments])
                with patch.object(windows_start, 'ordinary', side_effect=ordinary) as normal:
                    result = windows_start.main(['--root', str(self.new), '--browser'])
                normal.assert_called_once()
                return result
            return python_app._main(['--root', str(self.new), '--browser'])

    def test_windows_ready_entry_exits_real_old_owner_before_normal_attach(self):
        process, previous = self.start_host(self.old)
        self.assertEqual(self.launch(windows=True), 0)
        process.wait(timeout=8)
        current = read(session_host.control_path(self.new))
        self.assertEqual(current['program'], str(self.new))
        self.assertNotEqual(current['generation'], previous['generation'])
        self.assertTrue(read(self.old/'fixture-closed.json')['closed'])
        self.assertFalse((self.old/'.helene/exit-request.json').exists())
        self.assertEqual((self.data/'memory/history').read_bytes(), b'owner-history')
        note = read(self.new/'python-migration.json')
        self.assertEqual(note['id'], self.operation)
        self.assertTrue(note['app_retirement'])
        self.assertNotIn('startup_proof', note)  # Window fixture never claims rendered UI.
        self.assertTrue(self.old.is_dir())
        self.assertTrue(read(self.new/'helene.json')['autonomy']['paused'])

    def test_plain_python_prepared_entry_runs_same_handoff_and_reattaches_once(self):
        self.start_host(self.old)
        self.assertEqual(self.launch(), 0)
        first = read(session_host.control_path(self.new))
        self.assertEqual(self.launch(), 0)
        self.assertEqual(read(session_host.control_path(self.new))['generation'], first['generation'])
        self.assertEqual(len(self.processes), 2)

    def test_generic_attach_still_rejects_unadmitted_foreign_owner(self):
        process, _ = self.start_host(self.old)
        with self.assertRaisesRegex(RuntimeError, 'another program folder'):
            session_host.attach(self.new, Mock())
        self.assertIsNone(process.poll())
        self.assertFalse(read(self.new/'python-migration.json').get('app_retirement'))

    def test_stop_defers_normal_entry_without_stopping_or_startup_error(self):
        process, _ = self.start_host(self.old)
        with patch.object(owner_stop, 'stopped', return_value=True):
            self.assertEqual(self.launch(), 0)
        self.assertIsNone(process.poll())
        self.assertEqual(len(self.processes), 1)

    def test_busy_durable_update_defers_without_cancelling_job(self):
        from localharness import desktop_update_request as jobs
        process, _ = self.start_host(self.old)
        value = {'id':'a'*32, 'state':'running', 'program':str(self.old), 'tree':str(self.data)}
        jobs._save(self.data, value)
        before = jobs.path(self.data).read_bytes()
        self.assertEqual(self.launch(), 0)
        self.assertIsNone(process.poll())
        self.assertEqual(jobs.path(self.data).read_bytes(), before)

    def test_replaced_old_directory_is_refused_before_owner_rpc(self):
        self.old.rename(self.base/'displaced')
        self.old.mkdir()
        with patch.object(session_host, '_rpc') as rpc:
            with self.assertRaisesRegex(ValueError, 'replaced'):
                with clean_startup.handoff(self.new): pass
        rpc.assert_not_called()

    def test_changed_prepared_note_is_refused_before_owner_rpc(self):
        note = read(self.new/'python-migration.json'); note['id'] = 'b'*32
        write(self.new/'python-migration.json', note)
        with patch.object(session_host, '_rpc') as rpc:
            with self.assertRaisesRegex(ValueError, 'differs'):
                with clean_startup.handoff(self.new): pass
        rpc.assert_not_called()

    def test_unrelated_owner_is_not_stopped(self):
        other = self.base/'unrelated'; other.mkdir()
        write(other/'helene.json', read(self.old/'helene.json'))
        process, _ = self.start_host(other)
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            with clean_startup.handoff(self.new): pass
        self.assertIsNone(process.poll())

    def test_unknown_owner_is_not_stopped_or_relocated(self):
        write(session_host.control_path(self.old), {'format':session_host.FORMAT,
              'program':str(self.old), 'tree':str(self.data), 'pid':'unknown'})
        with patch.object(clean_update, 'relocate') as relocate, patch.object(session_host, '_rpc') as rpc:
            with self.assertRaisesRegex(RuntimeError, 'unknown'):
                with clean_startup.handoff(self.new): pass
        relocate.assert_not_called(); rpc.assert_not_called()

    def test_old_generation_change_after_status_is_not_exited(self):
        _process, descriptor = self.start_host(self.old)
        def readers(root):
            write(session_host.control_path(root), {**descriptor, 'generation':'replacement'})
        with patch.object(session_host, 'retire_observers', side_effect=readers), \
                patch.object(session_host, '_rpc', wraps=session_host._rpc) as rpc:
            with self.assertRaisesRegex(ValueError, 'generation changed'):
                with clean_startup.handoff(self.new): pass
        self.assertEqual([call.args[1] for call in rpc.call_args_list], ['status'])

    def test_no_reply_exit_is_observed_without_replay(self):
        process, _descriptor = self.start_host(self.old)
        original = session_host._rpc
        def lost(value, action, *args, **kwargs):
            result = original(value, action, *args, **kwargs)
            if action == 'exit': raise OSError('controlled lost reply')
            return result
        with patch.object(session_host, '_rpc', side_effect=lost) as rpc:
            with clean_startup.handoff(self.new): pass
        process.wait(timeout=8)
        self.assertEqual([call.args[1] for call in rpc.call_args_list].count('exit'), 1)

    def test_live_data_lease_blocks_relocation_after_graceful_exit(self):
        self.start_host(self.old)
        with patch.object(migration_lifecycle, 'owner_lease_available', return_value=False), \
                patch.object(clean_update, 'relocate') as relocate:
            with self.assertRaisesRegex(RuntimeError, 'writer'):
                with clean_startup.handoff(self.new, timeout=3): pass
        relocate.assert_not_called()

    def test_existing_installer_lease_does_not_deadlock_its_bound_window(self):
        clean_update.relocate(self.old, self.new, self.value)
        script = self.base/'controlled-installer.py'
        script.write_text("""
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import clean_update
from localharness.package_notice import read,write
from localharness.python_source_updater import prepared_install_path
from python_migrate import installation_lock
old,new=Path(sys.argv[2]),Path(sys.argv[3])
with installation_lock(new):
    value=read(prepared_install_path(new))
    value['note']['startup_helper']=clean_update.startup_helper(old,new,value['note']['id'])
    write(prepared_install_path(new),value)
    write(new/'python-migration.json',value['note'])
    write(new/'fixture-helper-ready.json',{'ready':True})
    time.sleep(60)
""", encoding='utf8')
        process = subprocess.Popen([sys.executable, '-B', str(script),
            str(Path(__file__).resolve().parents[1]), str(self.old), str(self.new)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.processes.append(process)
        deadline = time.monotonic()+30
        while not (self.new/'fixture-helper-ready.json').is_file():
            if process.poll() is not None: self.fail(process.stderr.read().decode('utf8','replace'))
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.03)
        self.assertEqual(self.launch(), 0)
        self.assertIsNone(process.poll())
        self.assertFalse(read(self.new/'python-migration.json').get('app_retirement'))
        self.assertEqual(read(session_host.control_path(self.new))['program'], str(self.new))

    def test_bare_installation_lease_does_not_authorize_a_bound_window(self):
        from python_migrate import installation_lock
        clean_update.relocate(self.old, self.new, self.value)
        with installation_lock(self.new), patch.object(clean_update, 'relocate') as relocate:
            with self.assertRaisesRegex(RuntimeError, 'custody is unconfirmed'):
                with clean_startup.handoff(self.new): pass
        relocate.assert_not_called()

    def test_stale_helper_birth_does_not_authorize_a_bound_window(self):
        from python_migrate import installation_lock
        value = read(python_source_updater.prepared_install_path(self.new))
        value['note']['startup_helper'] = clean_update.startup_helper(self.old, self.new, self.operation)
        value['note']['startup_helper']['birth'] -= 1
        write(python_source_updater.prepared_install_path(self.new), value)
        write(self.new/'python-migration.json', value['note'])
        with installation_lock(self.new):
            with self.assertRaisesRegex(RuntimeError, 'custody is unconfirmed'):
                with clean_startup.handoff(self.new): pass

    def test_two_candidates_share_the_real_data_launch_fence(self):
        from localharness import config_transaction
        self.start_host(self.old)
        other = self.base/'other-incoming'
        (other/'tree').mkdir(parents=True)
        clean_update.prepare(self.old, other)
        outcomes = []
        fenced = threading.Event()
        failures = []
        original = config_transaction.transaction
        @contextmanager
        def observed(path, **kwargs):
            with original(path, **kwargs):
                if threading.current_thread().name=='first-candidate': fenced.set()
                yield
        def first():
            try:
                with clean_startup.handoff(self.new):
                    write(session_host.control_path(self.new), {'format':session_host.FORMAT,
                        'tree':str(self.data), 'program':str(self.new), 'pid':os.getpid(),
                        'birth':__import__('psutil').Process().create_time(), 'generation':'new-owner'})
                    time.sleep(.1)
                outcomes.append('first')
            except BaseException as exc: failures.append(exc)
        def second():
            try:
                if not fenced.wait(180): raise RuntimeError('First candidate never acquired its real launch fence')
                with clean_startup.handoff(other): outcomes.append('wrongly-admitted')
            except ValueError: outcomes.append('foreign-refused')
            except BaseException as exc: failures.append(exc)
        threads = [threading.Thread(target=first,name='first-candidate'), threading.Thread(target=second)]
        with patch.object(config_transaction,'transaction',side_effect=observed):
            try:
                for thread in threads: thread.start()
                for thread in threads: thread.join(240)
            finally:
                # Never remove a fixture tree while its real lock/owner work
                # is live. The product wait itself remains bounded.
                for thread in threads:
                    if thread.ident is not None: thread.join()
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures,[])
        self.assertCountEqual(outcomes, ['first', 'foreign-refused'])

    def test_live_old_settings_refresh_retains_incoming_preimages_and_selected_paths(self):
        self.start_host(self.old)
        incoming = read(self.new/'helene.json')
        incoming['model']['name'] = 'incoming-edit-retained'
        write(self.new/'helene.json', incoming)
        preimage = (self.new/'helene.json').read_bytes()
        latest = read(self.old/'helene.json')
        latest['model']['name'] = 'latest-owner-selection'
        latest['autonomy']['paused'] = True
        write(self.old/'helene.json', latest)
        self.assertEqual(self.launch(), 0)
        selected = read(self.new/'helene.json')
        self.assertEqual(selected['model']['name'], 'latest-owner-selection')
        self.assertTrue(selected['autonomy']['paused'])
        self.assertEqual((self.new/'.helene/clean-settings'/self.operation/'incoming/helene.json').read_bytes(), preimage)
        current = read(python_source_updater.prepared_install_path(self.new))
        self.assertEqual(current['note']['id'], self.operation)
        self.assertEqual(current['note']['data_paths'], self.value['note']['data_paths'])
        self.assertEqual(current['moves'], self.value['moves'])

    def test_first_start_app_retirement_marker_in_both_records_is_compatible(self):
        value = read(python_source_updater.prepared_install_path(self.new))
        value['note']['app_retirement'] = True
        write(python_source_updater.prepared_install_path(self.new), value)
        write(self.new/'python-migration.json', value['note'])
        with clean_startup.handoff(self.new): pass
        self.assertTrue(read(self.new/'python-migration.json')['app_retirement'])

    def test_normal_entry_refresh_replaces_hidden_settings_and_retains_exact_preimages(self):
        from localharness.program_state import hidden
        names = ('agent-default.json', 'window-preferences.json', 'owner-stop.json')
        incoming = b'{ "fixture": "incoming", "stopped": false }\n'
        latest = b'{ "fixture": "latest", "stopped": false }\n'
        for name in names:
            (self.old/name).write_bytes(latest)
            (self.new/name).write_bytes(incoming)
            hidden(self.new/name)
        self.start_host(self.old)
        self.assertEqual(self.launch(windows=os.name == 'nt'), 0)
        retained = self.new/'.helene/clean-settings'/self.operation/'incoming'
        for name in names:
            self.assertEqual((self.new/name).read_bytes(), latest)
            self.assertEqual((retained/name).read_bytes(), incoming)
            if os.name == 'nt':
                self.assertTrue((self.new/name).stat().st_file_attributes & 2)

    def test_changed_old_profile_is_refused_before_exit_or_relocation(self):
        self.start_host(self.old)
        changed = read(self.old/'helene.json')
        changed['tree'] = str(self.base/'not-admitted-data')
        write(self.old/'helene.json', changed)
        with patch.object(session_host, '_rpc') as rpc, patch.object(clean_update, 'relocate') as relocate:
            with self.assertRaisesRegex(ValueError, 'profile changed'):
                with clean_startup.handoff(self.new): pass
        rpc.assert_not_called(); relocate.assert_not_called()

    def test_partial_relocation_reuses_committed_settings_without_rediscovery(self):
        internal = self.old/'profile'
        (internal/'memory').mkdir(parents=True)
        (internal/'memory/history').write_bytes(b'internal-history')
        write(self.old/'helene.json', {'tree':str(internal), 'code':'tree', 'model':{'name':'authoritative'}})
        incoming = self.base/'internal-candidate'
        (incoming/'tree').mkdir(parents=True)
        prepared = clean_update.prepare(self.old, incoming)
        original = __import__('shutil').move
        def interrupted(source, target):
            original(source, target)
            raise OSError('controlled interruption after one completed data rename')
        with patch('localharness.clean_update.shutil.move', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'controlled interruption'):
                with clean_startup.handoff(incoming): pass
        self.assertFalse(internal.exists())
        with patch.object(clean_update, 'prepare', side_effect=AssertionError('Committed settings must not be rediscovered')):
            with clean_startup.handoff(incoming): pass
        final = read(incoming/'python-migration.json')
        self.assertEqual(final['id'], prepared['note']['id'])
        self.assertEqual((Path(final['data_paths'][0])/'memory/history').read_bytes(), b'internal-history')
        self.assertEqual(read(incoming/'helene.json')['model']['name'], 'authoritative')

    def test_interrupted_atomic_settings_refresh_keeps_primary_config_and_can_retry(self):
        latest = read(self.old/'helene.json'); latest['model']['name'] = 'latest-before-failure'
        write(self.old/'helene.json', latest)
        preimage = (self.new/'helene.json').read_bytes()
        original = clean_update.write
        def interrupted(path, value):
            if Path(path) == self.new/'helene.json': raise OSError('controlled config publication failure')
            return original(path, value)
        with patch.object(clean_update, 'write', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                with clean_startup.handoff(self.new): pass
        self.assertEqual((self.new/'helene.json').read_bytes(), preimage)
        with clean_startup.handoff(self.new): pass
        self.assertEqual(read(self.new/'helene.json')['model']['name'], 'latest-before-failure')
        self.assertEqual((self.new/'.helene/clean-settings'/self.operation/'incoming/helene.json').read_bytes(), preimage)

    def test_interrupted_preimage_publication_never_overwrites_incoming_settings(self):
        from localharness import private_files
        preimage = (self.new/'helene.json').read_bytes()
        with patch.object(private_files, 'write_private', side_effect=OSError('controlled preimage failure')):
            with self.assertRaisesRegex(OSError, 'preimage failure'):
                with clean_startup.handoff(self.new): pass
        self.assertEqual((self.new/'helene.json').read_bytes(), preimage)
        target = self.new/'.helene/clean-settings'/self.operation/'incoming/helene.json'
        self.assertFalse(target.exists())
        with clean_startup.handoff(self.new): pass
        self.assertEqual(target.read_bytes(), preimage)

    def test_model_store_admitted_inside_old_moves_once_during_refreshed_startup(self):
        from localharness import component_store
        payload = self.base/'controlled-model'
        payload.mkdir(); (payload/'weights').write_bytes(b'synthetic-model-bytes')
        reference, _copied = component_store.import_directory(payload, program=self.old, store=self.old/'model-store')
        write(self.old/component_store.PINS, {'format':component_store.FORMAT, 'pins':{'voice':reference}})
        incoming = self.base/'model-candidate'; (incoming/'tree').mkdir(parents=True)
        prepared = clean_update.prepare(self.old, incoming)
        with patch.object(component_store, '_inventory', side_effect=AssertionError('No model scan or copy')):
            with clean_startup.handoff(incoming): pass
        current = component_store.export_references(incoming)['pins']['voice']
        self.assertEqual(current['identity'], reference['identity'])
        self.assertNotEqual(current['store'], reference['store'])
        self.assertFalse(Path(reference['store']).exists())
        self.assertEqual(component_store.path(incoming, 'voice').joinpath('weights').read_bytes(), b'synthetic-model-bytes')
        self.assertEqual(read(incoming/'python-migration.json')['id'], prepared['note']['id'])

    def test_actual_mechanical_producer_binds_live_entry_before_child_launch(self):
        args = types.SimpleNamespace(old=self.old, new=self.new, browser=False, job=None)
        captured = []
        def spawn(*arguments, **kwargs):
            note = read(self.new/'python-migration.json')
            helper = note['startup_helper']; captured.append(helper)
            self.assertEqual(helper['pid'], os.getpid())
            self.assertEqual(helper['operation'], self.operation)
            self.assertEqual(helper, read(python_source_updater.prepared_install_path(self.new))['note']['startup_helper'])
            write(self.new/'python-migration.json', dict(note, state='new_app_started', startup_proof={'fixture':True}))
            return types.SimpleNamespace(poll=lambda:None)
        with patch.object(migration_lifecycle, 'stop'), patch.object(migration_lifecycle, 'quiescent'), \
                patch.object(clean_update.subprocess, 'Popen', side_effect=spawn), \
                patch.object(clean_update, 'finish', side_effect=lambda old,new,note,**kwargs:dict(note,state='completed')):
            self.assertEqual(clean_update.run(args,self.value,lambda *a,**k:None,lambda:False),0)
        self.assertEqual(len(captured),1)

    def test_panic_and_automatic_stop_never_enter_prepared_handoff(self):
        with patch.object(clean_startup, 'pending', side_effect=AssertionError('No startup under Stop')), \
                patch.object(owner_stop, 'latch_client') as latch:
            self.assertEqual(python_app._main(['--root',str(self.new),'--panic']),0)
            latch.assert_called_once()
            with patch.object(owner_stop,'stopped',return_value=True):
                self.assertEqual(python_app._main(['--root',str(self.new),'--launch-intent','automatic']),0)

    def test_explicit_old_exit_blocks_automatic_handoff_without_clearing_marker(self):
        from localharness import session_exit
        session_exit.record(self.old, {'program':str(self.old),'tree':str(self.data)})
        marker = session_exit.path(self.old).read_bytes()
        with self.assertRaises(clean_startup.Deferred):
            with clean_startup.handoff(self.new, automatic=True): pass
        self.assertEqual(session_exit.path(self.old).read_bytes(), marker)
        with clean_startup.handoff(self.new): pass
        self.assertEqual(session_exit.path(self.old).read_bytes(), marker)

    def test_distinct_incoming_data_unknown_or_foreign_is_refused_before_effects(self):
        internal = self.old/'other-profile'; internal.mkdir()
        write(self.old/'helene.json', {'tree':str(internal),'code':'tree'})
        incoming = self.base/'different-data-candidate'; (incoming/'tree').mkdir(parents=True)
        value = clean_update.prepare(self.old, incoming)
        target = session_host.control_path(incoming)
        import psutil
        for descriptor in ({'pid':'unknown'}, {'pid':os.getpid(),'birth':psutil.Process().create_time(),
                'format':session_host.FORMAT,'program':str(self.base/'foreign'),'tree':value['note']['data_paths'][0]}):
            with self.subTest(descriptor=descriptor):
                write(target,descriptor)
                with patch.object(clean_update,'relocate') as relocate,patch.object(session_host,'_rpc') as rpc:
                    with self.assertRaises((RuntimeError,ValueError)):
                        with clean_startup.handoff(incoming): pass
                relocate.assert_not_called();rpc.assert_not_called()

    def test_concurrent_same_candidate_can_reattach_after_ui_ack_and_old_retirement(self):
        # The first launch may confirm and retire OLD while the second waits
        # for the shared launch fence. It must rejoin NEW without reading OLD.
        original = clean_startup.pending
        calls = []
        def pending(program, operation=''):
            value = original(program,operation)
            calls.append(True)
            if len(calls)==1:
                write(session_host.control_path(self.new), {'format':session_host.FORMAT,
                    'program':str(self.new),'tree':str(self.data),'pid':os.getpid(),
                    'birth':__import__('psutil').Process().create_time(),'generation':'already-new'})
                note = read(self.new/'python-migration.json')
                write(self.new/'python-migration.json', dict(note,state='completed',startup_proof={'fixture':True}))
                __import__('shutil').rmtree(self.old)
            return value
        with patch.object(clean_startup,'pending',side_effect=pending):
            with clean_startup.handoff(self.new) as locked: self.assertTrue(locked)
        self.assertFalse(self.old.exists())

    def test_profile_retarget_while_waiting_cannot_bypass_unheld_launch_fence(self):
        from localharness import config_transaction
        original = config_transaction.transaction
        @contextmanager
        def changed(path, **kwargs):
            with original(path, **kwargs):
                data = self.base/'unlocked-data'
                value = read(self.new/'helene.json');value['tree']=str(data)
                write(self.new/'helene.json',value)
                write(data/'memory/.control/desktop-session.json', {'format':session_host.FORMAT,
                    'tree':str(data),'program':str(self.new),'pid':os.getpid(),
                    'birth':__import__('psutil').Process().create_time(),'generation':'unlocked'})
                yield
        with patch.object(config_transaction,'transaction',side_effect=changed), \
                patch.object(clean_update,'relocate') as relocate:
            with self.assertRaisesRegex(ValueError,'awaiting clean startup custody'):
                with clean_startup.handoff(self.new): pass
        relocate.assert_not_called()

    def secondary_candidate(self):
        secondary = self.old/'secondary-profile'
        (secondary/'memory').mkdir(parents=True)
        (secondary/'memory/history').write_bytes(b'secondary-owner-history')
        write(self.old/'agents/secondary/helene.json', {'tree':str(secondary),'code':'../../../tree'})
        incoming = self.base/'two-profile-candidate';(incoming/'tree').mkdir(parents=True)
        value = clean_update.prepare(self.old,incoming)
        return secondary,incoming,value

    def fresh_candidate(self, name):
        incoming = self.base/name
        (incoming/'app/static').mkdir(parents=True)
        (incoming/'app/static/index.html').write_text('<html>fixture</html>',encoding='utf8')
        (incoming/'app/python_app.py').write_bytes(b'# fixture')
        (incoming/'tree').mkdir()
        write(incoming/'python-app.json',{'version':'2.5.1'})
        return incoming

    def internal_producer(self, name, *, webview=False):
        internal = self.old/(name+'-profile')
        (internal/'memory').mkdir(parents=True)
        (internal/'memory/history').write_bytes(b'producer-committed-history')
        current=read(self.old/'helene.json');current['tree']=str(internal)
        write(self.old/'helene.json',current)
        portable_profile.bind_program(self.old)
        identity=internal/'memory/.control/profile-identity.json'
        write(identity,dict(read(identity),agent_name='Fixture'))
        if webview:
            cache=self.old/'.helene/webview';cache.mkdir(parents=True)
            (cache/'state').write_bytes(b'committed-webview-state')
        self.new=self.fresh_candidate(name)
        process,descriptor=self.start_host(self.old)
        def stop(*args,**kwargs):
            session_host._rpc(descriptor,'exit',{'owner_exit':False},timeout=45)
            process.wait(timeout=8)
        return internal,stop

    def test_actual_first_start_preparation_rejoins_legacy_completed_internal_handoff(self):
        from localharness import first_start
        internal,stop=self.internal_producer('first-start-completed')
        backend=types.SimpleNamespace(exclusive=lambda target:__import__('contextlib').nullcontext(),
            stop=stop,assert_quiet=lambda *args:migration_lifecycle.quiescent(self.old))
        result=first_start.prepare(self.new,places=types.SimpleNamespace(data=internal),backend=backend)
        self.assertTrue(result['clean_switch']);self.assertFalse(internal.exists())
        witness=read(first_start.receipt(self.new))
        witness.pop('clean_handoff');witness.pop('clean_pins')
        write(first_start.receipt(self.new),witness)  # Actual pre-protocol completed receipt.
        with patch.object(clean_startup,'_settings_bindings',side_effect=AssertionError('Completed producer must not rediscover OLD')):
            self.assertEqual(self.launch(windows=True),0)
        note=read(self.new/'python-migration.json')
        self.assertEqual((Path(note['data_paths'][0])/'memory/history').read_bytes(),b'producer-committed-history')
        self.assertTrue(note['app_retirement'])

    def test_actual_archive_preparation_rejoins_external_data_and_moved_webview(self):
        from localharness import python_archive_update
        cache=self.old/'.helene/webview';cache.mkdir(parents=True)
        (cache/'state').write_bytes(b'prior-webview-state')
        process,descriptor=self.start_host(self.old)
        self.new=self.fresh_candidate('archive-external-completed')
        def stop(root):
            session_host._rpc(descriptor,'exit',{'owner_exit':False},timeout=45)
            process.wait(timeout=8)
        with patch.object(migration_lifecycle,'stop',side_effect=stop):
            note=python_archive_update.prepare(self.new,self.new,self.old,self.data,progress=lambda *args:None)
        value=read(python_source_updater.prepared_install_path(self.new))
        destination=next(Path(row['target']) for row in value['moves'] if row['source']==str(cache))
        self.assertFalse(cache.exists())
        with patch.object(clean_startup,'_settings_bindings',side_effect=AssertionError('WebView move is already committed')):
            self.assertEqual(self.launch(windows=True),0)
        self.assertEqual((destination/'state').read_bytes(),b'prior-webview-state')
        self.assertEqual(read(self.new/'python-migration.json')['id'],note['id'])

    def test_completed_remote_cache_reuses_staged_snapshot_without_old_rediscovery(self):
        write(self.old/'helene.json',{'mode':'remote','base':'http://127.0.0.1:1',
                                    'tree':str(self.base/'old-absent-remote-cache')})
        self.new=self.fresh_candidate('completed-remote-cache')
        value=clean_update.prepare(self.old,self.new)
        clean_update.relocate(self.old,self.new,value)
        with patch.object(clean_startup,'_settings_bindings',side_effect=AssertionError('Remote cache handoff is complete')):
            self.assertEqual(self.launch(windows=True),0)
        self.assertTrue(Path(value['note']['data_paths'][0]).is_dir())

    def test_bound_partial_archive_move_resumes_without_old_rediscovery(self):
        from localharness import python_archive_update,first_start
        internal,stop=self.internal_producer('archive-partial',webview=True)
        original=clean_update.shutil.move;moved=[]
        def interrupted(source,target):
            result=original(source,target);moved.append((source,target))
            if len(moved)==1:raise OSError('controlled first move completed before interruption')
            return result
        with patch.object(migration_lifecycle,'stop',side_effect=stop), \
                patch.object(clean_update.shutil,'move',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'first move completed'):
                python_archive_update.prepare(self.new,self.new,self.old,internal,progress=lambda *args:None)
        self.assertEqual(read(first_start.receipt(self.new))['state'],'handoff')
        value=read(python_source_updater.prepared_install_path(self.new))
        with patch.object(clean_startup,'_settings_bindings',side_effect=AssertionError('Bound partial move must not rediscover OLD')):
            self.assertEqual(self.launch(windows=True),0)
        for row in value['moves']:
            self.assertFalse(Path(row['source']).exists());self.assertTrue(Path(row['target']).exists())
        self.assertEqual(read(first_start.receipt(self.new))['state'],'completed')

    def test_archive_moves_before_final_receipt_are_recoverable_from_bound_intent(self):
        from localharness import python_archive_update,first_start
        internal,stop=self.internal_producer('archive-last-receipt')
        original=clean_update.write
        def interrupted(path,value):
            if Path(path)==first_start.receipt(self.new) and value.get('state')=='completed':
                raise OSError('controlled final receipt publication failure')
            return original(path,value)
        with patch.object(migration_lifecycle,'stop',side_effect=stop),patch.object(clean_update,'write',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'final receipt publication'):
                python_archive_update.prepare(self.new,self.new,self.old,internal,progress=lambda *args:None)
        self.assertFalse(internal.exists())
        self.assertEqual(read(first_start.receipt(self.new))['state'],'handoff')
        with patch.object(clean_startup,'_settings_bindings',side_effect=AssertionError('All data moves already completed')):
            self.assertEqual(self.launch(windows=True),0)
        self.assertEqual(read(first_start.receipt(self.new))['state'],'completed')

    def test_restore_invalidates_completed_relocation_witness_before_returning_data(self):
        from localharness import first_start
        internal=self.old/'restorable-profile';internal.mkdir()
        write(self.old/'helene.json',{'tree':str(internal),'code':'tree','model':{'name':'before-restore'}})
        incoming=self.fresh_candidate('restorable-candidate')
        value=clean_update.prepare(self.old,incoming)
        value=clean_startup._refresh_settings(self.old,incoming,value)
        clean_update.relocate(self.old,incoming,value)
        self.assertTrue(clean_startup._relocation_snapshot(self.old,incoming,value))
        clean_update.restore(value)
        self.assertEqual(read(first_start.receipt(incoming))['state'],'handoff_restored')
        self.assertFalse(clean_startup._relocation_snapshot(self.old,incoming,value))
        self.assertTrue(internal.is_dir())
        latest=read(self.old/'helene.json');latest['model']['name']='after-restore'
        write(self.old/'helene.json',latest)
        clean_startup._refresh_settings(self.old,incoming,value)
        self.assertEqual(read(incoming/'helene.json')['model']['name'],'after-restore')

    def test_vacuous_legacy_external_receipt_cannot_replace_current_old_settings(self):
        from localharness import first_start
        clean_update.relocate(self.old,self.new,self.value)
        witness=read(first_start.receipt(self.new));witness.pop('clean_handoff');witness.pop('clean_pins')
        write(first_start.receipt(self.new),witness)
        current=read(self.old/'helene.json');current['model']['name']='latest-after-vacuous-stage'
        write(self.old/'helene.json',current)
        self.assertFalse(clean_startup._relocation_snapshot(self.old,self.new,self.value))
        self.assertEqual(self.launch(windows=True),0)
        self.assertEqual(read(self.new/'helene.json')['model']['name'],'latest-after-vacuous-stage')

    def test_handoff_intent_rejects_unknown_pin_edits_before_resuming(self):
        from localharness import component_store
        internal=self.old/'pin-guard-profile';internal.mkdir()
        write(self.old/'helene.json',{'tree':str(internal),'code':'tree'})
        incoming=self.fresh_candidate('pin-guard-candidate')
        value=clean_update.prepare(self.old,incoming)
        with patch.object(clean_update.shutil,'move',side_effect=OSError('controlled before move')):
            with self.assertRaises(OSError):clean_update.relocate(self.old,incoming,value)
        write(incoming/component_store.PINS,{'format':component_store.FORMAT,'pins':{'voice':{'unexpected':'edit'}}})
        with self.assertRaisesRegex(ValueError,'programme pins changed'):
            clean_startup._relocation_snapshot(self.old,incoming,value)
        self.assertTrue(internal.exists())

    def test_bound_handoff_rejects_both_or_neither_move_locations(self):
        internal=self.old/'ambiguous-profile';internal.mkdir()
        (internal/'history').write_bytes(b'preserved-fixture')
        write(self.old/'helene.json',{'tree':str(internal),'code':'tree'})
        incoming=self.fresh_candidate('ambiguous-candidate')
        value=clean_update.prepare(self.old,incoming)
        with patch.object(clean_update.shutil,'move',side_effect=OSError('controlled before move')):
            with self.assertRaises(OSError):clean_update.relocate(self.old,incoming,value)
        target=Path(value['moves'][0]['target']);target.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'both locations'):
            clean_startup._relocation_snapshot(self.old,incoming,value)
        target.rmdir();internal.rename(self.base/'saved-outside-admitted-locations')
        with self.assertRaisesRegex(ValueError,'missing from both'):
            clean_startup._relocation_snapshot(self.old,incoming,value)

    def test_partial_handoff_rejects_after_pins_written_before_their_data_move(self):
        from localharness import component_store
        internal=self.old/'phase-profile';internal.mkdir()
        write(self.old/'helene.json',{'tree':str(internal),'code':'tree'})
        payload=self.base/'phase-model';payload.mkdir();(payload/'weights').write_bytes(b'phase-model-fixture')
        reference,_copied=component_store.import_directory(payload,program=self.old,store=self.old/'phase-store')
        pins={'format':component_store.FORMAT,'pins':{'models/whisper':reference}}
        write(internal/component_store.PINS,pins)
        incoming=self.fresh_candidate('phase-candidate');value=clean_update.prepare(self.old,incoming)
        with patch.object(clean_update.shutil,'move',side_effect=OSError('controlled before move')):
            with self.assertRaises(OSError):clean_update.relocate(self.old,incoming,value)
        self.assertTrue(clean_startup._relocation_snapshot(self.old,incoming,value))
        update=value['data_pin_updates'][0]
        write(update['source_path'],update['after'])
        with self.assertRaisesRegex(ValueError,'pins changed outside'):
            clean_startup._relocation_snapshot(self.old,incoming,value)

    def test_completed_witness_cannot_adopt_physically_restored_moves(self):
        internal=self.old/'externally-restored-profile';internal.mkdir()
        write(self.old/'helene.json',{'tree':str(internal),'code':'tree'})
        incoming=self.fresh_candidate('externally-restored-candidate')
        value=clean_update.prepare(self.old,incoming)
        clean_update.relocate(self.old,incoming,value)
        self.assertTrue(clean_startup._relocation_snapshot(self.old,incoming,value))
        for row in value['moves']:
            Path(row['target']).rename(row['source'])
        self.assertFalse(clean_startup._relocation_snapshot(self.old,incoming,value))

    def test_missing_remote_cache_windows_entry_consumes_admitted_empty_data(self):
        write(self.old/'helene.json',{'mode':'remote','base':'http://127.0.0.1:1',
                                    'tree':str(self.base/'missing-remote-cache')})
        self.new = self.fresh_candidate('remote-candidate')
        value = clean_update.prepare(self.old,self.new)
        cache = Path(value['empty_data'][0])
        self.assertFalse(cache.exists());self.assertFalse(clean_update.ready(self.new))
        with patch('first_start_window.main',side_effect=AssertionError('A bound prepared migration must use ordinary entry')):
            self.assertEqual(self.launch(windows=True),0)
        self.assertTrue(cache.is_dir())
        self.assertEqual(Path(read(self.new/'helene.json')['tree']),cache)
        self.assertEqual(read(self.new/'python-migration.json')['id'],value['note']['id'])
        self.assertFalse((self.base/'missing-remote-cache').exists())

    def test_explicit_attached_primary_survives_refresh_and_normal_windows_handoff(self):
        process,_ = self.start_host(self.old)
        selected = self.base/'explicit-existing-data'
        (selected/'memory').mkdir(parents=True)
        (selected/'memory/history').write_bytes(b'explicit-selected-history')
        self.new = self.fresh_candidate('attached-candidate')
        value = clean_update.prepare(self.old,self.new,attached_data=selected)
        latest = read(self.old/'helene.json');latest['model']['name']='latest-owner-model'
        write(self.old/'helene.json',latest)
        self.assertEqual(self.launch(windows=True),0)
        process.wait(timeout=8)
        current = read(self.new/'helene.json')
        self.assertEqual(Path(current['tree']),selected)
        self.assertEqual(current['model']['name'],'latest-owner-model')
        self.assertEqual((selected/'memory/history').read_bytes(),b'explicit-selected-history')
        self.assertEqual((self.data/'memory/history').read_bytes(),b'owner-history')
        self.assertEqual(read(self.new/'python-migration.json')['data_paths'],value['note']['data_paths'])

    def test_missing_remote_secondary_reuses_original_custom_cache_destination(self):
        write(self.old/'agents/remote/helene.json',{'mode':'remote','base':'http://127.0.0.1:1',
              'tree':str(self.base/'missing-secondary-cache')})
        incoming = self.fresh_candidate('remote-secondary-candidate')
        home = self.base/'explicit-retained-home'
        value = clean_update.prepare(self.old,incoming,data_destination=home)
        cache = Path(value['empty_data'][0])
        self.assertTrue(cache.is_relative_to(home));self.assertFalse(cache.exists())
        with clean_startup.handoff(incoming): pass
        refreshed = read(python_source_updater.prepared_install_path(incoming))
        self.assertEqual(refreshed['empty_data'],value['empty_data'])
        self.assertEqual(refreshed['note']['data_paths'],value['note']['data_paths'])
        self.assertTrue(cache.is_dir())
        self.assertEqual(Path(read(incoming/'agents/remote/helene.json')['tree']),cache)

    def test_windows_control_entries_keep_their_existing_admission_priority(self):
        for arguments in (['--panic'],['--session-job','fixture-job'],['--migration',self.operation]):
            with self.subTest(arguments=arguments), \
                    patch.object(clean_startup,'pending',side_effect=AssertionError('Control entry must retain its own admission')), \
                    patch.object(windows_start,'ordinary',return_value=0) as ordinary:
                self.assertEqual(windows_start.main(['--root',str(self.new),*arguments]),0)
                ordinary.assert_called_once_with(self.new,arguments)

    def test_internal_primary_windows_entry_moves_data_before_attaching_new_owner(self):
        internal = self.old/'internal-primary'
        (internal/'memory').mkdir(parents=True)
        (internal/'memory/history').write_bytes(b'internal-primary-history')
        current = read(self.old/'helene.json');current['tree']=str(internal)
        write(self.old/'helene.json',current)
        process,_ = self.start_host(self.old)
        self.new = self.fresh_candidate('internal-primary-candidate')
        value = clean_update.prepare(self.old,self.new)
        selected = Path(value['note']['data_paths'][0])
        self.assertFalse(selected.exists());self.assertFalse(clean_update.ready(self.new))
        with patch('first_start_window.main',side_effect=AssertionError('Prepared internal-data migration must use ordinary entry')):
            self.assertEqual(self.launch(windows=True),0)
        process.wait(timeout=8)
        self.assertFalse(internal.exists())
        self.assertEqual((selected/'memory/history').read_bytes(),b'internal-primary-history')
        self.assertEqual(read(session_host.control_path(self.new))['program'],str(self.new))

    def unsafe_attached_internal_candidate(self):
        internal = self.old/'unselected-internal-data'
        (internal/'memory').mkdir(parents=True)
        (internal/'memory/history').write_bytes(b'previous-profile-must-survive')
        current = read(self.old/'helene.json');current['tree']=str(internal)
        write(self.old/'helene.json',current)
        selected = self.base/'different-selected-data';selected.mkdir()
        incoming = self.fresh_candidate('unsafe-legacy-attached-candidate')
        value = clean_update.prepare(self.old,incoming,attached_data=selected)
        self.assertFalse(value['moves'])  # Legacy producer omitted unselected internal data.
        return internal,incoming,value

    def test_legacy_attached_override_defers_before_exiting_owner_with_unretained_internal_data(self):
        internal,incoming,_value = self.unsafe_attached_internal_candidate()
        process,_ = self.start_host(self.old)
        self.new = incoming
        with patch.object(session_host,'_rpc',wraps=session_host._rpc) as rpc, \
                patch.object(clean_update,'relocate') as relocate:
            self.assertEqual(self.launch(windows=True),0)
        self.assertIsNone(process.poll())
        rpc.assert_not_called();relocate.assert_not_called()
        self.assertEqual((internal/'memory/history').read_bytes(),b'previous-profile-must-survive')

    def test_direct_retirement_cannot_delete_unretained_old_internal_data(self):
        internal,incoming,value = self.unsafe_attached_internal_candidate()
        note = dict(value['note'],state='new_app_started',startup_proof={'fixture':True})
        write(incoming/'python-migration.json',note)
        with patch.object(migration_lifecycle,'cleanup_installation') as cleanup, \
                patch.object(clean_update.shutil,'rmtree') as remove:
            with self.assertRaisesRegex(ValueError,'Previous agent data still exists'):
                clean_update.finish(self.old,incoming,note)
        cleanup.assert_not_called();remove.assert_not_called()
        self.assertEqual((internal/'memory/history').read_bytes(),b'previous-profile-must-survive')

    def test_all_profile_launch_fences_share_one_budget_and_canonical_order(self):
        from localharness import config_transaction
        _secondary,incoming,value = self.secondary_candidate()
        expected = {Path(data)/'memory/.control/desktop-session-launch'
                    for data in [*value['note']['old_data_paths'],*value['note']['data_paths']]}
        moments = [0.0,*[10.0*index for index in range(1,len(expected)+1)]]
        clock = types.SimpleNamespace(monotonic=Mock(side_effect=moments),sleep=time.sleep)
        seen = [];original = config_transaction.transaction
        @contextmanager
        def observed(path,**kwargs):
            seen.append((Path(path),kwargs['timeout_sec']))
            with original(path,**kwargs): yield
        with patch.object(clean_startup,'time',clock),patch.object(config_transaction,'transaction',side_effect=observed):
            with clean_startup.handoff(incoming): pass
        canonical = lambda path: os.path.normcase(str(path.resolve()))
        self.assertEqual([path for path,_budget in seen],sorted(expected,key=canonical))
        self.assertEqual([budget for _path,budget in seen],[180.0-moment for moment in moments[1:]])

    def test_exhausted_shared_launch_budget_refuses_before_owner_effects(self):
        _secondary,incoming,_value = self.secondary_candidate()
        clock = types.SimpleNamespace(monotonic=Mock(side_effect=[0.0,1.0,181.0]),sleep=time.sleep)
        with patch.object(clean_startup,'time',clock),patch.object(session_host,'_rpc') as rpc, \
                patch.object(clean_update,'relocate') as relocate:
            with self.assertRaisesRegex(TimeoutError,'awaiting selected-data launch custody'):
                with clean_startup.handoff(incoming): pass
        rpc.assert_not_called();relocate.assert_not_called()

    def test_secondary_profile_fence_blocks_foreign_attach_through_actual_relocation(self):
        from localharness import config_transaction
        from localharness.desktop_supervisor import JobChild
        secondary,incoming,value = self.secondary_candidate()
        foreign = self.base/'foreign-secondary-program';foreign.mkdir()
        write(foreign/'helene.json',{'tree':str(secondary),'code':'tree'})
        attempted = threading.Event();launched = threading.Event();outcome = []
        original_transaction = config_transaction.transaction
        original_relocate = clean_update.relocate
        key = secondary/'memory/.control/desktop-session-launch'
        @contextmanager
        def observed(path, **kwargs):
            if threading.current_thread().name=='foreign-secondary' and Path(path)==key:
                attempted.set()
            with original_transaction(path, **kwargs): yield
        def foreign_start():
            try: session_host.attach(foreign,Mock())
            except RuntimeError as exc: outcome.append(str(exc))
        def launch_boundary(*args,**kwargs):
            self.assertFalse(secondary.exists())
            launched.set()
            raise RuntimeError('controlled foreign launch boundary after relocation')
        worker = threading.Thread(target=foreign_start,name='foreign-secondary')
        def relocating(old,new,prepared):
            worker.start();self.assertTrue(attempted.wait(10))
            self.assertFalse(launched.wait(.1),'Foreign attach passed the selected secondary-data fence')
            return original_relocate(old,new,prepared)
        try:
            with patch.object(config_transaction,'transaction',side_effect=observed), \
                    patch.object(JobChild,'spawn',side_effect=launch_boundary), \
                    patch.object(clean_update,'relocate',side_effect=relocating):
                with clean_startup.handoff(incoming):
                    self.assertFalse(launched.is_set())
                worker.join(20)
            self.assertFalse(worker.is_alive())
            self.assertTrue(launched.is_set())
            self.assertEqual(outcome,['controlled foreign launch boundary after relocation'])
            target = next(Path(row['target']) for row in value['moves'] if row['source']==str(secondary))
            self.assertEqual((target/'memory/history').read_bytes(),b'secondary-owner-history')
        finally:
            if worker.ident is not None: worker.join(20)

    def test_secondary_selection_changed_while_waiting_is_refused_before_effects(self):
        from localharness import config_transaction
        _secondary,incoming,value = self.secondary_candidate()
        original = config_transaction.transaction
        changed = []
        @contextmanager
        def change(path, **kwargs):
            with original(path, **kwargs):
                if not changed:
                    changed.append(True)
                    updated = read(python_source_updater.prepared_install_path(incoming))
                    updated['note']['data_paths'][1] = str(self.base/'unfenced-secondary')
                    write(python_source_updater.prepared_install_path(incoming),updated)
                    write(incoming/'python-migration.json',updated['note'])
                yield
        with patch.object(config_transaction,'transaction',side_effect=change), \
                patch.object(session_host,'_rpc') as rpc,patch.object(clean_update,'relocate') as relocate:
            with self.assertRaisesRegex(ValueError,'migration data changed while awaiting'):
                with clean_startup.handoff(incoming): pass
        rpc.assert_not_called();relocate.assert_not_called()


class QuiescenceExemptions(unittest.TestCase):
    def test_config_transaction_default_is_unchanged_and_handoff_wait_is_explicit(self):
        from localharness import config_transaction
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(config_transaction,'acquire_file_lock',return_value=123) as acquire, \
                patch.object(config_transaction,'release_file_lock'):
            with config_transaction.transaction(Path(folder)/'ordinary'): pass
            self.assertEqual(acquire.call_args.kwargs['timeout_sec'],15)
            with config_transaction.transaction(Path(folder)/'handoff',timeout_sec=180): pass
            self.assertEqual(acquire.call_args.kwargs['timeout_sec'],180)

    def test_windows_exempts_only_the_fresh_exact_helper_generation(self):
        from localharness import migration_windows as lifecycle
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            helper = {'pid':33,'birth':4.0}
            row = {'ProcessId':33,'Birth':4.0,'CommandLine':'python --old "'+str(root)+'"'}
            api = types.SimpleNamespace(name='nt',getpid=lambda:1,getppid=lambda:2)
            with patch.object(lifecycle,'os',api),patch.object(session_host,'process_state',return_value='alive'), \
                    patch.object(lifecycle,'process_snapshot',return_value=[row]):
                self.assertTrue(lifecycle.quiescent(root,exempt=helper))
                with self.assertRaises(RuntimeError): lifecycle.quiescent(root)
                with self.assertRaises(RuntimeError): lifecycle.quiescent(root,exempt={'pid':33,'birth':3.0})
                with patch.object(lifecycle,'process_snapshot',return_value=[row,dict(row,ProcessId=44,Birth=5.0)]):
                    with self.assertRaises(RuntimeError): lifecycle.quiescent(root,exempt=helper)

    def test_posix_retains_extra_writer_and_generation_checks(self):
        from localharness import migration_posix as lifecycle
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            helper = {'pid':33,'birth':4.0}
            row = (types.SimpleNamespace(pid=33),{'create_time':4.0})
            with patch.object(session_host,'process_state',return_value='alive'), \
                    patch.object(lifecycle,'_processes',return_value=[row]):
                self.assertTrue(lifecycle.quiescent(root,exempt=helper))
                with self.assertRaises(RuntimeError): lifecycle.quiescent(root)
                with self.assertRaises(RuntimeError): lifecycle.quiescent(root,exempt={'pid':33,'birth':3.0})
                with patch.object(lifecycle,'_processes',return_value=[row,(types.SimpleNamespace(pid=44),{'create_time':5.0})]):
                    with self.assertRaises(RuntimeError): lifecycle.quiescent(root,exempt=helper)

    def test_helper_runtime_or_entry_under_old_is_never_exempted(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new = Path(folder)/'old', Path(folder)/'new'
            old.mkdir();new.mkdir()
            for root in (old,new):
                (root/'python').write_bytes(b'fixture')
                (root/'helper.py').write_bytes(b'fixture')
            for runtime,entry in ((old/'python',new/'helper.py'),(new/'python',old/'helper.py')):
                helper = {'pid':33,'birth':4.0,'old':str(old),'new':str(new),'operation':'a'*32,
                          'runtime':str(runtime),'entry':str(entry)}
                process = Mock();process.exe.return_value=str(runtime)
                process.create_time.return_value=4.0
                process.cwd.return_value=str(new);process.cmdline.return_value=[str(runtime),str(entry)]
                value = {'note':{'id':'a'*32,'startup_helper':helper}}
                with patch.object(session_host,'process_state',return_value='alive'), \
                        patch('psutil.Process',return_value=process):
                    self.assertFalse(clean_startup._helper_alive(old,new,value))


if __name__ == '__main__': unittest.main()
