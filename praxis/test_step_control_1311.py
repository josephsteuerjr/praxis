"""Real Linux process custody and provider cancellation; no live services."""
import contextlib
import concurrent.futures
import dataclasses
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import llm
import model_step
import run_context
from run_context import RunContext
from run_manager import RunManager
import step_control
from command_process import CommandProcess
import forge_remote_run


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux custody integration')
class ForceControl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.manager = RunManager(self.base)
        self.registry = step_control.start(self.base)
        self.addCleanup(self.registry.close)
        self.ctx = RunContext.create(kind='chat_turn', goal='fixture', principal_id='owner', scope='owner')
        self.manager.create(self.ctx, 'fixture')
        self.manager.transition(self.ctx.run_id, 'running')
        self.handles = []
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for handle in self.handles:
            handle.stop_tree()

    def cancel(self):
        directory = self.base / 'memory/.control/run-cancel'
        directory.mkdir(parents=True, exist_ok=True)
        step_control._write(directory / (self.ctx.run_id + '.json'),
            {'id': 'fixture-request', 'run_id': self.ctx.run_id, 'action': 'cancel'})

    def wait_for(self, predicate, seconds=4):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.02)
        self.fail('condition was not met before deadline')

    def test_reset_kills_setsided_descendant_and_preserves_sibling(self):
        pid_file = self.base / 'child.pid'
        script = "import os,time,pathlib; pid=os.fork(); (os.setsid(),pathlib.Path(%r).write_text(str(os.getpid()))) if pid==0 else None; time.sleep(90)" % str(pid_file)
        with open(os.devnull, 'wb') as output:
            selected = CommandProcess([sys.executable, '-c', script], stdout=output, stderr=output)
            sibling = CommandProcess([sys.executable, '-c', 'import time; time.sleep(90)'], stdout=output, stderr=output)
        self.handles.extend([selected, sibling])
        self.registry.track(self.ctx.run_id, selected)
        self.wait_for(pid_file.exists)
        started = time.monotonic()
        self.cancel()
        self.wait_for(lambda: selected.poll() is not None)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNone(sibling.poll())
        self.assertFalse(Path('/proc/' + pid_file.read_text()).exists())
        self.assertEqual(self.manager.manifest(self.ctx.run_id)['status'], 'cancelled')

    def test_foreground_returns_while_background_remains_owned_and_resettable(self):
        with open(os.devnull, 'wb') as output:
            selected = CommandProcess(['sh', '-c', 'sleep 90 & exit 0'], stdout=output, stderr=output)
        self.handles.append(selected)
        self.registry.track(self.ctx.run_id, selected)
        self.assertEqual(selected.wait_foreground(2), 0)
        self.assertIsNone(selected.poll())
        self.cancel()
        self.wait_for(lambda: selected.poll() is not None)

    def test_nonowner_observer_cannot_acknowledge_another_executors_process(self):
        owner=step_control.Registry(self.base)
        owner.close()  # Hold its actual control tick while the observer runs.
        with open(os.devnull,'wb') as output:
            process=CommandProcess([sys.executable,'-c','import time; time.sleep(90)'],stdout=output,stderr=output)
        self.handles.append(process);owner.track(self.ctx.run_id,process)
        receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        self.cancel()
        self.wait_for(lambda:self.manager.manifest(self.ctx.run_id)['status']=='cancelled')
        self.assertIsNone(process.poll())
        self.assertFalse(receipt.exists())
        owner.once()
        self.wait_for(lambda: (owner.once() or json.loads(receipt.read_text()).get('ok')))
        self.assertTrue(json.loads(receipt.read_text())['ok'])
        self.assertIsNotNone(process.poll())

    def test_native_thread_is_not_claimed_killed_until_it_really_returns(self):
        release=threading.Event()
        pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.addCleanup(lambda: pool.shutdown(wait=True))
        self.addCleanup(release.set)
        future=pool.submit(release.wait)
        self.registry.track(self.ctx.run_id,step_control.FutureHandle(future))
        self.cancel()
        receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        self.wait_for(receipt.exists)
        self.assertFalse(json.loads(receipt.read_text())['ok'])
        self.assertFalse(future.done())
        release.set()
        self.wait_for(lambda: json.loads(receipt.read_text()).get('ok') is True)

    def test_spawn_reservation_prevents_early_success_before_handle_publication(self):
        receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        with step_control.starting(self.ctx.run_id,self.base):
            self.cancel()
            self.wait_for(receipt.exists)
            self.assertFalse(json.loads(receipt.read_text())['ok'])
        self.wait_for(lambda: json.loads(receipt.read_text()).get('ok') is True)

    @contextlib.contextmanager
    def provider(self, *, hang=False, streaming=False):
        captured, arrived, release = [], threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                if streaming:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    part={'id':'fixture','object':'chat.completion.chunk','model':'fixture',
                        'choices':[{'index':0,'delta':{'content':'partial'},'finish_reason':None}]}
                    self.wfile.write(('data: '+json.dumps(part)+'\n\n').encode());self.wfile.flush()
                    arrived.set();release.wait(8)
                    return
                arrived.set()
                if hang:
                    release.wait(8)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                packet = {'id':'fixture','object':'chat.completion.chunk','model':'fixture',
                    'choices':[{'index':0,'delta':{'content':'answer'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':11,'completion_tokens':2,'prompt_tokens_details':{'cached_tokens':7}}}
                try:
                    self.wfile.write(('data: ' + json.dumps(packet) + '\n\ndata: [DONE]\n\n').encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = {'frameworks': {'openai': {'api_key':'fixture-not-a-secret',
                  'base_url':'http://127.0.0.1:%s/v1' % server.server_port}}}
        try:
            yield config, captured, arrived
        finally:
            release.set()
            server.shutdown()
            server.server_close()

    def kwargs(self):
        return {'system':[{'type':'text','text':'unchanged system'}],
            'messages':[{'role':'user','content':'unchanged input'}], 'tools':None,
            'max_tokens':64, 'thinking':False, 'reasoning_effort':'low'}

    def test_provider_wire_and_normalized_response_match_direct_adapter(self):
        with self.provider() as (config, bodies, arrived), patch.dict(os.environ, {'PRAXIS_TEST':''}), patch.object(llm, '_config', return_value=config):
            direct = llm._call('openai','fixture',_resolved=True,**self.kwargs())
            child = model_step.call('openai','fixture',self.kwargs(),config)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(dataclasses.asdict(direct), dataclasses.asdict(child))

    def test_actual_durable_adapter_uses_child_with_identical_request(self):
        with self.provider() as (config,bodies,arrived), patch.dict(os.environ,{'PRAXIS_TEST':'','PRAXIS_BASE':str(self.base)}), patch.object(llm,'_config',return_value=config), patch.dict(llm._TEST_CLIENTS,{},clear=True):
            direct=llm._call('openai','fixture',_resolved=True,**self.kwargs())
            with run_context.bind_run(self.ctx):
                isolated=llm._call('openai','fixture',_resolved=True,**self.kwargs())
        self.assertEqual(bodies[0],bodies[1])
        self.assertEqual(dataclasses.asdict(direct),dataclasses.asdict(isolated))

    def test_reset_kills_provider_before_first_response(self):
        result = []
        with self.provider(hang=True) as (config, bodies, arrived), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            def invoke():
                try:
                    model_step.call('openai','fixture',self.kwargs(),config,
                                    run_id=self.ctx.run_id,base=self.base)
                except Exception as error:
                    result.append(error)
            thread = threading.Thread(target=invoke, daemon=True)
            thread.start()
            self.assertTrue(arrived.wait(5))
            started = time.monotonic()
            self.cancel()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic()-started,1.5)
            self.assertEqual(len(bodies),1)
            self.assertIsInstance(result[0],step_control.StepCancelled)

    def test_provider_errors_preserve_retry_and_terminal_contract(self):
        for error in (llm.EmptyResponseError('empty'), llm.TornStreamError('torn',llm.LLMResponse(text='partial')),
                      llm.QuotaExhaustedError('quota',slot='opaque',resets_at=1234), ValueError('invalid')):
            with self.assertRaises(type(error)) as caught:
                model_step._raise(model_step._error(error))
            self.assertEqual(str(caught.exception),str(error))
            self.assertEqual(llm._fallbackable(caught.exception),llm._fallbackable(error))

    def remote_fixture(self,scope):
        test=self
        class Backend:
            def __init__(self):
                self.process=None;self.identity='';self.calls=[];self.proof=True
            def start(self,identity):
                self.identity=identity
                with open(os.devnull,'wb') as out:
                    self.process=CommandProcess([sys.executable,'-c','import time; time.sleep(90)'],stdout=out,stderr=out)
                test.handles.append(self.process)
                return {'ok':True,'id':identity,'operation_id':identity,'status':'running'}
            def observe(self,identity):
                test.assertEqual(identity,self.identity)
                code=self.process.poll()
                row={'ok':True,'id':identity,'operation_id':identity,'status':'running' if code is None else 'stopped'}
                if code is not None:
                    if scope=='windows':row['terminal']={'status':'exited','exit_code':code}
                    elif self.proof:
                        receipt={'nonce':self.process._nonce,'settled':True,'exit_code':code}
                        row['tree_exit']={'contract':'command-tree-v1','settled':True,'operation_id':identity,'receipt':receipt,
                            'receipt_sha256':__import__('hashlib').sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
                return row
            def call(self,verb,args,**kwargs):
                self.calls.append((verb,dict(args),dict(kwargs)))
                if verb=='op.start':return self.start('op-'+__import__('hashlib').sha256(kwargs['request_id'].encode()).hexdigest()[:16])
                test.assertEqual(args['operation_id'],self.identity)
                if verb in ('op.stop','process.cancel'):
                    self.process.stop_tree();return {'ok':True}
                return self.observe(args['operation_id'])
            def process_start(self,**kwargs):
                self.calls.append(('process.start',{},dict(kwargs)))
                return self.start(kwargs['operation_id'])
            def process_status(self,identity,tail):
                return self.observe(identity)
        backend=Backend()
        def read(path,default):
            try:return json.loads(path.read_text())
            except (OSError,ValueError):return default
        forge=types.SimpleNamespace(BASE=self.base,_read_json=read,_atomic_json=step_control._write,
            _remote_client=lambda task:backend,_remote_observations=lambda task:contextlib.nullcontext(),_now=lambda:'fixture')
        task={'id':'fixture-task','scope':scope,'root':'/fixture' if scope=='host' else 'C:\\fixture'}
        directory=self.base/'remote-command'
        with patch.dict(os.environ,{'PRAXIS_TEST':''}),run_context.bind_run(self.ctx):
            request=forge_remote_run.start_at(forge,task,directory,'proc-fixture','fixture','.',90)
        return backend,request

    def test_reset_stops_only_its_host_operation_and_requires_tree_receipt(self):
        backend,request=self.remote_fixture('host')
        backend.proof=False
        self.cancel()
        self.wait_for(lambda:backend.process.poll() is not None)
        receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        self.wait_for(receipt.exists)
        self.assertFalse(json.loads(receipt.read_text())['ok'])
        stop=[c for c in backend.calls if c[0]=='op.stop']
        self.assertEqual(stop[0][1]['operation_id'],request['backend_operation_id'])
        self.assertEqual(stop[0][2]['request_id'],request['reset_request_id'])
        backend.proof=True
        self.wait_for(lambda:json.loads(receipt.read_text()).get('ok') is True)

    def test_windows_backend_reset_preserves_exact_operation_and_client_keys(self):
        backend,request=self.remote_fixture('windows')
        self.cancel()
        self.wait_for(lambda:backend.process.poll() is not None)
        receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        self.wait_for(lambda:receipt.exists() and json.loads(receipt.read_text()).get('ok') is True)
        stop=[c for c in backend.calls if c[0]=='process.cancel']
        self.assertEqual(stop[0][1]['operation_id'],request['backend_operation_id'])
        self.assertEqual(stop[0][2]['request_id'],request['reset_request_id'])
        self.assertEqual(stop[0][2]['operation_id'],request['reset_operation_id'])


if __name__ == '__main__':
    unittest.main()
