"""Language choices must not alter authority, retry clocks or Telegram payloads."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

import mtproto_runner as r

LANGS=('ru','en','fr','zh')
RAW='Nom 中文 Русский'


class RunnerCognitionLanguage(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.path)))
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.path.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def test_recent_sender_captions_keep_literal_names_scope_and_age_across16_axes(self):
        by_frame={};rows={'-1001':[(880,RAW,777)]}
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame)
                with patch.object(r,'_recent_senders',rows),patch.object(r.time,'time',return_value=1000):
                    result=r._recent_senders_hits(RAW)
                self.assertEqual(len(result),1)
                self.assertIn(RAW,result[0]);self.assertIn('id=777',result[0])
                self.assertIn('-1001',result[0]);self.assertIn('2',result[0])
                self.assertEqual(by_frame.setdefault(frame,result),result)
        self.assertEqual(len({tuple(v) for v in by_frame.values()}),4)
        self.assertEqual(rows,{'-1001':[(880,RAW,777)]})

    def test_one_mind_deferral_keeps_canonical_class_and_does_not_recount_after_language_change(self):
        self.choose('zh','en');note=Mock()
        with patch.object(r,'_ONE_MIND',SimpleNamespace(generation=42)), \
             patch.object(r,'_ONE_MIND_DEFERRED',{}),patch.object(r.perception,'note_skip',note):
            asyncio.run(r._note_one_mind_defer('fixture',RAW))
            self.choose('en','fr')
            asyncio.run(r._note_one_mind_defer('fixture',RAW))
        note.assert_called_once()
        self.assertEqual(note.call_args.args,('one_mind:fixture','отложила'))
        self.assertIn(RAW,note.call_args.kwargs['detail'])
        self.assertIn('authored turn',note.call_args.kwargs['detail'])

    def test_dialog_warmup_timeout_does_not_cancel_or_restart_the_warmup(self):
        async def exercise():
            task=asyncio.create_task(asyncio.Event().wait())
            with patch.object(r,'_dialog_name_cache',None),patch.object(r,'_start_dialog_warmup',return_value=task) as start, \
                 patch.object(r,'DIALOG_WARMUP_WAIT_SEC',0.001):
                try:
                    with self.assertRaises(TimeoutError) as caught:await r._ensure_dialog_cache()
                    self.assertFalse(task.done());start.assert_called_once()
                    self.assertIn('id/@username',str(caught.exception))
                finally:
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
        for frame in LANGS:
            self.choose('en',frame);asyncio.run(exercise())

    def test_offline_backfill_stays_before_room_access_with_localized_explanation(self):
        by_frame={}
        for frame in LANGS:
            self.choose('en',frame);note=AsyncMock()
            with patch.object(r,'_backfill_transport_online',return_value=False), \
                 patch.object(r,'_backfill_was_online',False),patch.object(r,'_EXPECT_DISCONNECT',threading.Event()), \
                 patch.object(r,'_note_backfill_defer',note),patch.object(r.rooms,'list_rooms',side_effect=AssertionError('read rooms')) as rooms:
                asyncio.run(r._group_context_backfill_once())
            note.assert_awaited_once();rooms.assert_not_called()
            by_frame[frame]=note.call_args.args[0]
        self.assertEqual(len(set(by_frame.values())),4)

    def test_zero_random_id_refuses_before_client_access_and_valid_payload_is_literal(self):
        client=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(id=42)))
        for frame in LANGS:
            self.choose('zh',frame)
            with patch.object(r,'client',client):
                with self.assertRaises(ValueError):asyncio.run(r._send_message_idempotent(777,RAW,delivery_key='fixture',random_id=0))
            client.send_message.assert_not_awaited()
        with patch.object(r,'client',client):
            sent,random_id=asyncio.run(r._send_message_idempotent(777,RAW,delivery_key='fixture',reply_to=42,random_id=123))
        self.assertEqual((sent.id,random_id),(42,123))
        client.send_message.assert_awaited_once_with(777,RAW,reply_to=42)

    def test_durable_execution_refuses_missing_identity_and_preserves_valid_fields(self):
        data={'run_id':'r1','call_id':'c1','tool':'send_message','idempotency_key':RAW}
        for frame in LANGS:
            self.choose('en',frame)
            with patch.object(r.agent,'current_tool_execution',return_value=None):
                with self.assertRaises(r.agent.DurableExecutionError) as caught:r._direct_tool_execution('send_message')
                self.assertIn('send_message',str(caught.exception))
            with patch.object(r.agent,'current_tool_execution',return_value=data):
                self.assertEqual(r._direct_tool_execution('send_message'),data)

    def test_invalid_acceptance_time_is_refused_before_projection(self):
        by_frame={}
        for frame in LANGS:
            self.choose('fr',frame)
            with patch.object(r.agent,'current_tool_execution',side_effect=AssertionError('late effect')):
                errors=[]
                for invalid in (True,None,'not-a-date','2026-11-15T10:00:00',float('inf')):
                    with self.assertRaises(r.agent.DurableExecutionError) as caught:
                        r._project_direct_outbox_acceptance({}, {'updated_at':invalid})
                    errors.append(str(caught.exception))
                by_frame[frame]=errors
        self.assertEqual(len({tuple(v) for v in by_frame.values()}),4)
