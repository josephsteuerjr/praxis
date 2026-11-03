"""Real loopback hand, real scoped edits, no provider/owner installation."""
from pathlib import Path
import json,os,subprocess,sys,threading,time,unittest,urllib.request,urllib.error
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
import psutil
import t_source_update_pipeline_220 as fixtures
from localharness import source_update_hand as hand,source_update as pipeline,source_merge,owner_stop
from localharness.package_notice import read,write


class HandTransport(unittest.TestCase):
    def test_automatic_finish_rejects_changed_runtime_before_synchronous_git(self):
        from localharness import app_source_update
        with self.serve() as descriptor:
            (self.f.new/'runtime/fixture.lock').write_text('runtime changed after hand admission')
            self.assertTrue(self.call(descriptor)['ok'])
            with patch.object(source_merge,'git',side_effect=AssertionError('Unvalidated Git executed')) as git, \
                    patch.object(app_source_update,'finish',side_effect=AssertionError('Unvalidated app finish entered')) as app_finish:
                with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,'finish')
                self.assertEqual(caught.exception.code,409)
                git.assert_not_called();app_finish.assert_not_called()
            self.assertIn('runtime changed',self.f.operation()['finish_error'])

    def test_legacy_finish_rejects_changed_runtime_before_synchronous_git(self):
        from localharness import app_source_update
        with self.serve() as descriptor:
            operation=self.f.operation();operation['continuation']='legacy';write(self.f.directory/'pipeline.json',operation)
            (self.f.new/'runtime/fixture.lock').write_text('runtime changed after hand admission')
            with patch.object(source_merge,'git',side_effect=AssertionError('Unvalidated Git executed')) as git, \
                    patch.object(app_source_update,'finish',side_effect=AssertionError('Unvalidated app finish entered')) as app_finish:
                with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,'finish')
                self.assertEqual(caught.exception.code,409)
                git.assert_not_called();app_finish.assert_not_called()

    def test_execution_requests_recheck_runtime_outside_the_editing_scope(self):
        from localharness import source_update_battery
        with self.serve() as descriptor:
            composition=self.call(descriptor)['result']['composition_sha256']
            (self.f.new/'runtime/fixture.lock').write_text('runtime changed after hand admission')
            for action in ('verify','review','apply'):
                with self.subTest(action=action),patch.object(source_update_battery,'prepare') as battery, \
                        patch.object(pipeline,'validate_ready') as apply:
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        self.call(descriptor,action,{'expected_composition_sha256':composition})
                    self.assertEqual(caught.exception.code,409)
                    battery.assert_not_called();apply.assert_not_called()

    def test_existing_coordinator_lives_through_idle_agent_finish_and_exact_review_gate(self):
        """Real host loop/RPC lifetime; battery/provider stay explicit fixture boundaries."""
        import contextlib,types
        from localharness import source_update_host,self_review,source_update_transport,boot
        f=self.f;errors=[];cancel=f.root/'cancel-host';llm=types.ModuleType('llm');llm._config=lambda:{}
        f.coordinator['plan_id']=f.directory.name;f.service.coordinator=f.coordinator
        operation=f.operation();operation['coordinator']=f.coordinator;write(f.directory/'pipeline.json',operation)
        def run():
            try:source_update_host.coordinate(f.old,f.new,f.directory,cancel)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=run)
        def cleanup():
            cancel.touch();thread.join(timeout=8)
            self.assertFalse(thread.is_alive(),'existing coordinator failed to settle')
        with patch.object(source_update_host,'verified_runtime',return_value=f.coordinator) as verified, \
             patch.object(self_review,'Service',return_value=f.service), \
             patch.object(f.forge,'__file__',str(f.new/'tree/forge.py')), \
             patch.dict(sys.modules,{'forge':f.forge,'llm':llm}), \
             patch.object(source_update_transport,'review_transport',return_value=contextlib.nullcontext()), \
             patch.object(boot,'apply_env'),patch.object(sys,'path',list(sys.path)),patch.dict(os.environ):
            thread.start()
            try:
                deadline=time.monotonic()+5
                while read(f.directory/hand.NAME).get('state')!='ready' and not errors and time.monotonic()<deadline:time.sleep(.02)
                self.assertFalse(errors,errors);descriptor=read(f.directory/hand.NAME)
                time.sleep(.6);self.assertTrue(thread.is_alive(),'waiting_agent must not exit75 in automatic policy')
                for _ in range(3):self.assertEqual(self.call(descriptor)['result']['state'],'waiting_agent')
                self.call(descriptor,'finish')
                deadline=time.monotonic()+5
                while f.operation()['state']!='reviewing' and not errors and time.monotonic()<deadline:time.sleep(.03)
                self.assertFalse(errors,errors);self.assertEqual(f.operation()['state'],'reviewing')
                f.review_result('clear');thread.join(timeout=5)
                self.assertFalse(thread.is_alive());self.assertFalse(errors,errors)
                self.assertEqual(f.operation()['state'],'ready_to_apply');verified.assert_called_once()
                self.assertEqual(read(f.directory/hand.NAME)['state'],'closed')
            finally:cleanup()
    def setUp(self):
        self.f=fixtures.PipelineTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.selected=self.f.selected_automatic_candidate()
        write(self.f.directory/'coordinator-process.json',{'pid':os.getpid(),'birth':psutil.Process().create_time(),
            'invocation':'fixture','state':'running','program':str(self.f.new)})
        for mocker in (patch.object(owner_stop,'stopped',return_value=False),patch.object(owner_stop,'paused',return_value=False)):
            mocker.start();self.addCleanup(mocker.stop)

    def serve(self):return hand.serve(self.f.directory,self.f.tree,self.f.new,self.f.coordinator)

    def call(self,descriptor,action='status',args=None,token=None,plan=None):
        body=json.dumps({'action':action,'args':args or {}}).encode()
        request=urllib.request.Request(descriptor['origin']+'/source-update',data=body,headers={
            'Content-Type':'application/json','X-Helene-Update':descriptor['token'] if token is None else token,
            'X-Helene-Plan':self.f.directory.name if plan is None else plan})
        http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with http.open(request,timeout=5) as response:return json.load(response)

    def test_finish_widening_crosses_real_hand_and_is_persisted(self):
        properties=pipeline.TOOL['input_schema']['properties']
        self.assertEqual(properties['full_tests']['type'],'boolean')
        self.assertEqual(properties['extra_tests']['items']['type'],'string')
        request={'full_tests':True,'extra_tests':['desk/tests/t_source_update_hand_3010.py'],
                 'test_reason':'indirect hand consumer'}
        with self.serve() as descriptor:
            self.call(descriptor,'finish',request)
        self.assertEqual(self.f.operation()['verification_request'],
            {'full_tests':True,'extra_tests':request['extra_tests'],'reason':request['test_reason']})

    def test_real_calls_return_scoped_status_and_finish_without_a_new_executor(self):
        with self.serve() as descriptor:
            status=self.call(descriptor)['result']
            self.assertEqual(status['state'],'waiting_agent')
            self.assertEqual(status['coordinator_context']['protocol'],2)
            self.assertNotIn('token',json.dumps(status))
            result=self.call(descriptor,'finish')['result']
            self.assertEqual(result['state'],'verification_requested')
        self.assertEqual(read(self.f.directory/hand.NAME)['state'],'closed')

    def test_actual_legacy_client_process_reuses_three_calls_without_cli_or_runtime_rechecks(self):
        script=self.f.root/'client.py'
        script.write_text("""import json,sys,subprocess,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import source_update as pipeline,legacy_update_tool
from localharness.package_notice import read,write
namespace={'__package__':'localharness','Path':Path,'json':json,'time':time,'read':read,'write':write,
 'path':lambda tree:Path(tree)/'memory/.control/source-update.json'}
exec(legacy_update_tool.shim(Path(sys.argv[3]),Path(sys.executable),pipeline.TOOL,'fixture'),namespace)
def forbidden(*args,**kwargs):raise AssertionError('CLI fallback would recheck the entire runtime')
subprocess.run=forbidden
values=[namespace['command'](Path(sys.argv[2])) for i in range(3)]
assert all(value['coordinator_context']['protocol']==2 for value in values)
assert all('token' not in json.dumps(value) for value in values)
print('THREE_SCOPED_CALLS_WITHOUT_CLI')
""",encoding='utf8')
        env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','PATHEXT'}}
        with self.serve(),patch('localharness.source_update_host.verified_runtime',side_effect=AssertionError('Repeated runtime check')):
            result=subprocess.run([sys.executable,'-B',str(script),str(Path(__file__).resolve().parents[1]),
                str(self.f.tree),str(self.f.new)],capture_output=True,text=True,env=env,timeout=30,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('THREE_SCOPED_CALLS_WITHOUT_CLI',result.stdout)

    def test_wrong_capability_or_plan_cannot_read_or_edit(self):
        before=(self.f.directory/'pipeline.json').read_bytes()
        with self.serve() as descriptor:
            for kwargs in ({'token':'wrong'},{'plan':'0'*64}):
                with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,**kwargs)
                self.assertEqual(caught.exception.code,403)
        self.assertEqual((self.f.directory/'pipeline.json').read_bytes(),before)

    def test_delayed_unauthorized_body_keeps_http_refusal_instead_of_reset(self):
        import http.client
        from localharness import agent_source_update
        original_server=hand.ThreadingHTTPServer
        original_send=http.client.HTTPConnection.send
        body=json.dumps({'action':'status','args':{}}).encode()
        before=(self.f.directory/'pipeline.json').read_bytes()
        for kwargs in ({'token':'wrong'},{'plan':'0'*64}):
            with self.subTest(kwargs=kwargs):
                closed=threading.Event()
                class Server(original_server):
                    def close_request(self,request):
                        super().close_request(request);closed.set()
                def delayed_send(connection,data):
                    if data==body:closed.wait(.3)
                    return original_send(connection,data)
                with patch.object(hand,'ThreadingHTTPServer',Server), \
                     patch.object(agent_source_update,'command',side_effect=AssertionError('Unauthorized command executed')) as command:
                    with self.serve() as descriptor,patch.object(http.client.HTTPConnection,'send',delayed_send):
                        with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,**kwargs)
                        self.assertEqual(caught.exception.code,403)
                        self.assertIn('capability refused',caught.exception.read().decode())
                    command.assert_not_called()
        self.assertEqual((self.f.directory/'pipeline.json').read_bytes(),before)

    def test_refusal_is_delivered_before_client_sends_body(self):
        import http.client,socket
        from urllib.parse import urlsplit
        from localharness import agent_source_update
        closed=threading.Event();original_server=hand.ThreadingHTTPServer
        class Server(original_server):
            def close_request(self,request):
                super().close_request(request);closed.set()
        body=b'not JSON; unauthorized bytes must only be discarded'
        before=(self.f.directory/'pipeline.json').read_bytes()
        with patch.object(hand,'ThreadingHTTPServer',Server), \
             patch.object(agent_source_update,'command',side_effect=AssertionError('Unauthorized command executed')) as command:
            with self.serve() as descriptor:
                url=urlsplit(descriptor['origin'])
                with socket.create_connection((url.hostname,url.port),timeout=hand.REQUEST_IO_TIMEOUT*2) as connection:
                    headers=('POST /source-update HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                        'X-Helene-Update: wrong\r\nX-Helene-Plan: '+self.f.directory.name+'\r\n'
                        'Content-Length: '+str(len(body))+'\r\n\r\n').encode()
                    connection.sendall(headers)
                    response=http.client.HTTPResponse(connection);response.begin()
                    self.assertEqual(response.status,403)
                    self.assertIn('capability refused',response.read().decode())
                    self.assertFalse(closed.is_set(),'read side closed before receiving bounded body')
                    connection.sendall(body)
                    self.assertTrue(closed.wait(hand.REQUEST_IO_TIMEOUT))
                    response.close()
            command.assert_not_called()
        self.assertEqual((self.f.directory/'pipeline.json').read_bytes(),before)

    def test_busy_verifier_rejects_edits_promptly_but_status_still_works(self):
        with self.serve() as descriptor,pipeline._LOCK:
            started=time.monotonic()
            with self.assertRaises(urllib.error.HTTPError) as caught:self.call(descriptor,'finish')
            self.assertEqual(caught.exception.code,409)
            self.assertLess(time.monotonic()-started,3)
            self.assertTrue(self.call(descriptor)['ok'])

    def test_pause_and_replacement_request_block_mutation(self):
        with self.serve() as descriptor:
            with patch.object(owner_stop,'paused',return_value=True):
                with self.assertRaises(urllib.error.HTTPError):self.call(descriptor,'finish')
            request=read(pipeline.path(self.f.tree));write(pipeline.path(self.f.tree),{**request,'id':'0'*64})
            with self.assertRaises(urllib.error.HTTPError):self.call(descriptor,'finish')
        self.assertEqual((self.f.code/'module.py').read_text(),fixtures.LOCAL)

    def test_client_checks_program_and_pid_generation_before_using_a_descriptor(self):
        with self.serve() as descriptor:
            with self.assertRaisesRegex(ValueError,'another incoming'):
                hand.try_request(self.f.tree,'status',{},expected_program=self.f.old)
            changed={**descriptor,'pid':os.getppid(),'birth':0}
            write(self.f.directory/hand.NAME,changed)
            write(self.f.directory/'coordinator-process.json',{k:changed[k] for k in ('pid','birth','invocation')})
            with self.assertRaisesRegex(ValueError,'PID was reused'):hand.try_request(self.f.tree,'status',{})

    def test_waiting_and_running_review_polls_do_not_rehash_source_inputs(self):
        operation=self.f.operation()
        with patch.object(pipeline,'_fresh',side_effect=AssertionError('Polling hashed all source inputs')):
            pipeline._advance(operation,self.f.directory,self.f.forge,self.f.service)
            operation.update(state='reviewing',review_id='fixture-review')
            with patch.object(self.f.service,'status',return_value={'execution':'running','critics':[]}):
                pipeline._advance(operation,self.f.directory,self.f.forge,self.f.service)

    def test_only_unused_selected_agent_placeholder_can_transfer_after_early_finish(self):
        (self.f.directory/'coordinator-process.json').unlink()
        operation=self.f.operation();operation['coordinator']={'kind':'waiting-selected-agent'}
        write(self.f.directory/'pipeline.json',operation)
        self.selected.command(self.f.tree,'finish')
        result=pipeline.ensure(self.f.tree,self.f.old,self.f.code,self.f.directory,'2.2.0',coordinator=self.f.coordinator)
        self.assertEqual(result['coordinator'],self.f.coordinator)
        with self.assertRaisesRegex(ValueError,'Another incoming'):
            pipeline.ensure(self.f.tree,self.f.old,self.f.code,self.f.directory,'2.2.0',coordinator={**self.f.coordinator,'program':str(self.f.old)})

