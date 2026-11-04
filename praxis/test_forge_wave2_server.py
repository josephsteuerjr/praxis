"""Praxis consumers for the port's new server boundaries, not UI-shaped mocks."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import forge
import forge_control
import forge_reader
import forge_review
import forge_review_worker
import forge_roots
import forge_runtime
import forge_usage
import forge_worker


class ServerWave2(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'project';self.root.mkdir()
        (self.root/'code.py').write_text('answer = 1\n')
        for name,value in [('BASE',self.base),('STATE_DIR',self.base/'memory/.forge'),('TASKS_DIR',self.base/'memory/.forge/tasks')]:
            p=mock.patch.object(forge,name,value);p.start();self.addCleanup(p.stop)
        self.tid='code-00112233'
        self.task={'id':self.tid,'root':str(self.root),'status':'active','scope':'self','goal':'exact candidate',
                   'max_active_workers':8,'success_criteria':['retain source intent']}
        forge._save_task(self.task)

    def unit(self,uid='agent-aabbccdd',role='worker',status='running',**more):
        directory=forge._unit_dir(self.tid,'agents',uid)
        request={'id':uid,'task_id':self.tid,'root':str(self.root),'role':role,'brief':'test',
                 'status':status,'parent_unit_id':'',**more}
        forge._atomic_json(directory/'request.json',request)
        return directory,request

    def test_authority_fields_and_hidden_tools_do_not_dispatch(self):
        _,request=self.unit(role='reviewer')
        with mock.patch.object(forge,'run') as run,mock.patch.object(forge,'agent') as spawn:
            for name,args in [('run',{'command':'touch forbidden'}),('delegate',{'role':'worker','brief':'x','_parent_request_path':'/forged'})]:
                out=forge_worker._dispatch(request,name,args)
                self.assertTrue('refused' in out or 'runtime-owned' in out)
        run.assert_not_called();spawn.assert_not_called()

    def test_overlapping_task_and_alias_hold_writer_but_sibling_root_does_not(self):
        directory,_=self.unit()
        other={**self.task,'id':'code-33445566'};forge._save_task(other)
        self.assertIn(self.tid,forge_roots.foreign_writer_reason(forge,other['id']))
        alias=self.base/'alias';alias.symlink_to(self.root,target_is_directory=True)
        other['root']=str(alias);forge._save_task(other)
        self.assertIn(self.tid,forge_roots.foreign_writer_reason(forge,other['id']))
        child=self.root/'nested';child.mkdir();other['root']=str(child);forge._save_task(other)
        self.assertIn(self.tid,forge_roots.foreign_writer_reason(forge,other['id']))
        separate=self.base/'separate';separate.mkdir();other['root']=str(separate);forge._save_task(other)
        self.assertEqual(forge_roots.foreign_writer_reason(forge,other['id']),'')
        forge._atomic_json(directory/'result.json',{'status':'done'})
        other['root']=str(self.root);forge._save_task(other)
        self.assertIn(self.tid,forge_roots.foreign_writer_reason(forge,other['id']))
        forge.release_worker_root(directory/'request.json')
        self.assertEqual(forge_roots.foreign_writer_reason(forge,other['id']),'')

    def test_workshop_explicit_physical_root_with_retained_tasks(self):
        import workshop
        self.unit(status='running')
        with mock.patch.object(workshop,'BASE',self.base),mock.patch.object(workshop,'REPO',self.base):
            target=self.base/'workspace/probe.txt'
            workshop.fs_write('workspace/probe.txt','first')
            self.assertEqual(target.read_text(),'first')
            task=forge.get(self.tid);task['root']=str(target.parent);forge._save_task(task)
            refused=workshop.fs_write('workspace/probe.txt','forbidden',overwrite=True)
            self.assertIn('Write not admitted',refused)
            self.assertEqual(target.read_text(),'first')

    def test_actual_process_hierarchical_transaction_and_independent_root(self):
        ready=self.base/'ready'
        child_root=self.root/'child';child_root.mkdir()
        source=('import forge_roots,time;from pathlib import Path;'
                f'guard=forge_roots.transaction({str(child_root)!r},Path({str(forge.STATE_DIR)!r}));'
                f'guard.__enter__();Path({str(ready)!r}).write_text("held");time.sleep(10)')
        process=subprocess.Popen([sys.executable,'-B','-c',source],cwd=Path(forge.__file__).parent)
        try:
            deadline=time.monotonic()+5
            while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(ready.exists())
            with self.assertRaises(TimeoutError):
                with forge_roots.transaction(self.root,forge.STATE_DIR,timeout=.15):pass
            with forge_roots.transaction(self.base/'independent',forge.STATE_DIR,timeout=.2):pass
        finally:
            process.terminate();process.wait(timeout=5)
        with forge_roots.transaction(self.root,forge.STATE_DIR,timeout=.2):pass

    def test_usage_deduplicates_and_preserves_unknown_cost(self):
        directory=forge._task_dir(self.tid)
        fields={'unit_id':'agent-aa','generation':'one','call_id':'1:model'}
        forge_usage.record(directory,**fields,kind='model_start')
        forge_usage.record(directory,**fields,kind='model_start')
        self.assertEqual(forge_usage.summary(directory)['unknown_calls'],1)
        forge_usage.record(directory,**fields,kind='model_result',usage={'in':11,'out':7})
        forge_usage.record(directory,**fields,kind='model_result',usage={'in':11,'out':7})
        value=forge_usage.summary(directory)
        self.assertEqual((value['logical_calls'],value['observed_input_tokens'],value['unknown_calls']),(1,11,0))
        self.assertIsNone(value['cost'])
        with self.assertRaises(ValueError):forge_usage.record(directory,**fields,kind='model_result',usage={'in':12})

    def test_real_worker_loop_preserves_entire_provider_message_prefix(self):
        directory,request=self.unit(role='scout',status='starting',max_iters=3)
        seen=[]
        def chat(_role,**kwargs):
            seen.append(copy.deepcopy(kwargs))
            if len(seen)<3:
                return types.SimpleNamespace(model='fixture',stop_reason='tool_use',text='',usage={'in':10,'out':2},
                    blocks=[{'type':'tool_use','id':f'read-{len(seen)}','name':'inspect','input':{'action':'read','path':'code.py'}}])
            return types.SimpleNamespace(model='fixture',stop_reason='end_turn',text='observed',usage={'in':12,'out':3},blocks=[])
        with mock.patch.object(forge_worker.llm,'chat',side_effect=chat),mock.patch.object(forge_worker,'_system',side_effect=['first exact frame','must not replace frame']) as orientation:
            self.assertEqual(forge_worker._run_request(directory/'request.json',request),0)
        self.assertEqual(orientation.call_count,1)
        for first,second in zip(seen,seen[1:]):
            self.assertEqual(first['system'],second['system'])
            self.assertEqual(first['tools'],second['tools'])
            self.assertEqual(first['messages'],second['messages'][:len(first['messages'])])
        checkpoint=forge_runtime._read(directory/'checkpoint.json')
        self.assertEqual(checkpoint['iteration'],3)
        self.assertTrue((directory/'writer.lock').is_file())
        self.assertEqual(forge_usage.summary(forge._task_dir(self.tid))['logical_calls'],3)

    def test_migration_preserves_exact_v1_bytes_and_never_accepts_done(self):
        import forge_swarm
        directory=forge._task_dir(self.tid)
        raw=b'{"version":1,"max_parallel":3,"nodes":[{"id":"a","role":"worker","deps":[],"agent_id":"agent-a","status":"running"}]}\n'
        (directory/'swarm.json').write_bytes(raw)
        plan=forge_swarm.refresh(directory,lambda _: {'status':'done','result_sha256':'a'*64})
        self.assertEqual(plan['nodes'][0]['status'],'awaiting_acceptance')
        backups=list((directory/'migrations').glob('*.preimage'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),raw)
        before=list((directory/'migrations').iterdir())
        forge_swarm.refresh(directory,lambda _: {'status':'done','result_sha256':'a'*64})
        self.assertEqual(before,list((directory/'migrations').iterdir()))
        (directory/'swarm.json').write_text('{broken')
        with self.assertRaises(ValueError):forge_swarm.load(directory)

    def test_frozen_review_actual_adapter_full_reads_and_stale_fixes(self):
        initial=forge_review.prepare(self.tid,criteria='check answer',paths=['code.py'])
        review_id=initial['id'];directory,record=forge_review._record(self.tid,review_id)
        tid='code-77889900'
        script=str(Path(forge_review_worker.__file__).resolve())
        forge.self_review_task(tid,str(directory),record['subject_sha256'],script)
        unit=forge._unit_dir(tid,'agents','agent-11223344')
        request={'id':unit.name,'task_id':tid,'root':str(directory),'role':'reviewer','brief':'review',
                 'status':'starting','self_review':forge.get(tid)['self_review'],'max_iters':2}
        forge._atomic_json(unit/'request.json',request)
        record.update(review_task_id=tid,units=[{'id':unit.name}]);forge_runtime.atomic_json(directory/'record.json',record)
        def chat(_role,**kwargs):
            if len(kwargs['messages'])==1:
                return types.SimpleNamespace(model='fixture',stop_reason='tool_use',text='',usage={},blocks=[
                    {'type':'tool_use','id':'read-source','name':'review_source','input':{'action':'read','side':'candidate','path':'code.py'}}])
            return types.SimpleNamespace(model='fixture',stop_reason='end_turn',usage={},blocks=[],text=json.dumps({
                'subject_sha256':record['subject_sha256'],'verdict':'clear','summary':'exact read','findings':[],'coverage':['candidate/code.py']}))
        with mock.patch.object(forge_worker.llm,'chat',side_effect=chat):self.assertEqual(forge_review_worker.run(unit/'request.json'),0)
        self.assertEqual(forge_review.status(self.tid,review_id)['verdict'],'clear')
        original=(directory/'candidate/code.py').read_bytes()
        (self.root/'code.py').write_text('answer = 2\n')
        self.assertEqual(forge_review.status(self.tid,review_id)['current_eligibility'],'stale')
        next_review=forge_review.prepare(self.tid,previous_review_id=review_id,paths=['code.py'])
        self.assertNotEqual(next_review['subject_sha256'],record['subject_sha256'])
        self.assertEqual((directory/'candidate/code.py').read_bytes(),original)
        with self.assertRaises(ValueError):forge_review.decide(self.tid,review_id,'f'*64,'accept','wrong subject')
        decision=forge_review.decide(self.tid,review_id,record['subject_sha256'],'override','retain the exact old candidate')
        self.assertEqual(decision['decision'],'override')
        self.assertEqual(forge_review.status(self.tid,review_id)['verdict'],'clear')

    def test_clear_without_actual_read_is_insufficient_and_no_role_escalation(self):
        initial=forge_review.prepare(self.tid,paths=['code.py']);directory,record=forge_review._record(self.tid,initial['id'])
        tid='code-77889900';unit=forge._unit_dir(tid,'agents','agent-11223344')
        forge._atomic_json(unit/'result.json',{'status':'done','result':json.dumps({'subject_sha256':record['subject_sha256'],'verdict':'clear','coverage':['candidate/code.py']})})
        record.update(review_task_id=tid,units=[{'id':unit.name}]);forge_runtime.atomic_json(directory/'record.json',record)
        value=forge_review.status(self.tid,record['id'])
        self.assertEqual(value['verdict'],'insufficient')
        self.assertIn('coverage',value['critics'][0]['error'])

    def test_isolated_ALBM_actual_merger_adapter_CAS_absence_binary_and_rework(self):
        roots={side:self.base/side for side in ('A','L','B')}
        for side,root in roots.items():
            root.mkdir();(root/'code.py').write_text({'A':'a=1\n','L':'a=2\n','B':'a=3\n'}[side])
        (roots['B']/'new.bin').write_bytes(b'\x00\xff\x88')
        result=forge_review.prepare_merge(self.tid,roots['A'],roots['L'],roots['B'])
        directory,record=forge_review._record(self.tid,result['id'])
        tid='code-44556677';script=str(Path(forge_review_worker.__file__).resolve())
        forge.source_update_task(tid,str(directory),record['plan_id'],record['input_sha256'],script)
        unit=forge._unit_dir(tid,'agents','agent-11223344')
        request={'id':unit.name,'task_id':tid,'root':str(directory),'role':'worker','status':'running',
                 '_verified_request_path':str(unit/'request.json'),'source_update':forge.get(tid)['source_update']}
        forge._atomic_json(unit/'request.json',request)
        adapter=forge_review_worker.MergeAdapter();adapter.prepare(None,request)
        args={'action':'resolve','path':'code.py','content':'a=4\n','reason':'preserve both contracts','expected_input_sha256':record['input_sha256'],
              'expected_candidate_sha256':'f'*64}
        before=(directory/'M/code.py').read_bytes()
        with self.assertRaises(ValueError):adapter.dispatch(request,'merge_source',args)
        self.assertEqual((directory/'M/code.py').read_bytes(),before)
        args['expected_candidate_sha256']=forge_review.candidate_signature(directory)
        adapter.dispatch(request,'merge_source',args)
        adapter.dispatch(request,'merge_source',{'action':'finish','expected_input_sha256':record['input_sha256'],
            'expected_candidate_sha256':forge_review.candidate_signature(directory)})
        self.assertEqual((directory/'M/new.bin').read_bytes(),b'\x00\xff\x88')
        self.assertEqual((roots['L']/'code.py').read_text(),'a=2\n')
        renewed=forge_review.rework_merge(self.tid,record['id'],'review found one remaining issue')
        next_dir,next_record=forge_review._record(self.tid,renewed['id'])
        self.assertEqual(next_record['input_sha256'],record['input_sha256'])
        self.assertEqual((next_dir/'M/code.py').read_text(),'a=4\n')
        with self.assertRaises(ValueError):adapter.dispatch(request,'run',{'command':'touch live'})
        (roots['A']/'code.py').write_text('new original input\n')
        self.assertEqual(forge_review.status(self.tid,record['id'])['stale_inputs'],['A'])
        self.assertEqual(forge_review.status(self.tid,renewed['id'])['stale_inputs'],['A'])
        with self.assertRaises(ValueError):forge_review.validate_merge(directory,record['input_sha256'],current=True)

    def test_owner_control_queue_receipts_generation_and_claim_crash(self):
        directory,request=self.unit(role='scout')
        forge._atomic_json(directory/'progress.json',{'generation':'one'})
        key='a'*32
        queued=forge_control.enqueue(self.base,self.tid,'pause',request['id'],'one',key)
        self.assertEqual(queued['state'],'queued')
        self.assertEqual(forge_control.enqueue(self.base,self.tid,'pause',request['id'],'one',key)['state'],'queued')
        with self.assertRaises(ValueError):forge_control.enqueue(self.base,self.tid,'stop',request['id'],'one',key)
        forge._atomic_json(directory/'progress.json',{'generation':'two'})
        called=mock.Mock()
        receipts=forge_control.consume_pending(self.base,called)
        called.assert_not_called();self.assertEqual(receipts[0]['state'],'rejected')
        unknown='b'*32
        forge_control.enqueue(self.base,self.tid,'stop',command_id=unknown)
        root=self.base/'memory/.control/forge'
        (root/'processing').mkdir(exist_ok=True)
        (root/'commands'/f'{unknown}.json').rename(root/'processing'/f'{unknown}.json')
        self.assertEqual(forge_control.consume_pending(self.base,called)[0]['state'],'unknown')
        called.assert_not_called()

    def test_owner_projection_has_real_tree_payload_and_no_model_calls(self):
        directory,request=self.unit(role='scout',status='queued',queued_reason='active slots exhausted')
        result={'status':'done','result':'<script>untrusted</script>'}
        forge._atomic_json(directory/'result.json',result)
        with mock.patch.object(forge_worker.llm,'chat') as chat:
            view=forge_reader.task_detail(self.base,self.tid)
            ref=view['agents'][0]['result_ref']
            page=forge_reader.payload(self.base,self.tid,request['id'],ref['sha256'],'result')
        chat.assert_not_called();self.assertIn('<script>',page['text'])
        self.assertEqual(view['agents'][0]['queued_reason'],'active slots exhausted')
        with self.assertRaises(ValueError):forge_reader.payload(self.base,self.tid,request['id'],'f'*64,'result')
        with self.assertRaises(ValueError):forge_reader.task_dir(self.base,'../other')

    def test_panic_and_task_pause_fence_all_new_work(self):
        (self.base/'memory/.panic').write_text('owner stop')
        self.assertIn('panic',forge._admission_reason(self.tid))
        (self.base/'memory/.panic').unlink()
        forge.control(self.tid,'pause')
        self.assertIn('pause',forge._admission_reason(self.tid))
        self.assertEqual(forge.control(self.tid,'resume')['status'],'resumed')
        forge.control(self.tid,'stop')
        self.assertEqual(forge.control(self.tid,'resume')['status'],'refused')

    def test_status_is_a_read_only_projection_and_legacy_cap_is_preserved(self):
        import forge_swarm
        directory=forge._task_dir(self.tid)
        forge_swarm.create(directory,[{'id':'a','role':'scout','brief':'read'}])
        before={p.relative_to(directory):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        with mock.patch.object(forge,'_atomic_json',side_effect=AssertionError('status write')):
            self.assertIn('swarm',forge.inspect(self.tid,'status'))
        self.assertEqual(before,{p.relative_to(directory):p.read_bytes() for p in directory.rglob('*') if p.is_file()})
        self.task.pop('max_active_workers');forge._save_task(self.task)
        old=(directory/'task.json').read_bytes()
        with forge._mutation_lock(self.tid):self.assertEqual(forge._admission_reason(self.tid),'')
        self.assertIsNone(forge.get(self.tid)['max_active_workers'])
        self.assertIn(old,[p.read_bytes() for p in (directory/'migrations').glob('*.preimage')])
        for i in range(10):self.unit(uid=f'agent-{i:08x}',role='scout')
        self.assertEqual(forge._agent_capacity(self.tid,{'id':'agent-ffffffff','role':'scout'}),'')

    def test_route_change_refuses_before_any_provider_reuse(self):
        directory,request=self.unit(role='scout',status='starting',max_iters=2)
        with mock.patch.object(forge_worker,'_route_identity',side_effect=['bound','changed']), \
             mock.patch.object(forge_worker.llm,'chat') as chat:
            self.assertEqual(forge_worker._run_request(directory/'request.json',request),2)
        chat.assert_not_called()
        self.assertEqual(forge_runtime._read(directory/'checkpoint.json')['next_boundary'],'model_ready')

    def test_complete_long_output_survives_journal_and_model_delivery(self):
        directory,request=self.unit(role='scout',status='starting',max_iters=2)
        long_output='exact \\"🍏'+('long output\n'*5000)
        seen=[]
        def chat(_role,**kwargs):
            seen.append(copy.deepcopy(kwargs['messages']))
            if len(seen)==1:return types.SimpleNamespace(model='fixture',stop_reason='tool_use',text='',usage={},blocks=[
                {'type':'tool_use','id':'long','name':'inspect','input':{'action':'read','path':'code.py'}}])
            return types.SimpleNamespace(model='fixture',stop_reason='end_turn',text='retained',usage={},blocks=[])
        with mock.patch.object(forge_worker.llm,'chat',side_effect=chat), \
             mock.patch.object(forge_worker,'_system',return_value='exact frame'), \
             mock.patch.object(forge,'inspect',return_value=long_output):
            self.assertEqual(forge_worker._run_request(directory/'request.json',request),0)
        self.assertEqual(seen[1][-1]['content'][0]['content'],long_output)
        trace=forge_runtime._read(directory/'checkpoint.json')['trace'][0]
        self.assertEqual(forge_runtime.read_payload_full(directory,trace['output_ref']),long_output)
        record=json.loads(forge.agent(self.tid,'payload',agent_id=request['id'],
            payload_sha256=forge._result_identity(directory/'result.json'),kind='result',limit=128))
        self.assertEqual(record['offset'],0);self.assertIsNotNone(record['next_offset'])

    def test_real_review_event_is_durable_deduplicated_and_explicitly_not_acceptance(self):
        from core import events,subagents
        value=forge_review.prepare(self.tid,paths=['code.py'])
        directory,record=forge_review._record(self.tid,value['id'])
        critic=forge._unit_dir('code-55667788','agents','agent-11223344')
        forge._atomic_json(critic/'result.json',{'status':'done','result':json.dumps({
            'subject_sha256':record['subject_sha256'],'verdict':'findings','summary':'one defect',
            'findings':[{'path':'candidate/code.py','reason':'fixture finding'}],'coverage':[]})})
        record.update(review_task_id='code-55667788',units=[{'id':critic.name}])
        forge_runtime.atomic_json(directory/'record.json',record)
        forge_review._OBSERVED.clear();captured=[]
        def emit(kind,source,**kw):captured.append((kind,kw['payload']));return {'dedup_key':kw['dedup_key']}
        with mock.patch.object(events,'emit',side_effect=emit):
            self.assertEqual(forge_review.tick(),1)
            self.assertEqual(forge_review.tick(),0)
            forge_review._OBSERVED.clear()
            self.assertEqual(forge_review.tick(),0)
        self.assertEqual(captured[0][0],'forge_review_result')
        invitation=subagents.invitation([captured[0][1]])
        self.assertIn(value['id'],invitation);self.assertIn('coding_review',invitation)
        self.assertFalse((directory/'author-disposition.json').exists())

    def test_group_stop_cascades_to_owned_independent_review_tasks(self):
        value=forge_review.prepare(self.tid,paths=['code.py']);directory,record=forge_review._record(self.tid,value['id'])
        child='code-55667788'
        task=forge.self_review_task(child,str(directory),record['subject_sha256'],str(Path(forge_review_worker.__file__).resolve()))
        task.update(coordinator_task_id=self.tid);forge._save_task(task)
        unit=forge._unit_dir(child,'agents','agent-11223344')
        forge._atomic_json(unit/'request.json',{'id':unit.name,'task_id':child,'role':'reviewer','status':'queued','root':str(directory)})
        result=forge.control(self.tid,'stop')
        self.assertEqual(result['linked_tasks'][0]['task_id'],child)
        self.assertEqual(forge.get(child)['status'],'stopped')
        self.assertEqual(forge_runtime._read(unit/'result.json')['status'],'stopped')

    def test_existing_app_auth_does_not_turn_snapshot_scope_into_forge_access(self):
        import praxis_app
        service=praxis_app.PraxisAppService(self.base,owner_id='1')
        with self.assertRaises(PermissionError):
            service.command(praxis_app.Viewer('2','trusted',('praxis.snapshot',)),{'domain':'forge','action':'list'})
        owner=praxis_app.Viewer('1','owner',())
        view=service.command(owner,{'domain':'forge','action':'detail','task_id':self.tid})
        self.assertEqual(view['forge']['id'],self.tid)


if __name__=='__main__':unittest.main()
