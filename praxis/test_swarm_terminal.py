"""Незавершённый результат освобождает слот; ни одного настоящего воркера."""
import _sandbox  # noqa: F401
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import forge
import forge_swarm
from core import subagents


class SwarmTerminal(unittest.TestCase):
    def test_real_tick_retains_stalled_and_blocks_dependent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, value in [('BASE', base), ('STATE_DIR', base / 'state'),
                                ('TASKS_DIR', base / 'state/tasks')]:
                replacement = patch.object(forge, name, value)
                replacement.start()
                self.addCleanup(replacement.stop)
            forge._save_task({'id': 'task', 'root': str(base), 'status': 'active',
                             'scope': 'self', 'goal': 'terminal scheduling'})
            root = forge._task_dir('task')
            unit = forge._unit_dir('task', 'agents', 'agent-aaa')
            forge._atomic_json(unit / 'request.json', {'id': 'agent-aaa', 'task_id': 'task',
                               'node_id': 'first', 'role': 'worker', 'status': 'running'})
            result = {"status": "stalled", "complete": False, "result": "частичный результат", "finished": "now"}
            (unit / "result.json").write_text(json.dumps(result), encoding="utf-8")
            forge.release_worker_root(unit / 'request.json')
            plan = forge_swarm.create(root, [
                {"id": "first", "brief": "первый"},
                {"id": "dependent", "brief": "зависимый", "deps": ["first"]},
                {"id": "independent", "brief": "независимый"},
            ], max_parallel=1)
            plan["nodes"][0].update(status="running", agent_id="agent-aaa")
            forge_swarm.save(root, plan)
            with patch.object(forge, '_spawn_runner', return_value=424242) as spawn, \
                    patch.object(forge, '_proc_started_at', return_value='fixture-birth'), \
                    patch.object(forge, '_pid_alive', return_value=True):
                out = forge.swarm("task", action="tick")
                self.assertIn("stalled", out)
                self.assertEqual(spawn.call_count, 1)
                loaded = forge_swarm.load(root)
                self.assertEqual([n["status"] for n in loaded["nodes"]], ["stalled", "pending", "running"])
                self.assertIn('first', loaded['nodes'][1]['queued_reason'])
                independent = loaded['nodes'][2]['agent_id']
                self.assertEqual(forge._read_json(forge._unit_dir('task', 'agents', independent)
                                                 / 'request.json')['node_id'], 'independent')
                # Terminal CAS refuses a redelivered different final, rather than
                # pretending a direct file overwrite is an admissible producer.
                import forge_worker
                self.assertFalse(forge_worker._publish_result(unit / 'request.json', 'task',
                                                              {'status': 'done'}))
                forge._atomic_json(forge._unit_dir('task', 'agents', independent) / 'result.json',
                                   {'status': 'done', 'result': 'independent complete'})
                forge.swarm("task", action="tick")
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual([n['status'] for n in forge_swarm.load(root)['nodes']],
                                 ['stalled', 'pending', 'awaiting_acceptance'])
                directory = forge._unit_dir('task', 'agents', independent)
                sha = forge._result_identity(directory / 'result.json')
                receipt = json.loads(forge.agent('task', 'disposition', agent_id=independent,
                                                result_sha256=sha, disposition='integrated'))
                self.assertEqual(receipt['result_sha256'], sha)
                forge.release_worker_root(directory / 'request.json')
                forge.swarm('task', 'tick')
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual([n["status"] for n in forge_swarm.load(root)["nodes"]], ["stalled", "pending", "done"])
                self.assertEqual(json.loads((unit / 'result.json').read_text(encoding='utf-8')), result)

    def test_event_retains_abi_and_names_incompleteness(self):
        payload = subagents.normalize("task", "agent", {"status": "stalled", "complete": False, "result": "ещё не всё"})
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["forge_status"], "stalled")
        line = subagents.invitation([payload])
        self.assertIn("без полного результата", line)
        self.assertNotIn("упал", line)
        self.assertIn("ещё не всё", line)


if __name__ == "__main__":
    unittest.main()
