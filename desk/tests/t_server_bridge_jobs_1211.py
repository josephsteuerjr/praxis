import contextlib,json,sys,tempfile,threading,time,types,unittest
from pathlib import Path
from unittest import mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
from localharness import server_connections as connections,server_bridge,server_bridge_install as jobs


class Jobs(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.folder=Path(temporary.name)
        patch=mock.patch.object(connections,'root',return_value=self.folder);patch.start();self.addCleanup(patch.stop)
        self.owner=types.SimpleNamespace(lock=threading.RLock(),transfer_lock=threading.Lock(),exiting=False)
        self.row={'id':'a'*32,'name':'Fixture','server_profile':'b'*32,'phase':'planned','ports':{'local_proxy_port':8888,'remote_proxy_port':8888},
                  'private_key':'PRIVATE-SENSITIVE','public_key':'PUBLIC-FIXTURE',
                  'plan':{'id':'a'*32,'sha256':'reviewed','changes':[{'message':'fixture only'}],
                          'inventory':{'specification':{'https':False,'proxy':True,'domain':''}}}}
        jobs.save(self.row)
    def run_job(self,remote):
        client=mock.MagicMock()
        def connect(raw,**kwargs):
            if kwargs.get('on_client'):kwargs['on_client'](client)
            return client
        stack=contextlib.ExitStack();self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(connections,'resolve',return_value={'host':'fixture.invalid','port':22,'fingerprint':'pin','password':'ADMIN-SENSITIVE'}))
        stack.enter_context(mock.patch.object(connections,'save',return_value={'ok':True,'profile':{'id':'c'*32}}))
        stack.enter_context(mock.patch.object(jobs.server_transfer,'connect',side_effect=connect))
        call=stack.enter_context(mock.patch.object(jobs,'remote',side_effect=remote))
        result=jobs.start(self.owner,{'id':self.row['id'],'accepted_sha256':'reviewed'})
        return result,client,call
    def test_reviewed_hash_required_before_side_effect_and_public_state_has_no_secrets(self):
        with mock.patch.object(jobs,'remote') as remote:
            with self.assertRaisesRegex(ValueError,'подтверди'):jobs.start(self.owner,{'id':self.row['id'],'accepted_sha256':'different'})
            remote.assert_not_called()
        self.assertNotIn('SENSITIVE',json.dumps(jobs.status()))
        self.assertTrue(self.owner.transfer_lock.acquire(blocking=False));self.owner.transfer_lock.release()
    def test_background_job_publishes_only_verified_runtime_account_and_releases_slot(self):
        started=threading.Event();finish=threading.Event()
        def remote(client,request,**kwargs):
            self.assertNotIn('SENSITIVE',json.dumps(request));started.set();finish.wait(3)
            return {'username':'fixture-bridge','message':'check connection','certificate_automation':False}
        result,_,call=self.run_job(remote)
        self.assertTrue(started.wait(2));self.assertTrue(result['active'])
        with self.assertRaises(RuntimeError):jobs.start(self.owner,{'id':self.row['id'],'accepted_sha256':'reviewed'})
        finish.set();job=self.owner.server_setup_jobs[self.row['id']];job.thread.join(4)
        self.assertFalse(job.thread.is_alive());self.assertEqual(jobs.read(self.row['id'])['phase'],'installed')
        self.assertEqual(call.call_count,1)
        self.assertNotIn('SENSITIVE',json.dumps(jobs.status()))
        self.assertNotIn('SENSITIVE',json.dumps(server_bridge.read(self.row['id'])))
        self.assertTrue(self.owner.transfer_lock.acquire(blocking=False));self.owner.transfer_lock.release()
    def test_interruption_retains_same_plan_for_resume(self):
        started=threading.Event();closed=threading.Event()
        def remote(client,request,**kwargs):
            client.close.side_effect=closed.set;started.set();closed.wait(4)
            raise RuntimeError('fixture disconnected')
        self.run_job(remote);self.assertTrue(started.wait(2));jobs.close(self.owner)
        saved=jobs.read(self.row['id']);self.assertEqual(saved['phase'],'interrupted')
        self.assertEqual(saved['plan'],self.row['plan']);self.assertEqual(saved['private_key'],self.row['private_key'])
        self.assertIn('Продолжи'.lower(),saved['message'].lower())
        self.assertTrue(self.owner.transfer_lock.acquire(blocking=False));self.owner.transfer_lock.release()
    def test_thread_start_failure_is_resumable_in_same_live_application(self):
        with mock.patch.object(jobs.Job,'start',side_effect=RuntimeError('thread capacity')):
            with self.assertRaisesRegex(RuntimeError,'capacity'):
                jobs.start(self.owner,{'id':self.row['id'],'accepted_sha256':'reviewed'})
        state=jobs.status()[0]
        self.assertFalse(state['active']);self.assertEqual(state['phase'],'interrupted')
        self.assertEqual(state['plan']['sha256'],'reviewed')
        self.assertTrue(self.owner.transfer_lock.acquire(blocking=False));self.owner.transfer_lock.release()
    def test_failed_final_disk_write_does_not_leave_eternal_running_status(self):
        original=jobs.save
        def save(row):
            if row['phase']=='installed':raise OSError('disk full')
            original(row)
        with mock.patch.object(jobs,'save',side_effect=save):
            self.run_job(lambda *args,**kwargs:{'username':'fixture-bridge','message':'check connection'})
            self.owner.server_setup_jobs[self.row['id']].thread.join(4)
        self.assertEqual(jobs.read(self.row['id'])['phase'],'applying')
        state=jobs.status()[0]
        self.assertFalse(state['active']);self.assertEqual(state['phase'],'interrupted')
        self.assertEqual(state['plan']['sha256'],'reviewed')
        self.assertTrue(self.owner.transfer_lock.acquire(blocking=False));self.owner.transfer_lock.release()
    def test_unknown_process_state_never_offers_unsafe_resume(self):
        self.row.update(phase='applying',worker={'pid':999999,'birth':1.0});jobs.save(self.row)
        with mock.patch.object(jobs,'alive',return_value=None):
            state=jobs.status()[0];self.assertEqual(state['phase'],'attention');self.assertNotIn('plan',state)
            with self.assertRaisesRegex(RuntimeError,'остановка'):
                jobs.start(self.owner,{'id':self.row['id'],'accepted_sha256':'reviewed'})


if __name__=='__main__':unittest.main()
