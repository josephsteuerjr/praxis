"""Owner Exit failure paths with fake UI, threads, RPC and process observations.

No Application/Host constructor, real HTTP server, thread, subprocess, native UI
or process inventory is started. This suite grants no real shutdown acceptance.
"""
import ast
from contextlib import contextmanager, nullcontext, redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_app
import python_tray
from localharness import session_host as session, python_controller, python_updates
from localharness import desktop_supervisor, desktop_update_request as jobs, session_exit
from localharness import python_source_updater, owner_stop
from localharness.package_notice import read, write


ERRORS = (OSError, ValueError, RuntimeError, TimeoutError)


class ApplicationExit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='application-exit-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def app(self, *, explicit=True, relaunch=False):
        app = object.__new__(python_app.Application)
        app.root = self.root;app.program = self.root
        app.exit_session = explicit;app.exiting = explicit;app.relaunch = relaunch
        app.session_exit_intent = True
        app.migration_wizard = False;app.stopping = threading.Event()
        app.lock = threading.Lock();app.confirm_lock = threading.Lock()
        app.voice_keys = None;app.window = Mock();app.tray = None
        app.backup_thread = Mock();app.backup_thread.is_alive.return_value = False
        app.thread = Mock();app.thread.is_alive.return_value = False
        app.server = Mock();app.control_file = Mock();app.event = Mock()
        app.host = Mock();app.host.shutdown.return_value = {'session_exited': True}
        return app

    def close_without_claiming_failure_is_success(self, app):
        try:app.close()
        except ERRORS:pass

    def test_explicit_exit_with_relaunch_false_overrides_a_prior_restart_intent(self):
        app = self.app(explicit=False, relaunch=True)
        app.invoke('python_window', {'action': 'exit', 'relaunch': False})
        self.assertFalse(app.relaunch)
        self.assertTrue(app.exit_session)
        self.assertTrue(app.exiting)
        app.window.destroy.assert_called_once()

    def test_observer_relaunch_still_detaches_without_owning_session_exit(self):
        app = self.app(explicit=False, relaunch=True)
        app.invoke('python_window', {'action': 'exit', 'observer_only': True})
        app.close()
        app.host.close.assert_called_once()
        app.host.shutdown.assert_not_called()

    def test_mechanical_window_exit_stops_host_without_durable_owner_exit_semantics(self):
        app = self.app(explicit=False, relaunch=True)
        app.invoke('python_window', {'action': 'exit', 'relaunch': False, 'owner_exit': False})
        app.close()
        self.assertFalse(app.relaunch)
        self.assertTrue(app.exit_session)
        self.assertFalse(app.session_exit_intent)
        app.host.shutdown.assert_called_once_with(owner_exit=False)

    def test_shutdown_barrier_precedes_ui_thread_and_server_cleanup(self):
        app = self.app();order = []
        app.host.shutdown.side_effect = lambda: order.append('host-stop')
        app.voice_keys = Mock();app.voice_keys.close.side_effect = lambda: order.append('voice-close')
        app.backup_thread.join.side_effect = lambda *a: order.append('backup-join')
        app.server.shutdown.side_effect = lambda: order.append('http-shutdown')
        app.thread.join.side_effect = lambda *a: order.append('http-join')
        app.close()
        for action in ('voice-close', 'backup-join', 'http-shutdown', 'http-join'):
            self.assertLess(order.index('host-stop'), order.index(action), order)

    def test_any_ui_cleanup_error_cannot_skip_host_shutdown(self):
        for failing in ('voice', 'backup', 'http-shutdown', 'http-close', 'http-join'):
            with self.subTest(failing=failing):
                app = self.app();app.voice_keys = Mock()
                target = {'voice': app.voice_keys.close, 'backup': app.backup_thread.join,
                          'http-shutdown': app.server.shutdown, 'http-close': app.server.server_close,
                          'http-join': app.thread.join}[failing]
                target.side_effect = RuntimeError('synthetic '+failing+' failure')
                self.close_without_claiming_failure_is_success(app)
                app.host.shutdown.assert_called_once()
                self.assertTrue(app.stopping.is_set())
                app.control_file.unlink.assert_called_once_with(missing_ok=True)

    def test_unconfirmed_host_shutdown_does_not_skip_other_resource_cleanup(self):
        app = self.app()
        app.host.shutdown.side_effect = RuntimeError('synthetic owned-child custody unknown')
        self.close_without_claiming_failure_is_success(app)
        app.server.shutdown.assert_called_once()
        app.server.server_close.assert_called_once()
        app.backup_thread.join.assert_called_once()
        app.thread.join.assert_called_once()
        app.control_file.unlink.assert_called_once_with(missing_ok=True)

    def test_actual_tray_exit_callback_clears_prior_relaunch_intent(self):
        app = self.app(explicit=False, relaunch=True)
        native = Mock()
        # Execute the actual callback body without importing WinForms/System or
        # creating a tray. Its closure contains only fake app/native objects.
        module = ast.parse(Path(python_tray.__file__).read_text(encoding='utf-8'))
        callback = next(n for n in ast.walk(module)
                        if isinstance(n, ast.FunctionDef) and n.name=='exit_app')
        namespace = {**vars(python_tray), 'app': app, 'native': native}
        exec(compile(ast.Module(body=[callback], type_ignores=[]), 'tray-exit-callback', 'exec'), namespace)
        namespace['exit_app'](None, None)
        self.assertFalse(app.relaunch)
        self.assertTrue(app.exit_session)
        self.assertTrue(app.exiting)


