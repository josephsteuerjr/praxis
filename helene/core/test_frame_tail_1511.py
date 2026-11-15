"""Durable result projections and live task/mail mechanics use the pinned frame axis."""
import copy
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import agent
import forge
import frame_language as fl
from test_truth_agent import _RunHarness

RAW='Literal Русский 中文 {count} <script>inert</script>'

class FrameTail(_RunHarness):
    def test_actual_result_storage_cursor_and_literal_head_tail_survive_all16_axes(self):
        context=self._create('frame-tail')
        source=RAW+'\n'+('literal-evidence-中文\n'*800)+'\n'+RAW
        ref=self.manager.store_result(context.run_id,source,call_id='tail',inline_chars=500)
        self.assertTrue(ref['inline']['truncated']);before=copy.deepcopy(ref)
        digest=hashlib.sha256(source.encode()).hexdigest();seen={}
        raw_file=(self.manager.path(context.run_id)/ref['path']).read_bytes()
        self.assertEqual(raw_file,source.encode())
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                    answer=agent._result_for_model(ref)
                    self.assertTrue(answer.startswith(ref['inline']['head']));self.assertIn(ref['inline']['tail'],answer)
                    self.assertIn('[ResultRef run='+context.run_id+' id='+ref['result_id']+' path='+ref['path']+' sha256='+digest,answer)
                    self.assertIn(fl.text('result.cursor_hint'),answer)
                    self.assertIn(fl.text('result.externalized',count=ref['inline']['omitted_chars']),answer)
                    self.assertEqual(seen.setdefault(language,answer),answer)
                    page=self.manager.read_result(context.run_id,ref['result_id'],byte_offset=0,byte_limit=len(source.encode())+1)
                    self.assertEqual(page['text'],source);self.assertEqual(page['sha256'],digest);self.assertEqual(ref,before)
        self.assertEqual(len(set(seen.values())),4)
        self.assertEqual((self.manager.path(context.run_id)/ref['path']).read_bytes(),raw_file)

    def test_actual_task_json_projects_counts_without_rewriting_goals_or_status(self):
        directory=self.base/'frame-tail-tasks'/'code-frame-tail';directory.mkdir(parents=True)
        value={'id':'code-frame-tail','status':'active','goal':RAW,'created':'2026-01-01T00:00:00'}
        path=directory/'task.json';path.write_text(json.dumps(value),encoding='utf8');before=path.read_bytes()
        (directory/'agents/a').mkdir(parents=True);(directory/'agents/b').mkdir();(directory/'agents/a/result.json').write_text('{}',encoding='utf8')
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(forge,'TASKS_DIR',directory.parent):
                    row=fl.text('runs.coding.row',id=value['id'],status='active',done=1,agents=2,goal=RAW[:70])
                    self.assertEqual(forge.live_tasks_brief(),[row]);self.assertEqual(agent._coding_tasks_line(),fl.text('runs.coding.active',count=1,rows='  '+row))
                    self.assertEqual(path.read_bytes(),before)

    def test_short_and_empty_result_do_not_claim_truncation(self):
        for value in ('',RAW):
            context=self._create('short-'+str(len(value)))
            ref=self.manager.store_result(context.run_id,value,call_id='short',inline_chars=500)
            for language in fl.LANGUAGES:
                with fl.use(language):
                    answer=agent._result_for_model(ref)
                    self.assertTrue(answer.startswith(value+'\n[ResultRef '));self.assertNotIn(fl.text('result.externalized',count=0),answer)

    def test_live_task_rpc_is_once_and_caption_remains_pinned_after_settings_change(self):
        choice={'language':'en'}
        def settings():return {'lang':'zh','agent':{'frame_language':choice['language']}}
        def read():choice['language']='fr';return [RAW]
        with patch.object(fl,'settings',side_effect=settings),patch.object(forge,'live_tasks_brief',side_effect=read) as rpc:
            answer=agent._coding_tasks_line();rpc.assert_called_once()
        with fl.use('en'):self.assertEqual(answer,fl.text('runs.coding.active',count=1,rows='  '+RAW))
        for language in fl.LANGUAGES:
            with fl.use(language):
                with patch.object(forge,'live_tasks_brief',return_value=[]):self.assertEqual(agent._coding_tasks_line(),fl.text('runs.coding.empty'))
                with patch.object(forge,'live_tasks_brief',side_effect=OSError(RAW)):self.assertEqual(agent._coding_tasks_line(),fl.text('runs.coding.unavailable'))

    def test_mail_data_remains_lower_role_and_mechanics_follow_frame_without_owner_name(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent,'_mailbox_index',return_value=RAW) as index:
                    answer=agent._mailbox_frame_block();index.assert_called_once()
                    self.assertEqual(answer,fl.text('mailbox.mechanics'));self.assertNotIn(RAW,answer)
                    self.assertIn(RAW,agent._mailbox_evidence())
                with fl.use(language),patch.object(agent,'_mailbox_index',return_value=''):
                    self.assertEqual(agent._mailbox_frame_block(),'');self.assertEqual(agent._mailbox_evidence(),'')

if __name__=='__main__':unittest.main()
