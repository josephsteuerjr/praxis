"""Actual in-process Forge loop and frozen adapters with fake provider boundaries."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import frame_language
import forge_worker
import tool_language
from test_tool_language_1511 import structural
import test_forge_worker_durability_2610 as durability
from localharness import self_review_worker,source_update_worker

RAW='Windows body praxis.body.v1 Yegor Егор 中文 {literal} $& C:/данные/file.txt'


def suppliers(worker=forge_worker):
    return {name:value for name,value in vars(worker).items() if name.endswith('_TOOL') and isinstance(value,dict)}|{
        'snapshot':self_review_worker.SnapshotReviewer.tools[0],
        'merger':source_update_worker.SourceMergeAdapter.tools[0]}


class WorkerLanguages(unittest.TestCase):
    def fixture(self):
        case=durability.WorkerDurability();case.setUp();self.addCleanup(case.doCleanups)
        return case

    def reviewer(self,case):
        subject=self_review_worker.SnapshotReviewer.__new__(self_review_worker.SnapshotReviewer)
        subject.root=case.base/'snapshot';subject.root.mkdir()
        (subject.root/'literal.txt').write_text(RAW,encoding='utf-8')
        (subject.root/'changes.diff').write_text(RAW,encoding='utf-8')
        subject.sha256='a'*64;subject.subject={'subject_kind':'source','manifest':[]}
        subject.files={'literal.txt'};subject.entries={'literal.txt':{'path':'literal.txt'}}
        return subject

    def test_exact_worker_supplier_census_and_all110_captions_follow16_independent_axes(self):
        original=suppliers();rules={row['variant']:row for rows in tool_language.worker_registry().values() for row in rows}
        self.assertEqual(set(original),set(rules));self.assertEqual(len(rules),12)
        self.assertEqual(sum(len(row['descriptions']) for row in rules.values()),110)
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                    for label,raw in original.items():
                        self.assertEqual(tool_language.fingerprint(raw),rules[label]['sha256'])
                        translated=tool_language.apply([raw])[0]
                        self.assertEqual(structural(translated),structural(raw))
                        for entry in rules[label]['descriptions']:
                            value=translated
                            for part in entry['path']:value=value[part]
                            self.assertEqual(value,frame_language.text(entry['key']))
                        authored=copy.deepcopy(raw);authored['description']=RAW
                        self.assertIs(tool_language.apply([authored])[0],authored)
        self.assertEqual(original,suppliers())

    def test_actual_main_and_both_auxiliary_systems_keep_authored_material_literal(self):
        critic=self_review_worker.SnapshotReviewer.__new__(self_review_worker.SnapshotReviewer)
        critic.root=Path('not-executed');critic.sha256='a'*64;critic.subject={'subject_kind':RAW}
        merger=source_update_worker.SourceMergeAdapter.__new__(source_update_worker.SourceMergeAdapter)
        merger.input_sha256='b'*64
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(self_review_worker,'validate_snapshot') as validate,patch.object(merger,'_check') as admission,patch.object(forge_worker.forge,'inspect',return_value=RAW):
                    request={'task_id':'code-test','id':'agent-test','brief':RAW,'goal':RAW}
                    for role in ('scout','reviewer','worker'):
                        result=forge_worker._system(dict(request,role=role))
                        self.assertTrue(result.startswith('audience_key=forge_'+role+'\ncache_scope='))
                        self.assertEqual(result.count(RAW),3)
                        self.assertIn(frame_language.text('worker.stance.'+role),result)
                    self.assertEqual(critic.system(request),frame_language.text('worker.snapshot.system',kind=RAW,sha256='a'*64,brief=RAW))
                    self.assertEqual(merger.system(request),frame_language.text('worker.merger.system',sha256='b'*64,brief=RAW))
                    validate.assert_called_once_with(critic.root,critic.sha256)
                    admission.assert_called_once_with(request)

    def test_real_worker_provider_uses_frozen_language_and_preserves_raw_tools_and_results(self):
        for language in frame_language.LANGUAGES:
            case=self.fixture();selected={'lang':'fr','agent':{'frame_language':language}}
            case.request.update(brief=RAW,goal=RAW)
            case.runtime.atomic_json(case.request_path,case.request)
            case.worker.forge.inspect=lambda *args,**kwargs:RAW
            response=case.response('inspect',1);response.blocks[0]['input']={'action':'read','path':RAW}
            case.responses=[response,case.response(text=RAW)]
            original_chat=case.chat
            def chat(*args,**kwargs):
                result=original_chat(*args,**kwargs)
                selected['agent']['frame_language']='zh' if language!='zh' else 'en'
                return result
            case.worker.llm.chat=chat
            with patch.object(frame_language,'settings',side_effect=lambda:selected):
                self.assertEqual(case.run_worker(),0)
            self.assertEqual(len(case.calls),2)
            self.assertEqual(case.calls[0]['system'],case.calls[1]['system'])
            self.assertEqual(case.calls[0]['tools'],case.calls[1]['tools'])
            checkpoint=json.loads((case.request_path.parent/'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(checkpoint['frame_language'],language)
            self.assertEqual(checkpoint['tools'][0],case.worker.INSPECT_TOOL)
            self.assertEqual(checkpoint['model_tools'],case.calls[0]['tools'])
            with frame_language.use(language):
                self.assertEqual(case.calls[0]['tools'],tool_language.apply(checkpoint['tools']))
            self.assertEqual(case.calls[0]['messages'][0]['content'],RAW)
            result=json.loads((case.request_path.parent/'result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['result'],RAW);self.assertEqual(result['trace'][0]['output'],RAW)
            case.doCleanups()

    def test_authorized_continuation_keeps_original_language_tools_system_and_exactly_once_dispatch(self):
        case=self.fixture();critic=self.reviewer(case);case.request['role']='reviewer'
        case.runtime.atomic_json(case.request_path,case.request)
        response=case.response('inspect',1);response.blocks[0]['input']={'action':'read','path':'literal.txt'}
        case.responses=[response]
        save=case.runtime.Journal.save
        def crash(journal,boundary,phase,**fields):
            save(journal,boundary,phase,**fields)
            if boundary=='tools_ready' and journal.state.get('trace'):raise durability.Interrupted()
        with frame_language.use('fr'),patch.object(self_review_worker,'validate_snapshot',return_value=critic.subject),patch.object(case.runtime.Journal,'save',crash),self.assertRaises(durability.Interrupted):
            case.run_worker(critic)
        first=copy.deepcopy(case.calls[0]);case.authorize();case.responses=[case.response(text=RAW)]
        with frame_language.use('zh'),patch.object(self_review_worker,'validate_snapshot',return_value=critic.subject):
            self.assertEqual(case.run_worker(critic),0)
        self.assertEqual(len(case.calls),2)
        self.assertEqual(case.calls[1]['tools'],first['tools']);self.assertEqual(case.calls[1]['system'],first['system'])
        result=json.loads((case.request_path.parent/'result.json').read_text(encoding='utf-8'))
        self.assertEqual(len(result['trace']),1)
        self.assertEqual(json.loads(result['trace'][0]['output'])['text'],RAW)

    def test_legacy_continuation_keeps_literal_recorded_presentation_without_migration(self):
        case=self.fixture();case._crash_adapter_once()
        path=case.request_path.parent/'checkpoint.json';state=json.loads(path.read_text(encoding='utf-8'))
        state.pop('model_tools');state.pop('frame_language');state['system']=RAW
        case.runtime.atomic_json(path,state);case.authorize();case.responses=[case.response(text=RAW)]
        from test_forge_worker_durability_2610 import Adapter
        with frame_language.use('zh'):self.assertEqual(case.run_worker(Adapter()),0)
        self.assertEqual(case.calls[-1]['system'],RAW);self.assertEqual(case.calls[-1]['tools'],state['tools'])
        after=json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn('model_tools',after);self.assertNotIn('frame_language',after)

    def test_frozen_prompt_keeps_adapter_admission_on_every_model_turn(self):
        case=self.fixture();adapter=durability.Adapter()
        from unittest.mock import Mock
        adapter.system=Mock(side_effect=[RAW,'changed owned caption'])
        case.responses=[case.response('inspect',1),case.response(text=RAW)]
        self.assertEqual(case.run_worker(adapter),0)
        self.assertEqual(adapter.system.call_count,2)
        self.assertEqual([call['system'] for call in case.calls],[RAW,RAW])


if __name__=='__main__':unittest.main()