class SessionExit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='session-exit-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def host(self):
        host = object.__new__(session.Host)
        host.root = self.root;host.control_tree = self.root/'data'
        host.migration_request = None;host.stop = threading.Event()
        host.lock = threading.RLock();host.observers = {}
        host.exit_lock = threading.RLock();host.exit_result = None;host.exit_owner = False
        host.descriptor = {'format': session.FORMAT, 'program': str(self.root),
            'tree': str(host.control_tree), 'pid': 42, 'birth': 123.0,
            'state': 'running', 'generation': 'fixture-generation'}
        host.owner = SimpleNamespace(root=self.root, stop=threading.Event(), exiting=False,
            lock=threading.RLock(),
            _default=lambda: 'base', selection=lambda _identity: nullcontext(),
            close=Mock(), start=Mock())
        host.cancel_owned_jobs = Mock(return_value=(False, {}));host.publish = Mock()
        return host

    def request_exit(self, host, arguments=None):
        with patch.object(session.agents, 'find', return_value=SimpleNamespace(id='base')):
            try:return host.dispatch('exit', arguments or {})
            except ERRORS:return None

    def test_job_cancellation_failure_cannot_prevent_owner_stop_barrier(self):
        host = self.host()
        host.cancel_owned_jobs.side_effect = ValueError('synthetic malformed update cancellation')
        self.request_exit(host)
        self.assertTrue(host.stop.is_set())
        host.owner.close.assert_called_once()
        self.assertTrue(session_exit.requested(self.root))

    def test_host_and_supervisor_stop_latches_precede_job_bookkeeping(self):
        host = self.host()
        def bookkeeping(_reason):
            self.assertTrue(host.stop.is_set())
            self.assertTrue(host.owner.stop.is_set())
            self.assertTrue(host.owner.exiting)
            return False, {}
        host.cancel_owned_jobs.side_effect = bookkeeping
        self.request_exit(host)
        host.owner.close.assert_called_once()

    def test_terminal_event_is_latched_under_owner_lock_released_before_cleanup(self):
        host = self.host()
        class Lock:
            held = False
            def __enter__(self):self.held = True;return self
            def __exit__(self, *_args):self.held = False
        lock = Lock();host.owner.lock = lock;host.owner.stop = Mock()
        host.owner.stop.set.side_effect = lambda: self.assertTrue(lock.held)
        def bookkeeping(_reason):
            self.assertFalse(lock.held)
            return False, {}
        host.cancel_owned_jobs.side_effect = bookkeeping
        host.owner.close.side_effect = lambda: self.assertFalse(lock.held)
        self.request_exit(host)
        host.owner.stop.set.assert_called()
        host.owner.close.assert_called_once()

    def test_owner_exit_is_durable_and_duplicate_requests_do_not_repeat_shutdown(self):
        host = self.host()
        first = self.request_exit(host)
        second = self.request_exit(host)
        self.assertTrue(session_exit.requested(self.root))
        self.assertTrue(host.owner.exiting)
        host.owner.close.assert_called_once()
        self.assertEqual(first, second)

    def test_mechanical_cutover_stops_host_without_owner_exit_or_job_cancellation(self):
        host = self.host()
        self.request_exit(host, {'owner_exit': False})
        self.assertTrue(host.stop.is_set())
        host.owner.close.assert_called_once()
        host.cancel_owned_jobs.assert_not_called()
        self.assertFalse(session_exit.requested(self.root))

    def test_mechanical_request_cannot_erase_existing_owner_exit_intent(self):
        host = self.host()
        session_exit.record(self.root, host.descriptor)
        self.request_exit(host, {'owner_exit': False})
        self.assertTrue(session_exit.requested(self.root))

    def test_owner_exit_after_mechanical_cutover_escalates_job_cancellation(self):
        host = self.host()
        self.request_exit(host, {'owner_exit': False})
        host.cancel_owned_jobs.assert_not_called()
        self.assertFalse(session_exit.requested(self.root))
        self.request_exit(host)
        host.cancel_owned_jobs.assert_called_once()
        host.owner.close.assert_called_once()
        self.assertTrue(session_exit.requested(self.root))
        self.assertTrue(host.exit_owner)

    def test_failed_exit_marker_can_retry_without_repeating_physical_close(self):
        host = self.host();actual_record = session_exit.record;attempts = []
        def record(program, descriptor):
            attempts.append(program)
            if len(attempts)==1:raise OSError('synthetic temporary marker write failure')
            return actual_record(program, descriptor)
        with patch.object(session_exit, 'record', side_effect=record):
            first = self.request_exit(host)
            self.assertTrue(host.stop.is_set())
            self.assertFalse(first['stopped'])
            second = self.request_exit(host)
        self.assertTrue(second['stopped'])
        self.assertTrue(session_exit.requested(self.root))
        host.owner.close.assert_called_once()
        self.assertEqual(host.cancel_owned_jobs.call_count, 2)

    def test_failed_job_cancellation_can_retry_without_repeating_physical_close(self):
        host = self.host()
        host.cancel_owned_jobs.side_effect = [ValueError('synthetic temporary cancellation failure'), (False, {})]
        first = self.request_exit(host)
        self.assertTrue(host.stop.is_set())
        self.assertFalse(first['stopped'])
        second = self.request_exit(host)
        self.assertTrue(second['stopped'])
        self.assertTrue(session_exit.requested(self.root))
        host.owner.close.assert_called_once()
        self.assertEqual(host.cancel_owned_jobs.call_count, 2)

    def test_observer_detach_never_creates_owner_exit_or_stops_host(self):
        host = self.host()
        with patch.object(session.agents, 'find', return_value=SimpleNamespace(id='base')):
            result = host.dispatch('detach', {'observer': 'fixture-observer'})
        self.assertTrue(result['owner_continues'])
        self.assertFalse(host.stop.is_set())
        self.assertFalse(session_exit.requested(self.root))
        host.owner.close.assert_not_called();host.cancel_owned_jobs.assert_not_called()

    def test_missing_selected_agent_cannot_reject_installation_exit(self):
        host = self.host()
        with patch.object(session.agents, 'find', return_value=None):
            try:host.dispatch('exit', {'agent': 'removed-agent'})
            except ERRORS:pass
        self.assertTrue(host.stop.is_set())
        host.owner.close.assert_called_once()

    def test_stale_migration_observer_job_cannot_reject_installation_exit(self):
        host = self.host()
        with patch.object(jobs, 'status', return_value={}):
            self.request_exit(host, {'session_job': 'no-longer-the-current-job'})
        self.assertTrue(host.stop.is_set())
        host.owner.close.assert_called_once()

    def test_failed_owner_close_keeps_exit_latched_and_cannot_claim_stopped(self):
        host = self.host();host.owner.close.side_effect = RuntimeError('owned children not confirmed empty')
        result = self.request_exit(host)
        self.assertTrue(host.stop.is_set())
        self.assertFalse(isinstance(result, dict) and result.get('stopped') is True)

    def test_late_start_after_exit_cannot_restart_the_owner(self):
        host = self.host();self.request_exit(host)
        with patch.object(session.agents, 'find', return_value=SimpleNamespace(id='base')):
            try:host.dispatch('start', {})
            except ERRORS:pass
        host.owner.start.assert_not_called()


