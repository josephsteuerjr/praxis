"""Cancellation recovery through real Forge fences, never new critic admission."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t_self_review_192 as legacy
import t_self_review_integration_192 as integrated

self_review = legacy.self_review


class Cancellation(unittest.TestCase):
    setUp = legacy.ReviewTests.setUp
    request = legacy.ReviewTests.request
    result = legacy.ReviewTests.result

    def test_fence_write_failure_is_durable_unknown_then_recovers_while_paused(self):
        row = self.request()
        self.service.tick()
        directory = self.forge._unit_dir(row['task_id'], 'agents', row['critics'][0]['unit_id'])
        atomic = self.forge._atomic_json
        observations = []

        def fail_fence(path, data):
            if Path(path).name == 'stop.json':
                observations.append(self.service.status(row['id'])['execution'])
                raise OSError('injected stop fence write failure')
            return atomic(path, data)

        with mock.patch.object(self.forge, '_atomic_json', side_effect=fail_fence):
            cancelled = self.service.cancel(row['id'])
        self.assertEqual(observations, ['unknown'])
        self.assertTrue(cancelled['cancel_requested'])
        self.assertEqual(cancelled['execution'], 'unknown')
        self.assertIn('OSError', cancelled['critics'][0]['stop_error'])
        self.assertFalse((directory / 'stop.json').exists())
        with self.assertRaises(ValueError):
            self.request(retry=True)
        path = self.result(row)
        canonical_result = path.read_bytes()
        recovered = self_review.Service(self.root / 'home', self.code, self.app.parent, self.forge)
        alive = {'value': True}

        def kill(*args, **kwargs):
            alive['value'] = False
            return 'остановлен'

        with mock.patch.object(self.forge, '_pid_alive', side_effect=lambda *args: alive['value']), \
             mock.patch.object(self.forge, '_kill_tree', side_effect=kill), \
             mock.patch.object(self.forge, 'self_review_task', side_effect=AssertionError('no admission on cancellation')):
            recovered.tick(paused=True)
        final = recovered.status(row['id'])
        self.assertEqual(final['execution'], 'cancelled')
        self.assertTrue(final['critics'][0]['cancel_confirmed'])
        self.assertEqual(self_review._read(directory / 'stop.json')['state'], 'settled')
        self.assertEqual(path.read_bytes(), canonical_result)
        self.assertEqual(len(self.spawns), 1)

    def test_unfenced_terminal_result_is_not_cancellation_proof(self):
        row = self.request()
        self.service.tick()
        self.result(row)
        with mock.patch.object(self.forge, 'agent', return_value='Stop not fenced: busy'):
            result = self.service.cancel(row['id'])
        self.assertEqual(result['execution'], 'unknown')
        self.assertFalse(result['critics'][0]['cancel_confirmed'])

    def test_one_stop_failure_does_not_skip_other_critics(self):
        self.service.settings({'max_parallel': 2})
        row = self.request(depth='deep')
        self.service.tick()
        attempted = []

        def stop(task_id, action, agent_id):
            attempted.append(agent_id)
            if agent_id == row['critics'][0]['unit_id']:
                raise OSError('first stop failed')
            directory = self.forge._unit_dir(task_id, 'agents', agent_id)
            self_review._write(directory / 'stop.json', {'state': 'settled'})
            return 'settled'

        with mock.patch.object(self.forge, 'agent', side_effect=stop):
            result = self.service.cancel(row['id'])
        self.assertEqual(len(attempted), 2)
        self.assertEqual(result['execution'], 'unknown')
        self.assertTrue(result['critics'][1]['cancel_confirmed'])

    def test_legacy_false_cancelled_record_is_reconciled(self):
        row = self.request()
        self.service.tick()
        row = self.service.status(row['id'])
        row.update(cancel_requested=True, execution='cancelled')
        self_review._write(self.service._path(row['id']), row)
        with self.assertRaises(ValueError):
            self.request(retry=True)
        with mock.patch.object(self.forge, 'agent', side_effect=OSError('still unavailable')):
            self.service.tick(paused=True)
        self.assertEqual(self.service.status(row['id'])['execution'], 'unknown')
        self.assertEqual(len(self.spawns), 1)


class PausedIntegration(unittest.TestCase):
    setUp = integrated.Runtime.setUp
    make_runtime = integrated.Runtime.make_runtime
    tick = integrated.Runtime.tick

    def test_failed_cancel_command_recovers_without_requeued_command_or_spawn(self):
        integrated.integration.command(self.tree, {'action': 'request'})
        self.tick()
        row = self.runtime.service.status()[0]
        integrated.integration.command(self.tree, {'action': 'cancel', 'id': row['id']})
        with mock.patch.object(self.forge, 'agent', side_effect=OSError('fence unavailable')):
            self.tick(paused=True)
        self.assertEqual(self.runtime.service.status(row['id'])['execution'], 'unknown')
        self.runtime = self.make_runtime()

        def stop(task_id, action, agent_id):
            self.assertEqual(action, 'stop')
            directory = self.forge._unit_dir(task_id, 'agents', agent_id)
            self_review._write(directory / 'stop.json', {'state': 'settled'})
            return 'settled'

        with mock.patch.object(self.forge, 'agent', side_effect=stop):
            self.tick(paused=True)
        self.assertEqual(self.runtime.service.status(row['id'])['execution'], 'cancelled')
        self.assertEqual(len(self.forge.spawned), 1)


if __name__ == '__main__':
    unittest.main()
