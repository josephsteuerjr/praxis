"""Actual auxiliary provider boundaries follow frame language, preserve evidence."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import formation
import frame_language
import identity
import llm
import memory_life as life
import sleep as sleep_module
from test_pass19 import Pass19Base
from test_state_language_1511 import RAW


class AuxiliaryCalls(unittest.TestCase):
    def test_owned_identity_explanations_and_report_preserve_literals(self):
        with patch.object(identity,'versions',return_value=[]), \
             patch.object(identity,'read',return_value=RAW), \
             patch.object(identity,'stress',return_value=[]), \
             patch.object(identity,'recent_shifts',return_value=[]):
            for language in frame_language.LANGUAGES:
                with frame_language.use(language):
                    self.assertIn(frame_language.text('identity.layer.self'),identity.describe())
                    self.assertNotIn('Егора',identity.describe())
                    error=identity.revise(RAW,'',reason='')['error']
                    self.assertIn(RAW,error)
                    if language!='ru':self.assertNotRegex(error.replace(RAW,''),'[А-Яа-яЁё]')
        with frame_language.use('ru'):
            self.assertEqual(frame_language.text('sleep.lesson_heuristic',minimum=sleep_module.lessons._MIN_LESSON_CHARS),
                             sleep_module.lessons.HEURISTIC)

    def test_night_and_rem_actual_sends_follow_frame_not_interface(self):
        settings = {}
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                settings.update(lang=ui, agent={'frame_language':language})
                calls = []
                def chat(role, **kw):
                    calls.append((role, copy.deepcopy(kw)))
                    return SimpleNamespace(text='{"links":[]}')
                with self.subTest(ui=ui, frame=language), \
                     patch.object(frame_language,'settings',side_effect=lambda:copy.deepcopy(settings)), \
                     patch.object(llm,'configured',return_value=True), \
                     patch.object(llm,'chat',side_effect=chat):
                    identity._night_model(RAW)
                    self.assertEqual(calls[-1][0],'evaluator')
                    self.assertEqual(calls[-1][1]['messages'],[{'role':'user','content':RAW}])
                    self.assertEqual(calls[-1][1]['max_tokens'],900)
                    self.assertEqual(calls[-1][1]['system'],frame_language.text('night.system'))
                    with patch.object(sleep_module.graph,'adjacency',return_value={RAW:{'node-ref':[]}}), \
                         patch.object(sleep_module,'_rem_nodes',return_value=[RAW]), \
                         patch.object(sleep_module.graph,'display',side_effect=lambda x:x), \
                         patch.object(sleep_module,'_journal') as journal:
                        self.assertEqual(sleep_module.rem_pass(apply=False),(0,False))
                        self.assertFalse(journal.called)
                    self.assertEqual(calls[-1][0],'voice')
                    self.assertEqual(calls[-1][1]['system'],frame_language.text('sleep.rem_system'))
                    self.assertIn(RAW,calls[-1][1]['messages'][0]['content'])
                    self.assertIn('node-ref',calls[-1][1]['messages'][0]['content'])
                    if language!='ru':
                        self.assertNotRegex(calls[-1][1]['system'],'[А-Яа-яЁё]')
                        self.assertNotRegex(calls[-2][1]['system'],'[А-Яа-яЁё]')
                    self.assertNotIn('Values in RUSSIAN',calls[-1][1]['system'])

    def test_schema_tokens_are_identical_and_person_lever_remains_effective(self):
        baseline = copy.deepcopy(formation._SCHEMAS)
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                for phase in ('harvest','dig','attack'):
                    system = formation._system(phase)
                    self.assertIn(phase.upper(),system)
                    self.assertIn(formation._SCHEMAS[phase],system)
                    self.assertIn(formation._first_person_rule(),system)
                    self.assertIsInstance(json.loads(formation._SCHEMAS[phase]),dict)
                    with patch.dict('os.environ',{'PRAXIS_CLAIMS_FIRST_PERSON':'off'}):
                        self.assertEqual(formation._first_person_rule(),'')
                        self.assertNotIn(frame_language.text('formation.first_person'),formation._system(phase))
        self.assertEqual(formation._SCHEMAS,baseline)


class FormationBoundary(Pass19Base):
    def test_real_pipeline_uses_localized_systems_and_unmodified_primary_evidence(self):
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with self.subTest(ui=ui,frame=language):
                    chat_id = str(700 + frame_language.LANGUAGES.index(ui)*4 + frame_language.LANGUAGES.index(language))
                    raw = self.add_messages(chat_id,6,start=1_700_000_000,gap_after={3})
                    prepared = life.prepare_authored_fold(chat_id)
                    self.assertTrue(prepared.get('selection'),prepared)
                    folded = life.apply_authored_fold(chat_id,prepared['selection'],RAW)
                    self.assertTrue(folded.get('ok'),folded)
                    event = raw[0]
                    candidate = {'subject':RAW,'kind':'topic','text':RAW,'visibility':'public',
                                 'confidence':'inferred','evidence_ids':[event['id']]}
                    calls=[]
                    settings={'lang':ui,'agent':{'frame_language':language}}
                    expected={}
                    with frame_language.use(language):
                        expected={p:formation._system(p) for p in ('harvest','dig','attack')}
                    def chat(role, **kw):
                        system=kw['system'];calls.append((role,copy.deepcopy(kw)))
                        # A saved setting may change during a multi-phase pass. It takes
                        # effect on the next operation, never halfway through this pass.
                        settings['agent']['frame_language']='fr' if language!='fr' else 'zh'
                        if system==expected['harvest']:
                            data={'entities':[{'name':RAW,'kind':'topic'}],'claims':[candidate]}
                        elif system==expected['dig']:
                            data={'evidence_found':[event['id']],'questions':[], 'stop_now':False}
                        elif system==expected['attack']:
                            key=formation._clean_claim(candidate,{event['id']})['key']
                            data={'verdicts':[{'key':key,'status':'unsupported','reason':RAW,'evidence_ids':[]}]}
                        else:
                            self.fail('phase switched language within one operation')
                        return SimpleNamespace(text=json.dumps(data,ensure_ascii=False),stop_reason='end_turn')
                    with patch.object(frame_language,'settings',side_effect=lambda:copy.deepcopy(settings)), \
                         patch.object(llm,'configured',return_value=True), patch.object(llm,'chat',side_effect=chat):
                        result=formation.run('light')
                    self.assertTrue(result['ok'],result)
                    self.assertEqual([r for r,kw in calls],['evaluator']*3)
                    self.assertEqual([kw['system'] for role,kw in calls],[expected[p] for p in ('harvest','dig','attack')])
                    for role,kw in calls:
                        self.assertIn(event['id'],kw['messages'][0]['content'])
                    self.assertIn(RAW,calls[1][1]['messages'][0]['content'])
                    self.assertIn(RAW,calls[2][1]['messages'][0]['content'])
                    report=(life.BASE/result['report']).read_text(encoding='utf-8')
                    first=report.splitlines()[0]
                    meta=json.loads(first.removeprefix('<!-- praxis-reflection-run: ').removesuffix(' -->'))
                    self.assertEqual(meta['schema'],'praxis.life.reflection_run.v1')
                    for key in ('id','depth','source_ids','stop_reason','passes','changes','coverage','usage'):
                        self.assertEqual(meta[key],result['run'][key])
                    self.assertIn(RAW,report)
                    # The shared base fixture supplies a literal Russian usage string.
                    # It is input data here, and must survive just like authored text.
                    if language!='ru':self.assertNotRegex(report.replace(RAW,'').replace(meta['usage'],''),'[А-Яа-яЁё]')


if __name__=='__main__':unittest.main()
