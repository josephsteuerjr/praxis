"""Real local room controls, preserving protocol state and literal profile content."""
import copy
import json
import os
from unittest.mock import Mock,patch
import agent
import frame_language as lang
import rooms
from test_room_authority import RoomsHarness

RAW='Windows Егор 中文 {literal} $&'

class RoomControls(RoomsHarness):
    def setUp(self):
        super().setUp()
        token=agent._TURN_CHANNEL.set(None);self.addCleanup(agent._TURN_CHANNEL.reset,token)
        for patcher in (patch.object(agent,'ROOMS_DIR',rooms.ROOMS_DIR),patch.object(agent,'_is_sovereign_actor',return_value=True),patch.object(agent,'tool_journal'),patch.dict(os.environ,{'TELEGRAM_ALLOWED_CHATS':''})):
            patcher.start();self.addCleanup(patcher.stop)
        self._write('42','# '+RAW+'\n\nmode: normal\ndisclosure: standard\n\n'+RAW+'\n')
    def own(self,value,language):
        text=value.replace(RAW,'').replace(RAW.lower(),'').replace('[режим]','')
        for token in (*rooms.TRANSFER_CLASSES,'снять'):text=text.replace(token,'')
        if language!='ru':self.assertNotRegex(text,'[А-Яа-яЁё]')
    def test_all16_axes_modes_preserve_clipped_reason_ttl_legacy_flag_and_normal_author(self):
        aliases=copy.deepcopy(rooms.MODE_WORD)
        for ui in lang.LANGUAGES:
            for language in lang.LANGUAGES:
                with patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                    value=agent.tool_manage_room('mode','42',mode='frozen',reason=RAW*30,ttl_h=0)
                    state=rooms.profile_read('42')
                    self.assertEqual((state['mode'],state['mode_set_by'],state['mode_until']),('frozen','praxis',''))
                    self.assertEqual(state['mode_reason'],(RAW*30)[:rooms.MODE_REASON_MAX]);self.assertTrue(rooms.is_frozen('42'))
                    self.assertIn(lang.text('room.control.reason_clipped',cap=rooms.MODE_REASON_MAX),value);self.own(value,language)
                    value=agent.tool_manage_room('mode','42',mode='normal',ttl_h=9)
                    state=rooms.room_state('42');self.assertEqual((state['mode'],state['mode_set_by'],state['mode_author'],state['mode_until']),('normal','','',''))
                    self.assertFalse(rooms.is_frozen('42'));self.own(value,language)
                    self.assertIn(lang.text('room.control.mode_normal_ttl',hours='9'),value)
                    self.assertIn(lang.text('room.control.span_normal'),agent.tool_journal.call_args.args[0])
                    self.assertEqual(rooms.profile_read('42')['body'],RAW)
        self.assertEqual(rooms.MODE_WORD,aliases)
    def test_sovereign_refusal_and_invalid_mode_do_not_modify_files(self):
        for language in lang.LANGUAGES:
            before=self._raw('42')
            with lang.use(language),patch.object(agent,'_is_sovereign_actor',return_value=False),patch.object(rooms,'set_own_mode') as mutate:
                self.assertEqual(agent.tool_manage_room('mode','42',mode='frozen'),lang.text('room.control.authority'));mutate.assert_not_called()
            with lang.use(language):
                value=agent.tool_manage_room('mode','42',mode=RAW)
                self.assertIn(RAW,value);self.own(value,language)
            self.assertEqual(self._raw('42'),before)
    def test_catalog_join_leave_rejoin_are_local_and_never_call_the_network_bridge(self):
        network=Mock(side_effect=AssertionError('catalog control called network'))
        for language in lang.LANGUAGES:
            cid=str(43+lang.LANGUAGES.index(language))
            with lang.use(language),patch.dict(agent._TELETHON,{'join_chat':network,'leave_chat':network},clear=True):
                value=agent.tool_manage_room('join',cid);self.own(value,language);self.assertIn(cid,rooms.allowed_chats())
                self.assertTrue(rooms.is_allowed(cid));self.assertIn(lang.text('room.control.seed_body').strip(),self._raw(cid))
                value=agent.tool_manage_room('leave',cid);self.own(value,language);self.assertFalse(rooms.is_allowed(cid))
                self.assertEqual(rooms.membership_state(cid)['state'],'left')
                value=agent.tool_manage_room('leave',cid);self.assertIn('no',value);self.assertFalse(rooms.is_allowed(cid))
                value=agent.tool_manage_room('join',cid);self.own(value,language);self.assertTrue(rooms.is_allowed(cid))
                network.assert_not_called()
    def test_config_validation_refuses_before_write_and_valid_values_keep_existing_clamp(self):
        rooms.add_room('42')
        for language in lang.LANGUAGES:
            before=self._raw('42')
            with lang.use(language):
                value=agent.tool_manage_room('configure','42',engagement='invalid')
                self.assertIn('addressed | reflective',value);self.own(value,language);self.assertEqual(self._raw('42'),before)
                value=agent.tool_manage_room('configure','42',context_hot=2)
                self.own(value.replace(json.dumps(agent._room_view('42',rooms.room_policy('42')),ensure_ascii=False,sort_keys=True),''),language)
                self.assertEqual(rooms.room_policy('42')['context_hot'],20)
                self.assertEqual(rooms.profile_read('42')['body'],RAW)
    def test_independent_presence_hidden_stays_visible_with_default_and_after_transfer_clear(self):
        for language in lang.LANGUAGES:
            with lang.use(language):
                ok,value=rooms.set_own_transfer('42','',presence_hidden='yes');self.assertTrue(ok)
                self.assertIn(lang.text('room.control.presence_hidden'),value)
                value=agent.tool_manage_room('transfer','42');self.assertIn(lang.text('room.control.presence_hidden'),value);self.own(value,language)
                value=agent.tool_manage_room('transfer','42',transfer=rooms.TRANSFER_CLASSES[0]);self.own(value,language)
                state=rooms.transfer_of('42');self.assertEqual((state['transfer'],state['presence_hidden'],state['set_by']),(rooms.TRANSFER_CLASSES[0],True,'praxis'))
                value=agent.tool_manage_room('transfer','42',transfer='снять');self.own(value,language)
                self.assertTrue(rooms.transfer_of('42')['presence_hidden']);self.assertEqual(rooms.transfer_of('42')['transfer'],'')
                self.assertIn(lang.text('room.control.presence_hidden'),agent.tool_manage_room('transfer','42'))
                self.assertEqual(rooms.profile_read('42')['body'],RAW)
    def test_dead_mode_is_not_revived_by_local_mode_control(self):
        for language in lang.LANGUAGES:
            self._write('42','mode: dead\nmode_set_by: protocol\n\n'+RAW);before=self._raw('42')
            with lang.use(language):
                value=agent.tool_manage_room('mode','42',mode='normal')
                self.assertIn(lang.text('room.control.dead_refusal'),value);self.own(value,language)
                self.assertEqual(rooms.membership_state('42')['state'],'unreachable')
                self.assertEqual(rooms.membership_state('42')['why'],lang.text('room.control.membership_dead'))
            self.assertEqual(self._raw('42'),before)
    def test_read_model_state_failure_uses_canonical_unknown_with_separate_caption(self):
        policy={'engagement':'addressed','literal':RAW}
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(rooms,'room_state',side_effect=RuntimeError(RAW)):
                value=agent._room_view('42',policy)
                self.assertEqual(value['mode'],'unknown');self.assertEqual(value['mode_error'],lang.text('room.control.state_unreadable'))
                self.assertEqual(value['literal'],RAW);self.assertEqual(policy,{'engagement':'addressed','literal':RAW})
    def test_actual_recall_origin_labels_distinguish_group_owner_and_person_without_translating_names(self):
        import telegram_contacts
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(telegram_contacts,'_load',return_value={'123':{'display_name':RAW}}):
                self.assertEqual(agent._recall_room_label('owner-dm'),lang.text('room.control.origin_owner'))
                self.assertEqual(agent._recall_room_label('-10042'),lang.text('room.control.origin_room',id='-10042'))
                self.assertEqual(agent._recall_room_label('123'),lang.text('room.control.origin_person',name=RAW,id='123'))
                self.assertEqual(agent._recall_room_label('456'),lang.text('room.control.origin_private',id='456'))
    def test_window_and_disclosure_queries_remain_readonly_and_use_frame_captions(self):
        for language in lang.LANGUAGES:
            with lang.use(language):
                before=self._raw('42')
                for action in ('mode','disclosure','transfer'):
                    value=agent.tool_manage_room(action,'42');self.own(value,language)
                    self.assertEqual(self._raw('42'),before)
