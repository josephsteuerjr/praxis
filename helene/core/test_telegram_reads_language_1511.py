"""Telegram captions use frame language; bridge data and dispatch stay literal."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import agent
import frame_language as lang
import rooms

RAW='Windows Алекс 中文 {literal} $&'

class TelegramReads(unittest.TestCase):
    def own(self,value,language):
        if language!='ru':self.assertNotRegex(value.replace(RAW,''),'[А-Яа-яЁё]')
    def test_all16_axes_bridge_data_limits_and_identifiers_remain_literal(self):
        for ui in lang.LANGUAGES:
            for language in lang.LANGUAGES:
                calls={name:Mock(return_value=RAW) for name in ('find_telegram_id','read_other_chat','fetch_context','search_private_messages')}
                with patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}), \
                     patch.dict(agent._TELETHON,calls,clear=True),patch.object(agent,'telegram_transport_status',return_value='connected'), \
                     patch.object(agent,'_current_chat',return_value='42'):
                    self.assertEqual(agent.tool_get_id(RAW),RAW);calls['find_telegram_id'].assert_called_once_with(RAW)
                    value=agent.tool_read_chat(RAW,17);self.assertIn(RAW,value);self.own(value,language)
                    self.assertEqual(value,lang.text('telegram.result.private',data=RAW));calls['read_other_chat'].assert_called_once_with(RAW,17)
                    self.assertEqual(agent.tool_read_context(19),RAW);calls['fetch_context'].assert_called_once_with('42',19)
                    self.assertEqual(agent.tool_search_private_messages(RAW,23),RAW);calls['search_private_messages'].assert_called_once_with(RAW,23)
    def test_missing_bridge_and_context_do_not_dispatch_or_claim_no_such_person(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.dict(agent._TELETHON,{},clear=True):
                for invoke in (lambda:agent.tool_get_id(RAW),lambda:agent.tool_read_chat(RAW),lambda:agent.tool_read_context(),lambda:agent.tool_search_private_messages(RAW)):
                    self.assertEqual(invoke(),lang.text('telegram.result.unavailable'))
            with lang.use(language),patch.dict(agent._TELETHON,{'fetch_context':Mock(side_effect=AssertionError('missing chat dispatched'))},clear=True),patch.object(agent,'_current_chat',return_value=''):
                self.assertEqual(agent.tool_read_context(),lang.text('telegram.result.context_missing'))
    def test_bridge_diagnostics_are_literal_with_owned_failure_not_absence_captions(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.dict(agent._TELETHON,{'find_telegram_id':Mock(side_effect=TimeoutError(RAW))},clear=True):
                self.assertEqual(agent.tool_get_id('x'),lang.text('telegram.result.id_timeout',detail=RAW))
            for function,bridge,key in [(lambda:agent.tool_get_id('x'),'find_telegram_id','id_error'),(lambda:agent.tool_read_chat('x'),'read_other_chat','read_error'),(lambda:agent.tool_read_context(),'fetch_context','context_error'),(lambda:agent.tool_search_private_messages('x'),'search_private_messages','private_error')]:
                with lang.use(language),patch.dict(agent._TELETHON,{bridge:Mock(side_effect=RuntimeError(RAW))},clear=True), \
                     patch.object(agent,'telegram_transport_status',return_value='connected'),patch.object(agent,'_current_chat',return_value='42'):
                    self.assertEqual(function(),lang.text('telegram.result.'+key,detail=RAW))
    def test_closed_transport_blocks_reads_but_local_room_memory_still_answers(self):
        row={'chat_id':'42','title':RAW,'mode':'frozen','where':'название'}
        for language in lang.LANGUAGES:
            for status,key in [('closed_for_window','transport_window'),('disconnected','transport_disconnected')]:
                search=Mock(side_effect=AssertionError('closed search dispatched'));read=Mock(side_effect=AssertionError('closed read dispatched'))
                with lang.use(language),patch.dict(agent._TELETHON,{'search_chats':search,'read_other_chat':read},clear=True), \
                     patch.object(agent,'telegram_transport_status',return_value=status),patch.object(rooms,'search_profiles',return_value=[row]):
                    value=agent.tool_search_chats(RAW);self.assertIn(RAW,value);self.own(value,language)
                    self.assertIn(lang.text('telegram.result.'+key),value)
                    self.assertIn(lang.text('telegram.result.intake_frozen'),value)
                    self.assertEqual(agent.tool_read_chat('42'),lang.text('telegram.result.'+key));search.assert_not_called();read.assert_not_called()
    def test_all16_axes_actual_room_file_and_dialog_search_preserve_authored_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'42.md').write_text('# '+RAW+'\n\nmode: frozen\n\n'+RAW,encoding='utf-8')
            for ui in lang.LANGUAGES:
                for language in lang.LANGUAGES:
                    with patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}), \
                         patch.object(rooms,'ROOMS_DIR',root),patch.object(rooms,'effective_mode',return_value='frozen'), \
                         patch.object(agent,'telegram_transport_status',return_value='connected'),patch.dict(agent._TELETHON,{'search_chats':lambda q:RAW},clear=True):
                        value=agent.tool_search_chats('Windows');self.assertEqual(value.count(RAW),2);self.own(value,language)
                        self.assertIn(lang.text('telegram.result.where_title'),value)
                        self.assertEqual((root/'42.md').read_text(encoding='utf-8'),'# '+RAW+'\n\nmode: frozen\n\n'+RAW)
    def test_no_matches_report_coverage_without_conflating_names_and_messages(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(rooms,'search_profiles',return_value=[]),patch.object(agent,'telegram_transport_status',return_value='connected'),patch.dict(agent._TELETHON,{'search_chats':lambda q:''},clear=True):
                self.assertEqual(agent.tool_search_chats(RAW),lang.text('telegram.result.scope_none')+lang.text('telegram.result.search_help'))
    def test_untitled_room_fallback_is_owned_while_authored_titles_stay_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'42.md').write_text('mode: normal\n\n'+RAW,encoding='utf-8')
            for language in lang.LANGUAGES:
                with lang.use(language),patch.object(rooms,'ROOMS_DIR',root),patch.object(rooms,'effective_mode',return_value='normal'):
                    row=rooms.search_profiles('42')[0]
                    self.assertEqual(row['title'],lang.text('telegram.result.room_name',id='42'))
    def test_unknown_supplied_transport_caption_and_profile_values_are_never_rewritten(self):
        with lang.use('fr'),patch.dict(agent._TRANSPORT_CLOSED_LINES,{'custom':RAW}):
            self.assertEqual(agent._transport_closed_line('custom'),RAW)
        with lang.use('zh'),patch.object(rooms,'search_profiles',return_value=[{'chat_id':'42','title':RAW,'mode':RAW,'where':RAW}]):
            value=agent._rooms_block(RAW);self.assertEqual(value.count(RAW),3)
    def test_read_caption_is_pinned_when_configuration_changes_during_bridge_call(self):
        settings={'agent':{'frame_language':'fr'}}
        def read(*a):settings['agent']['frame_language']='zh';return RAW
        with patch.object(lang,'settings',side_effect=lambda:settings),patch.dict(agent._TELETHON,{'read_other_chat':read},clear=True),patch.object(agent,'telegram_transport_status',return_value='connected'):
            with lang.use('fr'):expected=lang.text('telegram.result.private',data=RAW)
            self.assertEqual(agent.tool_read_chat('42'),expected)
