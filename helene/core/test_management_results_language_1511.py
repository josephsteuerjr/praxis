"""Independent language axes around management effects and actual durable records."""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import agent
import brain
import desires
import frame_language as frame
import identity
import perception

RAW='Windows Егор 中文 {literal} $&'

class ManagementResults(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(agent,'BASE',self.root))
        self.stack.enter_context(patch.object(agent,'_active_scope',return_value='owner'))
        self.stack.enter_context(patch.object(agent.run_context,'current_run',return_value=None))
        self.stack.enter_context(patch.object(agent,'_reindex'))
    def text(self,language,key,**values):
        with frame.use(language):return frame.text('management.result.'+key,**values)
    def test_brain_profile_switch_reasoning_and_errors_follow_frame_all16_axes_with_exact_calls(self):
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(brain,'apply_profile',return_value={'ok':True,'profile':'deep','model':RAW,'effort':'high'}) as profile,patch.object(brain,'switch',return_value={'ok':True,'role':'voice','framework':'account-b','model':RAW,'was':'previous'}) as switch,patch.object(brain,'set_reasoning',return_value={'ok':True,'role':'voice','was':'low','effort':''}) as reasoning:
                    self.assertEqual(agent.tool_switch_brain('profile',profile='deep',why=RAW,endpoint='account-b'),self.text(language,'profile',profile='deep',model=RAW,effort='high'))
                    profile.assert_called_once_with('deep',why=RAW,endpoint='account-b')
                    self.assertEqual(agent.tool_switch_brain('switch',role='voice',model=RAW,why=RAW,endpoint='account-b'),self.text(language,'switch',role='voice',framework='account-b',model=RAW,old='previous'))
                    switch.assert_called_once_with('voice',RAW,why=RAW,endpoint='account-b')
                    out=agent.tool_switch_brain('reasoning',role='voice',effort='',why=RAW)
                    self.assertIn(self.text(language,'reasoning_default'),out);reasoning.assert_called_once_with('voice','',why=RAW)
                with frame.use(language),patch.object(brain,'switch',return_value={'ok':False,'error':RAW}):
                    self.assertEqual(agent.tool_switch_brain('switch'),self.text(language,'switch_error',error=RAW))
    def test_unauthorized_actions_do_not_reach_mutators_and_preserve_denial_and_echo(self):
        for language in frame.LANGUAGES:
            with frame.use(language),patch.object(agent,'_active_scope',return_value='unknown'),patch.object(agent,'_is_sovereign_actor',return_value=False),patch.object(agent.rails,'deny') as deny,patch.object(brain,'switch') as switch,patch.object(brain,'use_account') as account,patch.object(perception,'set_knob') as knob,patch.object(identity,'revise') as revise:
                self.assertEqual(agent.tool_switch_brain('switch'),self.text(language,'brain_scope'))
                self.assertEqual(agent.tool_switch_brain('use_account'),self.text(language,'brain_scope'))
                self.assertEqual(agent.tool_manage_perception('set'),self.text(language,'perception_scope'))
                self.assertEqual(agent.tool_manage_identity('revise'),self.text(language,'identity_scope'))
                out=agent.tool_manage_desire('notice',statement=RAW,note=RAW,next_move=RAW)
                self.assertEqual(out.count(RAW),3);self.assertIn(self.text(language,'echo',text='statement: '+RAW+' | note: '+RAW+' | next_move: '+RAW),out)
                for effect in (switch,account,knob,revise):effect.assert_not_called()
                self.assertEqual(deny.call_count,5)
                self.assertFalse((self.root/'memory/desires/events.jsonl').exists())
    def test_identity_revision_retains_refs_actor_and_authored_content_and_localizes_only_receipt(self):
        current=SimpleNamespace(context_snapshot='snapshot-ref',origin_chat_id='42',origin_message_ids=(7,8))
        for language in frame.LANGUAGES:
            with frame.use(language),patch.object(agent.run_context,'current_run',return_value=current),patch.object(identity,'revise',return_value={'ok':True,'version':12,'history':'literal/path'}) as revise:
                self.assertEqual(agent.tool_manage_identity('revise',name='self',text=RAW,reason=RAW),self.text(language,'self_revision',version=12,history='literal/path'))
                revise.assert_called_once_with('self',RAW,reason=RAW,refs=['snapshot-ref','telegram:42:7','telegram:42:8'],by='praxis',source='tool')
            with frame.use(language),patch.object(identity,'rollback',return_value={'ok':True,'version':13,'restored_version':2}) as rollback:
                self.assertEqual(agent.tool_manage_identity('rollback',name='self',version=2,reason=RAW),self.text(language,'self_rollback',version=13,restored=2))
                rollback.assert_called_once_with('self',2,reason=RAW,refs=[],by='praxis')
    def test_load_acknowledges_real_append_and_does_not_claim_success_on_swallowed_write_error(self):
        path=self.root/'load.jsonl'
        with patch.object(identity,'LOAD_PATH',path),patch.object(agent,'_active_chat',return_value='42'):
            for language in frame.LANGUAGES:
                with frame.use(language):
                    self.assertEqual(agent.tool_manage_identity('load',theme=RAW,amplitude=2,detail=RAW),self.text(language,'load'))
            rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(rows),4)
            for row in rows:self.assertEqual((row['theme'],row['detail'],row['amp'],row['source'],row['chat']),(RAW,RAW,2,'praxis','42'))
        failed=SimpleNamespace(parent=SimpleNamespace(mkdir=Mock()),open=Mock(side_effect=OSError(RAW)))
        for language in frame.LANGUAGES:
            with frame.use(language),patch.object(identity,'LOAD_PATH',failed):
                self.assertFalse(identity.record_load(RAW,1,source='test'))
                self.assertEqual(agent.tool_manage_identity('load',theme=RAW),self.text(language,'load_unknown'))
        self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()),4)
    def test_real_desire_notice_and_transition_keep_content_provenance_and_full_history(self):
        for language in frame.LANGUAGES:
            name='desire-'+language
            with frame.use(language):
                agent.tool_manage_desire('notice',desire_id=name,statement=RAW,source=RAW,why_it_matters=RAW,next_move=RAW,evidence_refs=['artifact:literal'])
                agent.tool_manage_desire('want',desire_id=name,note=RAW,next_move=RAW)
                state=json.loads(agent.tool_manage_desire('get',desire_id=name))
                self.assertEqual((state['statement'],state['source'],state['why_it_matters'],state['next_move']),(RAW,RAW,RAW,RAW))
                self.assertEqual(state['stage'],'wanted');self.assertIn('artifact:literal',state['evidence_refs'])
                self.assertEqual(state['timeline'][-1]['note'],RAW)
                event=desires.DesireLedger(self.root).events(name)[-1]
                self.assertEqual(event['actor'],'praxis');self.assertEqual(event['note'],RAW)
    def test_paging_names_both_cuts_without_mutating_original_timeline_all16_axes(self):
        states=[{'id':'desire-'+str(i),'statement':RAW,'timeline':[{'note':RAW,'number':n} for n in range(9)]} for i in range(23)]
        original=copy.deepcopy(states)
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                ledger=SimpleNamespace(list=lambda:states)
                with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent.desires,'DesireLedger',return_value=ledger):
                    output=json.loads(agent.tool_manage_desire('list'))
                self.assertEqual(len(output),21)
                self.assertEqual(output[-1],{'_truncation':self.text(language,'desires_cut',shown=20,total=23)})
                self.assertEqual(output[0]['timeline_truncation'],self.text(language,'timeline_cut',shown=4,total=9,id='desire-0'))
                self.assertEqual(output[0]['statement'],RAW);self.assertEqual(output[0]['timeline'],states[0]['timeline'][-4:])
        self.assertEqual(states,original)
    def test_perception_effect_arguments_and_reason_are_literal(self):
        for language in frame.LANGUAGES:
            with frame.use(language),patch.object(perception,'set_knob',return_value={'ok':True,'old':2,'new':9}) as setter:
                self.assertEqual(agent.tool_manage_perception('set',knob='debounce_sec',value='9',reason=RAW),self.text(language,'perception_set',knob='debounce_sec',old=2,new=9,reason=self.text(language,'reason_recorded')))
                setter.assert_called_once_with('debounce_sec','9',by='praxis',reason=RAW)
    def test_pin_keeps_receipt_coherent_if_backend_changes_next_turn_configuration(self):
        settings={'agent':{'frame_language':'fr'}}
        def backend(*args,**kwargs):
            settings['agent']['frame_language']='zh';return {'ok':True,'profile':'deep','model':RAW,'effort':'high'}
        with patch.object(frame,'settings',side_effect=lambda:settings),patch.object(brain,'apply_profile',side_effect=backend):
            first=agent.tool_switch_brain('profile',profile='deep',why=RAW)
            second=agent.tool_switch_brain('profile',profile='deep',why=RAW)
        self.assertEqual(first,self.text('fr','profile',profile='deep',model=RAW,effort='high'))
        self.assertEqual(second,self.text('zh','profile',profile='deep',model=RAW,effort='high'))
