"""Causal first-review regressions at ordinary transition consumer boundaries."""
from pathlib import Path
import os,shutil,subprocess,sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
import t_source_update_pipeline_220 as fxt
from t_source_merge_211 import BASE,LOCAL,BOTH,put
from localharness import code_transition,source_update as pipeline,source_update_worker as worker
from localharness import direct_update as direct,update_composition as comp,source_update_host as host
from localharness import source_update_launcher as launcher
from localharness.package_notice import read,write
REAL_TICK=launcher.tick


class MixedFixture(fxt.PipelineTests):
    def preflight(self):
        if not getattr(self,'mixed',False):
            self.mixed=True;put(self.code,'module.py',BASE)
            self.peer=self.root/'peer-code';self.peer_tree=self.root/'peer-data'
            put(self.peer,'module.py',BASE if getattr(self,'pristine_peer',False) else LOCAL);self.peer_tree.mkdir()
            write(self.old/'agents/peer/helene.json',{'tree':str(self.peer_tree),'code':str(self.peer),'runner':''})
        return code_transition.preflight(self.old,self.new)


class Regressions(unittest.TestCase):
    def fixture(self,pristine=False):
        f=MixedFixture('runTest');f.pristine_peer=pristine;f.setUp();self.addCleanup(f.doCleanups);return f

    def test_secondary_only_conflict_reaches_merger_tests_review_and_apply(self):
        f=self.fixture();rows=direct.core_rows(f.directory)
        self.assertEqual(read(f.directory/'plan.json')['state'],'ready')
        self.assertNotEqual(read(Path(rows[1]['directory'])/'plan.json')['state'],'ready')
        operation=f.tick();self.assertEqual(operation['state'],'merging')
        unit=f.forge._unit_dir(operation['task_id'],'agents',operation['unit_id']);request=read(unit/'request.json')
        adapter=worker.SourceMergeAdapter(request['source_update'])
        adapter.prepare(SimpleNamespace(snapshot=lambda:{'voice':{'framework':'fake','model':'fake'}}),request)
        adapter.dispatch(request,'merge_source',{'action':'resolve','path':'core-1/module.py','content':BOTH,
            'reason':'Preserve secondary authored method','expected_input_sha256':operation['input_sha256'],
            'expected_composition_sha256':comp.signature(f.directory)})
        adapter.dispatch(request,'merge_source',{'action':'finish','expected_input_sha256':operation['input_sha256']})
        write(unit/'result.json',{'status':'done','complete':True,'result':'done','model':'fake',**adapter.result_stamp()})
        self.assertEqual(f.tick()['state'],'reviewing')
        f.review_result();self.assertEqual(f.tick()['state'],'ready_to_apply')
        pipeline.validate_ready(f.directory)
        self.assertEqual((f.peer/'module.py').read_text(),LOCAL)

    def test_fully_automatic_multi_core_group_still_progresses(self):
        f=self.fixture(pristine=True)
        self.assertTrue(direct.automatic(f.directory))
        self.assertEqual(f.tick()['state'],'reviewing')

    def test_secondary_and_shared_core_profile_controls_gate_admitted_review(self):
        f=self.fixture();value=direct.group(f.directory);shared=f.root/'shared-user'
        value['profiles'].append({'tree':str(shared),'source':str(f.code)})
        for row in value['members']:
            plan=read(Path(row['directory'])/'plan.json');plan['direct_update']=value;write(Path(row['directory'])/'plan.json',plan)
        service=SimpleNamespace(tick=Mock());operation={'review_id':'already-admitted'}
        paused=shared/'memory/.control/autonomy-paused.json';write(paused,{'by':'owner'})
        with patch.object(pipeline,'_owner_blocked',side_effect=lambda tree:(Path(tree)/'memory/.control/autonomy-paused.json').exists()):
            stopped=lambda:direct.controls(f.directory)['stop'];hold=lambda:direct.controls(f.directory)['pause']
            host.advance_review(service,operation,True,stopped,hold);service.tick.assert_not_called()
            paused.unlink();host.advance_review(service,operation,True,stopped,hold)
            service.tick.assert_called_once_with(review_ids={'already-admitted'})
            service.tick.reset_mock();write(Path(value['members'][1]['directory'])/'cancel.json',{'by':'owner'})
            host.advance_review(service,operation,True,stopped,hold);service.tick.assert_not_called()

    def test_live_launcher_observation_never_rehashes_composition(self):
        f=self.fixture();operation=f.operation();operation.update(state='verification_requested',test_launcher={'root':str(f.root/'running')})
        with patch.object(launcher,'_observe',return_value=({'pid':123},None)),patch.object(comp,'signature',side_effect=AssertionError('Hot poll hashed composition')):
            pipeline._advance(operation,f.directory,f.forge,f.service)
            self.assertIsNone(REAL_TICK(operation,f.directory,lambda:False))

    def test_app_only_packaged_child_imports_trusted_incoming_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            program=Path(tmp)/'program';source=Path(__file__).resolve().parents[1]
            shutil.copytree(source/'localharness',program/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
            put(program/'tree','command_process.py','TRUSTED_INCOMING=True\n')
            entry=program/'app/localharness/source_update_launcher.py'
            code='import runpy,sys;runpy.run_path(sys.argv[1],run_name="startup_probe");import command_process;assert command_process.TRUSTED_INCOMING is True'
            env={**os.environ,'PYTHONPATH':str(program/'app')}
            result=subprocess.run([sys.executable,'-B','-c',code,str(entry)],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout.decode('utf8',errors='replace'))

    def test_unconfirmed_descendants_survive_launcher_report_stop_and_job_observation(self):
        f=self.fixture();operation=f.operation();root=f.directory/'verification/custody-case';root.mkdir(parents=True)
        value=launcher.request(operation,f.directory)
        write(root/'request.json',{'request':value,'binding':{k:v for k,v in value.items() if k!='operation'},'invocation':'test'})
        from localharness import source_update_battery as battery,session_host,desktop_update_request as jobs,program_state
        with patch.object(battery,'prepare',side_effect=battery.CustodyUnconfirmed('synthetic command group unconfirmed')):
            self.assertEqual(launcher.run(root),1)
        result=read(root/'result.json');self.assertTrue(result['shared_inputs']['retained_for_custody'])
        self.addCleanup(shutil.rmtree,result['shared_inputs']['work'])
        operation['test_launcher']={'root':str(root),**read(root/'process.json')}
        with patch.object(session_host,'process_state',return_value='absent'):
            with self.assertRaises(battery.CustodyUnconfirmed):launcher._observe(root)
            pipeline._stop(operation,f.forge,f.service);self.assertFalse(operation['stop_confirmed'])
            write(f.directory/'pipeline.json',operation)
            staging=f.root/'staging';write(staging/'Helene/code-transition-plan.json',{'plans':[{'directory':str(f.directory)}]})
            with patch.object(jobs,'_incoming_stage_owned',return_value=False),patch.object(program_state,'staging_belongs',return_value=True):
                pending,observed=jobs.producers_pending({'program':str(f.old),'staging':str(staging)})
            self.assertTrue(pending);self.assertEqual(observed['test-launcher'],'live-or-unknown')
            # A pre-fix coordinator may have lost the linkage. The passive job
            # observer must still retain it, without silently repairing state.
            operation.pop('test_launcher');operation.update(state='needs_attention')
            write(f.directory/'pipeline.json',operation)
            with patch.object(launcher,'_paths',return_value=(root,value,{})),patch.object(jobs,'_incoming_stage_owned',return_value=False),patch.object(program_state,'staging_belongs',return_value=True):
                pending,_=jobs.producers_pending({'program':str(f.old),'staging':str(staging)})
                self.assertTrue(pending);self.assertNotIn('test_launcher',read(f.directory/'pipeline.json'))
                # RuntimeError from producer observation must not escape before
                # ordinary tick records Stop and attempts all other producers.
                pipeline.tick(f.tree,f.forge,f.service,coordinator=f.coordinator,stopped=True)
            current=read(f.directory/'pipeline.json')
            self.assertTrue(current['cancel_requested']);self.assertFalse(current.get('stop_confirmed',False))
            self.assertIn('test_launcher',current)

    def test_reference_is_durable_before_child_admission(self):
        f=self.fixture();operation=f.operation();root=f.directory/'verification/admission-case'
        value=launcher.request(operation,f.directory);bound={k:v for k,v in value.items() if k!='operation'}
        import command_process
        def crash(*args,**kwargs):
            saved=read(f.directory/'pipeline.json')['test_launcher']
            self.assertEqual(saved['root'],str(root));self.assertEqual(saved['invocation'],read(root/'process.json')['invocation'])
            raise RuntimeError('simulated coordinator loss at child handoff')
        with patch.object(launcher,'_paths',return_value=(root,value,bound)),patch.object(command_process,'CommandProcess',side_effect=crash):
            with self.assertRaisesRegex(RuntimeError,'coordinator loss'):REAL_TICK(operation,f.directory,lambda:False)
        saved=read(f.directory/'pipeline.json');self.assertIn('test_launcher',saved)
        self.assertFalse(launcher.cancel(saved,f.directory))

    def test_rejoin_and_stop_restore_an_old_missing_pipeline_reference_without_replay(self):
        f=self.fixture();operation=f.operation();root=f.directory/'verification/rejoin-case';root.mkdir(parents=True)
        descriptor={'pid':123,'birth':456,'invocation':'existing','state':'running'};write(root/'process.json',descriptor)
        from localharness import session_host
        import command_process
        with patch.object(launcher,'_paths',return_value=(root,{},{})),patch.object(session_host,'process_state',return_value='alive'),patch.object(command_process,'CommandProcess') as start:
            self.assertIsNone(REAL_TICK(operation,f.directory,lambda:False))
            self.assertEqual(read(f.directory/'pipeline.json')['test_launcher']['invocation'],'existing')
            operation.pop('test_launcher');self.assertFalse(launcher.cancel(operation,f.directory))
            self.assertEqual(read(f.directory/'pipeline.json')['test_launcher']['root'],str(root))
            self.assertTrue((root/'stop.json').is_file());start.assert_not_called()


def load_tests(loader,tests,pattern):return loader.loadTestsFromTestCase(Regressions)
if __name__=='__main__':unittest.main()
