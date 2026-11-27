"""Ordinary Forge work orders: actual tool admission, provider inputs and Resume.

Only the model, OS process launch and writer-exit proof are fixtures. Inputs,
worker dispatch, queued request, checkpoints, Resume, and tool evidence are real.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack
import json
import os
import unittest
from unittest import mock

import forge
import forge_runtime
import forge_worker
from test_forge_team_consumer_2610 import reply, tool
import test_forge_wave2_server as fixtures


class Interrupted(BaseException):
    pass


class WorkerContext(unittest.TestCase):
    setUp = fixtures.ServerWave2.setUp
    unit = fixtures.ServerWave2.unit
    base_commit = '1' * 40
    after_base = '2' * 40
    original = 'original selected text — \u03bb\r\n'
    updated = 'current source, not the captured revision\n'

    def record_for(self, name):
        return json.loads(forge.inspect(self.tid, 'source_revision', path=name))

    def configure(self, **changes):
        task = forge.get(self.tid)
        task.update(changes)
        forge._save_task(task)

    def path(self, unit_id):
        return forge._unit_dir(self.tid, 'agents', unit_id) / 'request.json'

    def provider_spy(self, responses, inputs):
        def call(_role, **kwargs):
            inputs.append(copy.deepcopy(kwargs))
            return responses.pop(0)
        return call

    def crash_at_model_ready(self):
        save = forge_runtime.Journal.save
        def crash(journal, boundary, phase, **fields):
            save(journal, boundary, phase, **fields)
            if boundary == 'model_ready' and journal.state['iteration'] and journal.state['trace']:
                raise Interrupted()
        return mock.patch.object(forge_runtime.Journal, 'save', crash)

    def run_fresh(self, path, responses, inputs):
        with mock.patch.object(forge_worker.llm, 'chat', side_effect=self.provider_spy(responses, inputs)):
            return forge_worker.run(path)

    def observe_resume(self, path, generation):
        with mock.patch.object(forge, '_writer_ended', side_effect=lambda directory: directory == path.parent):
            resumed = forge.control(self.tid, 'resume', path.parent.name, expected_generation=generation)
        self.assertEqual(resumed['status'], 'queued', resumed)
        return self.path(resumed['agent_id'])

    def guard_effects(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(forge_worker, '_route_identity', return_value='fixed-test-route'))
        self.spawn = stack.enter_context(mock.patch.object(forge, '_spawn_runner', return_value=271828))
        stack.enter_context(mock.patch.object(forge, '_pid_alive', return_value=True))
        stack.enter_context(mock.patch.object(forge, 'observe_liveness'))
        stack.enter_context(mock.patch.object(forge, 'emit_unit_event'))
        actual_inspect = forge.inspect
        def inspect(task_id, action, **kwargs):
            if action == 'orientation':
                raise AssertionError('do not read coordinator orientation into an ordinary worker')
            if action == 'diff':
                return 'fixture changed-file view'
            return actual_inspect(task_id, action, **kwargs)
        stack.enter_context(mock.patch.object(forge, 'inspect', side_effect=inspect))

    def test_actual_nested_first_and_restored_model_inputs_are_scoped(self):
        # Agent import configures subprocess env; this test does not retain it.
        with mock.patch.dict(os.environ):
            from agent import tool_coding_agent, tool_coding_inspect

        self.guard_effects()
        self.configure(goal='original governed task', success_criteria=['original task criterion'],
                       verify_commands=['verify synthetic assignment'], base_commit=self.base_commit,
                       max_active_workers=1)
        forge._atomic_json(forge._task_dir(self.tid) / 'coordinator-checkpoint.json',
                           {'private_chat': 'INJECTED COORDINATOR SECRET MARKER'})
        (self.root / 'selected.txt').write_bytes(self.original.encode('utf-8'))
        (self.root / 'parent-only.txt').write_text('PARENT ONLY MARKER', encoding='utf-8')
        (self.root / 'never-granted.txt').write_text('UNSELECTED MARKER', encoding='utf-8')
        grant = []
        for file in ('selected.txt', 'parent-only.txt'):
            metadata = json.loads(tool_coding_inspect(self.tid, 'source_revision', path=file))
            grant.append({key: metadata[key] for key in ('path', 'sha256')})
        parent = self.path('agent-aabbccdd')
        answer = tool_coding_agent(self.tid, 'spawn', agent_id=parent.parent.name, role='scout',
                                   brief='parent brief marker', phase='map',
                                   success_criteria=['parent local criterion'], sources=grant)
        self.assertEqual(self.spawn.call_count, 1, answer)
        self.configure(goal='changed root goal must not replace parent contract',
                       success_criteria=['changed root criterion'], base_commit=self.after_base)
        parent_input = []
        with self.crash_at_model_ready(), self.assertRaises(Interrupted):
            self.run_fresh(parent, [tool('delegate', action='spawn', role='reviewer', brief='child brief',
                                         phase='verify', success_criteria=['child local criterion'],
                                         sources=grant[:1])], parent_input)
        self.assertEqual(self.spawn.call_count, 1)
        siblings = [d / 'request.json' for d in parent.parents[1].iterdir() if d != parent.parent]
        self.assertEqual(len(siblings), 1)
        child, = siblings
        child_request = json.loads(child.read_text('utf-8'))
        self.assertEqual(child_request['status'], 'queued')
        self.assertEqual(child_request['worker_context']['ancestor_path'], [parent.parent.name])
        self.assertEqual([r['path'] for r in child_request['worker_context']['sources']], ['selected.txt'])
        self.assertIn('PARENT ONLY MARKER', parent_input[0]['messages'][0]['content'])

        # The already queued child must keep the original selected bytes, while
        # a live tool read still returns the changed workspace as a separate fact.
        (self.root / 'selected.txt').write_bytes(self.updated.encode('utf-8'))
        self.configure(max_active_workers=4)
        self.assertEqual([r['agent_id'] for r in forge.tick_queued(self.tid)], [child.parent.name])
        child_input = []
        with self.crash_at_model_ready(), self.assertRaises(Interrupted):
            self.run_fresh(child, [tool('inspect', action='read', path='selected.txt')], child_input)
        first, = child_input
        for item in ('original governed task', 'original task criterion', self.base_commit,
                     'verify synthetic assignment', 'parent local criterion', 'child local criterion',
                     f'{parent.parent.name} → {child.parent.name}'):
            self.assertIn(item, first['system'])
        self.assertNotIn(self.after_base, first['system'])
        self.assertNotIn('changed root goal', first['system'])
        user_message = first['messages'][0]['content']
        sources = json.loads(user_message.split('\n')[-1])
        self.assertEqual(sources[0]['text'], self.original)
        self.assertEqual(sources[0]['sha256'], grant[0]['sha256'])
        wire = json.dumps(first)
        for excluded in ('PARENT ONLY MARKER', 'UNSELECTED MARKER', 'INJECTED COORDINATOR SECRET MARKER',
                         'parent brief marker', self.updated.strip()):
            self.assertNotIn(excluded, wire)
        self.assertNotIn('edit', {row['name'] for row in first['tools']})
        self.assertIn('sources', next(row for row in first['tools'] if row['name'] == 'delegate')['input_schema']['properties'])

        info = forge_runtime.continuation_info(child.parent)
        self.assertTrue(info['safe'])
        cp = (child.parent / 'checkpoint.json').read_bytes()
        successor = self.observe_resume(child, info['generation'])
        self.assertEqual([r['agent_id'] for r in forge.tick_queued(self.tid)], [successor.parent.name])
        original_request = successor.read_bytes()
        tampered = json.loads(original_request)
        tampered['worker_context']['sources'][0]['text'] = 'other source falsely under old digest'
        forge._atomic_json(successor, tampered)
        with mock.patch.object(forge_worker.llm, 'chat', side_effect=AssertionError('no provider dispatch')) as model:
            self.assertEqual(forge_worker.run(successor), 2)
            model.assert_not_called()
        self.assertTrue((successor.parent / 'resume.json').exists(), 'mismatch must not spend the Resume receipt')
        self.assertFalse((successor.parent / 'checkpoint.json').exists())
        successor.write_bytes(original_request)
        resume_input = []
        self.assertEqual(self.run_fresh(successor, [reply('restored result')], resume_input), 0)
        continued, = resume_input
        self.assertEqual(continued['system'], first['system'])
        self.assertEqual(continued['messages'][:1], first['messages'])
        self.assertEqual(continued['tools'], first['tools'])
        self.assertIn(self.updated.strip(), continued['messages'][-1]['content'][0]['content'])
        result = json.loads((successor.parent / 'result.json').read_text('utf-8'))
        self.assertEqual(result['tool_calls'], 1, 'do not replay the already settled read')
        self.assertEqual((child.parent / 'checkpoint.json').read_bytes(), cp)

    def test_missing_parent_context_does_not_borrow_wider_task_or_auto_share(self):
        self.guard_effects()
        self.configure(goal='GOVERNING PRIVATE MARKER', success_criteria=['PRIVATE CRITERION MARKER'],
                       base_commit=self.base_commit)
        directory, _ = self.unit(role='scout')  # legacy parent request; no captured context
        path = directory / 'request.json'
        answer = forge.agent(self.tid, 'spawn', brief='local brief only', role='scout',
                             agent_id='agent-bbbbbbbb', _parent_request_path=str(path))
        self.assertEqual(self.spawn.call_count, 1, answer)
        child = self.path('agent-bbbbbbbb')
        child_request = json.loads(child.read_text('utf-8'))
        self.assertIsNone(child_request['worker_context']['governing'])
        self.assertIsNone(child_request['goal'])
        calls = []
        self.assertEqual(self.run_fresh(child, [reply('narrow assignment')], calls), 0)
        self.assertIn('verified parent branch has no captured governing contract', calls[0]['system'])
        self.assertNotIn('GOVERNING PRIVATE MARKER', json.dumps(calls))
        self.assertNotIn(self.base_commit, json.dumps(calls))
        self.assertIn('No source bytes were selected', calls[0]['messages'][0]['content'])

    def test_ordinary_grants_are_exact_subsets_and_refuse_changed_or_private_sources(self):
        self.guard_effects()
        (self.root / 'selected.txt').write_text('initial', encoding='utf-8')
        (self.root / 'ungranted.txt').write_text('not granted', encoding='utf-8')
        (self.root / 'private').mkdir()
        (self.root / 'private' / 'record.txt').write_text('private synthetic marker', encoding='utf-8')
        selection = self.record_for('selected.txt')
        unknown = self.record_for('ungranted.txt')
        grant = {key: selection[key] for key in ('path', 'sha256')}
        other = {key: unknown[key] for key in ('path', 'sha256')}
        root = self.path('agent-aabbccdd')
        answer = forge.agent(self.tid, 'spawn', brief='grant', role='scout', agent_id=root.parent.name, sources=[grant])
        self.assertEqual(self.spawn.call_count, 1, answer)
        # A source grant is never automatically forwarded to a fresh nested request.
        child = self.path('agent-bbbbbbbb')
        answer = forge.agent(self.tid, 'spawn', brief='explicitly no sources', role='reviewer',
                             agent_id=child.parent.name, _parent_request_path=str(root))
        self.assertEqual(self.spawn.call_count, 2, answer)
        self.assertEqual(json.loads(child.read_text('utf-8'))['worker_context']['sources'], [])
        refused = forge.agent(self.tid, 'spawn', brief='must refuse', role='reviewer',
                              sources=[other], _parent_request_path=str(root))
        self.assertIn('not granted', refused)
        (self.root / 'selected.txt').write_text('changed', encoding='utf-8')
        changed = forge.agent(self.tid, 'spawn', brief='must refuse', role='scout',
                              sources=[grant], _parent_request_path=str(root))
        self.assertIn('source revision changed', changed)
        for illegal in ('../outside.txt', str(self.root / 'selected.txt'), 'private/record.txt',
                        '.git/config', 'selected.txt:alternate'):
            with self.subTest(path=illegal):
                self.assertEqual(forge.inspect(self.tid, 'source_revision', path=illegal).outcome, 'refused')
        self.assertEqual(self.spawn.call_count, 2)

    def test_existing_ordinary_checkpoint_resumes_legacy_model_tool_schema(self):
        self.guard_effects()
        directory, _ = self.unit(role='scout')
        path = directory / 'request.json'
        before = []
        with mock.patch.object(forge_worker, '_system', return_value='legacy frozen system'), \
                self.crash_at_model_ready(), self.assertRaises(Interrupted):
            self.run_fresh(path, [tool('inspect', action='read', path='code.py')], before)
        info = forge_runtime.continuation_info(directory)
        self.assertTrue(info['safe'])
        successor = self.observe_resume(path, info['generation'])
        self.assertEqual([r['agent_id'] for r in forge.tick_queued(self.tid)], [successor.parent.name])
        after = []
        self.assertEqual(self.run_fresh(successor, [reply('legacy resumed')], after), 0)
        self.assertEqual(after[0]['system'], 'legacy frozen system')
        self.assertEqual(after[0]['tools'], before[0]['tools'])
        delegate = next(row for row in after[0]['tools'] if row['name'] == 'delegate')
        self.assertNotIn('sources', delegate['input_schema']['properties'])
        self.assertIn('answer = 1', after[0]['messages'][-1]['content'][0]['content'])


if __name__ == '__main__':
    unittest.main()
