"""Actual update reports/requests and runner inspection use a pinned frame axis."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import updates
import runner
import update_inspection
import frame_language as fl

RAW='Literal Русский 中文 {p0} <script>'


class UpdateFrameLanguages(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.config=self.root/'helene.json';self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.config)))
        self.agent=SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,frame_language=fl)
        self.choose('ru','ru')

    def choose(self,ui,frame):self.config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf8')

    def receipt(self):
        return {'id':'abcd1234','state':'done','from_version':'2.7.2','to_version':'2.8.0',
                'summary':RAW,'note':RAW,'plan':{'chat':RAW},'confirmed':{'by':'owner-words','words':RAW},
                'checks':[{'ok':True,'title':RAW,'note':RAW}],
                'trial':{'verdict':{'verdict':'accept','by':'agent','words':RAW}},
                'rollback':{'notes':[RAW],'ok':True},
                'agent_code':{'mounted':True,'edited':['a','b'],'carried':['a'],'merged':[],
                    'conflicts':[{'path':RAW,'why':RAW}],'folder':RAW,'materials_error':RAW}}

    def test_actual_reports_all16_axes_preserve_every_authored_field_and_receipt(self):
        receipt=self.receipt();before=copy.deepcopy(receipt);by_frame={}
        for ui in fl.LANGUAGES:
            for frame in fl.LANGUAGES:
                self.choose(ui,frame);answer=updates.report_note(receipt,owner=RAW,agent=self.agent)
                self.assertEqual(by_frame.setdefault(frame,answer),answer)
                self.assertGreaterEqual(answer.count(RAW),10);self.assertIn('2.7.2',answer);self.assertIn('2.8.0',answer)
                self.assertIn('/README.md',answer);self.assertEqual(receipt,before)
                fallback={'state':'done','agent_code':{'mounted':True,'no_base':RAW,'folder':RAW}}
                text=updates.report_note(fallback,agent=self.agent)
                self.assertIn(RAW,text);self.assertIn('/old-code/',text)
                self.assertIn(updates._words(self.agent,'native.updates.owner','владелец'),text)
        self.assertEqual(len(set(by_frame.values())),4)
        self.choose('zh','en');self.assertIn('владелец',updates.report_note(receipt,owner='владелец',agent=self.agent))

    def test_pinned_rpc_request_is_one_effect_and_uncertain_failure_does_not_claim_no_execution(self):
        self.choose('zh','en');control=Mock();control.update_state.return_value={}
        def plan(*args,**kwargs):self.choose('en','fr');raise TimeoutError(RAW)
        control.update_plan.side_effect=plan
        hand=updates.make_hand(self.root,lambda:True,lambda:RAW,agent=self.agent)
        with patch.object(updates,'_control',return_value=control):answer=hand(action='plan',owner_words=RAW,reason=RAW)
        control.update_plan.assert_called_once();control.update_confirm.assert_not_called()
        self.assertIn('outcome is unconfirmed',answer);self.assertIn(RAW,answer);self.assertIn('TimeoutError',answer)
        self.assertEqual(fl.language(),'fr');self.assertEqual(control.update_plan.call_args.kwargs['consent_words'],RAW)

    def test_preflight_refusal_cannot_forge_owner_consent_or_mutate_payload(self):
        control=Mock();control.update_state.return_value={'receipt':{'id':'abcd1234','nonce':'literal-nonce','state':'awaiting'}}
        hand=updates.make_hand(self.root,lambda:False,lambda:RAW,agent=self.agent)
        for frame in fl.LANGUAGES:
            self.choose('en',frame)
            with patch.object(updates,'_control',return_value=control):
                for action in ('plan','confirm'):
                    answer=hand(action=action,owner_words=RAW);self.assertIsInstance(answer,str)
            control.update_plan.assert_not_called();control.update_confirm.assert_not_called()

    def test_actual_runner_inspection_report_archives_same_pinned_text_sent_to_turn(self):
        receipt={'id':'abcd1234','kind':'package','state':'installed','to_version':'2.8.0'}
        request={'id':'inspect-literal','state':'pending','goals':[{'id':'goal-literal','title':RAW}]}
        for ui in fl.LANGUAGES:
            for frame in fl.LANGUAGES:
                self.choose(ui,frame);desk=Mock();turn=Mock(return_value='spoken');mark=Mock()
                with patch.multiple(runner,_tree=self.root,_desk=desk,_agent=self.agent,_speaker=RAW,_self_review=None,_turn_in_window=turn), \
                     patch.object(updates,'pending_report',return_value=receipt),patch.object(updates,'report_mark',return_value={}), \
                     patch.object(updates,'mark_reported',mark),patch('localharness.source_update.pending',return_value=False), \
                     patch.object(update_inspection,'ensure',return_value=request),patch.object(update_inspection,'read',return_value={}), \
                     patch.dict(sys.modules,{'llm':SimpleNamespace(configured=lambda:True)}):
                    runner._update_report_due()
                self.assertEqual(desk.archive.call_count,1);self.assertEqual(desk.life.call_count,1);turn.assert_called_once()
                note=desk.archive.call_args.args[0];self.assertEqual(turn.call_args.kwargs['origin_text'],note)
                self.assertEqual(turn.call_args.args[0],'update-abcd1234-installed');self.assertIn(RAW,note)
                with fl.use(frame):self.assertIn(fl.text('native.updates.inspection',p0=request['id'],p1='- goal-literal: '+RAW),note)
                self.assertTrue(mark.call_args.kwargs['noted']);self.assertFalse(mark.call_args.kwargs['done'])

    def test_legacy_core_keeps_original_callable_contract_and_prose(self):
        legacy=SimpleNamespace();receipt={'state':'trial','from_version':'1.0','to_version':'1.1'}
        self.choose('zh','en');answer=updates.report_note(receipt,agent=legacy)
        self.assertTrue(answer.startswith('Системное сообщение: проверь обновление'))
        self.assertEqual(answer,updates.trial_note(receipt))


if __name__=='__main__':unittest.main()
