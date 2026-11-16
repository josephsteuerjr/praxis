"""Graph language does not replace dependency acceptance or mutate authored evidence."""
from unittest import mock
import unittest

import forge
import forge_swarm as swarm
import frame_language as fl
import test_forge_coordination_2610 as fixture

RAW = 'literal Русский 中文 {v1} <script>inert</script>'


class SwarmHelperLanguage(unittest.TestCase):
    setUp = fixture.Coordination.setUp
    patch = fixture.Coordination.patch
    unit = fixture.Coordination.unit

    def settings(self, ui, frame):
        return mock.patch.object(fl, 'settings', return_value={'lang':ui, 'agent':{'frame_language':frame}})

    def fresh_task(self, index):
        self.tid = 'code-'+f'{index+100:08x}'; self.directory = forge._task_dir(self.tid)
        forge._atomic_json(self.directory/'task.json', {**self.task, 'id':self.tid})

    def test_dependency_acceptance_and_revision_proofs_stay_exact16axes(self):
        for index, (ui, language) in enumerate((ui, lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
            self.fresh_task(index)
            with self.subTest(ui=ui, frame=language), self.settings(ui, language):
                plan = swarm.create(self.directory, [{'id':'A','brief':RAW},
                    {'id':'B','brief':RAW,'deps':['A']}])
                first = self.unit('agent-aaaaaaaa', result={'status':'done','result':RAW})
                plan['nodes'][0]['agent_id'] = first.name; swarm.save(self.directory, plan)
                refresh = lambda:swarm.refresh(self.directory, lambda aid:forge._agent_snapshot(self.tid, aid))
                current = refresh()
                self.assertEqual(current['nodes'][0]['status'], 'awaiting_acceptance')
                self.assertEqual(swarm.ready_nodes(current), [])
                self.assertEqual(current['nodes'][1]['queued_reason'], fl.text('forge.swarm.23')+'A')
                first_digest = forge._result_identity(first/'result.json')
                forge.agent(self.tid, 'disposition', agent_id=first.name,
                    result_sha256=first_digest, disposition='integrated')
                current = refresh()
                self.assertEqual([node['id'] for node in swarm.ready_nodes(current)], ['B'])
                self.assertEqual(current['nodes'][0]['accepted_sha256'], first_digest)
                second = self.unit('agent-bbbbbbbb', result={'status':'done','result':RAW+'dependent'})
                current['nodes'][1].update(agent_id=second.name, dependency_results={'A':first_digest})
                swarm.save(self.directory, current)
                second_digest = forge._result_identity(second/'result.json')
                forge.agent(self.tid, 'disposition', agent_id=second.name,
                    result_sha256=second_digest, disposition='integrated')
                old_second = (second/'result.json').read_bytes()
                self.assertEqual(refresh()['nodes'][1]['status'], 'done')
                # An adversarial result replacement cannot inherit an old acceptance.
                forge._atomic_json(first/'result.json', {'status':'done','result':RAW+'revised'})
                new_digest = forge._result_identity(first/'result.json')
                forge.agent(self.tid, 'disposition', agent_id=first.name,
                    result_sha256=new_digest, disposition='integrated')
                current = refresh(); by_id = {node['id']:node for node in current['nodes']}
                self.assertEqual(by_id['B']['status'], 'stale_dependencies')
                self.assertEqual(by_id['B']['dependency_results'], {'A':first_digest})
                self.assertEqual(by_id['B']['accepted_sha256'], '')
                self.assertEqual(swarm.dependency_reason(by_id['B'], by_id), fl.text('forge.swarm.20')+'A')
                self.assertEqual(swarm.ready_nodes(current), [])
                self.assertEqual((second/'result.json').read_bytes(), old_second)
                self.assertEqual(len(list(second.glob('disposition-*.json'))), 1)
                self.spawn.assert_not_called()

    def test_invalid_graphs_and_empty_signals_do_not_publish_state16axes(self):
        for index, (ui, language) in enumerate((ui, lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
            directory = self.root/('validation-'+str(index))
            with self.subTest(ui=ui, frame=language), self.settings(ui, language):
                cases = [([{'id':'A','brief':RAW,'deps':['B']},{'id':'B','brief':RAW,'deps':['A']}],
                          fl.text('forge.swarm.177', v1='A')),
                         ([{'id':'A','brief':RAW,'deps':['Z']}],fl.text('forge.swarm.18', v1='A', v3="['Z']")),
                         ([{'id':'A','brief':RAW},{'id':'A','brief':RAW}],fl.text('forge.swarm.15', v1="'A'")),
                         ([{'id':'A','brief':' '}],fl.text('forge.swarm.17', v1='A'))]
                for plan, expected in cases:
                    with self.assertRaises(ValueError) as caught:swarm.create(directory, plan)
                    self.assertEqual(str(caught.exception), expected)
                    self.assertFalse((directory/'swarm.json').exists())
                swarm.create(directory, [{'id':'A','brief':RAW}])
                before = (directory/'swarm.json').read_bytes()
                with self.assertRaises(ValueError) as caught:swarm.create(directory, [{'id':'B','brief':RAW}])
                self.assertEqual(str(caught.exception), fl.text('forge.swarm.11'))
                with self.assertRaises(ValueError) as caught:
                    swarm.signal(directory, node_id='A', kind='finding', message='')
                self.assertEqual(str(caught.exception), fl.text('forge.swarm.26'))
                self.assertFalse((directory/'mailbox.jsonl').exists())
                self.assertEqual((directory/'swarm.json').read_bytes(), before)

    def test_summary_preserves_canonical_fields_and_literal_messages_without_rewriting_files(self):
        plan = swarm.create(self.directory, [{'id':'A','brief':RAW}])
        row = swarm.signal(self.directory, node_id='A', kind='finding', message=RAW, files=[RAW])
        plan_before = (self.directory/'swarm.json').read_bytes()
        mailbox_before = (self.directory/'mailbox.jsonl').read_bytes()
        for language in fl.LANGUAGES:
            with self.settings('zh', language):
                self.assertEqual(swarm.summary({}), fl.text('forge.swarm.27'))
                value = swarm.summary(plan, [row])
                self.assertIn('swarm max_parallel=', value)
                self.assertIn('phase=- acceptance=pending sha256=-', value)
                self.assertIn(RAW, value)
                self.assertEqual(swarm.mailbox(self.directory)[0]['message'], RAW)
                self.assertEqual((self.directory/'swarm.json').read_bytes(), plan_before)
                self.assertEqual((self.directory/'mailbox.jsonl').read_bytes(), mailbox_before)

    def test_validation_pins_language_then_next_call_uses_saved_change(self):
        settings = {'lang':'zh','agent':{'frame_language':'en'}}; original = swarm._node_id
        def changed(value):
            result = original(value); settings['agent']['frame_language']='fr'; return result
        with mock.patch.object(fl,'settings',side_effect=lambda:settings), mock.patch.object(swarm,'_node_id',side_effect=changed):
            with self.assertRaises(ValueError) as caught:
                swarm.create(self.directory, [{'id':'A','brief':RAW,'role':'invalid-role'}])
            with fl.use('en'):self.assertEqual(str(caught.exception), fl.text('forge.swarm.16', v1='A'))
            with self.assertRaises(ValueError) as caught:
                swarm.create(self.directory, [{'id':'A','brief':RAW,'role':'invalid-role'}])
            self.assertEqual(str(caught.exception), fl.text('forge.swarm.16', v1='A'))
            self.assertFalse((self.directory/'swarm.json').exists())
