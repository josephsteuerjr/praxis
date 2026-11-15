"""Real outbound wrappers and durable evidence; fake bridges, no send/provider calls."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import agent
from core import narration
import frame_language as frame
import run_manager
import telegram_outbox

RAW='Windows Егор 中文 {literal} $&'
TEXT='Windows Егор {literal} $&'

class OutboundResults(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(agent,'tool_journal'))
        self.stack.enter_context(patch.object(agent.outgoing_spelling,'preflight',return_value=('','')))
        self.stack.enter_context(patch.object(agent.outgoing_spelling,'receipt',side_effect=lambda out,note:out))
    def text(self,language,key,**values):
        with frame.use(language):return frame.text('outbound.result.'+key,**values)
    def context(self,value):
        token=agent._TURN_CHANNEL.set(value);self.addCleanup(agent._TURN_CHANNEL.reset,token)
    def test_actual_outbox_acceptance_pending_dead_and_missing_evidence_keep_outcomes_all16_axes(self):
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                for state,outcome in [('accepted','ok'),('pending','in_doubt'),('dead_letter','failed'),('absent','in_doubt')]:
                    box=telegram_outbox.TelegramOutbox(self.root/(ui+language+state));key='telegram-outbox:test:tool:call'
                    if state!='absent':box.prepare_text(key,peer_id=77,text=RAW,run_id='test',call_id='call',purpose='tool:send_message')
                    if state=='accepted':box.mark_accepted(key,message_id=1193)
                    if state=='dead_letter':box.dead_letter(key,RAW)
                    before=box.get(key,verify_file=False)
                    with self.subTest(ui=ui,frame=language,state=state),patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent,'_DIRECT_OUTBOX_READER',box),patch.object(agent,'current_tool_execution',return_value={'idempotency_key':key}),patch.object(agent.tool_report,'note') as report,patch.dict(agent._TELETHON,{},clear=True):
                        label=self.text(language,'label_message');result=agent._direct_send_outcome(label,run_manager.RunConflict(RAW))
                        self.assertEqual(report.call_args.kwargs['outcome'],outcome)
                        self.assertIn(RAW,result);self.assertIn(label,result)
                        if state=='accepted':self.assertIn('1193',result)
                        if language!='ru':self.assertNotRegex(result.replace(RAW,''),'[А-Яа-яЁё]')
                    self.assertEqual(box.get(key,verify_file=False),before)
    def test_reply_and_send_preserve_literal_payloads_and_bridge_receipts_under_all16_axes(self):
        self.context(agent.ChannelContext(chat_id='77',principal_id=77,is_dm=True,owner=True))
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                reply=Mock(return_value=RAW);send=Mock(return_value=RAW)
                with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.dict(agent._TELETHON,{'reply':reply,'send_message':send},clear=True),patch.object(agent,'guard_outbound_reply',side_effect=lambda text,*a,**kw:text),patch.object(agent.work_loop,'spoken',return_value=[]),patch.object(agent.work_loop,'note_sent') as sent,patch.object(agent.stewardship,'outgoing_denial',return_value=''):
                    token=agent._TURN_SILENCE.set({})
                    try:
                        self.assertEqual(agent.tool_reply(RAW,'88'),self.text(language,'reply_after',out=RAW))
                        reply.assert_called_once_with('77',RAW,'88');sent.assert_called_once_with(RAW)
                        self.assertEqual(agent.tool_send_message('@literal',TEXT),RAW);send.assert_called_once_with('@literal',TEXT)
                    finally:agent._TURN_SILENCE.reset(token)
    def test_silence_guard_and_literal_dedup_remain_no_effect_and_localized(self):
        self.context(agent.ChannelContext(chat_id='77',principal_id=77,is_dm=True,owner=True))
        for language in frame.LANGUAGES:
            with frame.use(language),patch.dict(agent._TELETHON,{'reply':Mock()},clear=True):
                token=agent._TURN_SILENCE.set({'chosen':True,'why':RAW})
                try:self.assertEqual(agent.tool_reply(RAW),self.text(language,'reply_silent',reason=' ('+RAW+')'))
                finally:agent._TURN_SILENCE.reset(token)
                with patch.object(agent,'guard_outbound_reply',side_effect=RuntimeError(RAW)):
                    self.assertEqual(agent.tool_reply(RAW),self.text(language,'reply_guard_error'))
                with patch.object(agent,'guard_outbound_reply',return_value=RAW),patch.object(agent.work_loop,'spoken',return_value=[RAW]):
                    self.assertEqual(agent.tool_reply(RAW),self.text(language,'reply_duplicate'))
                agent._TELETHON['reply'].assert_not_called()
    def test_account_permission_json_and_banned_transport_guards_do_not_dispatch(self):
        for language in frame.LANGUAGES:
            hook=Mock()
            with frame.use(language),patch.object(agent,'_is_sovereign_actor',return_value=False),patch.dict(agent._TELETHON,{'join_chat':hook},clear=True):
                self.assertEqual(agent.tool_telegram_account('join'),self.text(language,'account_scope'));hook.assert_not_called()
            with frame.use(language),patch.object(agent,'_is_sovereign_actor',return_value=True),patch.dict(agent._TELETHON,{'history_scan':hook,'admin_abstractdl':hook},clear=True):
                self.assertEqual(agent.tool_telegram_account('history_scan',params={'receipt_id':RAW}),self.text(language,'account_banned',keys='receipt_id'))
                self.assertEqual(agent.tool_telegram_account('admin_abstractdl',params_json='[]'),self.text(language,'account_object',action='admin_abstractdl'))
                hook.assert_not_called()
    def test_account_hook_arguments_principal_paging_and_external_text_are_unchanged(self):
        for language in frame.LANGUAGES:
            hook=Mock(return_value=RAW)
            with frame.use(language),patch.object(agent,'_is_sovereign_actor',return_value=True),patch.object(agent,'_active_principal',return_value='principal'),patch.dict(agent._TELETHON,{'history_scan':hook,'followups':hook},clear=True):
                self.assertEqual(agent.tool_telegram_account('history_scan',target=RAW,params_json=json.dumps({'query':RAW})),RAW)
                hook.assert_called_once_with(target=RAW,params={'query':RAW},_principal='principal');hook.reset_mock()
                self.assertEqual(agent.tool_telegram_account('watch_reply',followup_id=RAW,limit=7,offset=12),RAW)
                hook.assert_called_once_with(action='watch',followup_id=RAW,limit=7,offset=12)
    def test_staged_media_speech_and_raw_spoken_guard_never_send_directly(self):
        self.context(agent.ChannelContext(chat_id='77',principal_id=77,is_dm=True,owner=True))
        path=self.root/'speech.wav';path.write_bytes(b'RIFFliteral')
        for language in frame.LANGUAGES:
            outbound=[];guards={};queue=SimpleNamespace(queue_id='q')
            spool=SimpleNamespace(resolve_outbound=Mock(return_value=queue))
            t1=agent._TURN_OUTBOUND.set(outbound);t2=agent._TURN_MEDIA_GUARD.set(guards)
            try:
                with frame.use(language),patch.object(agent,'_media_spool',return_value=spool),patch.object(agent.run_context,'current_run',return_value=None),patch.object(agent.media_audio,'synthesize',return_value=path),patch.dict(agent._TELETHON,{'send_file':Mock()},clear=True):
                    value=agent.tool_speak(RAW,caption=RAW)
                    self.assertEqual(value,self.text(language,'media_staged',kind='audio'))
                    self.assertEqual(outbound,[queue]);self.assertEqual(guards,{'q':self.text(language,'speech_guard',text=RAW)})
                    spool.resolve_outbound.assert_called_once_with(path,kind='audio',target_chat_id='77',scope='owner',caption=RAW,voice_note=False,move=True)
                    agent._TELETHON['send_file'].assert_not_called()
            finally:agent._TURN_MEDIA_GUARD.reset(t2);agent._TURN_OUTBOUND.reset(t1)
    def test_media_transfer_error_is_unconfirmed_and_not_retried_with_literal_parameters(self):
        path=self.root/'literal.bin';path.write_bytes(b'literal')
        for language in frame.LANGUAGES:
            hook=Mock(side_effect=OSError(RAW))
            with frame.use(language),patch.object(agent.workshop,'_resolve_read',return_value=path),patch.dict(agent._TELETHON,{'send_file':hook},clear=True):
                value=agent.tool_send_media(str(path),'document',caption=TEXT,to='@literal')
                self.assertEqual(value,self.text(language,'media_error',error='OSError',reason=RAW))
                hook.assert_called_once_with(str(path),TEXT,'@literal','document',False)
    def test_natural_chinese_text_and_bilingual_product_names_are_not_generation_artifacts(self):
        for language in frame.LANGUAGES:
            for text in ('这是中文回复，请保持原文。','请用Windows打开中文文件，然后查看API-v2。'):
                with frame.use(language),patch.object(agent.cjk_vkladki,'_cheap_translate') as translate,patch.object(agent.cjk_vkladki,'_cheap_translate_ctx') as contextual:
                    self.assertEqual(agent._strip_generation_artifacts(text)[0],text)
                    translate.assert_not_called();contextual.assert_not_called()
    def test_whole_call_pin_survives_setting_change_by_bridge(self):
        self.context(agent.ChannelContext(chat_id='77',principal_id=77,is_dm=True,owner=True))
        config={'agent':{'frame_language':'fr'}}
        def bridge(*args):config['agent']['frame_language']='zh';return RAW
        token=agent._TURN_SILENCE.set({})
        try:
            with patch.object(frame,'settings',side_effect=lambda:config),patch.dict(agent._TELETHON,{'reply':bridge},clear=True),patch.object(agent,'guard_outbound_reply',return_value=RAW),patch.object(agent.work_loop,'spoken',return_value=[]):
                first=agent.tool_reply(RAW);second=agent.tool_reply(RAW)
                self.assertEqual(first,self.text('fr','reply_after',out=RAW));self.assertEqual(second,self.text('zh','reply_after',out=RAW))
        finally:agent._TURN_SILENCE.reset(token)
