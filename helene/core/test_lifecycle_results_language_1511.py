"""Lifecycle language at real handlers; all native/provider effects are replaced."""
from contextlib import ExitStack
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
import agent
import frame_language as lang
import tasks
from test_window_loop import FakeRuns

RAW='Windows Егор 中文 {literal} $&'
NOW=dt.datetime(2031,4,10,12,0)

class LifecycleResults(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(tasks,'TASKS',self.root/'tasks.json'))
        self.stack.enter_context(patch.object(tasks,'_now',return_value=NOW))
        self.journal=self.stack.enter_context(patch.object(agent,'tool_journal'))
        self.stack.enter_context(patch.object(agent,'_runs',return_value=FakeRuns({'alive':'running','past':'done'})))
        self.stack.enter_context(patch.object(agent.run_context,'current_run',return_value=None))
        self.stack.enter_context(patch.object(agent,'telegram_transport_status',return_value='disconnected'))
    def owned(self,value,language):
        if language!='ru':self.assertNotRegex(value.replace(RAW,''),'[А-Яа-яЁё]')
    def test_actual16_axes_preflight_refuses_without_scheduling_or_restart(self):
        cases=[(lambda:agent.tool_home_note(''),'home_empty'),(lambda:agent.tool_focus(''),'focus_empty'),
          (lambda:agent.tool_remind_self('message',RAW),'message_target'),
          (lambda:agent.tool_remind_self('message',RAW,target=RAW),'message_bridge'),
          (lambda:agent.tool_remind_self('note',RAW,when='invalid-date'),'when_invalid'),
          (lambda:agent.tool_remind_self('note',RAW,after_run=RAW),'run_unknown')]
        for ui in lang.LANGUAGES:
            for language in lang.LANGUAGES:
                with self.subTest(ui=ui,frame=language),patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}), \
                     patch.dict(agent._TELETHON,{},clear=True),patch.object(tasks,'add',side_effect=AssertionError('preflight scheduled')):
                    for invoke,key in cases:
                        value=invoke();self.assertTrue(value);self.owned(value,language)
                    self.assertEqual(tasks.list_open(),[])
    def test_home_seed_is_selected_once_and_authored_text_is_never_retranslated(self):
        for language in lang.LANGUAGES:
            home=self.root/(language+'.md')
            with lang.use(language),patch.object(agent,'HOME_MD',home):
                self.assertEqual(agent.tool_home_note(RAW),lang.text('lifecycle.result.home_written'))
                seeded=home.read_text(encoding='utf-8');self.assertTrue(seeded.startswith(lang.text('lifecycle.result.home_seed')))
                self.assertIn(RAW,seeded)
            with lang.use('en' if language!='en' else 'fr'),patch.object(agent,'HOME_MD',home):agent.tool_home_note(RAW)
            self.assertTrue(home.read_text(encoding='utf-8').startswith(seeded));self.assertEqual(home.read_text(encoding='utf-8').count(RAW),2)
    def test_silence_latch_and_full_journal_reason_survive_caption_language(self):
        reason=RAW*30
        for language in lang.LANGUAGES:
            holder={}; token=agent._TURN_SILENCE.set(holder)
            try:
                with lang.use(language):
                    value=agent.tool_stay_silent(reason)
                    self.assertEqual(holder,{'chosen':'1','why':reason[:agent.SILENCE_REASON_MAX]})
                    self.assertIn(lang.text('lifecycle.result.silence_clipped',cap=agent.SILENCE_REASON_MAX),value)
                    self.assertTrue(any(reason in c.args[0] for c in self.journal.call_args_list))
                    self.assertEqual(agent.tool_stay_silent(RAW,cancel=True),lang.text('lifecycle.result.silence_cancelled'))
                    self.assertEqual(holder,{})
                    self.assertEqual(agent.tool_stay_silent(cancel=True),lang.text('lifecycle.result.silence_not_chosen'))
            finally:agent._TURN_SILENCE.reset(token)
        token=agent._TURN_SILENCE.set(None)
        try:
            with lang.use('fr'):
                self.assertEqual(agent.tool_stay_silent(),lang.text('lifecycle.result.silence_unlatched'))
                self.assertEqual(agent.tool_stay_silent(cancel=True),lang.text('lifecycle.result.silence_no_boundary'))
        finally:agent._TURN_SILENCE.reset(token)
    def test_restart_is_request_not_claimed_readiness_and_short_fuse_has_no_second_effect(self):
        settings={'agent':{'frame_language':'fr'}}
        def exit_request():settings['agent']['frame_language']='zh'
        with patch.object(lang,'settings',side_effect=lambda:settings),patch.object(agent,'STATE_DIR',self.root), \
             patch.object(agent,'_restart_replay_winner',return_value=None),patch.object(agent,'_schedule_exit',side_effect=exit_request) as schedule, \
             patch.object(agent.selfdev,'clear_restart_request') as clear,patch.object(agent.selfgit,'snapshot') as snapshot:
            with lang.use('fr'):expected=lang.text('lifecycle.result.restart_queued')
            self.assertEqual(agent.tool_restart_self(RAW),expected)
            schedule.assert_called_once();clear.assert_called_once();snapshot.assert_called_once()
            marker=(self.root/'restart_reason.txt').read_text(encoding='utf-8');self.assertIn(RAW,marker)
            value=agent.tool_restart_self(RAW)
            self.assertIn(RAW,value);self.owned(value.replace(marker.strip(),''),'zh')
            self.assertEqual(schedule.call_count,1)
        with lang.use('en'),patch.object(agent,'_restart_replay_winner',return_value='earlier'), \
             patch.object(agent.run_context,'current_run',return_value=SimpleNamespace(run_id='later')),patch.object(agent,'_schedule_exit') as schedule:
            self.assertEqual(agent.tool_restart_self(RAW),lang.text('lifecycle.result.restart_duplicate'));schedule.assert_not_called()
    def test_room_guards_and_literal_reasons_preserve_exact_authority(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(agent,'_current_room',return_value=RAW),patch.object(agent,'_active_scope',return_value='known'), \
                 patch.object(agent,'_is_sovereign_actor',return_value=False),patch.object(agent.rooms,'set_mode') as set_mode:
                self.assertEqual(agent.tool_freeze_chat(),lang.text('lifecycle.result.freeze_owner'));set_mode.assert_not_called()
                self.assertEqual(agent.tool_freeze_chat('boundary','other'),lang.text('lifecycle.result.boundary_current'))
            with lang.use(language),patch.object(agent,'_current_room',return_value=RAW),patch.object(agent,'_is_sovereign_actor',return_value=True), \
                 patch.object(agent,'_is_praxis_self',return_value=False),patch.object(agent.rooms,'set_mode') as set_mode:
                value=agent.tool_freeze_chat(reason=RAW);self.assertIn(RAW,value)
                set_mode.assert_called_once_with(RAW,'frozen',reason=RAW,set_by='owner')
            with lang.use(language),patch.object(agent,'_current_room',return_value=RAW),patch.object(agent,'_active_scope',return_value='owner'), \
                 patch.object(agent.rooms,'self_demote') as demote:
                self.assertEqual(agent.tool_freeze_contact(RAW),lang.text('lifecycle.result.owner_boundary'));demote.assert_not_called()
    def test_real_scheduler_keeps_canonical_kind_goal_hold_and_swap_provenance(self):
        for language in lang.LANGUAGES:
            tasks._save([])
            with lang.use(language),patch.object(agent,'telegram_transport_status',return_value='closed_for_window'):
                value=agent.tool_remind_self('window',RAW)
                task=tasks.list_open()[0]
                self.assertEqual((task['kind'],task['goal'],task['swapped_from']),('wake',RAW,'window'))
                self.assertIn(lang.text('lifecycle.result.window_swapped'),value);self.owned(value,language)
                value=agent.tool_remind_self('window',RAW,after_run='alive')
                task=tasks.list_open()[-1];self.assertEqual((task['kind'],task['after_run']),('window','alive'))
                self.assertIn(lang.text('lifecycle.result.after_run',run='alive',then=''),value);self.owned(value,language)
                value=agent.tool_remind_self('note',RAW,after_run='past')
                self.assertIn(lang.text('lifecycle.result.nothing_wait',reason=lang.text('lifecycle.result.run_done',run='past')),value)
                self.assertEqual(len(tasks.due()),1)
                tasks.clear_after_run(tasks.list_open()[-1]['id'])
                self.assertEqual(len(tasks.due()),2)
    def test_rest_and_focus_are_scheduled_while_rest_prefix_remains_literal(self):
        for language in lang.LANGUAGES:
            tasks._save([])
            with lang.use(language),patch.object(agent,'telegram_transport_status',return_value='closed_for_window'):
                value=agent.tool_rest(RAW);task=tasks.list_open()[0]
                self.assertEqual((task['kind'],task['goal']),('window',agent.REST_PREFIX+' '+RAW))
                self.assertIn(lang.text('lifecycle.result.retreat_repeat'),value);self.owned(value,language)
                value=agent.tool_focus(RAW);self.assertEqual(tasks.list_open()[-1]['goal'],RAW);self.owned(value,language)
    def test_agenda_projects_owned_provenance_without_translating_deadline_or_goal(self):
        row={'id':'x','kind':'wake','goal':RAW,'when':RAW,'recur':None,'author':'owner','risky':True,'swapped_from':'window'}
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(tasks,'list_open',return_value=[row]):
                value=agent.tool_my_agenda();self.assertIn(RAW,value);self.owned(value,language)
                self.assertIn(lang.text('lifecycle.result.owner'),value)
                self.assertIn(lang.text('lifecycle.result.deadline_trouble',when=RAW),value)
                self.assertIn('[wake]',value);self.assertIn('window',value)
    def test_end_turn_guards_and_authored_note_are_unchanged_across16_axes(self):
        for ui in lang.LANGUAGES:
            for language in lang.LANGUAGES:
                with patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent.work_loop,'note_finished') as finish:
                    for kind,key in [('', 'end_outcome'),('wait','end_wait'),('blocked','end_blocked')]:
                        self.assertEqual(agent.tool_end_turn(kind),lang.text('lifecycle.result.'+key));finish.assert_not_called()
                    value=agent.tool_end_turn('done',RAW*20)
                    finish.assert_called_once_with('done: '+RAW*20)
                    self.assertIn((RAW*20)[:200],value);self.assertNotIn((RAW*20)[:201],value)
                    self.owned(value.replace((RAW*20)[:200],''),language)
    def test_pace_counts_created_intentions_without_claiming_executed_restarts(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(tasks,'recent_count',side_effect=[4,2]):
                value=agent._window_pace_note()
                self.assertIn(lang.text('lifecycle.result.pace',total=6),value)
                self.assertIn(lang.text('lifecycle.result.pace_windows',windows=4),value);self.owned(value,language)
