"""Bounded real tick discovery; synthetic journals, exact current PID, no model/spawn."""
import json
import os
from pathlib import Path
import time
import unittest
from unittest import mock
import forge
import forge_liveness as live
import test_forge_coordination_2610 as fixtures


class LivenessDiscovery(unittest.TestCase):
    setUp = fixtures.Coordination.setUp
    patch = fixtures.Coordination.patch
    unit = fixtures.Coordination.unit

    def put(self, path, row):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row), encoding='utf8')

    def test_terminal_history_is_not_rewritten_and_old_unfinished_child_is_discovered(self):
        self.put(self.directory / 'task.json', {**self.task, 'status': 'done'})
        for index in range(40):
            d = self.directory / 'agents' / f'agent-{index:08x}'
            self.put(d / 'request.json', {'id': d.name, 'task_id': self.tid, 'status': 'running'})
            self.put(d / 'result.json', {'status': 'done'})
        active = self.unit('agent-ffffffff')
        request = forge._read_json(active / 'request.json')
        request.update(supervisor_pid=os.getpid(), process_identity=live.capture(os.getpid()), created='2000-01-01')
        self.put(active / 'request.json', request)
        forge._QUEUE_OBSERVER.clear(); live._last.clear()
        with mock.patch.object(forge, '_atomic_json', wraps=forge._atomic_json) as writes:
            for _ in range(4):
                live._last.clear()  # Each bounded discovery tick is an observation interval.
                self.assertEqual(forge.tick_queued(), [])
        paths = [Path(call.args[0]) for call in writes.call_args_list]
        self.assertTrue(paths)
        self.assertEqual(set(paths), {active / 'liveness.json'})
        self.assertEqual(json.loads((active / 'liveness.json').read_bytes())['state'], 'alive')
        self.spawn.assert_not_called()
        # New unit under the already observed task remains discoverable.
        fresh = self.unit('agent-eeeeeeee', status='queued')
        with mock.patch.object(forge, '_admission_reason', return_value='synthetic pause'):
            forge.tick_queued()
        self.assertIn(fresh, forge._QUEUE_OBSERVER['current'])

    def test_result_does_not_retire_an_exact_still_alive_process(self):
        directory = self.unit()
        request = forge._read_json(directory / 'request.json')
        request.update(supervisor_pid=os.getpid(), process_identity=live.capture(os.getpid()))
        self.put(directory / 'request.json', request)
        root = directory.parents[2]
        self.assertEqual(live.refresh(root, self.put, {directory: request}, interval=0), set())
        self.put(directory / 'result.json', {'status': 'done'})
        self.assertEqual(live.refresh(root, self.put, {directory: request}, interval=0), set())
        self.assertEqual(json.loads((directory / 'liveness.json').read_bytes())['state'], 'alive')
        import psutil
        with mock.patch('psutil.Process', side_effect=psutil.NoSuchProcess(os.getpid())):
            self.assertEqual(live.refresh(root, self.put, {directory: request}, interval=0), {directory})
        self.assertEqual(json.loads((directory / 'liveness.json').read_bytes())['state'], 'exited')

    def test_terminal_missing_identity_retains_unknown_without_recurring_history_writes(self):
        directory = self.unit(result={'status': 'done'})
        request = forge._read_json(directory / 'request.json')
        root = directory.parents[2]
        retired = live.refresh(root, self.put, {directory: request}, interval=0)
        self.assertEqual(retired, {directory})
        self.assertEqual(json.loads((directory / 'liveness.json').read_bytes())['state'], 'unknown')
        with mock.patch.object(live, 'observe') as observe:
            live.refresh(root, self.put, {}, interval=0)
        observe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
