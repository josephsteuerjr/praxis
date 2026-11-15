"""Control result localization with all authority/effect backends replaced."""
from pathlib import Path
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock,patch
import agent
import frame_language as lang

RAW='Windows Егор 中文 {literal} $&'

class ControlResults(unittest.TestCase):
    def own(self,text,language):
        if language!='ru':self.assertNotRegex(text.replace(RAW,''),'[А-Яа-яЁё]')
    def test_alias_refusal_and_mutation_keep_canonical_subject_and_literal_alias(self):
        for language in lang.LANGUAGES:
            path=Mock();path.exists.return_value=False
            with lang.use(language),patch.object(agent.graph,'resolve',return_value='person-ref'),patch.object(agent.people,'path_for',return_value=path), \
                 patch.object(agent.people,'add_alias') as add:
                self.assertEqual(agent.tool_add_alias(RAW,RAW),lang.text('control.result.alias_missing',person=RAW));add.assert_not_called()
            path.exists.return_value=True
            for added in (True,False):
                with lang.use(language),patch.object(agent.graph,'resolve',return_value='person-ref'),patch.object(agent.graph,'display',return_value=RAW), \
                     patch.object(agent.people,'path_for',return_value=path),patch.object(agent.people,'add_alias',return_value=added) as add,patch.object(agent,'_reindex') as index:
                    value=agent.tool_add_alias(RAW,RAW);self.assertIn(RAW,value);self.own(value,language)
                    add.assert_called_once_with('person-ref',RAW);self.assertEqual(index.called,added)
    def test_loop_repeat_guard_and_explicit_override_preserve_subject_match_date_reason(self):
        path=Mock();path.exists.return_value=True
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(agent.graph,'resolve',return_value='person-ref'),patch.object(agent.people,'path_for',return_value=path), \
                 patch.object(agent.people,'park_count',return_value=2),patch.object(agent.people,'park_loop',return_value=True) as park, \
                 patch.object(agent,'_reindex'),patch.object(agent,'tool_journal') as journal:
                self.assertEqual(agent.tool_manage_loop('park',person=RAW,match=RAW,until='2031-04-10'),lang.text('control.result.loop_repeated',count=2));park.assert_not_called()
                value=agent.tool_manage_loop('park',person=RAW,match=RAW,until='2031-04-10',force=True,reason=RAW)
                park.assert_called_once_with('person-ref',RAW,'2031-04-10');self.assertIn(RAW,value);self.own(value,language)
                self.assertIn(RAW,journal.call_args.args[0])
    def test_loop_status_and_authored_thread_lists_are_not_retranslated(self):
        path=Mock();path.exists.return_value=True
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(agent.graph,'resolve',return_value='person-ref'),patch.object(agent.people,'path_for',return_value=path), \
                 patch.object(agent.people,'read',return_value=('',{agent.people.LOOPS:RAW})),patch.object(agent.people,'close_open_loop',return_value=False), \
                 patch.object(agent.people,'unpark_loops',return_value=3),patch.object(agent,'_reindex'):
                self.assertEqual(agent.tool_manage_loop('list',person=RAW),RAW)
                self.assertEqual(agent.tool_manage_loop('close',person=RAW,match=RAW),lang.text('control.result.loop_missing',match=RAW))
                self.assertEqual(agent.tool_manage_loop('reopen',person=RAW),lang.text('control.result.loop_reopened',count=3))
    def test_host16_axes_payload_receipts_and_all_diagnostic_fields_survive_together(self):
        result={'ok':False,'exit':9,'error':RAW,'text':RAW,'before':RAW,'after':RAW,'verify':RAW,'recovery':{'id':'receipt-x','status':'pending','deadline_epoch':234}}
        before=copy.deepcopy(result)
        for ui in lang.LANGUAGES:
            for language in lang.LANGUAGES:
                with patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent.stewardship,'check',return_value=None), \
                     patch.object(agent.serverd_client,'host_ctl',return_value=result) as backend:
                    value=agent.tool_host_ctl('file','write',path=RAW,content=RAW,recover_after=0)
                    backend.assert_called_once_with('file',action='write',path=RAW,content=RAW,recover_after=0)
                    for raw in ('receipt-x','pending','234',RAW):self.assertIn(raw,value)
                    self.assertEqual(value.count(RAW),6);self.own(value,language)
                    self.assertIn('verb=confirm receipt_id=receipt-x',value);self.assertEqual(result,before)
    def test_host_stewardship_denial_and_canonical_verbs_never_dispatch(self):
        with lang.use('fr'),patch.object(agent.stewardship,'check',return_value=RAW),patch.object(agent.serverd_client,'host_ctl') as backend:
            self.assertEqual(agent.tool_host_ctl('file','write',path=RAW),RAW);backend.assert_not_called()
            self.assertEqual(agent.tool_host_ctl('unknown'),'verb: systemctl | docker | pkg | file | net | reboot | confirm');backend.assert_not_called()
    def test_host_truncation_retains_both_edges_and_reports_exact_omission(self):
        value=RAW*100
        for language in lang.LANGUAGES:
            with lang.use(language):
                self.assertEqual(agent._clip_host_text(RAW,limit=100),RAW)
                self.assertEqual(agent._clip_host_text(value,limit=300),lang.text('control.result.host_clipped',head=value[:200],tail=value[-100:],omitted=len(value)-300,total=len(value),cap=300))
    def test_proposal_result_does_not_claim_tests_passed_or_live_apply_and_diff_is_literal(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(agent.selfdev,'begin',return_value={'ok':True,'id':'p1','path':RAW}) as begin,patch.object(agent.selfdev,'diff_text',return_value=RAW):
                value=agent.tool_start_proposal(RAW);self.assertIn(RAW,value);self.own(value,language)
                begin.assert_called_once_with(RAW);self.assertEqual(agent.tool_proposal_diff('p1'),RAW)
            with lang.use(language),patch.object(agent.selfdev,'begin',return_value={'ok':False,'msg':RAW}),patch.object(agent.selfdev,'diff_text',return_value=''):
                self.assertEqual(agent.tool_start_proposal(),lang.text('control.result.proposal_error',detail=RAW))
                self.assertEqual(agent.tool_proposal_diff('p1'),lang.text('control.result.proposal_empty'))
    def test_recent_place_audience_refusal_no_read_then_literal_note_and_turns(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(agent,'_may_name_other_rooms',return_value=False),patch.object(agent,'resolve_room') as resolve:
                self.assertEqual(agent.tool_recent_turns(room=RAW),lang.text('control.result.turns_scope'));resolve.assert_not_called()
            with lang.use(language),patch.object(agent,'_may_name_other_rooms',return_value=True),patch.object(agent,'resolve_room',return_value=('42','')), \
                 patch.object(agent,'_rooms_meta',return_value={'42':{'name':RAW}}),patch.object(agent.turns,'describe_room',return_value=RAW) as turns,patch.object(agent.notes,'read',return_value=RAW):
                value=agent.tool_recent_turns(3,RAW);self.assertEqual(value.count(RAW),3);self.own(value,language)
                turns.assert_called_once_with(n=3,chat_id='42')
    def test_describe_owned_lookup_captions_and_authored_schema_literal_under16_axes(self):
        custom={'name':'custom','description':RAW,'input_schema':{'type':'object','properties':{'literal':{'type':'string','description':RAW}}}}
        for ui in lang.LANGUAGES:
            for language in lang.LANGUAGES:
                with patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent,'_catalog_for_turn',return_value={'custom':custom}):
                    self.assertEqual(agent.tool_describe(),lang.text('control.result.describe_name'))
                    self.assertEqual(json.loads(agent.tool_describe('custom')),custom)
                    self.assertEqual(agent.tool_describe('absent'),lang.text('control.result.describe_missing',name='absent',similar=''))
    def test_capability_and_graph_owned_error_wrappers_do_not_rewrite_backend_success(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(agent.capabilities,'describe',return_value=RAW),patch.object(agent,'_active_scope',return_value='owner'),patch.object(agent.graph,'neighbors_text',return_value=RAW):
                self.assertEqual(agent.tool_my_capabilities(),RAW);self.assertEqual(agent.tool_connections(RAW),RAW)
            with lang.use(language),patch.object(agent.capabilities,'describe',side_effect=RuntimeError(RAW)),patch.object(agent,'_active_scope',return_value='owner'),patch.object(agent.graph,'neighbors_text',side_effect=RuntimeError(RAW)):
                self.assertEqual(agent.tool_my_capabilities(),lang.text('control.result.capabilities_error',error='RuntimeError'))
                self.assertEqual(agent.tool_connections(RAW),lang.text('control.result.connections_error',error='RuntimeError'))
