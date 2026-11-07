"""Real HTTP admission through ordinary tick contention, with unchanged gates."""
from contextlib import contextmanager
import io,json,os,threading,time,unittest,urllib.error
from unittest.mock import patch,Mock
from tests import t_source_update_hand_3010 as fixture
from localharness import source_update as pipeline,source_update_hand as hand,agent_source_update as selected,source_merge,owner_stop
from localharness.package_notice import read,write


class Custody(unittest.TestCase):
    setUp=fixture.HandTransport.setUp
    serve=fixture.HandTransport.serve
    call=fixture.HandTransport.call

    @contextmanager
    def held(self,maintenance=False):
        entered=threading.Event();release=threading.Event();errors=[]
        def observe(*args, **kwargs):
            entered.set()
            if not release.wait(5):raise TimeoutError('Fixture holder was not released')
            return {'watch':{},'current_id':None}
        def run():
            try:
                if maintenance:
                    with patch.object(pipeline,'_observe',side_effect=observe):
                        pipeline.tick(self.f.tree,self.f.forge,self.f.service,coordinator=self.f.coordinator)
                else:
                    with pipeline._LOCK:observe()
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=run);thread.start();self.assertTrue(entered.wait(2))
        try:yield release
        finally:
            release.set();thread.join(6);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])

    def edit_args(self):
        return {'path':'module.py','content':fixture.fixtures.BOTH+'\nfixture_diagnostic = True\n',
          'expected_candidate_sha256':source_merge.candidate_signature(self.f.directory)}

    def test_real_maintenance_tick_yields_to_the_same_admitted_edit(self):
        args=self.edit_args()
        with self.serve() as descriptor,self.held(maintenance=True) as release:
            timer=threading.Timer(.1,release.set);timer.start()
            try:result=self.call(descriptor,'resolve',args)
            finally:timer.join(2)
        self.assertTrue(result['ok'])
        self.assertEqual((self.f.directory/'result/module.py').read_text(),args['content'])
        self.assertEqual(self.f.operation()['state'],'waiting_agent')
        self.assertEqual(self.f.operation()['attempts'],[])

    def test_long_busy_waiting_candidate_is_not_reported_as_running_review(self):
        before=(self.f.directory/'result/module.py').read_bytes()
        with self.serve() as descriptor,self.held(),patch.object(hand,'REQUEST_LOCK_WAIT',.05,create=True):
            with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,'resolve',self.edit_args())
            self.assertEqual(caught.exception.code,409)
            error=json.load(caught.exception)['error']
            self.assertIn('busy',error.lower());self.assertIn('waiting_agent',error)
            self.assertNotIn('Verification/review is executing',error)
            self.assertTrue(self.call(descriptor)['ok'])
        self.assertEqual((self.f.directory/'result/module.py').read_bytes(),before)

    def test_actual_verification_or_review_states_remain_frozen(self):
        before=(self.f.directory/'result/module.py').read_bytes()
        with self.serve() as descriptor:
            for state in ('verification_requested','review_requested','reviewing'):
                with self.subTest(state=state):
                    operation=self.f.operation();operation['state']=state;write(self.f.directory/'pipeline.json',operation)
                    with self.held(),patch.object(hand,'REQUEST_LOCK_WAIT',.05,create=True):
                        with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,'resolve',self.edit_args())
                        self.assertEqual(caught.exception.code,409)
        self.assertEqual((self.f.directory/'result/module.py').read_bytes(),before)

    def test_pause_arriving_during_wait_blocks_the_edit(self):
        paused=[False];before=(self.f.directory/'result/module.py').read_bytes()
        with self.serve() as descriptor,self.held() as release,patch.object(owner_stop,'paused',side_effect=lambda:paused[0]):
            def stop():paused[0]=True;release.set()
            timer=threading.Timer(.1,stop);timer.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,'resolve',self.edit_args())
                self.assertEqual(caught.exception.code,409)
                self.assertIn('Stop/Pause',json.load(caught.exception)['error'])
            finally:timer.join(2)
        self.assertEqual((self.f.directory/'result/module.py').read_bytes(),before)

    def test_replacement_during_wait_cannot_dispatch_into_another_plan(self):
        with self.serve() as descriptor,self.held() as release:
            def replace():
                request=read(pipeline.path(self.f.tree));write(pipeline.path(self.f.tree),{**request,'id':'0'*64});release.set()
            timer=threading.Timer(.1,replace);timer.start()
            try:
                with patch.object(selected,'command',return_value={'unexpected':'dispatch'}) as dispatched:
                    with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,'resolve',self.edit_args())
                    self.assertEqual(caught.exception.code,409);dispatched.assert_not_called()
            finally:timer.join(2)

    def test_cancellation_during_wait_remains_binding(self):
        before=(self.f.directory/'result/module.py').read_bytes()
        with self.serve() as descriptor,self.held() as release:
            def cancel():write(self.f.directory/'cancel.json',{'reason':'fixture owner cancel'});release.set()
            timer=threading.Timer(.1,cancel);timer.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,'resolve',self.edit_args())
                self.assertEqual(caught.exception.code,409)
            finally:timer.join(2)
        self.assertEqual((self.f.directory/'result/module.py').read_bytes(),before)

    def test_existing_client_keeps_a_bounded_synchronous_response_deadline(self):
        with self.serve(),patch.object(os,'getpid',return_value=os.getpid()+1):
            http=Mock();http.open.return_value=io.BytesIO(b'{"ok":true,"result":{"state":"waiting_agent"}}')
            with patch('urllib.request.build_opener',return_value=http):
                handled,value=hand.try_request(self.f.tree,'status',{},expected_program=self.f.new)
            self.assertTrue(handled);self.assertEqual(value['state'],'waiting_agent')
            timeout=http.open.call_args.kwargs['timeout'];self.assertGreater(timeout,30);self.assertLessEqual(timeout,600)


if __name__=='__main__':unittest.main(verbosity=2)
