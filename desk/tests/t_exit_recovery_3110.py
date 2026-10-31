"""Automatic recovery respects Exit without suppressing ordinary failed updates."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import t_update_recovery_history_2610 as recovery_fixture
from localharness import python_source_updater as updater, python_updates as updates
from localharness import update_overlay as overlay, update_process, update_progress
from localharness import owner_stop, session_exit, session_host, migration_lifecycle
from localharness import windows_first_start, windows_hooks, vision_policy
from localharness.update_source import SourceChoiceCancelled
from localharness.package_notice import write
from python_migrate import installation_lock

_POPEN = subprocess.Popen


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.fx = recovery_fixture.RecoveryTests()
        self.addCleanup(self.fx.doCleanups)
        real_temporary = tempfile.TemporaryDirectory
        with patch.object(recovery_fixture.tempfile, 'TemporaryDirectory',
                          side_effect=lambda:real_temporary(prefix='exit-recovery with spaces-')):
            self.fx.setUp()
        self.old, self.new, self.base = self.fx.old, self.fx.new, self.fx.root
        self.children = []
        self.addCleanup(self._stop_children)
        self.enterContext(patch.dict(os.environ, LOCALAPPDATA=str(self.base/'private-local'),
                                     HELENE_EXIT_RECOVERY_FIXTURE=str(self.base)))
        self.enterContext(patch.object(owner_stop, 'stopped', return_value=False))
        self.enterContext(patch.object(updater, 'wait_owner'))
        self.enterContext(patch.object(update_process, 'interpreter_for', return_value=Path(sys.executable)))
        self.enterContext(patch.object(update_process, 'background_flags', return_value=0))
        forbidden = AssertionError('No real owner/process inventory/OS registration authority')
        for name in ('Backend', 'kernel', 'terminate', 'restart_holders', 'user_sid'):
            self.enterContext(patch.object(windows_first_start, name, side_effect=forbidden))
        self.enterContext(patch.object(windows_hooks, 'retire', side_effect=forbidden))
        self.enterContext(patch.object(subprocess, 'Popen', side_effect=forbidden))
        self.enterContext(patch.object(subprocess, 'run', side_effect=forbidden))
        def private_fixture_file(name):
            if not Path(name).resolve().is_relative_to(self.base.resolve()):
                raise AssertionError('Private file leaves this synthetic fixture')
        self.enterContext(patch.object(vision_policy, '_protect_private_temp', side_effect=private_fixture_file))

    def _stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)

    def _mark_exit(self, program):
        session_exit.record(program, {'program':str(program.resolve()),
                                      'pid':os.getpid(), 'birth':1, 'generation':'private-exit'})

    def _perform(self, *, failure=None, launch=None):
        with ExitStack() as stack:
            stack.enter_context(patch.object(updater, 'validate_prepared', return_value=self.fx.prepared))
            if failure is None:stack.enter_context(self.fx.fault(False))
            else:stack.enter_context(patch.object(updater, 'install', side_effect=failure))
            opened = stack.enter_context(patch.object(updater.subprocess, 'Popen',
                side_effect=launch) if launch else
                patch.object(updater.subprocess, 'Popen', return_value=SimpleNamespace(pid=12345)))
            outcome = None
            try:outcome = updater.perform(self.fx.args)
            except (OSError, RuntimeError, InterruptedError) as error:outcome = error
        return outcome, opened

    def _status(self):
        return update_progress.status(self.old)


class RecoveryExit(_Fixture):
    def test_ordinary_failed_overlay_restores_bytes_and_requests_previous_reopen(self):
        outcome, launch = self._perform()
        self.assertIsInstance(outcome, OSError)
        launch.assert_called_once()
        self.assertIs(self._status()['previous_reopen_requested'], True)
        self.assertFalse(updater.active_update(self.old).exists())
        for name in ('a', 'b'):self.assertEqual((self.old/name).read_bytes(), b'old-'+name.encode())
        self.assertFalse(session_exit.requested(self.old))
        self.assertFalse(session_exit.requested(self.new))

    def _assert_install_time_exit_suppresses_reopen(self, program):
        def fail(*args, **kwargs):
            self._mark_exit(program)
            raise OSError('synthetic failed install after admission')
        outcome, launch = self._perform(failure=fail)
        self.assertIsInstance(outcome, OSError)
        launch.assert_not_called()
        self.assertIs(self._status()['previous_reopen_requested'], False)
        self.assertTrue(session_exit.requested(program))

    def test_owner_exit_at_old_programme_suppresses_automatic_reopen(self):
        self._assert_install_time_exit_suppresses_reopen(self.old)

    def test_owner_exit_at_new_programme_suppresses_automatic_reopen(self):
        self._assert_install_time_exit_suppresses_reopen(self.new)

    def test_profile_exit_fallback_suppresses_reopen_when_programme_marker_is_unwritable(self):
        tree = self.base/'private profile data'
        tree.mkdir()
        (self.old/'helene.json').write_text(json.dumps({'mode':'local', 'tree':str(tree),
            'code':'tree', 'setup_complete':False}), encoding='utf-8')
        original_write = session_exit.write
        def write_profile_only(path, value):
            if Path(path)==session_exit.path(self.old):raise PermissionError('synthetic unwritable programme')
            return original_write(path, value)
        def fail(*args, **kwargs):
            with patch.object(session_exit, 'write', side_effect=write_profile_only):
                session_exit.record(self.old, {'program':str(self.old), 'tree':str(tree),
                    'pid':os.getpid(), 'birth':1, 'generation':'private-profile-fallback'})
            raise OSError('synthetic failed install after profile Exit fallback')

        outcome, launch = self._perform(failure=fail)

        self.assertIsInstance(outcome, OSError)
        launch.assert_not_called()
        self.assertFalse(session_exit.path(self.old).exists())
        marker = session_exit.profile_path(self.old, tree)
        self.assertTrue(marker.is_file())
        self.assertNotEqual(marker, session_exit.profile_path(self.new, tree))
        self.assertTrue(session_exit.requested(self.old))
        self.assertIs(self._status()['previous_reopen_requested'], False)

    def test_owner_stop_after_install_failure_suppresses_reopen_and_reports_false(self):
        stopped = {'value':False}
        def fail(*args, **kwargs):
            stopped['value'] = True
            raise OSError('synthetic failed install before Owner Stop')
        with patch.object(owner_stop, 'stopped', side_effect=lambda:stopped['value']):
            outcome, launch = self._perform(failure=fail)
        self.assertIsInstance(outcome, OSError)
        launch.assert_not_called()
        self.assertIs(self._status()['previous_reopen_requested'], False)
        self.assertFalse(session_exit.requested(self.old))

    def test_cancel_update_is_distinct_from_exit_and_reopens_the_previous_programme(self):
        outcome, launch = self._perform(failure=SourceChoiceCancelled('owner cancelled only this update'))
        self.assertEqual(outcome, 0)
        launch.assert_called_once()
        self.assertEqual(self._status()['stage'], 'cancelled')
        self.assertIs(self._status()['previous_reopen_requested'], True)
        self.assertFalse(session_exit.requested(self.old))
        self.assertFalse(session_exit.requested(self.new))

    def _trampoline_fixture(self):
        desk = Path(__file__).resolve().parents[1]
        sentinel = self.base/'synthetic-old-entry-executed.json'
        app = self.old/'app'
        app.mkdir(parents=True, exist_ok=True)
        (app/'python_app.py').write_text(
            'import json,os,sys\nfrom pathlib import Path\n'
            f'Path({str(sentinel)!r}).write_text(json.dumps({{"argv":sys.argv,'
            '"automatic":os.environ.get("HELENE_RECOVERY_AUTOMATIC")}))\n',
            encoding='utf-8')
        incoming_app = self.new/'app'
        incoming_app.mkdir(parents=True, exist_ok=True)
        # The trampoline imports its normal entry. This tiny bridge exposes the
        # exact production installation_lock, with Stop paths restricted to the
        # private fixture before any Stop lookup can touch an owner folder.
        bridge = (
            'import importlib.util,sys\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{str(desk)!r})\n'
            f'spec=importlib.util.spec_from_file_location("_private_real_lock",{str(desk/"python_migrate.py")!r})\n'
            'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n'
            'installation_lock=module.installation_lock\n'
            'from localharness import owner_stop,windows_first_start,windows_hooks\n'
            f'private_stop=Path({str(self.base/"private-owner-stop")!r})\n'
            'owner_stop.seed_file=lambda:private_stop\nowner_stop.client_file=lambda:private_stop\n'
            'def forbidden(*args,**kwargs):raise AssertionError("No native/registration authority")\n'
            'windows_first_start.Backend=forbidden\nwindows_hooks.retire=forbidden\n'
        )
        (incoming_app/'python_migrate.py').write_text(bridge, encoding='utf-8')
        shutil.rmtree(self.fx.recovery)
        overlay.prepare(self.new, self.old, self.fx.recovery)
        write(updater.prepared_install_path(self.new), self.fx.prepared)
        return sentinel

    def _late_intent(self, intent):
        sentinel = self._trampoline_fixture()
        def exact_trampoline(arguments, **kwargs):
            self.assertEqual(arguments[0], sys.executable)
            self.assertEqual(arguments[1:3], ['-s', '-c'])
            self.assertEqual(arguments[4:8],
                [str(self.new/'app'), str(self.old), sys.executable, str(self.old/'app/python_app.py')])
            self.assertEqual(Path(kwargs['cwd']), self.new)
            child = _POPEN(arguments, **kwargs)
            self.children.append(child)
            return child
        with installation_lock(self.old):
            outcome, launch = self._perform(launch=exact_trampoline)
            self.assertIsInstance(outcome, OSError)
            launch.assert_called_once()
            self.assertIs(self._status()['previous_reopen_requested'], True)
            log = self.old/'.helene/logs/python-update-recovery.log'
            deadline = time.monotonic()+8
            while time.monotonic()<deadline:
                if log.is_file() and 'Waiting for update installation lease' in log.read_text(encoding='utf-8', errors='replace'):
                    break
                if self.children[0].poll() is not None:self.fail(log.read_text(encoding='utf-8', errors='replace'))
                time.sleep(.025)
            else:self.fail('Exact recovery trampoline did not wait for the private installation lease')
            self.assertIsNone(self.children[0].poll())
            self.assertFalse(sentinel.exists())
            if intent=='old':self._mark_exit(self.old)
            elif intent=='new':self._mark_exit(self.new)
            elif intent=='stop':(self.base/'private-owner-stop').write_text('owner stop')
        self.assertEqual(self.children[0].wait(timeout=10), 0)
        if intent=='ordinary':
            # Windows execv may retire the trampoline handle before the new
            # interpreter has reached the synthetic entry's first file write.
            deadline = time.monotonic()+5
            while not sentinel.is_file() and time.monotonic()<deadline:time.sleep(.025)
            self.assertTrue(sentinel.is_file(), log.read_text(encoding='utf-8', errors='replace'))
            proof = json.loads(sentinel.read_text())
            self.assertEqual(proof['automatic'], '1')
            self.assertEqual(proof['argv'][1:], ['--root', str(self.old)])
        else:
            time.sleep(.15)
            self.assertFalse(sentinel.exists(), 'Late owner intent must suppress the old entry itself')
        print('EXIT_RECOVERY_PROOF '+json.dumps({'intent':intent, 'lease_wait_observed':True,
            'previous_reopen_requested':True, 'synthetic_old_entry_executed':sentinel.exists(),
            'trampoline_pid':self.children[0].pid, 'trampoline_exit':self.children[0].returncode}), flush=True)

    def test_recovery_trampoline_reopens_only_the_synthetic_old_entry_without_owner_exit(self):
        self._late_intent('ordinary')

    def test_late_old_exit_while_trampoline_waits_lease_suppresses_exec(self):
        self._late_intent('old')

    def test_late_new_exit_while_trampoline_waits_lease_suppresses_exec(self):
        self._late_intent('new')

    def test_late_stop_while_trampoline_waits_lease_suppresses_exec(self):
        self._late_intent('stop')


class HandoffExit(_Fixture):
    def _owner(self):
        helper = self.new/'app/localharness/python_source_updater.py'
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text('# admitted private helper, never executed\n', encoding='utf-8')
        return SimpleNamespace(root=self.old, event=Mock(), close=Mock(), exiting=False,
            bootstrap=True, session_host=True, update_cancelled=Mock(return_value=False))

    def _handoff(self, owner):
        callbacks = []
        progress = SimpleNamespace(path=self.fx.progress.path, value={'id':'fault'})
        def timer(seconds, callback):
            self.assertEqual(seconds, 2)
            callbacks.append(callback)
            return SimpleNamespace(start=lambda:None)
        stack = ExitStack()
        self.addCleanup(stack.close)
        launch = stack.enter_context(patch.object(updates.subprocess, 'Popen', return_value=SimpleNamespace(pid=12345)))
        stack.enter_context(patch.object(updates, 'await_admission', return_value={'pid':12345}))
        stack.enter_context(patch.object(updates.threading, 'Timer', side_effect=timer))
        exited = stack.enter_context(patch.object(updates.os, '_exit'))
        observers = stack.enter_context(patch.object(session_host, 'retire_observers'))
        stopped = stack.enter_context(patch.object(migration_lifecycle, 'stop'))
        quiet = stack.enter_context(patch.object(migration_lifecycle, 'quiescent'))
        return SimpleNamespace(callbacks=callbacks, launch=launch, exited=exited, observers=observers,
            stopped=stopped, quiet=quiet, invoke=lambda:updates._launch_handoff(
                owner, self.fx.staging, self.new, Path(sys.executable), progress))

    def _assert_no_timer_effects(self, state, owner):
        owner.close.assert_not_called()
        state.observers.assert_not_called()
        state.stopped.assert_not_called()
        state.quiet.assert_not_called()
        state.exited.assert_not_called()

    def test_exiting_owner_cannot_launch_a_handoff_helper_or_exit_timer(self):
        owner = self._owner(); owner.exiting = True
        state = self._handoff(owner)
        with self.assertRaises(InterruptedError):state.invoke()
        state.launch.assert_not_called()
        self.assertEqual(state.callbacks, [])
        self._assert_no_timer_effects(state, owner)

    def test_durable_exit_cannot_launch_a_handoff_helper_or_exit_timer(self):
        owner = self._owner(); self._mark_exit(self.old)
        state = self._handoff(owner)
        with self.assertRaises(InterruptedError):state.invoke()
        state.launch.assert_not_called()
        self.assertEqual(state.callbacks, [])
        self._assert_no_timer_effects(state, owner)

    def test_late_owner_exiting_makes_an_admitted_timer_inert(self):
        owner = self._owner()
        state = self._handoff(owner); state.invoke()
        self.assertEqual(len(state.callbacks), 1)
        owner.exiting = True
        state.callbacks[0]()
        state.launch.assert_called_once()
        self._assert_no_timer_effects(state, owner)

    def test_late_durable_exit_makes_an_admitted_timer_inert(self):
        owner = self._owner()
        state = self._handoff(owner); state.invoke()
        self.assertEqual(len(state.callbacks), 1)
        self._mark_exit(self.old)
        state.callbacks[0]()
        state.launch.assert_called_once()
        self._assert_no_timer_effects(state, owner)

    def test_manual_update_cancel_keeps_owner_alive_without_creating_exit_intent(self):
        owner = self._owner()
        state = self._handoff(owner); state.invoke()
        owner.update_cancelled.return_value = True
        state.callbacks[0]()
        self._assert_no_timer_effects(state, owner)
        self.assertFalse(session_exit.requested(self.old))


if __name__=='__main__':unittest.main()
