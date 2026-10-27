"""Real bounded command-group regressions; Forge integration runs on server only."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from command_process import CommandProcess


def command(root, delay):
    child = ("from pathlib import Path;import time;"
             f"p=Path({str(root)!r});(p/'ready').write_text('ready');"
             f"time.sleep({delay});(p/'written').write_text('done')")
    parent = f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{child!r}])"
    parts = [sys.executable, '-c', parent]
    return subprocess.list2cmdline(parts) if os.name == 'nt' else shlex.join(parts)


def until(fn, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if value := fn():return value
        time.sleep(.02)
    raise AssertionError('Condition not observed')


class CommandCustody(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def spawn(self, delay):
        proc = CommandProcess(command(self.root, delay), shell=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.stop_tree)
        until(lambda: (self.root/'ready').exists())
        until(proc._leader_exited)
        return proc

    def test_successful_shell_exit_does_not_release_live_descendant(self):
        proc = self.spawn(10)
        self.assertIsNone(proc.poll())
        with self.assertRaises(subprocess.TimeoutExpired):proc.wait(.15)
        stopped, note = proc.stop_tree()
        self.assertTrue(stopped, note)
        self.assertIsNotNone(proc.poll())
        self.assertFalse((self.root/'written').exists())

    def test_natural_descendant_completion_releases_group(self):
        proc = self.spawn(.5)
        self.assertEqual(proc.wait(5), 0)
        self.assertEqual((self.root/'written').read_text(), 'done')

    @unittest.skipIf(os.name == 'nt', 'POSIX child wait identity')
    def test_lost_unreaped_child_authority_never_signals_a_pid(self):
        proc = self.spawn(10)
        with mock.patch.object(proc, '_leader_exited', side_effect=ChildProcessError('lost custody')), \
             mock.patch('command_process.os.killpg') as signal_group:
            stopped, note = proc.stop_tree()
            self.assertFalse(stopped)
            signal_group.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX census')
    def test_unknown_census_keeps_command_unsettled(self):
        proc = self.spawn(10)
        with mock.patch('command_process.group_live', return_value=None), \
             mock.patch('command_process.os.killpg') as signal_group:
            self.assertIsNone(proc.poll())
            self.assertFalse(proc.stop_tree()[0])
            signal_group.assert_not_called()


@unittest.skipIf(os.name == 'nt', 'Forge process suites run on the owned Linux stand')
class ForgeCustody(unittest.TestCase):
    def test_successful_shell_keeps_root_busy_until_group_stop(self):
        self.exercise(False)

    def test_detached_shell_keeps_root_busy_until_group_stop(self):
        self.exercise(True)

    def exercise(self, detached):
        import contextlib
        import forge
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            root = Path(temp);tid = 'code-12345678'
            for name, value in [('TASKS_DIR', root/'tasks'), ('BASE', root),
                                ('_RUNNERS', {}), ('_event', mock.Mock()),
                                ('_task_run_context', lambda _: {}),
                                ('_remote_client', lambda _: None)]:
                stack.enter_context(mock.patch.object(forge, name, value))
            forge._save_task({'id':tid, 'root':str(root), 'status':'active',
                              'goal':'test', 'max_active_workers':4})
            outcomes = [];errors = []
            def run():
                try:
                    if detached:outcomes.append(forge.process(tid, 'start', command=command(root, 10), timeout=8))
                    else:outcomes.append(forge.run(tid, command(root, 10), timeout=8))
                except BaseException as exc:errors.append(exc)
            thread = threading.Thread(target=run);thread.start()
            try:
                until(lambda:(root/'ready').exists())
                if detached:
                    directory = next((forge._task_dir(tid)/'processes').iterdir())
                    def exited():
                        state = forge._read_json(directory/'state.json', {}) or {}
                        pid = state.get('child_pid')
                        return pid and (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[0] == 'Z'
                    until(exited)
                else:
                    proc = next(iter(forge._RUNNERS.values()))
                    until(proc._leader_exited)
                units = forge._units(tid, 'processes')
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0]['status'], 'running')
                self.assertIn('shared-root writer', forge.checkpoint(tid))
                directory = forge._unit_dir(tid, 'processes', units[0]['id'])
                self.assertFalse((directory/'result.json').exists())
                result = forge.control(tid, 'stop')
                self.assertEqual(result['status'], 'settled', result)
                thread.join(5)
                self.assertFalse(thread.is_alive());self.assertFalse(errors, errors)
                self.assertFalse((root/'written').exists())
                self.assertEqual(forge._units(tid, 'processes')[0]['status'], 'stopped')
            finally:
                forge.control(tid, 'stop')
                for proc in list(forge._RUNNERS.values()):
                    if isinstance(proc, CommandProcess):proc.stop_tree()
                    else:proc.wait(5)
                thread.join(10)


if __name__ == '__main__':unittest.main()
