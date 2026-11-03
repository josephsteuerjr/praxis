"""Cancellation custody after the owner has moved into its admitted stage."""
import json,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from localharness import desktop_update_request as jobs,program_state,session_host
from localharness.package_notice import read,write


class IncomingCancel(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name).resolve();self.old=self.base/'Retired programme';self.old.mkdir()
        self.tree=self.base/'Profile';self.tree.mkdir();self.identity=uuid.uuid4().hex
        self.stage=self.base/('Helene-update-recovery-'+self.identity);self.new=self.stage/'Helene';self.new.mkdir(parents=True)
        self.helper={'fixture_role':'helper'}
        self.value={'id':self.identity,'program':str(self.old),'tree':str(self.tree),'operation':'restore_upstream',
          'staging':str(self.stage),'state':'attention','cancel_requested':True,'helper':self.helper}
        jobs.bind_incoming(self.value,self.new)
        self.descriptor={'format':session_host.FORMAT,'state':'running','generation':'actual-generation',
          'fixture_role':'owner','program':str(self.new),'tree':str(self.tree)}
        self.host_path=self.tree/'memory/.control/desktop-session.json';write(self.host_path,self.descriptor)
        self.process_states={'helper':'absent','owner':'alive','worker':'alive'}
        self.enterContext(patch.object(session_host,'process_state',side_effect=lambda row:self.process_states[row['fixture_role']]))
        self.old_guard=self.enterContext(patch.object(program_state,'staging_belongs',return_value=False))

    def test_close_of_failed_attempt_uses_bound_live_incoming_owner_without_touching_either_programme(self):
        before=jobs.path(self.tree).exists();result=jobs.settle_cancel(self.tree,self.value)
        self.assertEqual(result['state'],'cancelled')
        self.assertEqual(result['cancel_observation']['staging'],'admitted-incoming-owner')
        self.assertTrue(self.new.is_dir());self.assertEqual(list(self.old.iterdir()),[])
        self.assertEqual(jobs.path(self.tree).exists(),before,'settlement observation does not rewrite the current pointer')
        self.old_guard.assert_not_called()

    def test_running_helper_keeps_the_job_active_even_with_the_incoming_owner(self):
        self.process_states['helper']='alive'
        self.assertEqual(jobs.settle_cancel(self.tree,self.value)['state'],'attention')

    def test_absent_changed_or_unknown_incoming_owner_never_authorizes_settlement(self):
        for status in ('absent','changed','unknown'):
            with self.subTest(status=status):
                self.process_states['owner']=status
                self.assertEqual(jobs.settle_cancel(self.tree,dict(self.value))['state'],'attention')

    def test_reference_identity_profile_old_programme_and_incoming_must_all_match(self):
        target=jobs.incoming_reference(self.new);original=read(target)
        for field in ('id','path','program','incoming'):
            with self.subTest(field=field):
                write(target,{**original,field:'not-the-admitted-value'})
                self.assertEqual(jobs.settle_cancel(self.tree,dict(self.value))['state'],'attention')
        write(target,original)
        for field,value in (('tree',str(self.base/'Other profile')),('program',str(self.old)),('state','stopping'),('format','foreign-host'),('generation',''),('generation',{'not':'a generation string'})):
            with self.subTest(descriptor=field):
                write(self.host_path,{**self.descriptor,field:value})
                self.assertEqual(jobs.settle_cancel(self.tree,dict(self.value))['state'],'attention')

    def test_unknown_stage_name_is_not_accepted_merely_because_a_descriptor_names_it(self):
        wrong=self.base/'unrelated';self.stage.rename(wrong)
        value={**self.value,'staging':str(wrong)};self.new=wrong/'Helene'
        jobs.bind_incoming(value,self.new);write(self.host_path,{**self.descriptor,'program':str(self.new)})
        self.assertEqual(jobs.settle_cancel(self.tree,value)['state'],'attention')

    def test_actual_coordinator_is_still_checked_after_stage_ownership_is_confirmed(self):
        directory=self.base/'semantic-work';directory.mkdir()
        write(self.new/'code-transition-plan.json',{'plans':[{'directory':str(directory)}]})
        write(directory/'coordinator-process.json',{'fixture_role':'worker'})
        self.assertEqual(jobs.settle_cancel(self.tree,self.value)['state'],'attention')
        self.process_states['worker']='absent'
        write(directory/'pipeline.json',{'task_id':'unsettled-real-task'})
        self.assertEqual(jobs.settle_cancel(self.tree,self.value)['state'],'attention')
        write(directory/'pipeline.json',{'task_id':'unsettled-real-task','stop_confirmed':True})
        self.assertEqual(jobs.settle_cancel(self.tree,self.value)['state'],'cancelled')

    def test_reused_helper_pid_is_dead_old_work_and_is_never_killed(self):
        self.process_states['helper']='changed'
        self.assertEqual(jobs.settle_cancel(self.tree,self.value)['state'],'cancelled')

    def test_quiet_producers_never_invent_an_owner_cancellation(self):
        self.value['cancel_requested']=False
        self.assertEqual(jobs.settle_cancel(self.tree,self.value)['state'],'attention')

    def test_official_cancel_succeeds_when_its_preflight_settles_the_same_failed_job(self):
        write(jobs.path(self.tree),self.value)
        result=jobs.request(self.tree,'cancel',root=self.new,identity=self.identity,by='window')
        self.assertEqual(result['state'],'cancelled')
        self.assertEqual(read(jobs.path(self.tree))['state'],'cancelled')
        self.assertEqual(read(jobs.path(self.tree).parent/'desktop-update-jobs'/(self.identity+'.json'))['state'],'cancelled')
        again=jobs.request(self.tree,'cancel',root=self.new,identity=self.identity,by='window')
        self.assertEqual(again['cancel_settlement'],result['cancel_settlement'])

    def test_cancelled_job_does_not_admit_a_different_cancellation_identity(self):
        settled=jobs.settle_cancel(self.tree,dict(self.value));write(jobs.path(self.tree),settled)
        other=uuid.uuid4().hex
        with self.assertRaisesRegex(ValueError,'No matching active job'):
            jobs.request(self.tree,'cancel',root=self.new,identity=other,by='window')
        self.assertFalse((jobs.path(self.tree).parent/'desktop-update-jobs'/(other+'.cancel.json')).exists())
        self.assertEqual(read(jobs.path(self.tree))['id'],self.identity)


if __name__=='__main__':unittest.main(verbosity=2)
