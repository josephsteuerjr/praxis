"""Supersession at the real legacy move/delete boundary; fake OS authority only."""
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import t_update_cleanup_3110 as cleanup_fixture
from localharness import desktop_update_request as jobs,session_host,source_update as source,agent_source_update as agent
from localharness.package_notice import read,write


class SupersessionWiring(unittest.TestCase):
    def setUp(self):
        self.fixture=cleanup_fixture.UpdateCleanup()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fx=self.fixture.fx
        self.observe=Mock(return_value='absent')
        self.enterContext(patch.object(session_host,'process_state',side_effect=self.observe))

    def _job(self,program,tree):
        value={'format':'helene-desktop-update-request.v2','id':'d'*32,'kind':'download',
            'program':str(program),'tree':str(tree),'state':'attention','phase':'download',
            'admission':{'pid':15101,'birth':151.0},'helper':{'pid':15102,'birth':152.0},
            'battery':{'passed':False,'reason':'exact retained fixture evidence'},
            'review_evidence':{'verdict':'findings'}}
        jobs._save(tree,value)
        return value

    def test_live_canonical_old_job_blocks_before_programme_retirement_or_data_move(self):
        old,data=self.fx.old(name='Programs/Helene blocked producer')
        self._job(old,self.fx.places.data)
        before=self.fixture._snapshot(old)
        self.observe.return_value='alive'
        with self.assertRaises(RuntimeError):self.fixture._sweep()
        self.assertTrue(old.is_dir(),'A blocked producer must retain its programme')
        self.assertEqual(self.fixture._snapshot(old),before)
        destructive=[action for action in self.fx.backend.actions if action[0] in ('retire','remove')]
        self.assertEqual(destructive,[])
        self.assertTrue(data.is_dir(),'Blocked source data must not already have moved')
        self.assertEqual(jobs.status(self.fx.places.data)['state'],'attention')

    def test_moving_legacy_job_is_superseded_before_its_original_pointer_disappears(self):
        old,data=self.fx.old(name='Programs/Helene moving producer')
        job=self._job(old,data)
        history=(data/'memory/groups/window.jsonl').read_bytes()
        self.fixture._sweep()
        result=read(self.fx.root/'.helene/legacy-supersession-result.json')
        self.assertEqual({row['id'] for row in result['superseded']},{job['id']})
        self.assertEqual(result['blocked'],[])
        self.assertFalse(any(row['id']==job['id'] for row in result['skipped']))
        self.assertEqual({call.args[0]['pid'] for call in self.observe.call_args_list},{15101,15102})
        self.assertFalse(old.exists())
        self.assertEqual((self.fx.places.data/'memory/groups/window.jsonl').read_bytes(),history)

    def test_moved_markers_fence_restored_original_pending_summaries_at_canonical_consumers(self):
        old,data=self.fx.old(name='Programs/Helene stale summary after move')
        desktop=self._job(old,data)
        identity='a'*64
        directory=data/'memory/.source-updates'/identity
        request={'id':identity,'program':str(old),'tree':str(data),'directory':str(directory),
            'input_sha256':'c'*64,'state':'pending','resolution_owner':'agent'}
        operation={**request,'state':'waiting_agent','attempts':[],
            'battery':{'passed':False},'review_clear':False}
        write(source.path(data),request)
        write(source._requests(data)/(identity+'.json'),request)
        write(directory/'pipeline.json',operation)
        write(directory/'coordinator-process.json',{'pid':15103,'birth':153.0})
        self.fixture._sweep()
        canonical=self.fx.places.data
        # Model a stale summary writer with exactly its pre-move identity.
        write(jobs.path(canonical),desktop)
        write(source.path(canonical),request)
        self.assertEqual(jobs.status(canonical)['state'],'superseded')
        with self.assertRaises(RuntimeError):jobs._save(canonical,desktop)
        self.assertIsNone(source.pending(canonical))
        turn=Mock()
        with patch.object(source,'locate',side_effect=AssertionError('Marker fence must precede old-directory lookup')):
            agent.notify(canonical,turn)
        turn.assert_not_called()
        with patch.object(source,'_advance') as advance,patch.object(source,'_stop') as stop:
            source.tick(canonical,Mock(),Mock())
        advance.assert_not_called();stop.assert_not_called()
        self.assertFalse(old.exists(),'No consumer may recreate the retired programme/data root')
        self.assertEqual(read(source.path(canonical)),request)
        self.assertEqual(read(jobs.path(canonical)),desktop)

    def test_mixed_programme_tree_job_is_captured_before_memory_is_separated(self):
        old,data=self.fx.old(name='Programs/Helene mixed tree producer')
        (data/'memory').rename(old/'memory');shutil.rmtree(data)
        config=read(old/'helene.json');config.update(tree='.',code='tree')
        write(old/'helene.json',config)
        original=self._job(old,old)
        self.fixture._sweep()
        result=read(self.fx.root/'.helene/legacy-supersession-result.json')
        self.assertEqual({row['id'] for row in result['superseded']},{original['id']})
        self.assertEqual({call.args[0]['pid'] for call in self.observe.call_args_list},{15101,15102})
        self.assertFalse(old.exists())
        write(jobs.path(self.fx.places.data),original)
        self.assertEqual(jobs.status(self.fx.places.data)['state'],'superseded')


if __name__=='__main__':unittest.main()
