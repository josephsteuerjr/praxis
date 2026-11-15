"""Actual Telegram producer leaves with fake SDK effects and literal receipts."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch
from telethon.tl.types import InputPeerUser
import frame_language as fl
import mtproto_runner as r

LANGS=('ru','en','fr','zh')
RAW='Authored Русский 中文 <value>'


class Client:
    def __init__(self,callback=None):self.requests=[];self.callback=callback
    async def __call__(self,request):
        self.requests.append(request)
        if self.callback:self.callback()
        await asyncio.sleep(0)
        return SimpleNamespace()


class RunnerResultLanguage(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name);self.path=self.base/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.path)))
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.path.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def entry(self,kind='text'):
        return {'state':'accepted','kind':kind,'peer_id':'-1001','topic_id':11,
            'receipt':{'message_id':42,'media_kind':'photo'},
            'payload':{'text':RAW,'visible_filename':RAW+'.png','media_kind':'photo'}}

    def test_accepted_receipts_keep_raw_payload_and_exact_ids_across16_axes(self):
        for kind in ('text','file'):
            entry=self.entry(kind);saved=copy.deepcopy(entry);by_frame={}
            for ui in LANGS:
                for frame in LANGS:
                    self.choose(ui,frame)
                    answer=r._direct_outbox_result(entry,label=RAW)
                    self.assertIn(RAW,answer);self.assertIn('message_id=42',answer)
                    self.assertIn('chat_id=-1001#topic:11',answer)
                    self.assertEqual(by_frame.setdefault(frame,answer),answer)
                    self.assertEqual(entry,saved)
            self.assertEqual(len(set(by_frame.values())),4)

    def test_long_receipt_preview_is_explicit_and_does_not_expand_exposure(self):
        entry=self.entry();entry['payload']['text']='😀'*60+'DO_NOT_EXPOSE_AFTER_PREVIEW'
        self.choose('zh','en')
        answer=r._direct_outbox_result(entry,label='fixture')
        self.assertTrue(answer.endswith('😀'*60+'…'))
        self.assertNotIn('DO_NOT_EXPOSE',answer)

    def test_ambiguous_book_keeps_names_and_ids_without_guessing(self):
        rows=[{'id':101,'display_name':RAW+' A','lexical':140},{'id':102,'display_name':RAW+' B','lexical':140}]
        saved=copy.deepcopy(rows)
        for frame in LANGS:
            self.choose('fr',frame)
            answer=r._ambiguous_book(RAW,rows)
            self.assertIn(RAW,answer);self.assertIn('101',answer);self.assertIn('102',answer)
            self.assertIn(RAW+' A',answer);self.assertIn(RAW+' B',answer)
            self.assertIsNone(r._ambiguous_book(RAW,rows[:1]))
        self.assertEqual(rows,saved)

    def test_address_book_age_reports_observation_instead_of_calendar_or_meeting(self):
        self.choose('zh','en');row={'id':101,'display_name':RAW,'last_seen':None}
        with patch.object(r.time,'time',return_value=1_000_000):
            self.assertIn('unknown',r._book_row_label(row))
            row['last_seen']=999_000
            self.assertIn('less than a day ago',r._book_row_label(row))
            row['last_seen']=827_200
            self.assertIn('2 days ago',r._book_row_label(row))

    def test_actual_async_profile_request_preserves_fields_and_pins_before_await(self):
        self.choose('zh','en');client=Client(lambda:self.choose('zh','fr'))
        with patch.object(r,'client',client):
            answer=asyncio.run(r._update_profile_async(RAW,RAW,''))
        self.assertEqual(len(client.requests),1)
        request=client.requests[0]
        self.assertEqual((request.about,request.first_name,request.last_name),(RAW,RAW,None))
        self.assertTrue(answer.startswith('Profile update accepted:'))
        self.assertIn(RAW,answer)
        with patch.object(r,'client',client):
            answer=asyncio.run(r._update_profile_async('','',''))
        self.assertEqual(answer,fl.text('helper.mtproto_runner._update_profile_async.7478'))
        self.assertEqual(len(client.requests),1)

    def test_actual_reaction_request_preserves_emoji_and_refuses_before_effect(self):
        for frame in LANGS:
            self.choose('en',frame);client=Client();peer=InputPeerUser(777,1)
            with patch.object(r,'client',client),patch.object(r,'_resolve_entity',new=AsyncMock(return_value=peer)):
                answer=asyncio.run(r._react_async('777',42,'👍',False))
            self.assertIn('👍',answer);self.assertIn('42',answer)
            self.assertEqual(len(client.requests),1)
            self.assertEqual(client.requests[0].reaction[0].emoticon,'👍')
            self.assertEqual(client.requests[0].msg_id,42)
            with patch.object(r,'_telegram_account_gate',return_value=None), \
                 patch.object(r,'_threadsafe_result',side_effect=AssertionError('effect')) as dispatch:
                refused=r._sync_react(message_id=0,emoji='👍')
            dispatch.assert_not_called()
            self.assertEqual(refused,fl.text('helper.mtproto_runner._sync_react.7518'))

    def test_followup_watch_control_is_canonical_and_sends_no_report(self):
        ledger=SimpleNamespace(set_notice=Mock(return_value={}),cancel=Mock(return_value=True))
        for frame in LANGS:
            self.choose('en',frame)
            with patch.object(r,'_telegram_account_gate',return_value=None), \
                 patch.object(r.telegram_followups,'LEDGER',ledger):
                answer=r._sync_followups('watch',RAW)
                ledger.set_notice.assert_called_with(RAW,True,source='praxis')
                self.assertIn(RAW,answer)
                answer=r._sync_followups('unwatch',RAW)
                ledger.set_notice.assert_called_with(RAW,False,source='praxis')
                self.assertIn(RAW,answer)
                self.assertIn(RAW,r._sync_followups('cancel',RAW))
        self.assertEqual(ledger.set_notice.call_count,8);self.assertEqual(ledger.cancel.call_count,4)

    def test_frozen_guard_keeps_its_predicate_and_literal_target(self):
        for frame in LANGS:
            self.choose('fr',frame)
            with patch.object(r.rooms,'is_frozen',return_value=True) as check:
                answer=r._frozen_refusal('-1001',RAW)
            check.assert_called_once_with('-1001')
            self.assertIn(RAW,answer);self.assertIn('freeze_chat on=false',answer)
            with patch.object(r.rooms,'is_frozen',return_value=False):
                self.assertIsNone(r._frozen_refusal('-1001',RAW))
