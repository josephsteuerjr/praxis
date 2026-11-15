"""Selected Stop remains scoped, durable and evidence based in every language pair."""
from contextlib import contextmanager
import unittest
from unittest.mock import patch

import forge
import frame_language as fl
import test_forge_tree_1310 as fixture

RAW = 'literal Русский 中文 {v1} <script>inert</script>'


class WorkerStopLanguage(unittest.TestCase):
    setUp = fixture.WorkerTree.setUp
    unit = fixture.WorkerTree.unit

    def settings(self, ui, frame):
        return patch.object(fl, 'settings', return_value={'lang':ui, 'agent':{'frame_language':frame}})

    def queued(self, ident, parent=''):
        directory = self.unit(ident, parent=parent)
        request = forge._read_json(directory/'request.json')
        request.update(status='queued', brief=RAW)
        request.pop('supervisor_pid'); request.pop('supervisor_started_at')
        forge._atomic_json(directory/'request.json', request)
        return directory

    def test_selected_queued_subtree_stops_before_launch_and_preserves_sibling16axes(self):
        for index, (ui, language) in enumerate((ui, lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
            selected = self.queued('agent-'+f'{index*3+100:08x}')
            child = self.queued('agent-'+f'{index*3+101:08x}', parent=selected.name)
            sibling = self.unit('agent-'+f'{index*3+102:08x}')
            before = (sibling/'request.json').read_bytes()
            with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                 patch.object(forge, '_pid_alive', return_value=True), \
                 patch.object(forge, '_kill_tree', side_effect=AssertionError('queued units never had a PID')):
                value = forge.control(self.task_id, 'stop', selected.name)
                self.assertEqual(value['status'], 'settled')
                self.assertEqual(value['unknown_units'], [])
                for directory in (selected, child):
                    result = forge._read_json(directory/'result.json')
                    self.assertEqual(result['status'], 'stopped')
                    self.assertTrue(result['never_started'])
                    self.assertEqual(result['note'], fl.text('forge.workers.41'))
                    self.assertEqual(forge._read_json(directory/'stop.json')['note'], fl.text('forge.workers.42'))
                    self.assertEqual(forge._read_json(directory/'request.json')['brief'], RAW)
                results = {d.name:(d/'result.json').read_bytes() for d in (selected, child)}
                repeated = forge.control(self.task_id, 'stop', selected.name)
                self.assertEqual(repeated['status'], 'settled')
                self.assertIn(fl.text('forge.workers.40', v0=selected.name), '\n'.join(repeated['receipts']))
                self.assertEqual({d.name:(d/'result.json').read_bytes() for d in (selected, child)}, results)
            self.assertEqual((sibling/'request.json').read_bytes(), before)
            self.assertFalse((sibling/'stop.json').exists())
            self.assertFalse((sibling/'result.json').exists())

    def test_untyped_stop_words_never_settle_then_current_evidence_settles_exact_unit16axes(self):
        for index, (ui, language) in enumerate((ui, lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
            selected = self.unit('agent-'+f'{index+300:08x}')
            with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                 patch.object(forge, '_pid_alive', return_value=False):
                with patch.object(forge, '_kill_tree', return_value=RAW):
                    first = forge.control(self.task_id, 'stop', selected.name)
                self.assertEqual(first['status'], 'unknown')
                self.assertEqual(first['unknown_units'], [selected.name])
                self.assertFalse((selected/'result.json').exists())
                stop = forge._read_json(selected/'stop.json')
                self.assertEqual(stop['state'], 'unresolved')
                self.assertEqual(stop['note'], RAW+fl.text('forge.workers.53'))
                with patch.object(forge, '_kill_tree', return_value=forge.KillTreeReply(RAW, status='stopped')):
                    second = forge.control(self.task_id, 'stop', selected.name)
                self.assertEqual(second['status'], 'settled')
                self.assertEqual(second['unknown_units'], [])
                self.assertEqual(forge._read_json(selected/'result.json')['status'], 'stopped')
                self.assertEqual(forge._read_json(selected/'result.json')['note'], RAW)
                self.assertEqual(forge._read_json(selected/'stop.json')['state'], 'settled')

    def test_late_lock_error_reports_unconfirmed_even_after_pending_fence_is_written(self):
        original_lock = forge._mutation_lock
        @contextmanager
        def late_error(*args, **kwargs):
            with original_lock(*args, **kwargs) as beat:yield beat
            raise TimeoutError(RAW)
        for index, language in enumerate(fl.LANGUAGES):
            directory = self.queued('agent-'+f'{index+500:08x}')
            before = (directory/'request.json').read_bytes()
            with self.settings('zh', language), patch.object(forge, '_mutation_lock', side_effect=late_error), \
                 patch.object(forge, '_kill_tree', side_effect=AssertionError('fencing did not reach any signal')):
                message, visited = forge._stop_agent_branch(self.task_id, directory.name)
                self.assertEqual(message, fl.text('forge.workers.37', v1=RAW))
                self.assertEqual(visited, set())
            self.assertEqual(forge._read_json(directory/'stop.json')['state'], 'pending')
            self.assertEqual((directory/'request.json').read_bytes(), before)
            self.assertFalse((directory/'result.json').exists())
