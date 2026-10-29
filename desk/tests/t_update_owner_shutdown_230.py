"""Self-update teardown with a real Controller and real OS data-tree lease.

Only helper launch/timer/process census are isolated. Linux/macOS run real
flock probes; Windows uses its real share-exclusive probe for the same order.
No user profile, service, process or update payload is touched.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import python_updates as updates, migration_lifecycle
from localharness import migration_posix, migration_windows, session_host
from localharness.desktop_supervisor import _tree_hold
from localharness.python_controller import Controller


class UpdateOwnerShutdown(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='update-shutdown-', dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'old'
        self.tree = self.base / 'data'
        self.root.mkdir(); self.tree.mkdir()
        (self.root / 'helene.json').write_text(json.dumps({'tree': str(self.tree), 'mode': 'local'}), encoding='utf-8')
        self.destination = self.base / '.helene-update-test'
        self.new = self.destination / 'Helene'
        helper = self.new / 'app/localharness/python_source_updater.py'
        helper.parent.mkdir(parents=True)
        helper.write_text('# never executed\n', encoding='utf-8')
        self.owner = Controller(self.root, lambda *unused: None)
        self.owner.bootstrap = True
        self.owner.session_host = True
        self.owner.update_cancelled = lambda: False
        self.events = []
        self.ready = threading.Event()
        self.thread_errors = []
        def supervise():
            try:
                with _tree_hold(self.tree) as clean:
                    self.ready.set()
                    self.owner.stop.wait(10)
                    clean()
            except BaseException as exc:
                self.thread_errors.append(exc)
                self.ready.set()
        self.owner.thread = threading.Thread(target=supervise, daemon=True)
        self.owner.thread.start()
        self.addCleanup(self.close_owner)
        self.assertTrue(self.ready.wait(5))
        self.assertEqual(self.thread_errors, [])
        self.assertFalse(migration_windows.owner_lease_available(self.tree / '.desktop-owner.lock'))
        self.progress = SimpleNamespace(path=self.base / 'progress.json', value={'id': 'test'})
        self.child = SimpleNamespace(pid=12345)

    def close_owner(self):
        self.owner.close()
        self.assertEqual(self.thread_errors, [])

    def callback(self, *, stop_error=None):
        """Capture production timer callback without waiting or starting a helper."""
        timers = []
        def timer(seconds, callback):
            self.assertEqual(seconds, 2)
            timers.append(callback)
            return SimpleNamespace(start=lambda: None)
        real_stop = migration_posix.stop
        def stop(root):
            self.events.append('external-stop')
            if stop_error:
                raise RuntimeError(stop_error)
            return real_stop(root)
        real_close = self.owner.close
        def close():
            self.events.append('controller-close')
            return real_close()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(updates.subprocess, 'Popen', return_value=self.child))
        stack.enter_context(patch.object(updates, 'await_admission', return_value={'pid': self.child.pid}))
        stack.enter_context(patch.object(updates.threading, 'Timer', side_effect=timer))
        stack.enter_context(patch.object(updates.os, '_exit', side_effect=lambda code: self.events.append(('exit', code))))
        stack.enter_context(patch.object(session_host, 'retire_observers', side_effect=lambda root: self.events.append('observers')))
        stack.enter_context(patch.object(self.owner, 'close', side_effect=close))
        stack.enter_context(patch.object(migration_lifecycle, 'stop', side_effect=stop, create=True))
        stack.enter_context(patch.object(migration_lifecycle, 'quiescent', side_effect=migration_posix.quiescent, create=True))
        # No service or selected external processes exist in this synthetic tree.
        # Keep real quiescent/discover/OS lease probe, never fake lease success.
        stack.enter_context(patch.object(migration_posix, 'service', return_value=None))
        stack.enter_context(patch.object(migration_posix, '_processes', return_value=[]))
        stack.enter_context(patch.object(migration_posix, 'owner_lease_available', side_effect=migration_windows.owner_lease_available))
        updates._launch_handoff(self.owner, self.destination, self.new, Path(sys.executable), self.progress)
        self.assertEqual(len(timers), 1)
        self.assertFalse(self.owner.stop.is_set(), 'admission must not itself release the old owner')
        self.assertNotIn(('exit', 0), self.events)
        return timers[0]

    def test_real_owner_lease_released_before_bootstrap_external_quiescence(self):
        self.callback()()
        self.assertEqual(self.events, ['observers', 'controller-close', 'external-stop', ('exit', 0)])
        self.assertTrue(migration_windows.owner_lease_available(self.tree / '.desktop-owner.lock'))
        self.assertEqual((self.tree / '.desktop-owner.lock').read_bytes(), b'clean\n')
        self.assertIsNone(self.owner.thread)

    def test_cancelled_handoff_keeps_real_owner_and_never_exits(self):
        self.owner.update_cancelled = Mock(return_value=True)
        callback = self.callback()
        callback()
        self.assertEqual(self.events, [])
        self.assertFalse(migration_windows.owner_lease_available(self.tree / '.desktop-owner.lock'))

    def test_controller_custody_failure_does_not_stop_external_or_exit(self):
        callback = self.callback()
        # A known uncertain handle remains owned, and Controller.close must fail.
        self.owner.helpers.append(Mock(handle=object()))
        from localharness import desktop_supervisor
        with patch.object(desktop_supervisor, 'stop_children', side_effect=RuntimeError('child custody unknown')):
            with self.assertRaisesRegex(RuntimeError, 'child custody unknown'):
                callback()
        self.owner.helpers.clear()
        self.owner.error = None
        self.assertEqual(self.events, ['observers', 'controller-close'])

    def test_external_stop_failure_does_not_exit_or_bless_quiescence(self):
        with self.assertRaisesRegex(RuntimeError, 'external writer still alive'):
            self.callback(stop_error='external writer still alive')()
        self.assertEqual(self.events, ['observers', 'controller-close', 'external-stop'])
        self.assertTrue(migration_windows.owner_lease_available(self.tree / '.desktop-owner.lock'))

    def test_other_profile_lease_still_blocks_after_this_owner_closes(self):
        other = self.base / 'other-data'; other.mkdir()
        folder = self.root / 'agents' / 'other'; folder.mkdir(parents=True)
        (folder / 'helene.json').write_text(json.dumps({'mode': 'local', 'tree': str(other)}), encoding='utf-8')
        with _tree_hold(other):
            with self.assertRaisesRegex(RuntimeError, 'держит папку данных'):
                self.callback()()
            self.assertNotIn(('exit', 0), self.events)
        self.assertTrue(migration_windows.owner_lease_available(self.tree / '.desktop-owner.lock'))


if __name__ == '__main__':
    unittest.main()