class RejectionDrainBounds(unittest.TestCase):
    def handler(self,length):
        from types import SimpleNamespace
        from unittest.mock import Mock
        return SimpleNamespace(headers={'Content-Length':str(length)},connection=Mock(),rfile=Mock())

    def test_unknown_and_excessive_lengths_are_not_consumed(self):
        for length in ('invalid',-1,0,hand.MAX_PAYLOAD+1):
            with self.subTest(length=length):
                handler=self.handler(length);hand._settle_rejected_body(handler)
                handler.rfile.read.assert_not_called();handler.connection.shutdown.assert_not_called()

    def test_valid_body_is_discarded_in_bounded_chunks(self):
        import socket
        handler=self.handler(hand.MAX_PAYLOAD)
        handler.rfile.read.side_effect=lambda amount:b'x'*amount
        with patch.object(hand.time,'monotonic',return_value=0):hand._settle_rejected_body(handler)
        chunks=[call.args[0] for call in handler.rfile.read.call_args_list]
        self.assertEqual(sum(chunks),hand.MAX_PAYLOAD)
        self.assertLessEqual(max(chunks),64*1024)
        handler.connection.shutdown.assert_called_once_with(socket.SHUT_WR)

    def test_slow_body_cannot_extend_absolute_drain_deadline(self):
        handler=self.handler(hand.MAX_PAYLOAD);handler.rfile.read.return_value=b'x'
        ticks=iter(range(int(hand.REQUEST_IO_TIMEOUT)+2))
        with patch.object(hand.time,'monotonic',side_effect=lambda:next(ticks)):
            hand._settle_rejected_body(handler)
        waits=[call.args[0] for call in handler.connection.settimeout.call_args_list]
        self.assertTrue(waits)
        self.assertTrue(all(0<seconds<=hand.REQUEST_IO_TIMEOUT for seconds in waits))
        self.assertEqual(waits,sorted(waits,reverse=True))
        self.assertLess(handler.rfile.read.call_count,hand.MAX_PAYLOAD)

    def test_disconnected_client_is_not_retried(self):
        handler=self.handler(10);handler.rfile.read.side_effect=ConnectionResetError('peer closed')
        hand._settle_rejected_body(handler)
        handler.rfile.read.assert_called_once()


if __name__=='__main__':unittest.main()
