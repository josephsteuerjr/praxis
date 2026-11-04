"""Actual host backend matrix consumers; no installed daemon or live socket."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
import forge
import forge_verify
import forge_reader
import serverd_client
import test_forge_wave2_server as fixtures

def backend(state):
    here=Path(__file__).parent/'serverd'
    if str(here) not in sys.path:sys.path.insert(0,str(here))
    import broker,brokerops
    brokerops.configure(Path(state))
    return broker,brokerops

@contextlib.contextmanager
def transport(state,hook=None):
    broker,ops=backend(state)
    def exchange(request,**kw):
        if hook:return hook(request,lambda:broker.dispatch(request['verb'],request['args'],request_id=request['request_id']))
        return broker.dispatch(request['verb'],request['args'],request_id=request['request_id'])
    with mock.patch.object(serverd_client,'available',return_value=True),mock.patch.object(serverd_client,'_token',return_value='fixture'),mock.patch.object(serverd_client,'_exchange',side_effect=exchange):yield ops

class VerificationCustody(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ServerWave2();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.base=self.fixture.base;self.root=self.fixture.root;self.tid=self.fixture.tid
        self.backend_state=self.base/'backend-state';self.vid='verify-11223344'
        self.directory=forge._unit_dir(self.tid,'verifications',self.vid)

    def request(self,command):
        _,ops=backend(self.backend_state)
        task=forge.get(self.tid);task.update(scope='host',root_binding=ops.root_binding(self.root));forge._save_task(task)
        request={'id':self.vid,'task_id':self.tid,'scope':'host','root':str(self.root),'root_binding':task['root_binding'],
                 'checks':[{'id':'check','command':command,'cwd':'.'}],'timeout':10,'max_parallel':1,
                 'verification_custody':'check-tree-v1','status':'running','supervisor_pid':os.getpid(),
                 'supervisor_started_at':forge._proc_started_at(os.getpid()),'test_backend_state':str(self.backend_state)}
        forge._atomic_json(self.directory/'request.json',request);return request

    def test_legacy_remote_matrix_cannot_release_or_disappear_from_owner_projection(self):
        for scope in ('host',None,'self'):
            for name in ('stop.json','stop-request.json'):(self.directory/name).unlink(missing_ok=True)
            request=self.request('never executed')
            if scope is None:request.pop('scope')
            else:request['scope']=scope
            forge._atomic_json(self.directory/'request.json',request)
            forge._atomic_json(self.directory/'result.json',{'status':'passed','checks':[{'status':'passed','exit':0}]})
            raw=(self.directory/'result.json').read_bytes()
            self.assertEqual(forge._unit_state(self.directory)['status'],'unknown',scope)
            self.assertEqual(forge_reader.task_detail(self.base,self.tid)['verifications'][0]['status'],'unknown')
            self.assertIn(self.vid,forge._shared_writer_reason(self.tid,''))
            self.assertIn('unproven',forge.verify(self.tid,'stop',verification_id=self.vid))
            self.assertEqual((self.directory/'result.json').read_bytes(),raw)

    def test_default_self_owner_does_not_inherit_a_remote_request_namespace(self):
        self.request('never executed')
        task=forge.get(self.tid);task.pop('scope');forge._save_task(task)
        request=forge._read_json(self.directory/'request.json');request['scope']='windows'
        forge._atomic_json(self.directory/'request.json',request)
        forge._atomic_json(self.directory/'result.json',{'status':'passed','checks':[{'status':'passed','exit':0}]})
        self.assertEqual(forge._unit_state(self.directory)['status'],'unknown')
        self.assertEqual(forge_reader.task_detail(self.base,self.tid)['verifications'][0]['status'],'unknown')
        raw=(self.directory/'result.json').read_bytes()
        with mock.patch.object(serverd_client,'call') as remote:
            self.assertIn('unproven',forge.verify(self.tid,'stop',verification_id=self.vid))
        remote.assert_not_called()
        self.assertTrue((self.directory/'stop-request.json').exists())
        self.assertEqual(forge._read_json(self.directory/'stop.json')['state'],'unresolved')
        self.assertEqual((self.directory/'result.json').read_bytes(),raw)

    def test_malformed_check_custody_projects_unknown_without_effects(self):
        self.request('never executed')
        for checks in (['broken'],{'wrong':'mapping'},[{'custody':['broken']} ]):
            forge._atomic_json(self.directory/'result.json',{'status':'passed','checks':checks})
            self.assertEqual(forge._unit_state(self.directory)['status'],'unknown')
            self.assertEqual(forge_reader.task_detail(self.base,self.tid)['verifications'][0]['status'],'unknown')

    def test_nested_tree_proof_damage_is_unknown_in_core_reader_and_direct_stop(self):
        import forge_remote_run as remote
        self.request('never executed')
        self.assertEqual(remote.custody_status({'scope':'host','remote':True},
            {'status':'failed','prelaunch_refused':'not a boolean'},scope='host'),'unknown')
        broken=(["broken"],{'contract':'command-tree-v1','settled':True,'receipt':['broken']},
                {'contract':'command-tree-v1','settled':True,'receipt':{'settled':True,'nonce':['wrong'],'exit_code':0}},
                {'contract':'command-tree-v1','settled':True,'receipt':{'settled':True,'nonce':'owned','exit_code':True}})
        for proof in broken:
            with self.subTest(proof=proof):
                for name in ('stop.json','stop-request.json'):(self.directory/name).unlink(missing_ok=True)
                row={'status':'passed','checks':[{'custody':{'remote':True,'spawned':True,'group_exit_verified':True,
                    'backend_operation_id':'op-owned','tree_exit':proof}}]}
                forge._atomic_json(self.directory/'result.json',row);raw=(self.directory/'result.json').read_bytes()
                self.assertFalse(remote.tree_settled({'tree_exit':proof},'op-owned'))
                self.assertEqual(forge._unit_state(self.directory)['status'],'unknown')
                self.assertEqual(forge_reader.task_detail(self.base,self.tid)['verifications'][0]['status'],'unknown')
                with mock.patch.object(serverd_client,'call') as rpc:
                    self.assertIn('unproven',forge.verify(self.tid,'stop',verification_id=self.vid))
                rpc.assert_not_called();self.assertEqual((self.directory/'result.json').read_bytes(),raw)

    def test_partial_result_stop_commit_heals_receipt_without_replacing_terminal_bytes(self):
        import forge_verify_remote as verify_remote
        self.request('never admitted')
        forge._atomic_json(self.directory/'stop.json',{'state':'unresolved','note':'old timeout'})
        atomic=forge._atomic_json;injected=[]
        def fail_receipt(path,value):
            if Path(path)==self.directory/'stop.json' and value.get('state')=='settled' and not injected:
                injected.append(True);raise OSError('controller lost after result commit')
            return atomic(path,value)
        with mock.patch.object(forge,'_writer_ended',return_value=True):
            with mock.patch.object(forge,'_atomic_json',side_effect=fail_receipt):
                with self.assertRaises(OSError):verify_remote.reconcile_dead_supervisor(forge,self.directory)
            raw=(self.directory/'result.json').read_bytes()
            self.assertEqual(forge._unit_state(self.directory)['status'],'unknown')
            self.assertIn('all check trees settled',forge.verify(self.tid,'stop',verification_id=self.vid))
            self.assertEqual(forge._unit_state(self.directory)['status'],'stopped')
            stop=(self.directory/'stop.json').read_bytes()
            self.assertEqual(forge._read_json(self.directory/'stop.json')['result_sha256'],hashlib.sha256(raw).hexdigest())
            forge.verify(self.tid,'stop',verification_id=self.vid)
            self.assertEqual((self.directory/'result.json').read_bytes(),raw)
            self.assertEqual((self.directory/'stop.json').read_bytes(),stop)

    def test_producer_receipt_write_failure_retains_committed_result(self):
        self.request('fixture')
        (self.directory/'stop-request.json').write_text('{}')
        write=forge_verify._write;committed=[];injected=[]
        def observed(path,value):
            if Path(path)==self.directory/'stop.json' and value.get('state')=='settled' and not injected:
                injected.append(True);raise OSError('receipt storage interrupted')
            write(path,value)
            if Path(path)==self.directory/'result.json':committed.append(Path(path).read_bytes())
        row={'id':'check','status':'stopped','custody':{'spawned':False,'group_exit_verified':True}}
        with mock.patch.object(forge_verify,'_write',side_effect=observed),mock.patch.object(forge_verify,'_run_one',return_value=row):
            self.assertEqual(forge_verify.run(self.directory/'request.json'),2)
        self.assertEqual(len(committed),1);self.assertEqual((self.directory/'result.json').read_bytes(),committed[0])
        self.assertEqual(forge._read_json(self.directory/'stop.json')['state'],'unresolved')
        with mock.patch.object(forge,'_writer_ended',return_value=True):
            self.assertIn('all check trees settled',forge.verify(self.tid,'stop',verification_id=self.vid))
        self.assertEqual((self.directory/'result.json').read_bytes(),committed[0])

    def test_proofless_terminal_and_lost_poll_are_unknown_without_second_admission(self):
        self.request('no actual backend command')
        calls=[]
        def exchange(request,**kw):
            calls.append(request['verb'])
            if request['verb']=='op.start':
                paths=list(self.directory.glob('remote-checks/*/request.json'));self.assertEqual(len(paths),1)
                saved=json.loads(paths[0].read_text());self.assertEqual(saved['backend_request_id'],request['request_id'])
                return {'ok':True,'id':saved['backend_operation_id']}
            return {'ok':True,'id':request['args']['operation_id'],'status':'done','exit':0}
        with mock.patch.object(serverd_client,'available',return_value=True),mock.patch.object(serverd_client,'_token',return_value='fixture'),mock.patch.object(serverd_client,'_exchange',side_effect=exchange):
            self.assertEqual(forge_verify.run(self.directory/'request.json'),2)
            before=list(calls)
            self.assertEqual(forge_verify.run(self.directory/'request.json'),2)
            self.assertEqual(calls,before)
        self.assertEqual(calls.count('op.start'),1);self.assertFalse((self.directory/'result.json').exists())
        self.assertEqual(forge._unit_state(self.directory)['status'],'unknown')

    def test_malformed_poll_status_returns_unknown_after_one_readback_without_admission(self):
        import forge_verify_remote as verify_remote
        request=self.request('never replay');operation='op-'+'a'*16
        safe=hashlib.sha256(b'check').hexdigest()[:24];job=verify_remote._job(self.directory,0,safe)
        remote_request={'id':'check-'+safe,'task_id':self.tid,'scope':'host','remote':True,
            'status':'running','remote_id':operation,'backend_operation_id':operation}
        forge._atomic_json(job/'request.json',remote_request)
        (self.directory/'stop-request.json').write_text('{}')
        for raw in (['done'],{'value':'done'},True,17,'future-status',None):
            for nested in (False,True):
                with self.subTest(raw=raw,nested=nested):
                    calls=[]
                    reply={'ok':True,'id':operation,'status':'running' if nested else raw}
                    if nested:reply['terminal']={'status':raw}
                    def exchange(row,**kw):
                        calls.append(row['verb'])
                        return reply if row['verb']=='op.poll' else {'ok':True,'id':operation}
                    with mock.patch.object(serverd_client,'available',return_value=True),mock.patch.object(serverd_client,'_token',return_value='fixture'),mock.patch.object(serverd_client,'_exchange',side_effect=exchange):
                        value=verify_remote.run_check(0,request['checks'][0],self.root,self.directory,10,request,stopped=lambda:True)
                    self.assertEqual(value['status'],'unknown');self.assertFalse(value['custody']['group_exit_verified'])
                    self.assertEqual(calls,['op.stop','op.poll'])
                    self.assertEqual(forge._read_json(job/'request.json')['remote_id'],operation)
                    self.assertFalse((job/'result.json').exists())

    def test_every_host_live_status_is_preserved_until_proven_done(self):
        import forge_remote_run as remote
        import forge_verify_remote as verify_remote
        request=self.request('never replay');_,ops=backend(self.backend_state)
        self.assertTrue(ops.LIVE_STATUSES<=remote.ACTIVE)
        operation='op-'+'b'*16;safe=hashlib.sha256(b'check').hexdigest()[:24]
        job=verify_remote._job(self.directory,0,safe)
        req={'id':'check-'+safe,'task_id':self.tid,'scope':'host','remote':True,
            'status':'running','remote_id':operation,'backend_operation_id':operation}
        forge._atomic_json(job/'request.json',req)
        receipt={'settled':True,'nonce':'fixture-owned','exit_code':0}
        proof={'contract':'command-tree-v1','settled':True,'operation_id':operation,'receipt':receipt,
            'receipt_sha256':hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        for nested in (False,True):
            (job/'result.json').unlink(missing_ok=True)
            statuses=['queued','starting','running','finishing','stopping','done'];calls=[]
            def exchange(row,**kw):
                calls.append(row['verb']);self.assertEqual(row['verb'],'op.poll')
                status=statuses.pop(0)
                if status!='done':self.assertFalse((job/'result.json').exists())
                result={'ok':True,'id':operation,'status':status,'exit':0}
                if nested:result.update(status='running',terminal={'status':status,'exit_code':0})
                if status=='done':result['tree_exit']=proof
                return result
            with mock.patch.object(serverd_client,'available',return_value=True),mock.patch.object(serverd_client,'_token',return_value='fixture'),mock.patch.object(serverd_client,'_exchange',side_effect=exchange):
                value=verify_remote.run_check(0,request['checks'][0],self.root,self.directory,10,request,stopped=lambda:False)
            self.assertEqual(value['status'],'passed');self.assertTrue(value['custody']['group_exit_verified'])
            self.assertEqual(calls,['op.poll']*6);self.assertFalse(statuses)

    def test_legacy_or_contradictory_matrix_bootstrap_never_runs_local_or_remote(self):
        for value in (None,'self'):
            request=self.request('must never execute')
            if value is None:request.pop('scope');request.pop('verification_custody')
            else:request['scope']=value
            forge._atomic_json(self.directory/'request.json',request)
            with mock.patch.object(forge_verify,'CommandProcess') as local,mock.patch.object(serverd_client,'call') as remote:
                self.assertEqual(forge_verify.run(self.directory/'request.json'),2)
            local.assert_not_called();remote.assert_not_called()
            self.assertFalse((self.directory/'result.json').exists())

    @unittest.skipUnless(sys.platform.startswith('linux'),'isolated Linux host backend')
    def test_actual_matrix_lost_start_reply_reads_back_one_op_and_waits_setsided_child(self):
        marker=self.root/'finished';self.request(f'(setsid sh -c "sleep .5; touch {marker}" &) ; exit 0')
        admissions=[]
        def hook(request,call):
            if request['verb']=='op.start':
                admissions.append(request);result=call();self.assertTrue(result['ok']);return {'ok':False,'code':'timeout'}
            return call()
        with transport(self.backend_state,hook):
            self.assertEqual(forge_verify.run(self.directory/'request.json'),0)
            original=(self.directory/'result.json').read_bytes()
            self.assertEqual(forge_verify.run(self.directory/'request.json'),0)
            self.assertEqual((self.directory/'result.json').read_bytes(),original)
        result=forge._read_json(self.directory/'result.json');self.assertEqual(result['status'],'passed')
        self.assertEqual(len(admissions),1);self.assertTrue(marker.exists())
        self.assertTrue(result['checks'][0]['custody']['group_exit_verified'])
        self.assertIn('expected_root_binding',admissions[0]['args'])
        self.assertEqual(forge._unit_state(self.directory)['status'],'passed')

    @unittest.skipUnless(sys.platform.startswith('linux'),'isolated Linux host backend')
    def test_actual_remote_matrix_stop_after_supervisor_death_reconciles_exact_op(self):
        marker=self.root/'started';request=self.request(f'touch {marker}; setsid sleep 30 & wait')
        proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--matrix-run',str(self.directory/'request.json')],start_new_session=True)
        request.update(supervisor_pid=proc.pid,supervisor_started_at=forge._proc_started_at(proc.pid))
        forge._atomic_json(self.directory/'request.json',request)
        try:
            deadline=time.monotonic()+7
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(marker.exists(),forge._tail(self.directory/'state.json'))
            proc.kill();proc.wait(timeout=5)
            self.assertEqual(forge._unit_state(self.directory)['status'],'lost')
            # A thin retained outer record must not make remote commands look
            # like never-started local checks during dead-supervisor recovery.
            request.pop('scope');forge._atomic_json(self.directory/'request.json',request)
            with transport(self.backend_state):response=forge.verify(self.tid,'stop',verification_id=self.vid)
            self.assertIn('all check trees settled',response)
            self.assertEqual(forge._unit_state(self.directory)['status'],'stopped')
            self.assertEqual(forge._read_json(self.directory/'stop.json')['state'],'settled')
            custody=forge._read_json(self.directory/'result.json')['checks'][0]['custody']
            self.assertTrue(custody['remote']);self.assertTrue(custody['spawned']);self.assertTrue(custody['tree_exit']['settled'])
        finally:
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=5)
            with transport(self.backend_state):
                import forge_verify_remote
                forge_verify_remote.stop_owned(forge,self.directory)

    @unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('PRAXIS_TEST_FORGE_PROCESS')=='1','isolated Linux concurrent controllers')
    def test_two_actual_stop_controllers_publish_one_immutable_matrix_result(self):
        request=self.request('never admitted before supervisor death')
        old=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(.15)'],start_new_session=True)
        request.update(supervisor_pid=old.pid,supervisor_started_at=forge._proc_started_at(old.pid))
        forge._atomic_json(self.directory/'request.json',request);old.wait(timeout=5)
        processes=[]
        try:
            for side in ('left','right'):
                processes.append(subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--stop-controller',str(self.directory),side]))
            end=time.monotonic()+7
            while len(list(self.directory.glob('controller-ready-*')))<2 and time.monotonic()<end:time.sleep(.01)
            self.assertEqual(len(list(self.directory.glob('controller-ready-*'))),2)
            (self.directory/'controller-release').write_text('go')
            for proc in processes:self.assertEqual(proc.wait(timeout=8),0)
            observed=[json.loads((self.directory/('controller-'+side+'.json')).read_text()) for side in ('left','right')]
            self.assertTrue(all(x['ok'] for x in observed));self.assertEqual(observed[0]['result_sha256'],observed[1]['result_sha256'])
            self.assertEqual(observed[0]['stop_sha256'],observed[1]['stop_sha256'])
            self.assertEqual(hashlib.sha256((self.directory/'result.json').read_bytes()).hexdigest(),observed[0]['result_sha256'])
        finally:
            (self.directory/'controller-release').write_text('go')
            for proc in processes:
                if proc.poll() is None:proc.kill()
                proc.wait(timeout=5)

    def test_local_check_intent_and_identity_precede_actual_admission(self):
        state={'checks':{}}
        observed=[]
        class Stub:
            pid=123456
            def __init__(self,*a,**kw):
                observed.append('constructor');self.kw=kw
                self.assertion=kw.get('defer_start')
            def custody_identity(self):return {'nonce':'owned'}
            def admit(self):
                saved=json.loads((self_directory/'state.json').read_text())
                assert saved['checks']['check']['command_identity']['nonce']=='owned'
                observed.append('admitted')
        self_directory=self.directory;self_directory.mkdir(parents=True,exist_ok=True)
        with mock.patch.object(forge_verify,'CommandProcess',Stub),mock.patch.object(forge_verify,'_wait_local',return_value=(0,'passed','settled')):
            result=forge_verify._run_one(0,{'id':'check','command':'fixture'},self.root,self_directory,5,state,threading.Lock())
        self.assertEqual(result['status'],'passed');self.assertEqual(observed,['constructor','admitted'])
        self.assertTrue(result['custody']['group_exit_verified'])

if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--stop-controller':
        import datetime
        import forge_verify_remote
        directory=Path(sys.argv[2]);side=sys.argv[3];base=directory.parents[5]
        forge.BASE=base;forge.STATE_DIR=base/'memory/.forge';forge.TASKS_DIR=forge.STATE_DIR/'tasks'
        forge._now=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='microseconds')
        read=forge._read_json;first=[True]
        class GateList(list):
            def __iter__(self):
                (directory/('controller-ready-'+side)).write_text('past initial result precheck')
                while not (directory/'controller-release').exists():time.sleep(.01)
                return super().__iter__()
        def observed(path,*args,**kw):
            value=read(path,*args,**kw)
            if Path(path)==directory/'request.json' and first[0]:
                first[0]=False;value={**value,'checks':GateList(value['checks'])}
            return value
        with mock.patch.object(forge,'_read_json',side_effect=observed):ok=forge_verify_remote.reconcile_dead_supervisor(forge,directory)
        (directory/('controller-'+side+'.json')).write_text(json.dumps({'ok':ok,
            'result_sha256':hashlib.sha256((directory/'result.json').read_bytes()).hexdigest(),
            'stop_sha256':hashlib.sha256((directory/'stop.json').read_bytes()).hexdigest()}))
        raise SystemExit(not ok)
    if len(sys.argv)==3 and sys.argv[1]=='--matrix-run':
        path=Path(sys.argv[2]);request=json.loads(path.read_text());base=Path(request['root']).parent
        forge.BASE=base;forge.STATE_DIR=base/'memory/.forge';forge.TASKS_DIR=forge.STATE_DIR/'tasks'
        with transport(request['test_backend_state']):raise SystemExit(forge_verify.run(path))
    unittest.main()
