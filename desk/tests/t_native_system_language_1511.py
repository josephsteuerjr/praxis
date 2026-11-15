"""SYSTEM captions must not infer execution, installed services or a local target."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import frame_language as fl
from localharness import body

LANGS=('ru','en','fr','zh')
RAW='Literal Русский 中文 {goal}'


class SystemLanguages(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.config=self.root/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.config),HELENE_BROKER_DISABLED='0'))
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def agent(self,hand=None):
        return SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,frame_language=fl,TOOL_IMPL={'broker_request':hand} if hand else {})

    def test_actual_broker_call_preserves_payload_receipt_and16_independent_axes(self):
        arguments={'command':RAW,'goal':RAW,'timeout_ms':45000};before=copy.deepcopy(arguments);by_frame={}
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame);hand=Mock(return_value='RAW receipt: '+RAW)
                answer=body._system_via_broker(self.agent(hand),arguments)
                hand.assert_called_once();call=hand.call_args.kwargs
                self.assertEqual(call['op'],'exec');self.assertEqual(call['action'],'ask')
                self.assertEqual((call['cmd'],call['args']),body._system_shell(RAW))
                self.assertEqual(call['timeout_sec'],45);self.assertEqual(call['why'],RAW)
                self.assertTrue(answer.endswith('RAW receipt: '+RAW));self.assertEqual(arguments,before)
                self.assertEqual(by_frame.setdefault(frame,answer),answer)
        self.assertEqual(len(set(by_frame.values())),4)

    def test_rpc_return_does_not_claim_system_execution_and_language_is_pinned(self):
        self.choose('zh','en')
        def hand(**kw):self.choose('en','fr');return 'REFUSED '+RAW
        answer=body._system_via_broker(self.agent(hand),{'command':RAW})
        self.assertIn('determine execution from the receipt',answer)
        self.assertTrue(answer.endswith('REFUSED '+RAW));self.assertEqual(fl.language(),'fr')

    def test_missing_tool_does_not_probe_or_claim_no_installed_service(self):
        self.choose('fr','en')
        with patch.object(body,'_raw_request',side_effect=AssertionError('probe')) as rpc:
            answer=body._system_via_broker(self.agent(),{'command':RAW})
        rpc.assert_not_called();self.assertIn('does not establish whether a service is installed',answer)

    def test_disabled_route_and_empty_command_refuse_before_dispatch(self):
        for frame in LANGS:
            self.choose('en',frame);hand=Mock(side_effect=AssertionError('dispatch'))
            with patch.dict(os.environ,HELENE_BROKER_DISABLED='1'):
                self.assertIn('execution=system',body._system_via_broker(self.agent(hand),{'command':RAW}))
            self.assertIn('execution=system',body._system_via_broker(self.agent(hand),{'command':'  '}))
            hand.assert_not_called()

    def test_remote_identity_mismatch_keeps_exact_selected_route_and_never_falls_back(self):
        for frame in LANGS:
            self.choose('zh',frame);local=Mock(side_effect=AssertionError('local fallback'))
            raw={'host':'linux','device_id':'other-device','receipt':RAW}
            with patch.object(body,'_settings',return_value=('http://fixture','secret-fixture','selected-device')), \
                 patch.object(body,'_raw_request',return_value=raw) as rpc:
                answer=body._remote_system(self.agent(local),{'command':RAW,'goal':RAW,'timeout_ms':45000})
            rpc.assert_called_once();args=rpc.call_args.args
            self.assertEqual(args[:2],('POST','/v1/controller/selected-device/broker'))
            self.assertEqual(args[2],{'op':'exec','shell':'powershell','command':RAW,'why':RAW,'timeout_sec':45})
            self.assertIn(str(raw),answer);self.assertNotIn('secret-fixture',answer);local.assert_not_called()

    def test_remote_preflight_and_timeout_never_try_another_target(self):
        local=Mock(side_effect=AssertionError('local fallback'));agent=self.agent(local)
        with patch.object(body,'_settings',return_value=('http://fixture','secret-fixture','')),patch.object(body,'_raw_request') as rpc:
            body._remote_system(agent,{'command':RAW});rpc.assert_not_called()
        with patch.object(body,'_settings',return_value=('http://fixture','secret-fixture','selected-device')), \
             patch.object(body,'_raw_request',side_effect=TimeoutError('unknown outcome')) as rpc:
            with self.assertRaises(TimeoutError):body._remote_system(agent,{'command':RAW})
        rpc.assert_called_once();local.assert_not_called()

    def test_legacy_fallback_is_owned_and_does_not_claim_service_absence(self):
        answer=body._system_via_broker(SimpleNamespace(TOOL_IMPL={}),{'command':RAW})
        self.assertIn('не проверка наличия службы',answer)
        self.assertNotIn('служба не установлена',answer)
