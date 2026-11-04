"""Actual isolated test execution bound to exact frozen review composition."""
import json
from pathlib import Path
import re
import sys
import time
import types
import unittest
from unittest import mock
import forge
import forge_battery
import forge_review
import forge_review_worker
import forge_runtime
import forge_worker
import test_forge_wave2_server as fixtures


class BoundBattery(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ServerWave2();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.tid=self.fixture.tid
        (self.root/'test_candidate.py').write_text('import unittest,runpy\nclass Actual(unittest.TestCase):\n def test_answer(self):\n  self.assertEqual(runpy.run_path("code.py")["answer"],1)\n')
        value=forge_review.prepare(self.tid)
        self.rid=value['id']
        self.plan=forge_battery.prepare(self.tid,self.rid)

    def execute(self):
        text=forge.verify(self.plan['task_id'],'start',full=True,max_parallel=2,timeout=30)
        match=re.search(r'verify-[0-9a-f]{8}',text)
        self.assertIsNotNone(match,text)
        unit=forge._unit_dir(self.plan['task_id'],'verifications',match.group())
        end=time.monotonic()+15
        while not (unit/'result.json').exists() and time.monotonic()<end:time.sleep(.03)
        self.assertTrue((unit/'result.json').exists(),text)
        self.assertEqual(json.loads((unit/'result.json').read_text())['status'],'passed')
        return match.group(),unit

    def test_actual_battery_attaches_exact_logs_to_fresh_critic_subject(self):
        vid,unit=self.execute()
        bound=forge_battery.bind(self.tid,self.rid,vid,self.plan['candidate_sha256'])
        self.assertTrue(bound['qualified']);self.assertGreater(bound['report']['tests_observed'],0)
        self.assertEqual(forge_battery.bind(self.tid,self.rid,vid,self.plan['candidate_sha256'])['report'],bound['report'])
        value=forge_review.prepare(self.tid,previous_review_id=self.rid)
        directory,record=forge_review._record(self.tid,value['id'])
        subject=json.loads((directory/'subject.json').read_text())
        self.assertTrue(subject['verification']['qualified'])
        self.assertIn('evidence',subject['manifests'])
        self.assertFalse((directory/'author-disposition.json').exists())
        (Path(self.plan['root'])/'code.py').write_text('answer=9\n')
        self.assertFalse(forge_battery.status(self.tid,self.rid)['qualified'])

    def test_substituted_matrix_or_failed_result_is_not_qualification(self):
        vid,unit=self.execute()
        result=json.loads((unit/'result.json').read_text())
        (unit/'result.json').write_text(json.dumps({**result,'status':'failed'}))
        with self.assertRaises(ValueError):forge_battery.bind(self.tid,self.rid,vid,self.plan['candidate_sha256'])
        (unit/'result.json').write_text(json.dumps(result))
        request=json.loads((unit/'request.json').read_text());request['checks']=request['checks'][:1]
        (unit/'request.json').write_text(json.dumps(request))
        with self.assertRaises(ValueError):forge_battery.bind(self.tid,self.rid,vid,self.plan['candidate_sha256'])

    def test_same_command_at_another_cwd_cannot_claim_complete_composition(self):
        vid,unit=self.execute()
        request=json.loads((unit/'request.json').read_text())
        request['checks'][0]['cwd']='unrelated-tests'
        (unit/'request.json').write_text(json.dumps(request))
        with self.assertRaisesRegex(ValueError,'contract'):forge_battery.bind(self.tid,self.rid,vid,self.plan['candidate_sha256'])

    def test_bound_evidence_or_producer_corruption_revokes_qualification(self):
        vid,unit=self.execute()
        bound=forge_battery.bind(self.tid,self.rid,vid,self.plan['candidate_sha256'])
        directory,_=forge_review._record(self.tid,self.rid)
        path=directory/'verification/evidence'/bound['report']['logs'][0]['file'];raw=path.read_bytes()
        path.write_bytes(b'corrupted archived evidence')
        self.assertFalse(forge_battery.status(self.tid,self.rid)['qualified'])
        path.write_bytes(raw)
        self.assertTrue(forge_battery.status(self.tid,self.rid)['qualified'])
        (unit/'result.json').write_text('{}')
        self.assertFalse(forge_battery.status(self.tid,self.rid)['qualified'])

    def test_ALBM_finished_candidate_actual_battery_and_two_fresh_process_critics(self):
        roots={side:self.fixture.base/side for side in ('A','L','B')}
        test_source=(self.root/'test_candidate.py').read_text().replace('],1)','],4)')
        for side,root in roots.items():
            root.mkdir();(root/'code.py').write_text('answer='+{'A':'1','L':'2','B':'3'}[side]+'\n')
            (root/'test_candidate.py').write_text(test_source)
        merged=forge_review.prepare_merge(self.tid,roots['A'],roots['L'],roots['B'])
        directory,record=forge_review._record(self.tid,merged['id'])
        merge_tid='code-44556677'
        forge.source_update_task(merge_tid,str(directory),record['plan_id'],record['input_sha256'],str(Path(forge_review_worker.__file__).resolve()))
        unit=forge._unit_dir(merge_tid,'agents','agent-11223344')
        request={'id':unit.name,'task_id':merge_tid,'root':str(directory),'role':'worker','status':'running',
                 '_verified_request_path':str(unit/'request.json'),'source_update':forge.get(merge_tid)['source_update']}
        forge._atomic_json(unit/'request.json',request)
        adapter=forge_review_worker.MergeAdapter();adapter.prepare(None,request)
        for action,fields in [('resolve',{'path':'code.py','content':'answer=4\n','reason':'retain both intended changes'}),('finish',{})]:
            adapter.dispatch(request,'merge_source',{'action':action,**fields,'expected_input_sha256':record['input_sha256'],
                'expected_candidate_sha256':forge_review.candidate_signature(directory)})
        forge._atomic_json(unit/'result.json',{'status':'done'});forge.release_worker_root(unit/'request.json')
        self.rid=merged['id'];self.plan=forge_battery.prepare(self.tid,self.rid)
        vid,_=self.execute();forge_battery.bind(self.tid,self.rid,vid,self.plan['candidate_sha256'])
        subject=forge_review.prepare(self.tid,merge_id=merged['id'])
        spawn=forge._spawn_runner
        def critics(script,request,log):return spawn(Path(__file__).resolve(),request,log)
        with mock.patch.object(forge,'_spawn_runner',side_effect=critics):
            started=forge_review.start(self.tid,subject['id'],critics=2,max_iters=3)
        review_tid=started['review_task_id']
        try:
            end=time.monotonic()+20
            while time.monotonic()<end:
                if all((forge._unit_dir(review_tid,'agents',u['id'])/'result.json').exists() for u in started['units']):break
                time.sleep(.03)
            status=forge_review.status(self.tid,subject['id'])
            self.assertEqual(status['verdict'],'clear',status)
            self.assertTrue(status['tested_and_reviewed'])
            self.assertIsNone(status['author_disposition'])
            requests=[forge_runtime._read(forge._unit_dir(review_tid,'agents',u['id'])/'request.json') for u in started['units']]
            self.assertEqual(len({r['supervisor_pid'] for r in requests}),2)
            self.assertEqual(len({r['id'] for r in requests}),2)
        finally:
            for u in started['units']:
                req=forge_runtime._read(forge._unit_dir(review_tid,'agents',u['id'])/'request.json')
                proc=forge._RUNNERS.get(req.get('supervisor_pid'))
                if proc:proc.wait(timeout=10)


def process_critic(path):
    path=Path(path).resolve();request=json.loads(path.read_text())
    forge.TASKS_DIR=path.parents[3];forge.STATE_DIR=forge.TASKS_DIR.parent;forge.BASE=forge.TASKS_DIR.parents[2]
    directory=Path(request['root']);subject=json.loads((directory/'subject.json').read_text())
    coverage=[side+'/'+name for side,rows in subject['manifests'].items() for name,row in rows.items() if row['exists']]
    calls=[{'type':'tool_use','id':'read-'+str(i),'name':'review_source','input':{'action':'read','side':value.split('/',1)[0],
            'path':value.split('/',1)[1]}} for i,value in enumerate(coverage)]
    count=0
    def chat(*args,**kwargs):
        nonlocal count
        count+=1
        if count==1:return types.SimpleNamespace(model='deterministic-critic',stop_reason='tool_use',text='',usage={},blocks=calls)
        return types.SimpleNamespace(model='deterministic-critic',stop_reason='end_turn',usage={},blocks=[],text=json.dumps({
            'subject_sha256':request['self_review']['subject_sha256'],'verdict':'clear','summary':'actual frozen-source and battery-log reads',
            'findings':[],'coverage':coverage}))
    with mock.patch.object(forge_worker.llm,'chat',side_effect=chat):return forge_review_worker.run(path)


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--request':raise SystemExit(process_critic(sys.argv[2]))
    unittest.main()
