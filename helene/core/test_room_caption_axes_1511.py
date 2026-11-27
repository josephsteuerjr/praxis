"""Real room state/API captions: UI and model language remain independent."""
import json
import os
from unittest.mock import patch
import frame_language as frame
import interface_language as ui
import panel
import rooms
from localharness import ui_language as native_ui
from test_room_authority import RoomsHarness

RAW='Windows Алекс 中文 {literal} $&'

class RoomCaptionAxes(RoomsHarness):
    def config(self,ui_language,frame_language):
        path=self.tmp/'effective-profile.json'
        value={'lang':ui_language,'agent':{'frame_language':frame_language}}
        path.write_text(json.dumps(value),encoding='utf-8')
        return path,value
    def profile(self):
        self._write('42','# '+RAW+'\n\nmode: quiet\nmode_reason: '+RAW+'\nmode_set_by: owner\n'
                    'disclosure: open\ndisclosure_set_by: protocol\n\n'+RAW+'\n')
    def text_for(self,language,key,**values):
        with frame.use(language):return frame.text('room.caption.'+key,**values)
    def test_real_room_metadata_and_readonly_panel_follow_opposite_axes_all16_pairs(self):
        for u in frame.LANGUAGES:
            for f in frame.LANGUAGES:
                self.profile();path,config=self.config(u,f);before=self._raw('42')
                with patch.dict(os.environ,{'HELENE_CONFIG':str(path)}),patch.object(native_ui,'system_tags',return_value=('en-US',)),patch.object(rooms,'allowed_chats',return_value={'42'}):
                    state=rooms.room_state('42')
                    self.assertEqual(state['mode_author'],self.text_for(f,'author_owner'))
                    self.assertEqual(state['disclosure_author'],self.text_for(f,'author_protocol'))
                    self.assertEqual((state['mode'],state['mode_set_by'],state['mode_reason']),('quiet','owner',RAW))
                    item=panel.rooms_list()['items'][0]
                    self.assertEqual(item['author'],self.text_for(u,'author_owner'))
                    self.assertEqual(item['disclosure_author'],self.text_for(u,'author_protocol'))
                    self.assertEqual((item['title'],item['reason'],item['set_by']), (RAW,RAW,'owner'))
                    with frame.use(u):expected=rooms.mode_label('quiet')
                    self.assertEqual(item['mode_word'],expected)
                    self.assertEqual(frame.language(),f)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')),config);self.assertEqual(self._raw('42'),before)
    def test_panel_disclosure_return_is_ui_but_journal_is_frame_and_only_canonical_state_is_written(self):
        for u in frame.LANGUAGES:
            for f in frame.LANGUAGES:
                self.profile();path,_=self.config(u,f)
                rooms.profile_update('42',disclosure='standard')
                with patch.dict(os.environ,{'HELENE_CONFIG':str(path)}),patch.object(native_ui,'system_tags',return_value=('en-US',)),patch.object(panel,'_journal_panel') as journal:
                    result=panel.room_set('42','disclosure')
                    self.assertEqual((result['ok'],result['disclosure'],result['set_by']),(True,'open','owner'))
                    self.assertEqual(result['note'],self.text_for(u,'disclosure_open',id='42',author=self.text_for(u,'author_owner')))
                    journal.assert_called_once_with(self.text_for(f,'journal_disclosure',id='42',level='open'))
                    stored=rooms.profile_read('42')
                    self.assertEqual((stored['disclosure'],stored['disclosure_set_by'],stored['mode_reason']),('open','owner',RAW))
                    self.assertEqual(stored['body'],RAW)
                    self.assertEqual(frame.language(),f)
    def test_owner_freeze_journal_does_not_promise_automatic_return_without_expiry(self):
        for u in frame.LANGUAGES:
            for f in frame.LANGUAGES:
                self.profile();path,_=self.config(u,f)
                with patch.dict(os.environ,{'HELENE_CONFIG':str(path)}),patch.object(native_ui,'system_tags',return_value=('en-US',)),patch.object(panel,'_journal_panel') as journal:
                    self.assertEqual(panel.room_set('42','freeze'),{'ok':True,'mode':'frozen'})
                    stored=rooms.profile_read('42');self.assertEqual(stored['mode_until'],'')
                    self.assertEqual(stored['mode_reason'],self.text_for(f,'owner_freeze'))
                    journal.assert_called_once_with(self.text_for(f,'journal_frozen',id='42',before='quiet'))
    def test_model_mode_and_disclosure_prompt_captions_keep_literal_reason_and_deadline(self):
        for language in frame.LANGUAGES:
            with frame.use(language):
                for mode in ('observer','quiet'):
                    value=rooms._mode_line(mode,RAW,'protocol','2031-04-10T12:00Z')
                    self.assertIn(RAW,value);self.assertIn('2031-04-10T12:00Z',value)
                    self.assertIn(frame.text('room.caption.author_protocol'),value);self.assertIn('mode=normal',value)
                    if language!='ru':self.assertNotRegex(value.replace(RAW,''),'[А-Яа-яЁё]')
                value=rooms._disclosure_line('open','owner')
                self.assertIn(frame.text('room.caption.author_owner'),value)
                if language!='ru':self.assertNotRegex(value,'[А-Яа-яЁё]')
                self.assertEqual(rooms._mode_line('normal',RAW,'owner',''),'');self.assertEqual(rooms._disclosure_line('standard','owner'),'')
    def test_unknown_author_never_becomes_owner_and_legacy_mode_aliases_remain_canonical(self):
        original=dict(rooms.MODE_WORD)
        for language in frame.LANGUAGES:
            self._write('42','mode: observer\nmode_set_by: unrecognized\n\n'+RAW)
            with frame.use(language):
                state=rooms.room_state('42')
                self.assertEqual(state['mode_set_by'],'unknown')
                self.assertEqual(state['mode_author'],frame.text('room.caption.author_unknown'))
                self.assertNotEqual(state['mode_author'],frame.text('room.caption.author_owner'))
                ok,_=rooms.set_own_mode('42','наблюдай',ttl_h=0)
                self.assertTrue(ok);self.assertEqual(rooms.profile_read('42')['mode'],'observer')
            self.assertEqual(rooms.MODE_WORD,original)
    def test_interface_automatic_selection_reuses_native_order_and_never_agent_frame(self):
        for tags,expected in [(('zh-Hant-TW','fr-CA'),'fr'),(('zh-Hans-CN',),'zh'),(('de-DE',),'en')]:
            path,_=self.config('','ru')
            with patch.dict(os.environ,{'HELENE_CONFIG':str(path)}),patch.object(native_ui,'system_tags',return_value=tags) as resolver:
                self.assertEqual(ui.language(),expected);resolver.assert_called_once();self.assertEqual(frame.language(),'ru')
    def test_unconfigured_standalone_retains_legacy_interface_without_changing_frame(self):
        with patch.dict(os.environ,{'HELENE_CONFIG':''}),frame.use('fr'):
            self.assertEqual(ui.language(),'ru')
            self.assertEqual(ui.text('room.caption.author_owner'),self.text_for('ru','author_owner'))
            self.assertEqual(frame.language(),'fr')
    def test_panel_error_caption_uses_interface_and_leaves_state_and_journal_untouched(self):
        self.profile();before=self._raw('42');path,_=self.config('fr','zh')
        with patch.dict(os.environ,{'HELENE_CONFIG':str(path)}),patch.object(native_ui,'system_tags',return_value=('en-US',)),patch.object(panel,'_journal_panel') as journal:
            self.assertEqual(panel.room_set('','freeze'),{'error':self.text_for('fr','panel_missing')})
            self.assertEqual(panel.room_set('42',RAW),{'error':self.text_for('fr','panel_action',action=RAW.lower())})
            journal.assert_not_called();self.assertEqual(frame.language(),'zh')
        self.assertEqual(self._raw('42'),before)
