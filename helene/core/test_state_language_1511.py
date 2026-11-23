"""Exercise actual owned state builders without providers, process control or writes."""
import ast
import copy
from contextlib import nullcontext
import datetime
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace,ModuleType
import unittest
from unittest.mock import patch

import frame_language

ROOT=Path(__file__).parent
RAW='AUTHORED Русский 日本語 {v0} $&'


def functions(file,names,**namespace):
    tree=ast.parse((ROOT/file).read_text(encoding='utf-8'))
    nodes=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names]
    assert len(nodes)==len(names),(file,names)
    scope={'frame_language':frame_language,'time':time,'json':json,**namespace}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/file),'exec'),scope)
    return SimpleNamespace(**{name:scope[name] for name in names})


class StateLanguage(unittest.TestCase):
    def test_model_configuration_and_observed_backend_keep_raw_names(self):
        snapshot={role:{'model':RAW,'framework':'openai','on_fallback':True} for role in ('voice','evaluator')}
        before=copy.deepcopy(snapshot)
        built=functions('llm.py',['state_line'],ROLES=('voice','evaluator'),snapshot=lambda:snapshot,
                        observed_models=lambda role:({'model':RAW+'-actual','at':'2026-11-15T00:00Z'},{RAW:2,RAW+'-actual':1}))
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                result=built.state_line()
                self.assertIn(frame_language.text('state.role.voice'),result)
                self.assertIn(frame_language.text('state.role.evaluator'),result)
                self.assertIn(RAW+'-actual',result)
                self.assertNotIn('настроено',result.replace(RAW,'') if language!='ru' else '')
        self.assertEqual(snapshot,before)

    def test_budget_and_pause_truth_stay_identical_across_languages(self):
        state={'mode':'considerate','owner_request':{'kind':'pause_background','ts':2},
               'interpretation':{'ts':1},'promise':{'daily_tokens':1000,'daily_cost':1},
               'external_provider_boundary':'PLAN_QUOTA'}
        observed={'tokens_today':2000,'calls_today':2,'estimated_cost_today':2}
        before=copy.deepcopy(state)
        built=functions('appetite.py',['_k','promise_check','background_hold','promise_line','state_line','short_line','usage_delta'],
                        _load=lambda:state,observed_today=lambda:observed,
                        unacked_request=lambda:{'kind':'pause_background'},
                        _provider_remaining_value=lambda value,key:None,
                        log=SimpleNamespace(debug=lambda *a,**k:None))
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                self.assertTrue(built.background_hold())
                self.assertTrue(built.promise_check())
                rendered=built.state_line()
                self.assertIn('PLAN_QUOTA',rendered);self.assertIn('manage_appetite',rendered)
                self.assertIn('considerate',built.short_line())
                self.assertNotIn('Алекс',rendered)
                self.assertIn('2',built.usage_delta({'tokens_today':0,'calls_today':0,'estimated_cost_today':0}))
                observed['tokens_today']=0
                unknown=built.usage_delta({'tokens_today':0,'calls_today':0,'estimated_cost_today':0})
                self.assertIn('relay' if language in ('ru','en') else ('relais' if language=='fr' else '中继'),unknown)
                observed['tokens_today']=2000
        self.assertEqual(state,before)
        state['owner_request']={};state['mode']='free'
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):self.assertIsNone(built.background_hold())

    def test_one_render_pins_the_choice_even_when_config_changes_mid_read(self):
        config={'agent':{'frame_language':'en'}}
        def load():
            config['agent']['frame_language']='fr'
            return {'mode':'free','external_provider_boundary':'absent'}
        built=functions('appetite.py',['state_line'],_load=load,observed_today=lambda:{},
                        unacked_request=lambda:None,background_hold=lambda:None,
                        log=SimpleNamespace(debug=lambda *a,**k:None))
        with patch.object(frame_language,'settings',side_effect=lambda:copy.deepcopy(config)):
            self.assertEqual(built.state_line(),'mode free')
            # Different wording is intentional only on the following read.
            config['agent']['frame_language']='zh'
            self.assertEqual(built.state_line(),'模式 free')

    def test_identity_unanswered_and_process_data_are_literal(self):
        identity=functions('identity.py',['stress_line','state_line'],
                           os=SimpleNamespace(getenv=lambda name,default:default),HALF_LIFE_DAYS=7,
                           stress=lambda:[{'theme':RAW,'score':2.5,'count':3}],
                           recent_shifts=lambda limit:[{'kind':'identity_shift','ts':'2026-11-15T00:00','text':RAW}])
        unanswered=functions('unanswered.py',['_fmt_age','state_line'],
                             entries=lambda now:[{'name':RAW,'chat_id':'1','hours':0.5},{'name':'Other','chat_id':'2','hours':52}])
        rows=[{'name':RAW,'pid':i,'ts':0,'run_id':'run-RAW','run_kind':'task_window'} for i in range(10)]
        processes=functions('core/processes.py',['state_line'],_alive=lambda row:True,live=lambda **kw:rows)
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                self.assertIn(RAW,identity.state_line());self.assertIn('2.5',identity.stress_line())
                self.assertIn(RAW,unanswered.state_line(cap=1));self.assertIn('30',unanswered.state_line(cap=1))
                value=processes.state_line()
                self.assertIn(RAW,value);self.assertIn('task_window',value);self.assertIn('pid 9',value)
                if language!='ru':
                    self.assertNotIn('соб.',identity.stress_line().replace(RAW,''))
                    self.assertNotIn('запущен',value.replace(RAW,''))

    def test_actual_evidence_envelope_keeps_typed_keys_and_authored_content(self):
        empty=SimpleNamespace(state_line=lambda **kw:'')
        core=ModuleType('core');core.processes=SimpleNamespace(state_line=lambda:RAW)
        built=functions('agent.py',['build_state_evidence_block'],
            NOT_HERS_LABELS=set(),build_runner_counters=lambda:{'calls':1},
            build_frame_tail=lambda:None,_notices_block=lambda **kw:RAW,
            pre_model_timing=SimpleNamespace(span=lambda name:nullcontext()),
            _read=lambda path:'',STATE_DIR=Path('unused'),MEM_DIR=Path('unused'),BASE=Path('unused'),
            memory_life=SimpleNamespace(fold_offers=lambda:{'room-RAW':{'count':10,'keep':5,'hi':8,'hard_hi':20,'hard':False}}),
            appetite=SimpleNamespace(state=lambda:{},state_line=lambda:''),llm=empty,capabilities=empty,unanswered=empty,
            identity=empty,computer_memory=SimpleNamespace(recent_artifact_evidence=lambda:[]),
            turns=empty,_BOOT_TS=datetime.datetime.now(),undelivered_media_state_line=lambda:[],
            selfdev=SimpleNamespace(pending_review=lambda:[]),selfgit=SimpleNamespace(recent=lambda limit:[]))
        keys=None
        for language in frame_language.LANGUAGES:
            with frame_language.use(language),patch.dict('sys.modules',{'core':core}):
                rows=[json.loads(line) for line in built.build_state_evidence_block().splitlines()]
                current=[row['label'] for row in rows]
                if keys is None:keys=current
                self.assertEqual(current,keys)
                fold=next(row for row in rows if row['label']=='fold_offers')['content']
                self.assertEqual(fold['places'][0]['place'],'room-RAW')
                self.assertEqual(fold['places'][0]['hot'],10)
                self.assertIn('memory_compact(prepare, place)',fold['note'])
                self.assertIn('fold(selection,text)',fold['note'])
                if language!='ru':self.assertNotRegex(fold['note'],'[А-Яа-яЁё]')
                self.assertEqual(next(row['content'] for row in rows if row['label']=='background_processes'),RAW)


if __name__=='__main__':unittest.main()
