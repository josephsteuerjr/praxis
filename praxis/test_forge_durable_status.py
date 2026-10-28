"""Read-only task metadata survives disposal of its working tree."""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import forge


class DurableStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.tasks = self.base / 'tasks'
        self.task_id = 'code-durable-fixture'
        self.directory = self.tasks / self.task_id
        self.directory.mkdir(parents=True)
        self.root = self.base / 'removed-worktree'
        self.task = dict(id=self.task_id, goal='durable goal', status='done',
                         root=str(self.root), worktree_root=str(self.root),
                         isolation='git-worktree')
        self.save()
        (self.directory / 'events.jsonl').write_text(
            json.dumps(dict(at='now', kind='task_finished', summary='durable receipt')) + '\n',
            encoding='utf-8')
        for name in ('agents', 'processes', 'verifications'):
            unit = self.directory / name / 'unit-fixture'
            unit.mkdir(parents=True)
            (unit / 'result.json').write_text('{"status": "done"}', encoding='utf-8')
        self.patcher = patch.object(forge, 'TASKS_DIR', self.tasks)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def save(self):
        (self.directory / 'task.json').write_text(json.dumps(self.task), encoding='utf-8')

    def test_done_status_reads_metadata_without_workspace_probes_or_writes(self):
        before = {p.relative_to(self.directory): p.read_bytes()
                  for p in self.directory.rglob('*') if p.is_file()}
        with patch.object(forge, '_git_root', side_effect=AssertionError('git probe')), \
             patch.object(forge, '_remote_client', side_effect=AssertionError('remote probe')), \
             patch.object(forge.forge_swarm, 'refresh', side_effect=AssertionError('swarm write')):
            out = forge.inspect(self.task_id, 'status')
        for text in ('[done]', 'durable goal', 'durable receipt', 'agents: done 1',
                     'processes: done 1', 'verification: done 1', '⚠', 'недоступно'):
            self.assertIn(text, out)
        self.assertNotIn('abandon', out)
        self.assertFalse(self.root.exists())
        self.assertEqual(before, {p.relative_to(self.directory): p.read_bytes()
                                 for p in self.directory.rglob('*') if p.is_file()})

    def test_history_remains_json_and_honors_limit_for_terminal_task(self):
        with patch.object(forge, '_remote_client', side_effect=AssertionError('remote probe')):
            rows = json.loads(forge.inspect(self.task_id, 'history', end=1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['summary'], 'durable receipt')

    def test_active_missing_root_also_exposes_status_and_history(self):
        self.task['status'] = 'active'
        self.save()
        out = forge.inspect(self.task_id, 'status')
        self.assertIn('[active]', out)
        self.assertIn('⚠', out)
        self.assertIn('durable receipt', out)
        self.assertEqual(len(json.loads(forge.inspect(self.task_id, 'history'))), 1)
        self.assertEqual(forge.get(self.task_id)['status'], 'active')

    def test_file_and_mutation_operations_still_require_root(self):
        for status in ('active', 'done'):
            self.task['status'] = status
            self.save()
            err = forge._task_root(self.task_id)[2]
            for action in ('read', 'list', 'search', 'diff', 'model', 'orientation'):
                with self.subTest(status=status, action=action):
                    self.assertEqual(forge.inspect(self.task_id, action, path='x'), err)
            # Terminal-task admission now refuses before resolving its root;
            # active file operations still report the missing worktree exactly.
            edit = forge.edit(self.task_id, 'write', path='x', content='no')
            self.assertEqual(edit, 'Edit refused: task is terminal or missing' if status == 'done' else err)
            self.assertFalse(self.root.exists())

    def test_unknown_task_still_fails(self):
        for action in ('status', 'history'):
            self.assertIn('нет coding-задачи', forge.inspect('code-absent', action))

    def test_public_session_and_inspect_routes(self):
        # Execute the actual thin API wrappers without importing the full agent runtime.
        tree = ast.parse((Path(forge.__file__).parent / 'agent.py').read_text(encoding='utf-8'))
        names = {'tool_coding_session', 'tool_coding_inspect'}
        module = ast.Module(body=[n for n in tree.body
                                 if isinstance(n, ast.FunctionDef) and n.name in names],
                            type_ignores=[])
        namespace = {'forge': forge}
        exec(compile(module, 'agent.py', 'exec'), namespace)
        self.assertIn('[done]', namespace['tool_coding_session']('status', task_id=self.task_id))
        self.assertIn('[done]', namespace['tool_coding_inspect'](self.task_id, 'status'))
        self.assertEqual(len(json.loads(namespace['tool_coding_inspect'](
            self.task_id, 'history'))), 1)


if __name__ == '__main__':
    unittest.main()
