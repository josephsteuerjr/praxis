"""Real app/core bytes with a fake settled Forge boundary, no approval claim."""
from pathlib import Path
from types import SimpleNamespace
import sys,unittest
from unittest.mock import Mock,patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from localharness import app_source_update,source_update as pipeline,source_merge as merge,update_composition as composition,owner_stop
from localharness.package_notice import read,write
import t_app_source_update_3110 as material

class ApplicationCorrection(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(owner_stop,'stopped',return_value=False))
        self.fx=material.AppSourceUpdate();self.addCleanup(self.fx.doCleanups);self.fx.setUp()
        self.fx.owner_edit()
        self.fx.put(self.fx.new,'app/localharness/feature.py',material.FEATURE.replace('owner = "factory"','owner = "upstream"'))
        _,self.operation=self.fx.prepare_pair();self.directory=self.fx.directory
        self.finish('rejected')
        self.core=merge.candidate_signature(self.directory)
        self.rejected=composition.signature(self.directory)
        self.operation.update(attempts=[{'kind':'previous-rejected'}],max_attempts=0)
        pipeline._start_attempt(self.operation,self.directory,None)
        self.unit=self.directory/'fake-forge'/self.operation['task_id']/self.operation['unit_id'];self.unit.mkdir(parents=True)
        self.forge=SimpleNamespace(source_update_task=lambda *args:None,tick_queued=lambda *args:None,
            _unit_dir=lambda *args:self.unit,_unit_state=lambda unit:{'status':'done'})

    def finish(self,value):
        content=material.FEATURE.replace('owner = "factory"','owner = '+repr(value))
        composition.repair(self.directory,'app/localharness/feature.py',content,
            expected=composition.signature(self.directory),reason='Reconcile authored feature and shared application API')
        app_source_update.finish(self.directory)
        merge.finish(self.directory,exe=self.fx.git)

    def result(self,**changes):
        value={'status':'done','complete':True,'plan_id':self.operation['id'],
            'input_sha256':self.operation['input_sha256'],'candidate_verified':True,
            'candidate_sha256':merge.candidate_signature(self.directory),'composition_verified':True,
            'composition_sha256':composition.signature(self.directory),**changes}
        write(self.unit/'result.json',value)

    def test_app_only_correction_changes_full_identity_without_dummy_core_edit(self):
        self.assertEqual(self.operation['before_composition_sha256'],self.rejected)
        self.finish('repaired')
        self.assertEqual(merge.candidate_signature(self.directory),self.core)
        self.assertNotEqual(composition.signature(self.directory),self.rejected)
        self.result()
        self.assertTrue(pipeline._merge_tick(self.operation,self.directory,self.forge))
        self.assertEqual(self.operation['composition_sha256'],composition.signature(self.directory))

    def test_unchanged_rejected_app_cannot_gain_another_acceptance(self):
        merge.finish(self.directory,exe=self.fx.git);self.result()
        with self.assertRaisesRegex(ValueError,'did not change'):
            pipeline._merge_tick(self.operation,self.directory,self.forge)

    def test_missing_or_stale_full_app_stamp_cannot_pass_core_only_proof(self):
        self.finish('repaired')
        for changes in ({'composition_verified':False},{'composition_sha256':self.rejected}):
            with self.subTest(changes=changes):
                self.result(**changes)
                with self.assertRaisesRegex(ValueError,'application composition evidence'):
                    pipeline._merge_tick(self.operation,self.directory,self.forge)

if __name__=='__main__':unittest.main()
