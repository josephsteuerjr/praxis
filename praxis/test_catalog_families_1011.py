"""Action equivalence, exact media receipts and the offered catalogue boundary."""
import copy
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent
import workshop

RETIRED = set('propose_host_change list_host_changes restart_mailbot consolidate_context project_run inbox_read inbox_list fs_write start_proposal submit_proposal proposal_diff list_proposals manage_autonomy project_create project_list project_status project_test pip_install connections add_alias forget_connection observe_self set_avatar freeze_contact server_logs code_map send_media'.split())


class CatalogFamilies(unittest.TestCase):
    def ctx(self, owner=True):
        return agent.ChannelContext(chat_id='42', room_id='42', is_dm=True, owner=owner, known=True)

    def call(self, tool, **args):
        return agent.TOOL_IMPL[tool](**args)

    def test_retired_names_have_no_schema_or_dispatch_alias(self):
        for owner in (True, False):
            names={t['name'] for t in agent.catalog_tools_for(self.ctx(owner)) if t.get('name')}
            self.assertFalse(names & RETIRED)
            self.assertTrue({'python','proposal','project','memory_graph'} <= names)
            for spec in agent.catalog_tools_for(self.ctx(owner)):
                text=json.dumps(spec,ensure_ascii=False)
                for old in RETIRED:
                    self.assertIsNone(re.search(r'(?<!\w)'+re.escape(old)+r'(?!\w)',text),(spec.get('name'),old))
        self.assertFalse(RETIRED & agent.TOOL_IMPL.keys())

    def test_every_speaker_gets_the_same_catalogue(self):
        # Egor 10.11: all rights everywhere; the old owner pair (admit/computer_access) is removed.
        owner=agent.catalog_tools_for(self.ctx()); guest=agent.catalog_tools_for(self.ctx(False))
        self.assertEqual(owner,guest)
        self.assertFalse({'admit','computer_access'}&{t.get('name') for t in owner})
        names=[t.get('name') for t in guest]
        if 'end_turn' in names:
            self.assertEqual(names[-1],'end_turn')
        self.assertLess(names.index('python'),names.index('edit'))

    def test_family_map_has_no_retired_names_and_keeps_outcomes_compact(self):
        names=[n for _,group in agent.HAND_GROUPS for n in group]
        self.assertEqual(len(names),len(set(names)))
        self.assertFalse(RETIRED & set(names))
        self.assertTrue({'memory_compact','coding_review','coding_control','second_look'}<=set(names))
        contract=agent._hands_contract_text()
        # Egor 10.11: every schema is native again, so K carries no rare signatures; it is the
        # family map plus the shared outcome and scripting contract, kept compact.
        self.assertLess(len(contract),4000)
        for word in ('confirmed','refused','in_doubt','partial','expected_sha256','Stop','Python'):
            self.assertIn(word,contract)

    def test_preflight_refusal_is_an_observed_non_effect_not_uncertainty(self):
        import tool_report
        report=tool_report.new();token=tool_report.attach(report)
        try:
            with patch.object(workshop,'pip_install') as backend:
                out=agent._call_tool_with_ceiling('project',agent.TOOL_IMPL['project'],{'action':'pip','name':'x'})
            self.assertIn('refused:',out);self.assertEqual(report['outcome'],'refused')
            backend.assert_not_called()
        finally:tool_report.detach(token)

    def test_backend_failure_is_never_relabelled_as_preflight_refusal(self):
        with patch.object(workshop,'pip_install',side_effect=ValueError('after attempted operation')):
            with self.assertRaisesRegex(ValueError,'after attempted operation'):
                agent._call_tool_with_ceiling('project',agent.TOOL_IMPL['project'],{'action':'pip','name':'x','packages':'pkg'})

    def test_read_only_scout_and_window_hints_use_live_names(self):
        self.assertTrue(set(agent._SCOUT_TOOL_NAMES) <= set(agent.TOOL_IMPL))
        self.assertFalse(set(agent._SCOUT_TOOL_NAMES) & RETIRED)
        for text in (agent._HEARTBEAT_FRAME, agent._CODING_WINDOW_FRAME, agent._TASK_WINDOW_BODY):
            for name in RETIRED:
                self.assertNotIn(name, text, name)

    def test_project_routes_and_validates_before_effect(self):
        for action,func,args in [('create','project_create',('x','purpose')),
                                 ('status','project_status',('x',)),
                                 ('test','run_tests',('x',)),('pip','pip_install',('x','pkg==1'))]:
            with self.subTest(action=action),patch.object(workshop,func,return_value='result') as fn:
                kw={'name':'x'}
                if action=='create':kw['brief']='purpose'
                if action=='pip':kw['packages']='pkg==1'
                self.assertEqual(self.call('project',action=action,**kw),'result');fn.assert_called_once_with(*args)
        with patch.object(workshop,'pip_install') as fn:
            with self.assertRaises(ValueError):self.call('project',action='pip',name='x')
            fn.assert_not_called()

    def test_proposal_submit_passes_all_evidence_and_risk_is_not_an_alias(self):
        args=dict(id='p',title='change',why='why',review='review',checked='checked',override_reason='override',reviewer='reviewer')
        with patch.object(agent,'tool_submit_proposal',return_value='submitted') as fn:
            self.assertEqual(self.call('proposal',action='submit',**args),'submitted')
            fn.assert_called_once_with(*args.values())
        with patch.object(agent.selfdev,'autonomy_change',return_value='risk') as fn:
            self.assertEqual(self.call('proposal',action='risk',risk_action='add',pattern='x/*'),'risk')
            fn.assert_called_once_with('add','x/*')
        with patch.object(workshop,'run_tests',return_value='tests') as fn:
            self.assertEqual(self.call('proposal',action='test'),'tests');fn.assert_called_once_with('self')

    def test_family_effect_classification_does_not_replay_mutations_as_reads(self):
        for name,read,write in [('proposal',{'action':'diff'},{'action':'submit'}),
                              ('proposal',{'action':'risk'},{'action':'risk','risk_action':'add'}),
                              ('project',{'action':'list'},{'action':'test'}),
                              ('memory_graph',{'action':'path'},{'action':'forget'}),
                              ('manage_identity',{'action':'status'},{'action':'observe'})]:
            self.assertFalse(agent._tool_has_side_effect(name,read),(name,read))
            self.assertTrue(agent._tool_has_side_effect(name,write),(name,write))

    def test_absence_graph_cannot_write_even_with_forged_action(self):
        with agent._bind_tool_catalog(self.ctx(False),agent.ABSENCE_TOOLS),patch.object(agent,'tool_add_alias') as fn:
            with self.assertRaises(ValueError):self.call('memory_graph',action='alias',name='x',to='y')
            fn.assert_not_called()

    def test_graph_actions_retain_nodes_and_depth(self):
        with patch.object(agent,'tool_connections',return_value='graph') as fn:
            self.assertEqual(self.call('memory_graph',action='path',name='x',to='y',depth=2),'graph')
            fn.assert_called_once_with('x',2,'y')
        with patch.object(agent,'tool_forget_connection',return_value='forgot') as fn:
            self.assertEqual(self.call('memory_graph',action='forget',name='x',to='y'),'forgot')
            fn.assert_called_once_with('x','y')

    def test_identity_observation_preserves_its_original_evidence_route(self):
        with patch.object(agent,'tool_update_self',return_value='observed') as fn:
            self.assertEqual(self.call('manage_identity',action='observe',note='evidence'),'observed')
            fn.assert_called_once_with('evidence')

    def test_boundary_is_current_only_and_owner_dm_is_preserved(self):
        with patch.object(agent,'_current_room',return_value='42'),patch.object(agent,'_active_scope',return_value='owner'),patch.object(agent.rooms,'self_demote') as fn:
            self.assertIn('Егора',self.call('freeze_chat',action='boundary',reason='boundary'))
            self.assertIn('current',self.call('freeze_chat',action='boundary',chat_id='other'))
            fn.assert_not_called()
        with patch.object(agent,'_current_room',return_value='99'),patch.object(agent,'_active_scope',return_value='guest'),patch.object(agent.rooms,'self_demote',return_value=(True,'frozen')) as fn,patch.object(agent,'tool_journal'):
            self.call('freeze_chat',action='boundary',reason='my boundary')
            fn.assert_called_once_with('99','frozen',reason='my boundary',ttl_h=0)

    def test_avatar_only_needs_only_photo_bridge_and_mixed_reports_both(self):
        with patch.dict(agent._TELETHON,{'set_profile_photo':lambda path:'photo '+path},clear=True):
            self.assertEqual(self.call('update_profile',avatar='a.png'),'avatar: photo a.png')
        with patch.dict(agent._TELETHON,{'set_profile_photo':lambda path:'photo refused','update_profile':lambda **kw:'metadata saved'},clear=True):
            self.assertEqual(self.call('update_profile',about='about',avatar='a.png'),'metadata saved\navatar: photo refused')

    def test_service_log_defaults_and_query_do_not_silently_disappear(self):
        with patch.object(agent.hostview,'logs',return_value='log') as fn:
            self.assertEqual(self.call('read_log',unit='praxis'),'log');fn.assert_called_once_with('praxis',60)
            with self.assertRaises(ValueError):self.call('read_log',unit='praxis',query='needle')
            self.assertEqual(fn.call_count,1)

    def test_outline_file_directory_and_project_roots(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'a.py').write_text('def unique_symbol():\n    pass\n')
            with patch.object(workshop,'BASE',root),patch.object(workshop,'REPO',root),patch.object(workshop,'_project_dir',return_value=root):
                for args in ({'path':'a.py'},{'path':str(root)},{'scope':'self'},{'scope':'project'}):
                    self.assertIn('unique_symbol',self.call('code_outline',**args))
                with self.assertRaises(ValueError):self.call('code_outline',path='a.py',scope='self')

    def test_regular_read_replaces_inbox_binary_guard_and_keeps_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'blob').write_bytes(b'header\x00payload')
            with patch.object(workshop,'BASE',root),patch.object(workshop,'REPO',root):
                out=workshop.fs_read('blob')
                self.assertIn('бинарный',out);self.assertIn('sha256=',out);self.assertNotIn('payload',out)


