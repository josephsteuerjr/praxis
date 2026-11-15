"""Termination is proved by current producer metadata, never translated words."""
import json
import signal
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import forge
import frame_language as fl
from test_forge_tree_1310 import WorkerTree


class TerminationEvidence(unittest.TestCase):
    # Borrow only the fixture, not the existing inherited test methods.
    setUp = WorkerTree.setUp
    unit = WorkerTree.unit
    call = WorkerTree.call

    def process(self, ident, foreground=False):
        directory = self.dir / 'processes' / ident
        directory.mkdir(parents=True)
        forge._atomic_json(directory/'request.json', {'id': ident, 'task_id': self.task_id,
            'command': 'literal command', 'supervisor_pid': 72 if foreground else 71,
            'supervisor_started_at': 'born', 'status': 'running'})
        forge._atomic_json(directory/'state.json', {'child_pid': 72, 'child_started_at': 'born',
            'status': 'running', 'foreground': foreground})
        return directory

    def test_actual_kill_producer_keeps_windows_return_code_facts_without_native_calls(self):
        for code, status in [(0, 'stopped'), (128, 'already_exited'), (1, 'unknown')]:
            with self.subTest(code=code), patch.object(forge, 'os', SimpleNamespace(name='nt')), \
                 patch.object(forge, '_kill_identity', return_value=''), \
                 patch.object(forge.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, '', 'raw Русский 中文')) as run:
                result = forge._kill_tree(12345, 'born')
            self.assertIsInstance(result, forge.KillTreeReply)
            self.assertEqual(result.termination, status)
            self.assertEqual(run.call_args.args[0], ['taskkill', '/PID', '12345', '/T', '/F'])
        with patch.object(forge, '_kill_identity', return_value='identity mismatch'), patch.object(forge.subprocess, 'run') as run:
            result = forge._kill_tree(12345, 'born')
            self.assertEqual(result.termination, 'unknown')
            self.assertEqual(str(result), 'identity mismatch')
            run.assert_not_called()

    def test_unknown_posix_census_does_not_invent_a_stopped_group(self):
        kill = Mock()
        with patch.object(forge, 'os', SimpleNamespace(name='posix', killpg=kill)), \
             patch.object(forge, '_kill_identity', return_value=''), \
             patch.object(forge, '_group_live_members', return_value=None), \
             patch.object(forge.time, 'monotonic', side_effect=[0, 2]):
            result = forge._kill_tree(12345, 'born')
        self.assertEqual(result.termination, 'unknown')
        kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_process_stop_uses_evidence_with_arbitrary_display_words_in_four_languages(self):
        for index, language in enumerate(fl.LANGUAGES):
            directory = self.process('proc-' + f'{index:08x}')
            with fl.use(language), patch.object(forge, '_pid_alive', return_value=False), \
                 patch.object(forge, '_kill_tree', return_value='остановлен'):
                forge.process(self.task_id, 'stop', process_id=directory.name)
            self.assertFalse((directory/'result.json').exists())
            self.assertEqual(forge._read_json(directory/'stop.json')['state'], 'unresolved')
            reply = forge.KillTreeReply('arbitrary Русский 中文 ' + language, status='stopped')
            with fl.use(language), patch.object(forge, '_pid_alive', return_value=False), patch.object(forge, '_kill_tree', return_value=reply):
                shown = forge.process(self.task_id, 'stop', process_id=directory.name)
            self.assertIn(str(reply), shown)
            self.assertEqual(forge._read_json(directory/'result.json')['status'], 'stopped')
            self.assertEqual(forge._read_json(directory/'stop.json')['state'], 'settled')

    def test_already_exited_process_and_unfinished_worker_keep_their_distinct_rules(self):
        process = self.process('proc-aaaaaaaa')
        worker = self.unit('agent-aaaaaaaa')
        gone = forge.KillTreeReply('different gone label', status='already_exited')
        with patch.object(forge, '_pid_alive', return_value=False), patch.object(forge, '_kill_tree', return_value=gone):
            forge.process(self.task_id, 'stop', process_id=process.name)
            self.call('stop', agent_id=worker.name)
        self.assertEqual(forge._read_json(process/'result.json')['status'], 'stopped')
        self.assertFalse((worker/'result.json').exists())
        self.assertEqual(forge._read_json(worker/'stop.json')['state'], 'unresolved')

    def test_worker_requires_typed_stopped_evidence_and_preserves_natural_result_bytes(self):
        worker = self.unit('agent-bbbbbbbb')
        with patch.object(forge, '_pid_alive', return_value=False), patch.object(forge, '_kill_tree', return_value='остановлен'):
            self.call('stop', agent_id=worker.name)
        self.assertFalse((worker/'result.json').exists())
        reply = forge.KillTreeReply('not a Russian status sentence', status='stopped')
        with patch.object(forge, '_pid_alive', return_value=False), patch.object(forge, '_kill_tree', return_value=reply):
            self.call('stop', agent_id=worker.name)
        self.assertEqual(forge._read_json(worker/'result.json')['status'], 'stopped')
        natural = self.unit('agent-cccccccc', result={'status':'done', 'result':'authored observation'})
        before = (natural/'result.json').read_bytes()
        with patch.object(forge, '_pid_alive', side_effect=[True, False]), patch.object(forge, '_kill_tree', return_value=reply):
            self.call('stop', agent_id=natural.name)
        self.assertEqual((natural/'result.json').read_bytes(), before)

    def test_foreground_stop_uses_original_handle_boolean_and_needs_no_supervisor(self):
        for index, stopped in enumerate((False, True)):
            directory = self.process('proc-' + f'{index+10:08x}', foreground=True)
            original = object()
            with patch.dict(forge._RUNNERS, {72:original}), patch.object(forge, '_pid_alive', return_value=False), \
                 patch.object(forge, '_stop_foreground', return_value=(stopped,'literal detail')) as stop, \
                 patch.object(forge, '_kill_tree', side_effect=AssertionError('must not signal recycled handle')):
                forge.process(self.task_id, 'stop', process_id=directory.name)
            stop.assert_called_once_with(original)
            self.assertEqual((directory/'result.json').exists(), stopped)
            self.assertEqual(forge._read_json(directory/'stop.json')['state'], 'settled' if stopped else 'unresolved')
