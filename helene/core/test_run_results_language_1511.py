"""Actual run/file/card ledgers plus fake git dispatch; no external effects."""
from contextlib import ExitStack
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import agent
import frame_language as frame
import logsink
from test_truth_agent import _RunHarness

RAW='Windows Егор 中文 {literal} $&'

class RunResults(_RunHarness):
    def setUp(self):
        super().setUp();self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(agent,'BASE',self.base))
        self.stack.enter_context(patch.object(agent,'tool_journal'))
    def text(self,language,key,**values):
        with frame.use(language):return frame.text('run.result.'+key,**values)
    def stuck(self,suffix):
        current=self._create(suffix,kind='task_window',channel=self._internal_channel())
        self.manager.start_tool(current.run_id,'call','narrate',{'text':RAW},side_effect=True)
        self.manager.transition(current.run_id,'in_doubt',expected='running',reason='fixture uncertain')
        return current
    def test_real_reconcile_and_close_preserve_literal_evidence_actor_and_status_all16_axes(self):
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                current=self.stuck(ui+language)
                with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent,'_reconcile_actor',return_value='principal:literal'):
                    denied=agent.tool_reconcile_run(current.run_id,'call','completed')
                    self.assertEqual(denied,self.text(language,'evidence'));self.assertEqual(self.manager.manifest(current.run_id)['status'],'in_doubt')
                    output=agent.tool_reconcile_run(current.run_id,'call','not_applied',evidence=RAW,reason=RAW)
                    self.assertIn(self.text(language,'actor',actor='principal:literal'),output)
                    row=[r for r in self.manager.events(current.run_id) if r.get('kind')=='tool_reconciled'][-1]
                    self.assertEqual((row['evidence']['praxis_evidence'],row['reason'],row['resolved_by']),(RAW,RAW,'principal:literal'))
                    self.assertEqual(self.manager.manifest(current.run_id)['status'],'paused')
                    output=agent.tool_reconcile_run(current.run_id,close=True,reason=RAW)
                    self.assertIn(self.text(language,'proof_state',status='paused'),output)
                    self.assertEqual(self.manager.manifest(current.run_id)['status'],'cancelled')
                    self.assertTrue((self.manager.path(current.run_id)/'RECAP.md').exists())
    def test_close_of_current_live_run_only_requests_pause_and_never_claims_process_exit(self):
        for language in frame.LANGUAGES:
            current=self._create('live-'+language)
            with frame.use(language),patch.object(agent.run_context,'current_run',return_value=current),patch.object(agent,'_reconcile_actor',return_value='principal:literal'):
                result=agent.tool_reconcile_run(current.run_id,close=True,reason=RAW)
                self.assertEqual(result,self.text(language,'pause_requested',id=current.run_id,proof=self.text(language,'proof_current'),status='paused',actor='principal:literal'))
                self.assertNotIn(self.manager.manifest(current.run_id)['status'],('done','cancelled','failed'))
    def test_git_pathspecs_and_raw_output_stay_literal_while_caps_and_labels_follow_frame_all16_axes(self):
        raw_output=RAW+'\n'+'x'*3500
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent,'_git_run',return_value=(0,raw_output)) as run:
                    output=agent.tool_git(action='diff',paths=['a b.py','-option',RAW])
                    run.assert_called_once_with(self.base,['diff','--stat','--','a b.py','-option',RAW])
                    self.assertTrue(output.startswith('[git self/diff]\n'+raw_output[:3000]))
                    self.assertTrue(output.endswith(self.text(language,'excerpt',shown=3000,total=len(raw_output))))
    def test_git_network_failure_is_not_fabricated_divergence_and_push_failure_does_not_push(self):
        for language in frame.LANGUAGES:
            with frame.use(language),patch.object(agent,'_git_run',side_effect=[(0,'main'),(128,'fatal: could not resolve '+RAW)]) as run:
                output=agent.tool_git(action='pull')
                self.assertIn('fatal: could not resolve '+RAW,output);self.assertTrue(output.endswith(self.text(language,'git_pull')))
                self.assertEqual(run.call_count,2)
            with frame.use(language),patch.object(agent,'_git_run',return_value=(128,RAW+'x'*700)) as run:
                output=agent.tool_git(action='push');run.assert_called_once_with(self.base,['fetch','origin'],timeout=180)
                self.assertEqual(output,self.text(language,'git_fetch',output=(RAW+'x'*700)[:600]+self.text(language,'excerpt',shown=600,total=len(RAW)+700)))
    def test_owner_marker_listing_is_readonly_reports_total_and_clear_preserves_all_other_bytes(self):
        for language in frame.LANGUAGES:
            root=self.base/language;root.mkdir();original={}
            for n in range(23):
                p=root/(str(n)+'.md');text=RAW+'\n'+agent._OWNER_MARK_PREFIX+RAW+'x'*200+']\n\n'+RAW+'\n'
                p.write_text(text,encoding='utf-8',newline='\n');original[p]=p.read_bytes()
            with frame.use(language),patch.object(agent,'_OWNER_MARK_GROUPS',(language,)):
                output=agent.tool_clear_owner_marks()
                self.assertTrue(output.startswith(self.text(language,'mark_count',total=23,shown=20)))
                self.assertEqual(sum(line.startswith('— ') for line in output.splitlines()),20)
                self.assertIn(self.text(language,'excerpt',shown=160,total=len(agent._OWNER_MARK_PREFIX+RAW+'x'*200+']')),output)
                for p,before in original.items():self.assertEqual(p.read_bytes(),before)
                self.assertEqual(agent.tool_clear_owner_marks('clear'),self.text(language,'mark_cleared',count=23))
                for p in original:self.assertEqual(p.read_bytes(),(RAW+'\n\n'+RAW+'\n').encode())
    def test_real_log_excerpt_names_each_existing_cut_without_rewriting_raw_text(self):
        root=self.base/'logs';root.mkdir();(root/'praxis.log').write_text('\n'.join([RAW+'x'*500]*40),encoding='utf-8')
        for language in frame.LANGUAGES:
            with frame.use(language),patch.object(logsink,'LOGS_DIR',root):
                shown='\n'.join([(RAW+'x'*500)[:280]]*40)
                self.assertEqual(agent.tool_read_log(lines=40),self.text(language,'log_cut',text=shown[-7000:],cap=280,lines=40,omitted=len(shown)-7000))
                self.assertEqual(agent.tool_read_log(query='absent'),self.text(language,'log_none',query=self.text(language,'log_query',query='absent')))
    def test_real_work_card_keeps_authored_summary_evidence_blocker_and_canonical_state(self):
        with patch.object(agent.work_store,'BASE',self.base):
            for language in frame.LANGUAGES:
                current=SimpleNamespace(run_id='run-'+language,goal=RAW,kind='task_window',principal_id='principal:literal')
                with frame.use(language):
                    result=agent._work_card_after_word({'action':'blocked','summary':RAW,'evidence':RAW,'blocker':RAW},current,None)
                    card=agent.work_store.for_run(current.run_id)
                    self.assertEqual((card['goal'],card['status'],card['blocker']),(RAW,'blocked',RAW))
                    self.assertEqual(result,self.text(language,'card',id=card['id'],status='blocked'))
                    records=agent.work_store.events(card['id'])
                    self.assertIn(RAW,json.dumps(records,ensure_ascii=False));self.assertTrue((self.base/'memory/work/BOARD.md').exists())
    def test_preflight_failures_do_not_dispatch_and_pins_do_not_bleed_to_next_git_call(self):
        for language in frame.LANGUAGES:
            with frame.use(language),patch.object(agent,'_home_command') as home:
                self.assertEqual(agent.tool_python(None),self.text(language,'python'));home.assert_not_called()
            with frame.use(language),patch.object(agent,'_git_run') as run:
                with self.assertRaises(agent.catalog_families.PreflightRefusal):agent.tool_git(action='add',paths=[None])
                run.assert_not_called()
        settings={'agent':{'frame_language':'fr'}}
        def git(*args,**kwargs):settings['agent']['frame_language']='zh';return 1,''
        with patch.object(frame,'settings',side_effect=lambda:settings),patch.object(agent,'_git_run',side_effect=git):
            first=agent.tool_git();second=agent.tool_git()
        self.assertEqual(first,self.text('fr','git_header',repo='self',action='status',code=self.text('fr','git_code',code=1))+self.text('fr','git_empty'))
        self.assertEqual(second,self.text('zh','git_header',repo='self',action='status',code=self.text('zh','git_code',code=1))+self.text('zh','git_empty'))
    def test_full_four_language_k_keeps_contracts_and_stays_within_existing_budget(self):
        for language in frame.LANGUAGES:
            with frame.use(language):text=agent._hands_contract_text()
            self.assertLess(len(text),4000,(language,len(text)))
            for token in ('windows_mcp_','Snapshot','FileSystem(mode=read,path,offset,limit)',
                          'Computer','run_command(argv=[...])','stdin_text','scratch','outputs',
                          'in_doubt','partial','after_sha256','expected_sha256','proposal_id',
                          'mode=snapshot','sequential','custodian','h.hand(',"r['outcome']",
                          'code_query','coverage/ambiguous','cursor','[exit unobserved]'):
                self.assertIn(token,text,(language,token))