class UnifiedMediaReceipts(unittest.TestCase):
    def setUp(self):
        import test_send_media_identity_0311 as fixtures
        self.fx=fixtures.AddressedMediaIdentity();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.fx.args={'path':str(self.fx.source),'caption':'','to':'42','kind':'audio','voice_note':True}
        self.fx.key=agent._tool_idempotency_key(self.fx.context,'unified','send_file',self.fx.args)
        self.fx.manager.start_tool(self.fx.context.run_id,'unified','send_file',self.fx.args,side_effect=True,idempotency_key=self.fx.key)
        token=agent._TOOL_EXECUTION.set({'run_id':self.fx.context.run_id,'call_id':'unified','tool':'send_file',
                                       'args':self.fx.args,'idempotency_key':self.fx.key,'side_effect':True})
        self.addCleanup(agent._TOOL_EXECUTION.reset,token)

    def invoke(self):return agent.TOOL_IMPL['send_file'](**self.fx.args)

    def test_voice_send_and_exact_retry_keep_new_name_kind_and_proof(self):
        result=self.invoke();entry=self.fx.outbox.get(self.fx.key)
        self.assertEqual(entry['state'],'accepted',result)
        proof=agent._direct_outbox_identity(entry,verify_file=True)
        self.assertEqual(proof['tool'],'send_file');self.assertEqual(proof['payload']['media_kind'],'audio')
        self.assertTrue(proof['payload']['voice_note']);self.assertTrue(agent.direct_outbox_prepared(entry))
        self.assertEqual(self.invoke(),result);self.assertEqual(self.fx.send.await_count,1)
        changed=copy.deepcopy(entry);changed['payload']['voice_note']=False
        with self.assertRaises(agent.DurableExecutionError):agent.run_direct_outbox_prepared(changed)

    def test_pending_unified_voice_closes_paused_call_from_receipt_without_resend(self):
        self.fx.send.side_effect=ConnectionError('offline fixture')
        with self.assertRaises(agent.DurableSideEffectPending):self.invoke()
        self.fx.fx._pause(self.fx.context,reason='durable send_file intent awaits Telegram acceptance')
        accepted=self.fx.outbox.mark_accepted(self.fx.key,message_id=509)
        self.assertTrue(agent.run_direct_outbox_accepted(accepted))
        self.assertTrue(agent.run_direct_outbox_accepted(accepted))
        self.assertNotIn('unified',self.fx.manager.outstanding_tools(self.fx.context.run_id))
        self.assertEqual(self.fx.send.await_count,1)
