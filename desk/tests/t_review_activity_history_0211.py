"""Reviewer monitoring survives conflict repair; it never grants execution authority."""
from pathlib import Path
import os,tempfile,time,unittest
from unittest.mock import patch
from localharness import self_review_integration as integration,source_update_activity
from localharness.package_notice import write

class ReviewerHistory(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.tree=Path(self.temp.name)/'agent'
        self.reader=patch.object(source_update_activity,'activity',return_value=None);self.activity=self.reader.start();self.addCleanup(self.reader.stop)
    def record(self,index,*,execution='done',age=3600,cancel=False,count=1):
        finished=time.time()-age;identity='review-'+format(index,'016x')
        path=integration._root(self.tree).with_name('.source-update-reviews')/identity/'request.json'
        critics=[{'unit_id':'agent-'+format(n,'08x'),'model':'fixture-model','execution':execution,
            'started_at':finished-70,'finished_at':finished if execution=='done' else None,
            'summary':'Проверка завершена','verdict':'findings','findings':[{'path':'one.py','detail':'Нужна поправка'}]} for n in range(count)]
        write(path,{'id':identity,'task_id':'code-'+str(index),'execution':execution,'created':finished-80,'cancel_requested':cancel,'critics':critics})
        os.utime(path,(finished,finished));return identity,path
    def test_completed_unselected_review_is_recent_and_never_live(self):
        identity,path=self.record(1);before=path.read_bytes();value=integration.activity(self.tree)
        rows=value.get('recent_reviewers',[]);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['review_id'],identity)
        self.assertFalse(rows[0]['live']);self.assertEqual(rows[0]['task_id'],'code-1');self.assertEqual(rows[0]['unit_id'],'agent-00000000')
        self.assertEqual(rows[0]['findings'][0]['path'],'one.py');self.assertEqual(value['running'],0);self.assertEqual(before,path.read_bytes())
    def test_findings_remain_visible_after_pipeline_clears_review_id_for_agent_repairs(self):
        identity,_=self.record(2);self.activity.return_value={'id':'update','state':'waiting_agent','editing':{'completed':1,'total':3}}
        value=integration.activity(self.tree);self.assertEqual(value['update']['state'],'waiting_agent')
        self.assertIn(identity,[r['review_id'] for r in value.get('recent_reviewers',[])])
    def test_stale_selected_running_record_is_visible_without_live_claim(self):
        identity,_=self.record(3,execution='running',age=60);self.activity.return_value={'id':'update','review_id':identity,'state':'reviewing'}
        value=integration.activity(self.tree);self.assertEqual(len(value['reviewers']),1);self.assertFalse(value['reviewers'][0]['live']);self.assertEqual(value['running'],0)
    def test_active_roster_is_not_cropped_by_recent_page_limit(self):
        _,_=self.record(4,execution='running',age=0,count=27)
        value=integration.activity(self.tree,review_limit=2);self.assertEqual(len(value['reviewers']),27)
    def test_cancelled_parent_does_not_erase_a_completed_critic(self):
        identity,_=self.record(5,cancel=True)
        self.assertIn(identity,[r['review_id'] for r in integration.activity(self.tree).get('recent_reviewers',[])])
    def test_cancel_request_does_not_claim_a_stale_running_critic_is_live(self):
        identity,_=self.record(12,cancel=True,execution='running',age=0)
        self.activity.return_value={'id':'update','review_id':identity}
        value=integration.activity(self.tree);self.assertFalse(value['reviewers'][0]['live']);self.assertEqual(value['running'],0)
    def test_recent_cursor_is_stable_and_does_not_repeat_a_page(self):
        for index in range(6,10):self.record(index,age=index*100)
        first=integration.activity(self.tree,review_limit=2);self.assertEqual(len(first['recent_reviewers']),2)
        self.assertTrue(first['recent_reviewers_cursor'])
        second=integration.activity(self.tree,review_limit=2,review_cursor=first['recent_reviewers_cursor'])
        self.assertEqual(len(second['recent_reviewers']),2);self.assertFalse(second['recent_reviewers_cursor'])
        self.assertFalse({r['id'] for r in first['recent_reviewers']} & {r['id'] for r in second['recent_reviewers']})
    def test_another_profile_does_not_receive_review_history(self):
        self.record(11);other=integration.activity(self.tree.parent/'another')
        self.assertEqual(other.get('recent_reviewers',[]),[]);self.assertEqual(other['reviewers'],[])
    def test_invalid_cursor_is_rejected(self):
        with self.assertRaises(ValueError):integration.activity(self.tree,review_cursor='not-a-cursor')

if __name__=='__main__':unittest.main()
