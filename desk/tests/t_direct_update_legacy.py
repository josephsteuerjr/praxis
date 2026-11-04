"""Historical non-direct admission is seeded before the current preflight runs."""
from pathlib import Path
import sys,tempfile,unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
import t_source_update_pipeline_220 as fxt
from localharness import code_transition,direct_update as direct,source_merge as merge,source_update as pipeline
from localharness.package_notice import read,write


class LegacyFixture(fxt.PipelineTests):
    def preflight(self):
        if not getattr(self,'seeded',False):
            self.seeded=True;incoming=merge.inventory(self.new/'tree');local=merge.inventory(self.code)
            root=self.tree/'memory/.source-updates';root.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root) as temporary:
                base=code_transition.baseline(self.old,Path(temporary),[self.code]);files=merge.inventory(base)
                identity=merge.signature({'base':files,'local':local,'incoming':incoming,'source':str(self.code.resolve()),
                    'operation':'','app_source':None,'version':self.incoming_version})
                directory=root/identity;plan=merge.create(base,self.code,self.new/'tree',directory)
            # Exact historical pre-direct carrier, not a new grouped plan with
            # metadata stripped after it was admitted.
            write(self.new/'code-transition-plan.json',{'format':'helene-code-plan.v1','old':str(self.old.resolve()),
                'require_review':False,'choice':None,'sources':[str(self.code.resolve())],'incoming':incoming,
                'plans':[{'source':str(self.code.resolve()),'directory':str(directory),'state':plan['state'],'error':''}]})
            pipeline.ensure(self.tree,self.old,self.code,directory,self.incoming_version,coordinator=self.coordinator)
        return code_transition.preflight(self.old,self.new)


class Legacy(unittest.TestCase):
    def fixture(self):
        f=LegacyFixture('runTest');f.setUp();self.addCleanup(f.doCleanups);return f

    def test_restart_preflight_keeps_admitted_worker_identity_and_then_completes(self):
        f=self.fixture();self.assertIsNone(direct.group(f.directory));f.tick()
        before=f.operation();identity=merge.input_signature(read(f.directory/'plan.json'))
        for _ in range(2):code_transition.preflight(f.old,f.new)
        after=f.operation()
        self.assertIsNone(direct.group(f.directory));self.assertEqual(merge.input_signature(read(f.directory/'plan.json')),identity)
        for name in ('id','task_id','unit_id','attempts','input_sha256'):self.assertEqual(after[name],before[name])
        f.merge_result();self.assertEqual(f.tick()['state'],'reviewing')
        f.review_result();self.assertEqual(f.tick()['state'],'ready_to_apply');pipeline.validate_ready(f.directory)
        review_id=f.operation()['review_id'];code_transition.preflight(f.old,f.new);code_transition.validate(f.new)
        self.assertEqual(f.operation()['review_id'],review_id)

    def test_fresh_direct_reentry_keeps_its_group_and_input_signature(self):
        f=fxt.PipelineTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        value=direct.group(f.directory);identity=merge.input_signature(read(f.directory/'plan.json'))
        for _ in range(2):code_transition.preflight(f.old,f.new)
        self.assertEqual(direct.group(f.directory),value)
        self.assertEqual(merge.input_signature(read(f.directory/'plan.json')),identity)

    def test_damaged_carrier_refuses_before_rewriting_admitted_plan(self):
        f=self.fixture();before=(f.directory/'plan.json').read_bytes()
        (f.new/'code-transition-plan.json').write_text('{invalid',encoding='utf8')
        with self.assertRaises(ValueError):code_transition.preflight(f.old,f.new)
        self.assertEqual((f.directory/'plan.json').read_bytes(),before)

    def test_marker_only_legacy_preparation_is_not_converted_after_missing_carrier(self):
        f=self.fixture();(f.new/'code-transition-plan.json').unlink()
        # Existing plan-local admission alone suffices; no directory-wide scan.
        before=merge.input_signature(read(f.directory/'plan.json'))
        code_transition.preflight(f.old,f.new)
        self.assertIsNone(direct.group(f.directory));self.assertEqual(merge.input_signature(read(f.directory/'plan.json')),before)


def load_tests(loader,tests,pattern):return loader.loadTestsFromTestCase(Legacy)
if __name__=='__main__':unittest.main()