class ShutdownObservation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shutdown-observation-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def client(self, response=None, error=None):
        client = object.__new__(session.SessionClient)
        client.root = self.root;client.session_job = '';client.observer = 'fixture-observer'
        client.selected = 'base'
        client.descriptor = {'pid': 42, 'birth': 123.0, 'generation': 'exact-fixture-generation'}
        client._request = Mock(return_value=response or {'stop_requested': True, 'stopped': True}, side_effect=error)
        client.close = Mock()
        return client

    def test_successful_exit_reply_still_waits_for_exact_host_death(self):
        client = self.client();states = ['alive', 'alive', 'absent'];observed = []
        def observe(descriptor):
            self.assertEqual(descriptor, client.descriptor)
            value = states.pop(0) if len(states)>1 else states[0]
            observed.append(value);return value
        def detach():self.assertTrue(observed and observed[-1]=='absent', observed)
        client.close.side_effect = detach
        with patch.object(session, 'process_state', side_effect=observe), patch.object(session.time, 'sleep'):
            client.shutdown()
        self.assertIn('alive', observed)
        self.assertEqual(observed[-1], 'absent')
        client._request.assert_called_once()

    def test_successful_reply_with_live_or_unknown_owner_is_not_shutdown_acceptance(self):
        for state in ('alive', 'unknown'):
            with self.subTest(state=state):
                client = self.client();clock = iter(range(0, 100000, 1000))
                with patch.object(session, 'process_state', return_value=state), \
                     patch.object(session.time, 'monotonic', side_effect=lambda: next(clock)), \
                     patch.object(session.time, 'sleep'):
                    with self.assertRaises(ERRORS):client.shutdown()
                client._request.assert_called_once()

    def test_replaced_pid_birth_is_absence_of_exact_owner_without_exit_replay(self):
        client = self.client()
        with patch.object(session, 'process_state', return_value='changed'):
            client.shutdown()
        client._request.assert_called_once();client.close.assert_called_once()

    def test_lost_reply_observes_absence_without_replaying_exit(self):
        client = self.client(error=OSError('synthetic lost exit reply'))
        with patch.object(session, 'process_state', return_value='absent'):
            client.shutdown()
        client._request.assert_called_once();client.close.assert_called_once()

    def test_mechanical_shutdown_explicitly_preserves_nonowner_exit_semantics(self):
        client = self.client()
        with patch.object(session, 'process_state', return_value='absent'):
            client.shutdown(owner_exit=False)
        client._request.assert_called_once_with('exit', {'owner_exit': False}, timeout=45)
        self.assertFalse(session_exit.requested(self.root))


