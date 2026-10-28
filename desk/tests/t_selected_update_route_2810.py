from pathlib import Path
import sys,unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from t_source_update_pipeline_220 import PipelineTests,BOTH
from localharness import code_transition,source_update,update_source,agent_source_update as selected
from localharness.package_notice import read,write

class SelectedRoute(unittest.TestCase):
    def setUp(self):
        self.f=PipelineTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        code_transition.seal(self.f.new)
    def modern(self):
        p=self.f.old/'app/localharness/source_update.py';p.parent.mkdir(parents=True)
        p.write_text('TOOL = '+repr(source_update.TOOL),encoding='utf-8')
    def test_main_agent_patch_then_incoming_review_without_merger(self):
        f=self.f;self.modern();update_source.select(f.old,f.new,'preserve',by='window',require_review=True)
        result=code_transition.preflight(f.old,f.new,notify=False)
        f.directory=Path(result['plans'][0]['directory']);op=read(f.directory/'pipeline.json')
        self.assertEqual(op['state'],'waiting_agent');self.assertFalse(f.spawns)
        status=source_update.command(f.tree);diff=source_update.command(f.tree,'diff',path='module.py')
        self.assertIn('incoming/module.py',diff['text'])
        before=(f.directory/'result/module.py').read_text()
        source_update.command(f.tree,'patch',path='module.py',before=before,after=BOTH,expected_candidate_sha256=status['candidate_sha256'])
        with self.assertRaisesRegex(ValueError,'Candidate changed'):
            source_update.command(f.tree,'patch',path='module.py',before=BOTH,after=before,expected_candidate_sha256=status['candidate_sha256'])
        source_update.command(f.tree,'finish')
        binding={'kind':'incoming-test','program':str(f.new),'plan_id':f.directory.name}
        source_update.ensure(f.tree,f.old,f.code,f.directory,'next',coordinator=binding)
        source_update.tick(f.tree,f.forge,f.service,coordinator=binding)
        self.assertEqual(f.operation()['state'],'reviewing');self.assertFalse(f.operation().get('task_id'))
        f.review_result();source_update.tick(f.tree,f.forge,f.service,coordinator=binding)
        self.assertEqual(source_update.validate_ready(f.directory)['state'],'ready_to_apply')
    def test_legacy_agent_gets_explicit_choice_before_any_worker(self):
        f=self.f;update_source.select(f.old,f.new,'preserve',by='window',require_review=True)
        with self.assertRaises(selected.AgentResolutionUnavailable):code_transition.preflight(f.old,f.new,notify=False)
        self.assertFalse(f.spawns)
    def test_clean_retry_only_after_recorded_capability_failure(self):
        f=self.f;update_source.select(f.old,f.new,'preserve',by='window',require_review=True)
        with self.assertRaises(ValueError):update_source.select(f.old,f.new,'clean',by='window')
        config=(f.old/'helene.json').read_bytes()
        write(f.new/'source-resolution-unavailable.json',update_source.identity(f.old,f.new))
        choice=update_source.select(f.old,f.new,'clean',by='window')
        self.assertEqual(choice['mode'],'clean');self.assertEqual((f.old/'helene.json').read_bytes(),config)
        self.assertTrue(list(f.new.parent.glob('source-choice-preserve-*.json')))

if __name__=='__main__':unittest.main()
