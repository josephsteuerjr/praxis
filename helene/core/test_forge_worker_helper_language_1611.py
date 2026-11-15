"""Translated reasons cannot grant admission, invent liveness or duplicate queued work."""
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import forge
import frame_language as fl
import test_forge_run_language_1611 as fixture

RAW = fixture.RAW


class WorkerHelperLanguage(unittest.TestCase):
    setUp = fixture.ForgeRunLanguage.setUp
    git_run = fixture.ForgeRunLanguage.git_run
    settings = fixture.ForgeRunLanguage.settings

    def legacy_unit(self):
        directory = forge._task_dir(self.task)/'processes'/'proc-aaaaaaaa'
        forge._atomic_json(directory/'request.json', {'id':directory.name, 'task_id':self.task,
            'status':'running', 'supervisor_pid':777777, 'created':'1970-01-02T00:00:00+00:00',
            'command':RAW})
        return directory

    def test_occupied_legacy_pid_stays_unknown_without_rewriting_history16axes(self):
        directory = self.legacy_unit(); before = (directory/'request.json').read_bytes()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                     patch.object(forge, '_pid_alive', return_value=True), \
                     patch.object(forge, 'UNIT_LIVENESS_TRUST_SEC', 3600), \
                     patch.object(forge.time, 'time', return_value=93600):
                    state = forge._unit_state(directory)
                    self.assertEqual(state['status'], 'unknown')
                    self.assertEqual(state['liveness'], fl.text('forge.workers.4',
                        v1='777777', v3='2', v5='1'))
                    self.assertEqual(state['command'], RAW)
                    self.assertEqual(state['supervisor_pid'], 777777)
                    self.assertFalse((directory/'result.json').exists())
                    self.assertEqual((directory/'request.json').read_bytes(), before)

    def test_owner_pause_file_prevents_a_process_request_and_launch16axes(self):
        marker = self.base/'memory/.control/autonomy-paused.json'
        marker.parent.mkdir(parents=True); marker.write_text('{}', encoding='utf8')
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                     patch.dict(sys.modules, {'localharness.owner_stop':SimpleNamespace(paused=lambda:False)}), \
                     patch.object(forge, '_spawn_runner') as spawn:
                    before = list(forge._task_dir(self.task).glob('processes/*/request.json'))
                    answer = forge.process(self.task, 'start', command=RAW)
                    self.assertEqual(answer, fl.text('forge.execution.114',
                        v1=fl.text('forge.workers.11')))
                    spawn.assert_not_called()
                    self.assertEqual(list(forge._task_dir(self.task).glob('processes/*/request.json')), before)
                    self.assertEqual(forge.get(self.task)['status'], 'active')
                    self.assertEqual(marker.read_text(), '{}')

    def test_ancestor_stop_uses_durable_identity_and_never_admits_the_child(self):
        directory = forge._task_dir(self.task)/'agents'/'agent-aaaaaaaa'
        forge._atomic_json(directory/'request.json', {'id':directory.name, 'task_id':self.task,
            'status':'running', 'role':'worker', 'parent_unit_id':'', 'brief':RAW})
        forge._atomic_json(directory/'stop.json', {'state':'pending', 'requested_by':'owner'})
        before = {p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
        for language in fl.LANGUAGES:
            with self.settings('zh', language), patch.object(forge, '_spawn_runner') as spawn:
                answer = forge.process(self.task, 'start', command=RAW,
                    _parent_request_path=str(directory/'request.json'))
                self.assertEqual(answer, fl.text('forge.execution.114',
                    v1=fl.text('forge.workers.13', v1=directory.name)))
                spawn.assert_not_called()
            self.assertEqual({p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}, before)
            self.assertFalse(list(forge._task_dir(self.task).glob('processes/*/request.json')))

    def test_capacity_queues_once_and_drains_one_original_request_after_root_release(self):
        task = forge.get(self.task); task['max_active_workers'] = 1; forge._save_task(task)
        for index, language in enumerate(fl.LANGUAGES):
            blocker = forge._task_dir(self.task)/'agents'/('agent-'+f'{index+600:08x}')
            forge._atomic_json(blocker/'request.json', {'id':blocker.name, 'task_id':self.task,
                'role':'worker', 'brief':RAW, 'status':'running', 'parent_unit_id':'',
                'supervisor_pid':777777, 'supervisor_started_at':'born'})
            ident = 'agent-'+f'{index+700:08x}'
            with self.settings('ru', language), patch.object(forge, '_pid_alive', return_value=True), \
                 patch.object(forge, '_spawn_runner', return_value=777777) as spawn, \
                 patch.object(forge, '_proc_started_at', return_value='born'):
                forge.agent(self.task, 'spawn', agent_id=ident, brief=RAW)
                directory = forge._task_dir(self.task)/'agents'/ident
                request = forge._read_json(directory/'request.json')
                self.assertEqual(request['status'], 'queued')
                self.assertEqual(request['queued_reason'], fl.text('forge.workers.20', v1='1', v3='1'))
                self.assertEqual(request['brief'], RAW)
                self.assertNotIn('supervisor_pid', request)
                spawn.assert_not_called()
                forge._atomic_json(blocker/'result.json', {'status':'done', 'result':RAW})
                forge.release_worker_root(blocker/'request.json')
                outcomes = forge.tick_queued(self.task)
                self.assertEqual([row['agent_id'] for row in outcomes], [ident])
                spawn.assert_called_once()
                self.assertEqual(forge._read_json(directory/'request.json')['status'], 'running')
                self.assertEqual(forge.tick_queued(self.task), [])
                self.assertEqual(spawn.call_count, 1)
                forge._atomic_json(directory/'result.json', {'status':'done', 'result':RAW})
                forge.release_worker_root(directory/'request.json')

    def test_liveness_read_pins_language_even_if_settings_change_during_the_probe(self):
        directory = self.legacy_unit(); settings = {'lang':'zh', 'agent':{'frame_language':'en'}}
        def alive(*args):
            settings['agent']['frame_language'] = 'fr'
            return True
        with patch.object(fl, 'settings', side_effect=lambda:settings), \
             patch.object(forge, '_pid_alive', side_effect=alive), \
             patch.object(forge, 'UNIT_LIVENESS_TRUST_SEC', 3600), \
             patch.object(forge.time, 'time', return_value=93600):
            first = forge._unit_state(directory)
            with fl.use('en'):
                self.assertEqual(first['liveness'], fl.text('forge.workers.4', v1='777777', v3='2', v5='1'))
            second = forge._unit_state(directory)
            self.assertEqual(second['liveness'], fl.text('forge.workers.4', v1='777777', v3='2', v5='1'))
            self.assertNotEqual(first['liveness'], second['liveness'])
