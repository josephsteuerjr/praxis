"""Trial control flow with a synthetic clock; no OS or apply approval.

The independent native stand still requires actual 300 wall-clock seconds.
"""
from pathlib import Path
from types import SimpleNamespace
import sys,tempfile,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import portable_switch as switch,python_source_updater as updater,agents,owner_stop


class PausedTrial(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='paused-trial-unit-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.old=self.root/'Old';self.new=self.root/'New'
        self.old.mkdir();self.new.mkdir();self.clock=100.0
        self.args=SimpleNamespace(old=self.old,new=self.new)
        self.value={'note':{'id':'synthetic-unit-note'}}
        self.report=Mock();self.progress=SimpleNamespace(phase=self.report)
        self.enterContext(patch.object(switch.time,'monotonic',side_effect=lambda:self.clock))
        self.enterContext(patch.object(switch.time,'sleep',side_effect=self.sleep))
        self.enterContext(patch.object(switch,'read',return_value={'id':'synthetic-unit-note','state':'new_app_started'}))
        self.enterContext(patch.object(switch.subprocess,'Popen',return_value=SimpleNamespace(poll=lambda:None)))
        self.enterContext(patch.object(updater,'_job_report'))
        self.enterContext(patch.object(switch,'scratch_paths'))
        self.enterContext(patch.object(switch,'cleanup_staging'))
        self.finish=self.enterContext(patch.object(switch,'finish'))

    def sleep(self,seconds):self.clock+=seconds

    def test_explicit_pause_keeps_new_session_and_requires_fresh_full_working_window(self):
        samples=[]
        def health(_program):
            samples.append(self.clock)
            self.assertFalse(105<=self.clock<135,'Paused time must never be sampled as work')
            return 'stable-working-generation'
        with patch.object(switch,'trial_paused',side_effect=lambda _program:105<=self.clock<135),patch.object(switch,'health',side_effect=health):
            self.assertEqual(switch._trial_admitted(self.args,self.value,self.progress,lambda:False),0)
        self.finish.assert_called_once();window=self.finish.call_args.args[3]
        self.assertGreaterEqual(window.since,135)
        self.assertGreaterEqual(self.clock-window.since,switch.TRIAL_SECONDS)
        self.assertTrue(any(call.args[0]=='paused' for call in self.report.call_args_list))

    def test_unknown_missing_health_is_still_refused_without_a_known_pause(self):
        with patch.object(switch,'trial_paused',return_value=False),patch.object(switch,'health',side_effect=RuntimeError('No actual runner')):
            with self.assertRaisesRegex(RuntimeError,'actual runner'):
                switch._trial_admitted(self.args,self.value,self.progress,lambda:False)
        self.finish.assert_not_called()

    def test_pause_after_last_sample_restarts_before_final_admission(self):
        paused_until=[0];last_samples=[0]
        def health(_program):
            if self.clock==100+switch.TRIAL_SECONDS:
                last_samples[0]+=1
                if last_samples[0]==2:
                    paused_until[0]=self.clock+20
                    raise switch.TrialPaused('Explicit pause at final readiness')
            return 'stable-working-generation'
        with patch.object(switch,'trial_paused',side_effect=lambda _program:self.clock<paused_until[0]),patch.object(switch,'health',side_effect=health):
            self.assertEqual(switch._trial_admitted(self.args,self.value,self.progress,lambda:False),0)
        self.finish.assert_called_once()
        self.assertGreaterEqual(self.finish.call_args.args[3].since,paused_until[0])
        self.assertGreaterEqual(self.clock,paused_until[0]+switch.TRIAL_SECONDS)

    def test_observed_pause_still_resets_after_immediate_resume(self):
        paused=[False]
        def health(_program):
            if not paused[0] and self.clock>=105:
                paused[0]=True
                raise switch.TrialPaused('Pause observed just before owner resumed')
            return 'stable-working-generation'
        with patch.object(switch,'trial_paused',return_value=False),patch.object(switch,'health',side_effect=health):
            self.assertEqual(switch._trial_admitted(self.args,self.value,self.progress,lambda:False),0)
        self.assertTrue(paused[0]);self.finish.assert_called_once()
        self.assertGreaterEqual(self.finish.call_args.args[3].since,105)
        self.assertGreaterEqual(self.clock,105+switch.TRIAL_SECONDS)

    def test_pause_inside_finish_admission_restarts_without_reopening_old(self):
        paused_until=[0];attempts=[]
        def finish(*args,**_kwargs):
            attempts.append(self.clock)
            if len(attempts)==1:
                paused_until[0]=self.clock+20
                raise switch.TrialPaused('Explicit pause at finish admission')
        self.finish.side_effect=finish
        with patch.object(switch,'trial_paused',side_effect=lambda _program:self.clock<paused_until[0]),patch.object(switch,'health',return_value='stable-working-generation'):
            self.assertEqual(switch._trial_admitted(self.args,self.value,self.progress,lambda:False),0)
        self.assertEqual(len(attempts),2)
        self.assertGreaterEqual(attempts[1],paused_until[0]+switch.TRIAL_SECONDS)

    def test_pause_after_retirement_effects_stays_partial_and_keeps_new(self):
        from localharness import portable_retirement
        self.value['retirement']={'id':'synthetic-retirement'}
        def read(path):
            if Path(path)==portable_retirement.path(self.new):return {'state':'partial'}
            return {'id':'synthetic-unit-note','state':'new_app_started'}
        self.finish.side_effect=switch.TrialPaused('Explicit pause during retirement effects')
        with patch.object(switch,'trial_paused',return_value=False),patch.object(switch,'health',return_value='stable-working-generation'),patch.object(switch,'read',side_effect=read):
            with self.assertRaises(switch.TrialPaused):
                switch._trial_admitted(self.args,self.value,self.progress,lambda:False)
        self.finish.assert_called_once();self.assertTrue(self.value['_retirement_started'])

    def test_explicit_stop_remains_responsive_while_paused(self):
        with patch.object(switch,'trial_paused',return_value=True),patch.object(switch,'health') as health:
            with self.assertRaises(InterruptedError):
                switch._trial_admitted(self.args,self.value,self.progress,lambda:self.clock>=101)
        self.finish.assert_not_called();health.assert_not_called()
        self.assertLess(self.clock,102)

    def test_pause_observation_reads_only_admitted_trees_without_altering_owner_intent(self):
        data=self.root/'Selected data';other=self.root/'Unrelated data'
        owner_stop.change_pause(data,True);owner_stop.change_pause(other,True)
        marker=data/'memory/.control/autonomy-paused.json';before=marker.read_bytes()
        with patch.object(agents,'raisable',return_value=[SimpleNamespace(tree=data)]),patch.object(owner_stop,'_tree',other):
            self.assertTrue(switch.trial_paused(self.new))
            self.assertEqual(marker.read_bytes(),before)
            owner_stop.change_pause(data,False)
            self.assertFalse(switch.trial_paused(self.new),'The unrelated profile must not pause this trial')

    def test_unknown_pause_generation_refuses_instead_of_becoming_a_waiting_approval(self):
        data=self.root/'Selected data';epoch=data/'memory/.control/autonomy-generation.json'
        epoch.parent.mkdir(parents=True);epoch.write_text('{}',encoding='utf8')
        with patch.object(agents,'raisable',return_value=[SimpleNamespace(tree=data)]):
            with self.assertRaises(RuntimeError):switch.trial_paused(self.new)


if __name__=='__main__':unittest.main()
