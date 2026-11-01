"""Passive progress is scoped, honest about liveness, and never an apply gate."""
from pathlib import Path
import os,sys,tempfile,time,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_update as pipeline,source_update_activity as activity,session_host,runtime_identity
from localharness.package_notice import read,write


class Projection(unittest.TestCase):
    def test_completed_parent_job_does_not_look_like_an_update_still_running(self):
        from localharness import desktop_update_request as jobs
        operation=read(self.directory/'pipeline.json')
        operation['coordinator']={'job_control':{'id':'parent-job','tree':str(self.tree),'path':str(jobs.path(self.tree))}}
        write(self.directory/'pipeline.json',operation)
        write(jobs.path(self.tree),{'id':'parent-job','program':str(self.program),'state':'completed'})
        value=activity.activity(self.tree);self.assertEqual(value['state'],'completed');self.assertEqual(value['role'],'none')
        write(jobs.path(self.tree),{'id':'unrelated-job','program':str(self.program),'state':'completed'})
        self.assertEqual(activity.activity(self.tree)['state'],'verification_requested')
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.tree=self.root/'profile';self.program=self.root/'incoming';self.identity='a'*64
        self.directory=self.program/'.source-updates'/self.identity
        self.operation={'id':self.identity,'tree':str(self.tree),'program':str(self.program),'directory':str(self.directory),
            'state':'verification_requested','input_sha256':'b'*64,'candidate_sha256':'c'*64,'composition_sha256':'d'*64,'version':'2.3.test'}
        write(pipeline.path(self.tree),self.operation);write(self.directory/'pipeline.json',self.operation)
        write(self.directory/'coordinator-process.json',{'pid':123,'birth':456})
        write(self.directory/'test-battery/current.json',{'key':'e'*64,**{key:self.operation[key] for key in ('input_sha256','candidate_sha256','composition_sha256')}})
        self.progress=self.directory/'test-battery'/('e'*64)/'progress.json'
        write(self.progress,{'state':'running','updated_at':time.time(),'completed':3,'total':12,'tests_completed':74,
            'active':[{'slot':0,'file':'test_real.py','phase':'running','started_at':time.time(),
                       'current_test':'ActualCase.test_in_progress','tests_completed':2}]})

    def test_projection_reads_receipts_without_hashing_or_mutating_inputs(self):
        before={p:p.read_bytes() for p in self.root.rglob('*.json')}
        with patch.object(pipeline.merge,'validate_inputs',side_effect=AssertionError('UI may not hash source')), \
             patch.object(session_host,'process_state',return_value='alive'):
            value=activity.activity(self.tree)
        self.assertEqual(value['tests']['completed'],3);self.assertTrue(value['tests']['live'])
        self.assertEqual(value['tests']['active'][0]['file'],'test_real.py')
        self.assertEqual(value['tests']['active'][0]['current_test'],'ActualCase.test_in_progress')
        self.assertEqual(value['tests']['active'][0]['tests_completed'],2)
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*.json')})

    def test_old_composition_and_path_traversal_cannot_select_other_results(self):
        pointer=read(self.directory/'test-battery/current.json');pointer['composition_sha256']='f'*64
        write(self.directory/'test-battery/current.json',pointer)
        with patch.object(session_host,'process_state',return_value='alive'):
            self.assertNotIn('tests',activity.activity(self.tree))
        request=read(pipeline.path(self.tree));request['directory']=str(self.root/'foreign')
        write(pipeline.path(self.tree),request)
        with self.assertRaises(ValueError):activity.activity(self.tree)

    def test_stale_or_dead_producer_is_never_displayed_as_live(self):
        with patch.object(session_host,'process_state',return_value='absent'):
            self.assertFalse(activity.activity(self.tree)['tests']['live'])
        row=read(self.progress);row['updated_at']=time.time()-60;write(self.progress,row)
        with patch.object(session_host,'process_state',return_value='alive'):
            self.assertFalse(activity.activity(self.tree)['tests']['live'])

    def test_runtime_context_retains_startup_version_when_disk_changes(self):
        (self.program/'app/localharness').mkdir(parents=True);(self.program/'tree').mkdir()
        (self.program/'tree/agent.py').write_text('old=True')
        (self.program/'app/localharness/runner.py').write_text('runner=True')
        write(self.program/'python-app.json',{'version':'old-loaded'})
        context=runtime_identity.capture(self.program,self.program/'tree')
        write(self.tree/'memory/.control/desk_inbox/.reader.json',{'pid':os.getpid(),'code_context':context})
        write(self.program/'python-app.json',{'version':'new-on-disk'})
        self.assertEqual(runtime_identity.current(self.tree)['version_at_start'],'old-loaded')
        self.assertTrue(runtime_identity.current(self.tree)['available'])
        context['birth']-=100;write(self.tree/'memory/.control/desk_inbox/.reader.json',{'pid':os.getpid(),'code_context':context})
        self.assertFalse(runtime_identity.current(self.tree)['available'])


if __name__=='__main__':unittest.main()
