"""Cold admission budgets and fail-closed cleanup, without native processes."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import darwin_process as darwin
from localharness import posix_process as posix


class Admission(unittest.TestCase):
    def adapters(self):
        return ((darwin, darwin.DarwinChild, 'darwin', [
            {'state': 'ready'}, {'state': 'prepared', 'pid': 99},
            {'state': 'started', 'pid': 99}]),
            (posix, posix.PosixChild, 'linux', [
                {'state': 'ready'}, {'state': 'started', 'pid': 99}]))

    def spawn(self, adapter, child, platform, replies, process):
        with mock.patch.object(adapter.sys, 'platform', platform), \
             mock.patch.object(adapter.subprocess, 'Popen', return_value=process), \
             mock.patch.object(child, '_message', side_effect=replies) as message, \
             mock.patch.object(child, '_send') as send:
            result = child.spawn(['sentinel'], cwd='.', env={}, log='unused', stopped=lambda: False)
        return result, message, send

    def test_every_cold_admission_phase_uses_the_shared_budget(self):
        self.assertGreaterEqual(posix.GUARDIAN_START_TIMEOUT_SECONDS, 60)
        for adapter, child, platform, replies in self.adapters():
            with self.subTest(platform=platform):
                process = mock.Mock()
                result, message, send = self.spawn(adapter, child, platform, replies, process)
                self.assertEqual(result.pid, 99)
                self.assertEqual(message.call_args_list,
                    [mock.call(posix.GUARDIAN_START_TIMEOUT_SECONDS)] * len(replies))
                process.stdin.close.assert_not_called()
                process.wait.assert_not_called()
                self.assertEqual(send.call_args_list[-1], mock.call({'action':
                    'release-target' if platform == 'darwin' else 'start'}))

    def test_missing_reply_at_every_phase_refuses_and_requests_cleanup(self):
        for adapter, child, platform, replies in self.adapters():
            for phase in range(len(replies)):
                with self.subTest(platform=platform, phase=phase):
                    process = mock.Mock()
                    process.wait.return_value = 0
                    with self.assertRaisesRegex(RuntimeError, 'failed|refused|unconfirmed'):
                        self.spawn(adapter, child, platform, replies[:phase] + [None], process)
                    process.stdin.close.assert_called_once()
                    process.wait.assert_called_once()

    def test_unconfirmed_cleanup_stays_a_custody_fault(self):
        for adapter, child, platform, _ in self.adapters():
            with self.subTest(platform=platform):
                process = mock.Mock()
                process.wait.side_effect = subprocess.TimeoutExpired('guardian', 15)
                with self.assertRaises(posix.CustodyFault):
                    self.spawn(adapter, child, platform, [None], process)
                process.stdin.close.assert_called_once()

    def test_failed_darwin_cleanup_retains_the_child(self):
        process = mock.Mock()
        process.wait.return_value = 1
        with self.assertRaises(posix.CustodyFault) as caught:
            self.spawn(darwin, darwin.DarwinChild, 'darwin', [None], process)
        self.assertEqual(caught.exception.children[0].process, process)


if __name__ == '__main__':
    unittest.main()
