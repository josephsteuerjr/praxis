"""Exercise localized preflight/timeout boundaries without a Telegram account."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import frame_language as fl
import mtproto_runner as r

LANGS=('ru','en','fr','zh')
RAW='Authored 中文 Русский'


class RunnerDiagnosticLanguage(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.path)))
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.path.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def test_membership_refs_keep_exact_wire_targets_across16_axes(self):
        refusals={}
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame)
                for raw,want in [('https://t.me/+abcdefgh',('invite','abcdefgh')),
                                 ('tg://join?invite=Abcd_123',('invite','Abcd_123')),
                                 ('https://t.me/publicname',('public','@publicname')),
                                 ('-100123',('entity','-100123'))]:
                    self.assertEqual(r._membership_target(raw),want)
                with self.assertRaises(ValueError) as caught:r._membership_target('https://t.me/publicname/42')
                text=str(caught.exception)
                self.assertEqual(refusals.setdefault(frame,text),text)
        self.assertEqual(len(set(refusals.values())),4)

    def test_account_refusal_precedes_intent_or_dispatch(self):
        for frame in LANGS:
            self.choose('zh',frame)
            with patch.object(r.agent,'_is_sovereign_actor',return_value=False), \
                 patch.object(r,'_membership_ledger',side_effect=AssertionError('intent')) as ledger, \
                 patch.object(r,'_threadsafe_result',side_effect=AssertionError('dispatch')) as dispatch:
                denied=r._telegram_account_gate()
                with self.assertRaises(PermissionError) as caught:r._begin_membership_transaction('join','@publicname')
                self.assertEqual(str(caught.exception),denied)
                self.assertEqual(r._sync_telegram_account(action='call'),denied)
                ledger.assert_not_called();dispatch.assert_not_called()

    def test_history_preflight_never_schedules_invalid_ranges(self):
        cases=[{}, {'ceiling_id':9}, {'ceiling_id':9,'freeze':True,'apply':True},
               {'ceiling_id':'latest','freeze':True,'floor_id':1}]
        for frame in LANGS:
            self.choose('en',frame)
            with patch.object(r,'OWNER_ID',123),patch.object(r,'client',object()), \
                 patch.object(r.asyncio,'run_coroutine_threadsafe',side_effect=AssertionError('dispatch')) as schedule:
                for params in cases:
                    before=copy.deepcopy(params)
                    self.assertIn('history_scan',r._sync_history_scan(RAW,params,_principal='123'))
                    self.assertEqual(params,before)
                self.assertIn('history_scan',r._sync_history_scan(RAW,{},_principal='456'))
                schedule.assert_not_called()

    def test_history_timeout_schedules_once_and_does_not_claim_rollback(self):
        scheduled=[]
        def schedule(coro,loop):
            scheduled.append(coro);coro.close()
            return SimpleNamespace(result=Mock(side_effect=TimeoutError))
        self.choose('zh','en')
        with patch.object(r,'OWNER_ID',123),patch.object(r,'client',object()), \
             patch.object(r.asyncio,'run_coroutine_threadsafe',side_effect=schedule):
            answer=r._sync_history_scan(RAW,{'floor_id':1,'ceiling_id':9,'apply':True},_principal='123')
        self.assertEqual(len(scheduled),1)
        self.assertIn('unconfirmed',answer);self.assertIn('write may have completed',answer)
        self.assertNotIn('nothing',answer)

    def test_strict_moderation_and_admin_validation_keep_original_inputs(self):
        client=SimpleNamespace(is_connected=lambda:True)
        invalid=[{'peer_id':1,'message_id':2,'sender_id':3,'decision':'delete','extra':RAW},
                 {'peer_id':1},
                 {'peer_id':True,'message_id':2,'sender_id':3,'decision':'delete'},
                 {'peer_id':1,'message_id':2,'sender_id':3,'decision':42}]
        for frame in LANGS:
            self.choose('fr',frame)
            with patch.object(r,'client',client),patch.object(r,'_LOOP',object()), \
                 patch.object(r,'_telegram_account_gate',return_value=None), \
                 patch.object(r.asyncio,'run_coroutine_threadsafe',side_effect=AssertionError('dispatch')) as schedule:
                for data in invalid:
                    before=copy.deepcopy(data)
                    self.assertIn('moderation',r._sync_moderate_abstractdl(data))
                    self.assertEqual(data,before)
                self.assertIn('admin',r._sync_admin_abstractdl({'peer_id':1,'extra':RAW}))
                schedule.assert_not_called()

    def test_successful_dispatch_keeps_canonical_json_and_single_effect(self):
        wire={'status':'completed','idempotency_key':RAW,'peer_id':-100123,'deleted':True}
        scheduled=[]
        def schedule(coro,loop):
            scheduled.append(coro);coro.close()
            return SimpleNamespace(result=lambda **kw:copy.deepcopy(wire))
        for frame in LANGS:
            self.choose('en',frame)
            with patch.object(r,'client',SimpleNamespace(is_connected=lambda:True)), \
                 patch.object(r,'_LOOP',object()),patch.object(r,'_telegram_account_gate',return_value=None), \
                 patch.object(r,'_telegram_account_principal',return_value='123'), \
                 patch.object(r.asyncio,'run_coroutine_threadsafe',side_effect=schedule):
                result=r._sync_moderate_abstractdl({'peer_id':-100123,'message_id':2,'sender_id':3,'decision':'delete'})
            self.assertEqual(json.loads(result),wire)
        self.assertEqual(len(scheduled),4)

    def test_raw_exception_is_preserved_in_localized_wrapper(self):
        def schedule(coro,loop):
            coro.close();return SimpleNamespace(result=Mock(side_effect=RuntimeError(RAW)))
        for frame in LANGS:
            self.choose('fr',frame)
            with patch.object(r,'client',SimpleNamespace(is_connected=lambda:True)), \
                 patch.object(r,'_LOOP',object()),patch.object(r,'_telegram_account_gate',return_value=None), \
                 patch.object(r,'_telegram_account_principal',return_value='123'), \
                 patch.object(r.asyncio,'run_coroutine_threadsafe',side_effect=schedule):
                answer=r._sync_admin_abstractdl({'peer_id':-100123,'action':'slow_mode','params':{}})
            self.assertIn('RuntimeError',answer);self.assertIn(RAW,answer)