class ControllerExit(unittest.TestCase):
    def test_continuation_cancellation_error_cannot_skip_supervisor_stop(self):
        temporary = tempfile.TemporaryDirectory(prefix='controller-exit-test-')
        self.addCleanup(temporary.cleanup)
        owner = object.__new__(python_controller.Controller)
        owner.root = Path(temporary.name);owner.stop = threading.Event()
        owner.exiting = False
        owner.lock = threading.RLock();owner.helpers = [];owner.thread = None
        owner.error = None;owner.publishers = {}
        with patch.object(python_updates, 'cancel_waiting', side_effect=ValueError('synthetic cancellation error')), \
             patch.object(desktop_supervisor, 'stop_children'):
            try:owner.close()
            except ERRORS:pass
        self.assertTrue(owner.stop.is_set())


class DurableExitIntent(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='durable-exit-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_exit_marker_binds_exact_programme_generation_and_manual_start_clears_it(self):
        self.assertFalse(session_exit.requested(self.root))
        descriptor = {'program': str(self.root), 'pid': 42, 'birth': 123.0,
                      'generation': 'fixture-generation'}
        session_exit.record(self.root, descriptor)
        value = read(session_exit.path(self.root))
        self.assertEqual(value['format'], session_exit.FORMAT)
        for field in ('program', 'pid', 'birth', 'generation'):
            self.assertEqual(value[field], descriptor[field])
        self.assertTrue(session_exit.requested(self.root))
        session_exit.clear_for_manual_start(self.root)
        self.assertFalse(session_exit.requested(self.root))

    def test_foreign_programme_cannot_record_this_programmes_exit(self):
        with self.assertRaises(ValueError):
            session_exit.record(self.root, {'program': str(self.root/'other'), 'generation': 'foreign'})
        self.assertFalse(session_exit.path(self.root).exists())

    def test_malformed_exit_marker_never_authorizes_automatic_restart(self):
        path = session_exit.path(self.root);path.parent.mkdir()
        path.write_bytes(b'{incomplete durable Exit intent')
        self.assertTrue(session_exit.requested(self.root))
        self.assertEqual(path.read_bytes(), b'{incomplete durable Exit intent')

    def test_unwritable_programme_uses_profile_fallback_scoped_to_that_programme(self):
        program = self.root/'first';other = self.root/'second';data = self.root/'shared-profile'
        for root in (program, other):write(root/'helene.json', {'tree': str(data), 'code': 'tree'})
        actual_write = session_exit.write
        def write_intent(path, value):
            if Path(path)==session_exit.path(program):raise PermissionError('synthetic read-only programme')
            return actual_write(path, value)
        descriptor = {'program': str(program), 'tree': str(data), 'pid': 42,
                      'birth': 123.0, 'generation': 'fixture-generation'}
        with patch.object(session_exit, 'write', side_effect=write_intent):
            session_exit.record(program, descriptor)
        self.assertFalse(session_exit.path(program).exists())
        self.assertTrue(session_exit.profile_path(program).is_file())
        self.assertTrue(session_exit.requested(program))
        self.assertFalse(session_exit.requested(other))
        self.assertNotEqual(session_exit.profile_path(program), session_exit.profile_path(other))
        session_exit.clear_for_manual_start(program)
        self.assertFalse(session_exit.requested(program))
        self.assertFalse(session_exit.requested(other))

    def test_programme_marker_still_works_when_profile_write_is_denied(self):
        data = self.root/'shared-profile';write(self.root/'helene.json', {'tree': str(data), 'code': 'tree'})
        actual_write = session_exit.write
        def write_intent(path, value):
            if Path(path)!=session_exit.path(self.root):raise PermissionError('synthetic read-only profile')
            return actual_write(path, value)
        with patch.object(session_exit, 'write', side_effect=write_intent):
            session_exit.record(self.root, {'program': str(self.root), 'tree': str(data),
                'pid': 42, 'birth': 123.0, 'generation': 'fixture-generation'})
        self.assertTrue(session_exit.path(self.root).is_file())
        self.assertTrue(session_exit.requested(self.root))

    def test_selected_profile_fallback_is_found_and_cleared_when_primary_is_unwritable(self):
        program = self.root/'Programme';other = self.root/'Other programme'
        primary = self.root/'primary-data';selected = self.root/'selected-data'
        for root in (program, other):
            write(root/'helene.json', {'tree': str(primary), 'code': 'tree'})
            write(root/'agents/selected/helene.json', {'tree': str(selected), 'code': 'tree'})
        selected_marker = session_exit.profile_path(program, selected)
        actual_write = session_exit.write
        def write_intent(path, value):
            if Path(path)!=selected_marker:
                raise PermissionError('synthetic programme and primary-profile write denial')
            return actual_write(path, value)
        with patch.object(session_exit, 'write', side_effect=write_intent):
            session_exit.record(program, {'program': str(program), 'tree': str(selected),
                'pid': 42, 'birth': 123.0, 'generation': 'selected-profile-generation'})
        self.assertFalse(session_exit.path(program).exists())
        self.assertFalse(session_exit.profile_path(program, primary).exists())
        self.assertTrue(selected_marker.is_file())
        self.assertTrue(session_exit.requested(program))
        self.assertFalse(session_exit.requested(other))
        session_exit.clear_for_manual_start(program)
        self.assertFalse(selected_marker.exists())
        self.assertFalse(session_exit.requested(program))
        self.assertFalse(session_exit.requested(other))

    def test_failure_of_both_exit_intent_locations_is_not_reported_as_persisted(self):
        data = self.root/'shared-profile';write(self.root/'helene.json', {'tree': str(data), 'code': 'tree'})
        with patch.object(session_exit, 'write', side_effect=PermissionError('synthetic no writable intent location')):
            with self.assertRaises(RuntimeError):
                session_exit.record(self.root, {'program': str(self.root), 'tree': str(data),
                    'pid': 42, 'birth': 123.0, 'generation': 'fixture-generation'})
        self.assertFalse(session_exit.requested(self.root))


class RecoveryExitIntent(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='recovery-exit-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.old = self.root/'old';self.new = self.root/'new'
        self.old.mkdir();self.new.mkdir()
        self.processes = Mock()
        self.enterContext(patch.object(owner_stop, 'stopped', return_value=False))

    def marker(self, program):
        session_exit.record(program, {'program': str(program), 'pid': 42, 'birth': 123.0,
                                      'generation': 'fixture-generation'})

    def reopen(self):
        # Execute the production nested callback with only private paths and a
        # fake Popen; perform() itself would own a real mechanical update.
        module = ast.parse(Path(python_source_updater.__file__).read_text(encoding='utf-8'))
        callback = next(n for n in ast.walk(module)
                        if isinstance(n, ast.FunctionDef) and n.name=='reopen_previous')
        namespace = {**vars(python_source_updater), 'old': self.old, 'new': self.new,
            'presentation': [], 'subprocess': self.processes,
            'interpreter_for': lambda program: program/'runtime/python.exe',
            'background_flags': lambda: 0}
        exec(compile(ast.Module(body=[callback], type_ignores=[]), 'recovery-callback', 'exec'), namespace)
        return namespace['reopen_previous']()

    def test_owner_exit_on_old_or_new_suppresses_rollback_reopen(self):
        for program in (self.old, self.new):
            with self.subTest(program=program.name):
                self.marker(program)
                self.assertFalse(self.reopen())
                self.processes.Popen.assert_not_called()
                session_exit.clear_for_manual_start(program)

    def test_owner_stop_suppresses_rollback_reopen(self):
        with patch.object(owner_stop, 'stopped', return_value=True):
            self.assertFalse(self.reopen())
        self.processes.Popen.assert_not_called()

    def test_update_rollback_without_owner_exit_keeps_reopen_available(self):
        self.assertTrue(self.reopen())
        self.processes.Popen.assert_called_once()

    def trampoline(self, lock):
        self.assertTrue(self.reopen())
        command = self.processes.Popen.call_args.args[0]
        position = command.index('-c')
        script = command[position+1]
        arguments = ['-c', *command[position+2:]]
        fake_migrate = SimpleNamespace(installation_lock=lock)
        return script, arguments, fake_migrate

    def test_exit_arriving_after_reopen_schedule_is_rechecked_before_exec(self):
        @contextmanager
        def lease(_program, **_kwargs):
            self.marker(self.old)
            yield
        script, arguments, fake_migrate = self.trampoline(lease)
        with patch.dict(sys.modules, {'python_migrate': fake_migrate}), \
             patch.object(sys, 'argv', arguments), patch.object(sys, 'path', list(sys.path)), \
             patch.dict(os.environ, {}, clear=False), patch.object(os, 'chdir'), \
             patch.object(os, 'execv') as execute, redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                exec(compile(script, 'recovery-trampoline', 'exec'), {})
        self.assertEqual(stopped.exception.code, 0)
        execute.assert_not_called()

    def test_automatic_recovery_exec_carries_automatic_intent_to_new_host(self):
        script, arguments, fake_migrate = self.trampoline(lambda *_a, **_k: nullcontext())
        with patch.dict(sys.modules, {'python_migrate': fake_migrate}), \
             patch.object(sys, 'argv', arguments), patch.object(sys, 'path', list(sys.path)), \
             patch.dict(os.environ, {}, clear=False), patch.object(os, 'chdir'), \
             patch.object(os, 'execv') as execute, redirect_stdout(io.StringIO()):
            exec(compile(script, 'recovery-trampoline', 'exec'), {})
            self.assertEqual(os.environ.get('HELENE_RECOVERY_AUTOMATIC'), '1')
        execute.assert_called_once()

    @unittest.skipUnless(os.name=='nt', 'Windows CRT exec argument quoting')
    def test_windows_recovery_quotes_programme_paths_with_spaces_without_changing_executable(self):
        self.old = self.root/'Program Files/Previous Hélène'
        self.new = self.root/'Programme staging/New Hélène'
        self.old.mkdir(parents=True);self.new.mkdir(parents=True)
        script, arguments, fake_migrate = self.trampoline(lambda *_a, **_k: nullcontext())
        with patch.dict(sys.modules, {'python_migrate': fake_migrate}), \
             patch.object(sys, 'argv', arguments), patch.object(sys, 'path', list(sys.path)), \
             patch.dict(os.environ, {}, clear=False), patch.object(os, 'chdir'), \
             patch.object(os, 'execv') as execute, redirect_stdout(io.StringIO()):
            exec(compile(script, 'recovery-trampoline', 'exec'), {})
        executable, command = execute.call_args.args
        self.assertEqual(executable, str(self.old/'runtime/python.exe'))
        self.assertEqual(command[2], '--root')
        for actual, expected in ((command[0], executable),
                                 (command[1], str(self.old/'app/python_app.py')),
                                 (command[3], str(self.old))):
            self.assertTrue(actual.startswith('"') and actual.endswith('"'), actual)
            self.assertEqual(actual[1:-1], expected)


if __name__=='__main__':unittest.main()
