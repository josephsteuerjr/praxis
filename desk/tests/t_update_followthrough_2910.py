"""Current-job reminders survive idle turns without replaying stopped work."""
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import agent_source_update as agent,source_update as pipeline,owner_stop,session_host
from localharness.package_notice import read,write


class Followthrough(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.tree=Path(self.temp.name);self.directory=self.tree/'plan';self.directory.mkdir()
        self.request={'id':'current','directory':str(self.directory),'resolution_owner':'agent','state':'waiting_agent','tries':3}
        self.operation={'state':'waiting_agent','tree':str(self.tree),'candidate_sha256':'one'}
        self.real_job_blocked=pipeline.job_blocked
        write(pipeline.path(self.tree),self.request);write(self.directory/'pipeline.json',self.operation)
        write(self.directory/'plan.json',{'state':'ready','result':{}})
        for mocker in [patch.object(pipeline,'_bound',return_value=(self.directory,{'state':'ready','result':{}})),
            patch.object(pipeline,'locate',return_value=self.directory),
            patch.object(pipeline,'job_blocked',return_value=False),patch.object(owner_stop,'stopped',return_value=False),
            patch.object(owner_stop,'paused',return_value=False)]:
            mocker.start();self.addCleanup(mocker.stop)
        self.turn=Mock()

    def test_prior_three_turn_cap_does_not_hide_a_new_event_and_time_alone_never_replays_it(self):
        with patch.object(agent.time,'time',return_value=1000):agent.notify(self.tree,self.turn)
        self.turn.assert_called_once();self.assertIn('Следующий шаг',self.turn.call_args.kwargs['origin_text'])
        with patch.object(agent.time,'time',return_value=1001):agent.notify(self.tree,self.turn)
        self.assertEqual(self.turn.call_count,1)
        with patch.object(agent.time,'time',return_value=1400):agent.notify(self.tree,self.turn)
        self.assertEqual(self.turn.call_count,1)

    def test_progress_changes_next_step_without_old_notice_replay(self):
        agent.notify(self.tree,self.turn)
        self.operation['review_clear']=True;write(self.directory/'pipeline.json',self.operation)
        agent.notify(self.tree,self.turn)
        self.assertEqual(self.turn.call_count,2)
        self.assertIn('вызови apply',self.turn.call_args.kwargs['origin_text'])

    def test_active_verifier_is_not_nagged_but_missing_verifier_gets_concrete_diagnosis(self):
        self.operation['state']='verification_requested';write(self.directory/'pipeline.json',self.operation)
        write(self.directory/'coordinator-process.json',{'pid':123,'birth':1})
        with patch.object(session_host,'process_state',return_value='alive'):agent.notify(self.tree,self.turn)
        self.turn.assert_not_called()
        with patch.object(session_host,'process_state',return_value='absent'):agent.notify(self.tree,self.turn)
        self.assertIn('coordinator-result.json',self.turn.call_args.kwargs['origin_text'])

    def test_terminal_cancel_pause_and_owner_stop_do_not_wake(self):
        for changes in [{'state':'cancelled'},{'state':'ready_to_apply'},{'cancel_requested':True},{'paused':True}]:
            write(self.directory/'pipeline.json',{**self.operation,**changes});agent.notify(self.tree,self.turn)
        self.turn.assert_not_called()
        write(self.directory/'pipeline.json',self.operation)
        with patch.object(owner_stop,'stopped',return_value=True):agent.notify(self.tree,self.turn)
        with patch.object(owner_stop,'paused',return_value=True):agent.notify(self.tree,self.turn)
        self.turn.assert_not_called()

    def test_completed_parent_job_never_wakes_an_old_agent_resolution(self):
        job_path=self.tree/'job.json'
        self.operation['coordinator']={'job_control':{'id':'active-job','tree':str(self.tree),'path':str(job_path)}}
        write(self.directory/'pipeline.json',self.operation)
        with patch.object(pipeline,'job_blocked',self.real_job_blocked):
            for state in ('completed','cancelled','superseded'):
                write(job_path,{'id':'active-job','state':state});agent.notify(self.tree,self.turn)
        self.turn.assert_not_called()

    def test_progress_racing_with_reminder_reservation_is_not_nagged_as_old_work(self):
        original=agent.write
        def save(path,value):
            original(path,value)
            if Path(path).name=='agent-notice.json':
                write(self.directory/'pipeline.json',{**self.operation,'state':'ready_to_apply','review_clear':True})
        with patch.object(agent,'write',side_effect=save):agent.notify(self.tree,self.turn)
        self.turn.assert_not_called()

    def test_notification_never_overwrites_the_current_request_pointer(self):
        before=pipeline.path(self.tree).read_bytes()
        agent.notify(self.tree,self.turn)
        self.assertEqual(pipeline.path(self.tree).read_bytes(),before)

    def test_only_a_failed_delivery_is_retried_without_new_work(self):
        self.turn.return_value='failed'
        with patch.object(agent.time,'time',return_value=1000):agent.notify(self.tree,self.turn)
        with patch.object(agent.time,'time',return_value=1001):agent.notify(self.tree,self.turn)
        self.assertEqual(self.turn.call_count,1)
        self.turn.return_value='silent'
        with patch.object(agent.time,'time',return_value=1061):agent.notify(self.tree,self.turn)
        self.assertEqual(self.turn.call_count,2)
        with patch.object(agent.time,'time',return_value=2000):agent.notify(self.tree,self.turn)
        self.assertEqual(self.turn.call_count,2)

if __name__=='__main__':unittest.main()
