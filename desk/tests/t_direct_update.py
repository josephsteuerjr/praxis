"""Transition-level consumer contracts, not a full product battery."""
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch,Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
import t_update_contract_230 as fixture
from localharness import direct_update as direct,update_composition as comp,source_merge as merge
from localharness import source_update as pipeline,agent_source_update as hand
from localharness.package_notice import read,write


class Transition(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CompositionTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.tree,self.peer,self.rows=self.f.peer()
        self.rows[0]['tree']=str(self.f.tree);self.rows[1]['tree']=str(self.tree)
        # This legacy fixture prepared per-core app copies. New preflight binds
        # membership BEFORE preparing app; rebuild only these disposable copies.
        for row in self.rows:
            root=Path(row['directory']);(root/'composition.json').unlink()
            shutil.rmtree(root/'compound')
        direct.bind(self.f.program,self.rows)
        self.operation=direct.operation(self.f.directory,self.f.operation)
        write(self.f.directory/'pipeline.json',self.operation);write(pipeline.path(self.f.tree),self.operation)
        write(hand.marker(self.f.directory),{'format':'helene-agent-resolution.v1',
            'input_sha256':self.operation['input_sha256'],'source':self.operation['source'],'owner_update_requested':True})
        for row in self.rows:
            operation=direct.operation(row['directory'],self.operation)
            comp.prepare(row['directory'],operation)
        self.patches=[patch.object(pipeline,'_owner_blocked',return_value=False)]
        for context in self.patches:context.start();self.addCleanup(context.stop)

    def test_one_actual_app_directory_and_whole_core_identity(self):
        self.assertEqual(comp.app_root(self.peer),comp.app_root(self.f.directory))
        self.assertFalse((self.peer/'compound').exists())
        before=comp.signature(self.f.directory)
        comp.repair(self.peer,'app/localharness/feature.py','answer=42\n',
            expected=comp.signature(self.peer),reason='One authored app repair')
        self.assertEqual((comp.app_root(self.f.directory)/'localharness/feature.py').read_text(),'answer=42\n')
        self.assertTrue(comp.require_shared(self.f.program,self.rows)['consistent'])
        self.assertNotEqual(comp.signature(self.f.directory),before)
        before=comp.signature(self.f.directory)
        (self.peer/'result/module.py').write_text('secondary=42\n')
        self.assertNotEqual(comp.signature(self.f.directory),before)

    def test_one_hand_can_resolve_secondary_core_without_ordinary_live_edit(self):
        before=(self.peer/'local/module.py').read_bytes()
        result=hand.command(self.f.tree,'patch',path='core-1/module.py',before='authored=2',after='authored=99',
            expected_composition_sha256=comp.signature(self.f.directory),reason='Reconcile second core')
        self.assertEqual(result['core'],'core-1')
        self.assertIn('authored=99',(self.peer/'result/module.py').read_text())
        self.assertEqual((self.peer/'local/module.py').read_bytes(),before)
        direct.finish(self.f.directory,merge.executable(self.f.program))
        direct.require_ready(self.f.directory)

    def test_secondary_drift_and_reordered_members_refuse(self):
        value=read(self.peer/'plan.json');value['direct_update']['members'].reverse();write(self.peer/'plan.json',value)
        with self.assertRaisesRegex(ValueError,'identity|changed'):direct.group(self.f.directory)

    def test_review_navigation_covers_secondary_delta_and_absences(self):
        (self.peer/'result/new.py').unlink()
        materials=direct.materials(self.f.directory)
        self.assertIsNone(materials['resolved']['merge_files']['core-1/new.py'])
        self.assertIsNotNone(materials['release']['merge_files']['core-1/new.py'])
        from localharness.self_review import collect
        manifest,gaps=collect(materials,projection='full-update-v1')
        self.assertFalse(gaps)
        from localharness.self_review_merge import overview
        index=overview(manifest,{'merge_identity':{'test':True}})
        self.assertIn('core-0/new.py',index['changed_from_local'])
        self.assertIn('core-1/new.py',index['groups']['retained-local'])

    def test_all_profile_stop_controls_are_effective(self):
        self.patches[0].stop()
        with patch.object(pipeline,'_owner_blocked',side_effect=lambda tree:Path(tree)==self.tree):
            self.assertTrue(direct.blocked(self.f.directory))
            self.assertTrue(pipeline.job_blocked(self.operation))

    def test_no_application_or_core_can_apply_without_whole_review(self):
        direct.finish(self.f.directory,merge.executable(self.f.program))
        with self.assertRaises((ValueError,KeyError)):
            pipeline.validate_ready(self.peer)
        self.assertFalse((self.f.program.parent/'compound-overlay.json').exists())

    def test_equal_final_core_does_not_hide_different_historical_policy(self):
        from localharness.source_update_launcher import _equivalent
        rows=direct.core_rows(self.f.directory)
        self.assertEqual(_equivalent(rows[0],self.operation),_equivalent(rows[1],self.operation))
        plan=read(self.peer/'plan.json');plan['files']['base'].pop('module.py');write(self.peer/'plan.json',plan)
        # validate_inputs must reject this damaged historical declaration before reuse.
        with self.assertRaises(ValueError):_equivalent(rows[1],self.operation)


if __name__=='__main__':unittest.main()
