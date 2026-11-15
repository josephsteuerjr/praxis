"""Localized lifecycle/disposition captions keep exact identities and durable evidence."""
from contextlib import contextmanager
import hashlib
import json
import unittest
from unittest import mock

import forge
import frame_language as fl
import test_forge_coordination_2610 as fixture

RAW = 'literal Русский 中文 {v1} <script>inert</script>'


class AgentSwarmLanguage(unittest.TestCase):
    setUp = fixture.Coordination.setUp
    patch = fixture.Coordination.patch
    unit = fixture.Coordination.unit

    def settings(self, ui, frame):
        return mock.patch.object(fl, 'settings', return_value={'lang':ui, 'agent':{'frame_language':frame}})

    def test_spawn_poll_disposition_and_stale_result_keep_machine_proofs16axes(self):
        for index, (ui, language) in enumerate((ui, lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
            ident = 'agent-'+f'{index+100:08x}'
            with self.subTest(ui=ui, frame=language), self.settings(ui, language):
                before = self.spawn.call_count
                answer = forge.agent(self.tid, 'spawn', agent_id=ident, role='scout', brief=RAW, model='fixture/model')
                directory = self.directory/'agents'/ident
                request = forge._read_json(directory/'request.json')
                self.assertEqual(request['brief'], RAW)
                self.assertEqual(request['model'], 'fixture/model')
                self.assertEqual(request['id'], ident)
                self.assertEqual(request['status'], 'running')
                self.assertEqual(self.spawn.call_count, before+1)
                self.assertEqual(answer, fl.text('forge.workers.84', v1=ident, v3='scout', v5='781'))
                poll = forge.agent(self.tid, 'poll', agent_id=ident)
                value = json.loads(poll.split('\n--- worker log ---\n')[0])
                self.assertEqual(value['agent_id'], ident)
                self.assertEqual(value['status'], 'running')
                self.assertEqual(value['result_sha256'], '')
                self.assertIn(fl.text('forge.workers.92'), poll)
                forge._atomic_json(directory/'result.json', {'status':'done', 'result':RAW, 'complete':True})
                raw = (directory/'result.json').read_bytes(); digest = hashlib.sha256(raw).hexdigest()
                for revision, choice in enumerate(('deferred', 'integrated'), 1):
                    disposition = json.loads(forge.agent(self.tid, 'disposition', agent_id=ident,
                        result_sha256=digest, disposition=choice))
                    self.assertEqual(disposition['result_sha256'], digest)
                    self.assertEqual(disposition['disposition'], choice)
                    self.assertEqual(disposition['revision'], revision)
                repeated = json.loads(forge.agent(self.tid, 'disposition', agent_id=ident,
                    result_sha256=digest, disposition='integrated'))
                self.assertEqual(repeated['revision'], 2)
                self.assertEqual(len(list(directory.glob('disposition-*.json'))), 2)
                self.assertEqual((directory/'result.json').read_bytes(), raw)
                forge._atomic_json(directory/'result.json', {'status':'done', 'result':RAW+'changed'})
                changed = forge._result_identity(directory/'result.json')
                refused = forge.agent(self.tid, 'disposition', agent_id=ident,
                    result_sha256=digest, disposition='integrated')
                self.assertEqual(refused, fl.text('forge.workers.96', v1=changed))
                self.assertEqual(len(list(directory.glob('disposition-*.json'))), 2)
                forge.release_worker_root(directory/'request.json')

    def test_late_disposition_lock_error_does_not_claim_that_the_record_was_not_written(self):
        original_lock = forge._mutation_lock
        @contextmanager
        def late_error(*args, **kwargs):
            with original_lock(*args, **kwargs) as beat:yield beat
            raise TimeoutError(RAW)
        for index, language in enumerate(fl.LANGUAGES):
            directory = self.unit('agent-'+f'{index+300:08x}', result={'status':'done', 'result':RAW})
            digest = forge._result_identity(directory/'result.json')
            with self.settings('zh', language), mock.patch.object(forge, '_mutation_lock', side_effect=late_error):
                answer = forge.agent(self.tid, 'disposition', agent_id=directory.name,
                    result_sha256=digest, disposition='integrated')
                self.assertEqual(answer, fl.text('forge.workers.98', v1=RAW))
            records = list(directory.glob('disposition-*.json')); self.assertEqual(len(records), 1)
            self.assertEqual(forge._read_json(records[0])['result_sha256'], digest)
            before = records[0].read_bytes()
            forge.agent(self.tid, 'disposition', agent_id=directory.name,
                result_sha256=digest, disposition='integrated')
            self.assertEqual(records[0].read_bytes(), before)
            self.assertEqual(len(list(directory.glob('disposition-*.json'))), 1)

    def test_swarm_recovers_one_existing_binding_without_spawning_a_duplicate16axes(self):
        original_task = dict(self.task)
        for index, (ui, language) in enumerate((ui, lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
            node_id = 'node-'+str(index); ident = 'agent-'+f'{index+500:08x}'
            self.tid = 'code-'+f'{index+700:08x}'
            self.directory = forge._task_dir(self.tid)
            forge._atomic_json(self.directory/'task.json', {**original_task, 'id':self.tid})
            with self.subTest(ui=ui, frame=language), self.settings(ui, language):
                plan = forge.forge_swarm.create(self.directory, [{'id':node_id, 'brief':RAW, 'role':'scout'}])
                plan['nodes'][0]['reserved_agent_id'] = ident
                forge.forge_swarm.save(self.directory, plan)
                directory = self.unit(ident, role='scout')
                request = forge._read_json(directory/'request.json')
                request.update(node_id=node_id, previous_unit_id='', brief=RAW)
                forge._atomic_json(directory/'request.json', request)
                before = (directory/'request.json').read_bytes(); count = self.spawn.call_count
                answer = forge.swarm(self.tid, 'start')
                current = forge.forge_swarm.load(self.directory)
                self.assertEqual(current['nodes'][0]['agent_id'], ident)
                self.assertEqual(self.spawn.call_count, count)
                self.assertEqual((directory/'request.json').read_bytes(), before)
                notices = [call.kwargs['summary'] for call in forge._event.call_args_list
                           if call.args[:2]==(self.tid, 'swarm_tick')]
                self.assertEqual(notices, [fl.text('forge.workers.112', v0=node_id, v2=ident)])
                self.assertIn(ident, answer)
                matching = [p for p in (self.directory/'agents').glob('*/request.json')
                            if forge._read_json(p).get('node_id')==node_id]
                self.assertEqual(matching, [directory/'request.json'])

    def test_spawn_pins_language_then_poll_uses_the_new_saved_language(self):
        settings = {'lang':'zh', 'agent':{'frame_language':'en'}}
        def launch(*args):
            settings['agent']['frame_language'] = 'fr'
            return 781
        self.spawn.side_effect = launch
        with mock.patch.object(fl, 'settings', side_effect=lambda:settings):
            answer = forge.agent(self.tid, 'spawn', agent_id='agent-eeeeeeee', role='scout', brief=RAW)
            with fl.use('en'):
                self.assertEqual(answer, fl.text('forge.workers.84', v1='agent-eeeeeeee', v3='scout', v5='781'))
            self.assertIn(fl.text('forge.workers.92'), forge.agent(self.tid, 'poll', agent_id='agent-eeeeeeee'))
