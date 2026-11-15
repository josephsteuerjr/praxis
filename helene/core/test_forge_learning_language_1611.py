"""Localized lesson views preserve canonical verdicts, authored text and actual evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import forge
import forge_learning as learning
import frame_language as fl

RAW = 'literal Русский 中文 {v1} <script>inert</script>'


class LearningLanguage(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name); self.root = self.base/'project'; self.root.mkdir()
        self.state = self.base/'state'
        self.task = {'id':'code-12345678', 'goal':RAW, 'status':'active', 'root':str(self.root)}
        for name, value in [('BASE',self.base), ('STATE_DIR',self.state), ('TASKS_DIR',self.state/'tasks')]:
            self.enterContext(patch.object(forge, name, value))

    def settings(self, ui, frame):
        return patch.object(fl, 'settings', return_value={'lang':ui, 'agent':{'frame_language':frame}})

    def record(self, **kwargs):
        return learning.record(self.state, task=self.task, root=self.root,
            events=kwargs.pop('events', []), changed=kwargs.pop('changed', []), **kwargs)

    def test_authored_lessons_and_literal_payloads_survive_record_recall_and_render16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.subTest(ui=ui, frame=language), self.settings(ui, language):
                    row = self.record(lesson=RAW, regression=RAW, events=[{
                        'kind':'command', 'command':RAW, 'status':'ok', 'code':0, 'log':RAW}])
                    self.assertEqual(row['lesson'], RAW); self.assertEqual(row['regression'], RAW)
                    self.assertEqual(row['commands'][0]['command'], RAW)
                    self.assertEqual(row['commands'][0]['log'], RAW)
                    self.assertEqual(row['commands'][0]['exit'], 0)
                    before = learning.lesson_path(self.state).read_bytes()
                    display = learning.format_recall([{**row,'score':0.375}])
                    self.assertEqual(display, fl.text('forge.learning.9', v0=row['id'], v2='0.375',
                        v4='project', v6=RAW, v8=RAW, v10=RAW))
                    recalled = learning.recall(self.state, RAW, self.root)
                    self.assertTrue(recalled)
                    self.assertEqual(recalled[0]['lesson'], RAW)
                    self.assertEqual(learning.lesson_path(self.state).read_bytes(), before)

    def test_generated_defaults_do_not_invent_a_gate_or_a_completed_task(self):
        for language in fl.LANGUAGES:
            with self.settings('zh', language):
                changed = self.record(changed=[RAW])
                self.assertEqual(changed['outcome'], 'active')
                self.assertEqual(changed['commands'], [])
                self.assertEqual(changed['verification'], [])
                self.assertEqual(changed['lesson'], fl.text('forge.learning.5'))
                empty = self.record()
                self.assertEqual(empty['outcome'], 'active')
                self.assertEqual(empty['changed'], [])
                self.assertEqual(empty['lesson'], fl.text('forge.learning.6'))
                unknown = self.record(events=[{'kind':'command','command':RAW,'status':'unknown','code':None}])
                self.assertEqual(unknown['commands'][0]['status'], 'unknown')
                self.assertEqual(unknown['failures'][0]['command'], RAW)
                self.assertEqual(unknown['lesson'], fl.text('forge.learning.4', v1=RAW))

    def test_contract_storage_and_actual_verdict_consumer_remain_canonical_in_every_language(self):
        cases = [({'met':1,'unmet':0,'unknown':0},'выполнен','met'),
                 ({'met':1,'unmet':1,'unknown':0},'не выполнен','unmet'),
                 ({'met':0,'unmet':0,'unknown':1},'неизвестно','unknown')]
        for language in fl.LANGUAGES:
            with self.settings('fr', language):
                for verify, canonical, label in cases:
                    contract = {'verify':verify}
                    self.assertEqual(learning.contract_status(contract), canonical)
                    self.assertEqual(learning.format_contract_status(canonical), fl.text('forge.learning.contract.'+label))
                    row = self.record(contract=contract, lesson=RAW)
                    self.assertEqual(row['contract'], canonical)
                    self.assertEqual(row['lesson'], RAW)
                self.assertEqual(learning.format_contract_status(RAW), RAW)
                task = {**self.task, 'verify_commands':[RAW]}; forge._save_task(task)
                directory = forge._task_dir(task['id'])/'verifications'/'verify-aaaaaaaa'
                forge._atomic_json(directory/'request.json', {'id':directory.name,'task_id':task['id']})
                forge._atomic_json(directory/'result.json', {'status':'passed','checks':[
                    {'command':RAW,'status':'passed'}]})
                self.assertEqual(forge._verdict_for(task), 'выполнен')

    def test_finish_renders_the_label_but_records_the_original_contract_verdict16axes(self):
        for index, (ui, language) in enumerate((ui, lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
            task = {**self.task, 'id':'code-'+f'{index+100:08x}'}; forge._save_task(task)
            contract = {'verify':{'met':1,'unmet':0,'unknown':0}}
            with self.subTest(ui=ui, frame=language), self.settings(ui, language), patch.object(forge,'_git_root',return_value=None):
                answer = forge._finish_unlocked(task['id'], checked=RAW, submit=False, survey={'contract':contract})
                self.assertEqual(forge.get(task['id'])['status'], 'done')
                self.assertIn(fl.text('forge.finish.36', v1=fl.text('forge.learning.contract.met'),
                    v3='1', v5='0', v7='0'), answer)
                row = learning._read(learning.lesson_path(self.state))[-1]
                self.assertEqual(row['contract'], 'выполнен')
                self.assertEqual(row['regression'], RAW)

    def test_generated_lesson_pins_language_while_canonical_verdict_is_calculated(self):
        settings = {'lang':'zh','agent':{'frame_language':'en'}}
        original = learning.contract_status
        def changed(contract):
            value = original(contract); settings['agent']['frame_language']='fr'; return value
        contract = {'verify':{'met':1,'unmet':0,'unknown':0}}
        with patch.object(fl,'settings',side_effect=lambda:settings), patch.object(learning,'contract_status',side_effect=changed):
            first = self.record(changed=[RAW], contract=contract)
            with fl.use('en'):
                self.assertEqual(first['lesson'], fl.text('forge.learning.5')
                    +fl.text('forge.learning.7')+fl.text('forge.learning.contract.met'))
            second = self.record(changed=[RAW], contract=contract)
            self.assertEqual(second['lesson'], fl.text('forge.learning.5')
                +fl.text('forge.learning.7')+fl.text('forge.learning.contract.met'))
            self.assertEqual((first['contract'],second['contract']), ('выполнен','выполнен'))
            self.assertNotEqual(first['lesson'], second['lesson'])
