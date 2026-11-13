"""Read hands name their outcome in the journal; the model text stays the same (10.11)."""
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent
import forge
import tool_report
import workshop


class ReadOutcomes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);(self.base/'workspace').mkdir()
        self.root=self.base/'task';self.root.mkdir()
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        for obj,name,value in [(workshop,'BASE',self.base),(agent,'BASE',self.base),
                               (forge,'BASE',self.base),(forge,'STATE_DIR',self.base/'memory/.forge'),
                               (forge,'TASKS_DIR',self.base/'memory/.forge/tasks')]:
            self.stack.enter_context(patch.object(obj,name,value))
        forge._save_task(dict(id='code-outcomes',root=str(self.root),scope='self',status='active',
                             goal='outcome tests',isolation='direct'))
        (self.base/'workspace/a.txt').write_text('needle\n',encoding='utf-8')
        (self.root/'b.txt').write_text('needle\n',encoding='utf-8')

    def outcome(self,call,*args,**kwargs):
        slot=tool_report.new();token=tool_report.attach(slot)
        try:out=call(*args,**kwargs)
        finally:tool_report.detach(token)
        return out,tool_report.summary(slot)['outcome']

    def test_home_listing_and_search_name_ok_and_refused(self):
        self.assertEqual(self.outcome(agent.tool_fs_ls,'workspace')[1],'ok')
        self.assertEqual(self.outcome(agent.tool_fs_ls,'workspace/nothing')[1],'refused')
        out,outcome=self.outcome(agent.tool_fs_search,'needle','**/*.txt','workspace')
        self.assertIn('a.txt',out);self.assertEqual(outcome,'ok')
        self.assertEqual(self.outcome(agent.tool_fs_search,'(','**/*','workspace')[1],'refused')
        self.assertEqual(self.outcome(agent.tool_fs_search,'needle','**/*','workspace/nothing')[1],'refused')

    def test_task_reads_and_inspect_name_their_outcome_and_stay_strings(self):
        task='code-outcomes'
        for call,kwargs,expected in [(agent.tool_fs_read,dict(path='b.txt',task_id=task),'ok'),
                                     (agent.tool_fs_read,dict(path='missing.txt',task_id=task),'refused'),
                                     (agent.tool_fs_ls,dict(task_id=task),'ok'),
                                     (agent.tool_fs_search,dict(pattern='needle',task_id=task),'ok'),
                                     (agent.tool_coding_inspect,dict(task_id=task,action='nonsense'),'refused'),
                                     (agent.tool_coding_inspect,dict(task_id='code-missing'),'refused')]:
            out,outcome=self.outcome(call,**kwargs)
            self.assertEqual(outcome,expected,(kwargs,out));self.assertIsInstance(out,str)
        self.assertIsInstance(forge.inspect(task,'status'),forge.InspectReply)

    def test_remote_reply_is_not_claimed_ok(self):
        remote=unittest.mock.Mock();remote.workspace_inspect.return_value='remote said something'
        with patch.object(forge,'_remote_client',return_value=remote):
            out,outcome=self.outcome(agent.tool_fs_ls,task_id='code-outcomes')
        self.assertEqual(out,'remote said something');self.assertEqual(outcome,'unknown')

    def test_batch_read_has_one_outcome_for_all_files(self):
        self.assertEqual(self.outcome(agent.tool_fs_read,paths=['workspace/a.txt'])[1],'ok')
        self.assertEqual(self.outcome(agent.tool_fs_read,paths=['workspace/a.txt','workspace/no'])[1],'partial')
        self.assertEqual(self.outcome(agent.tool_fs_read,paths=['workspace/no','workspace/none'])[1],'refused')

    def test_paged_home_read_version_conflict_is_refused(self):
        out,outcome=self.outcome(agent.tool_fs_read,'workspace/a.txt',expected_sha256='0'*64)
        self.assertIn('Конфликт версии',out);self.assertEqual(outcome,'refused')

    def test_outside_a_recorded_call_nothing_is_noted(self):
        self.assertEqual(tool_report.current(),'')
        self.assertIn('a.txt',agent.tool_fs_ls('workspace'))


if __name__=='__main__':unittest.main()
