"""Numeric relay progress through real warm workers; no provider calls."""
import contextlib
import concurrent.futures
import dataclasses
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import sys
import threading
import types
import unittest
from unittest.mock import patch

import llm
import model_progress as progress
import model_step
import step_control
import test_step_control_1311 as fixture
import test_relay_terminal_2509 as terminal_fixture


def metrics(**kw):
    return dict(schema='relay.generation.v1',attempt_id='a'*32,argument_bytes=1234,
                argument_deltas=20,attempt_ms=5000,outcome='streaming',**kw)


def chunk(value=None, text=None, terminal=None):
    return types.SimpleNamespace(relay_generation=value, relay_terminal=terminal,usage=None,
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=text,tool_calls=None),finish_reason='stop')] if text else [])


class Projection(unittest.TestCase):
    def test_untrusted_metadata_is_numeric_only_and_never_breaks_adapter(self):
        packet=metrics(secret='private',usage={'output_tokens':2,'text':'private'},limits={'argument_bytes':0,'path':'private'})
        packet.update(outside_string_whitespace_ratio=10**1000,argument_chars=True,longest_whitespace_run=-2)
        safe=progress.project(packet)
        self.assertNotIn('private',json.dumps(safe));self.assertNotIn('argument_chars',safe)
        self.assertNotIn('outside_string_whitespace_ratio',safe)
        self.assertEqual(safe['limits'],{'argument_bytes':0})
        for value in (None,[],{'schema':'other'},{}):self.assertEqual(progress.project(value),{})

    def test_throttle_is_per_observer_and_terminal_is_delivered(self):
        seen=[]
        with progress.observe(seen.append),patch.object(progress.time,'monotonic',side_effect=[10,11,16,17]):
            progress.emit(metrics());progress.emit(metrics());progress.emit(metrics())
            final=metrics();final['outcome']='completed';progress.emit(final)
        self.assertEqual(len(seen),3);self.assertEqual(seen[-1]['outcome'],'completed')
        with progress.observe(lambda _:(_ for _ in ()).throw(OSError('sink'))):
            self.assertEqual(llm._openai_from_stream(iter([chunk(metrics()),chunk(text='answer')]),'fixture').text,'answer')

    def test_metadata_is_not_text_tools_or_usage(self):
        reference=llm._openai_from_stream(iter([chunk(text='answer')]),'fixture')
        seen=[]
        with progress.observe(seen.append):
            actual=llm._openai_from_stream(iter([chunk(metrics()),chunk(text='answer')]),'fixture')
        self.assertEqual(dataclasses.asdict(reference),dataclasses.asdict(actual))
        self.assertEqual(len(seen),1)

    def test_runaway_discards_partial_and_closes_stream_without_subscription_hold(self):
        class Stream:
            closed=False
            def __iter__(self):
                yield chunk(text='partial')
                final=metrics();final['outcome']='runaway_generation'
                yield chunk(final,terminal={'code':'runaway_generation','message':'private'})
            def close(self):self.closed=True
        stream=Stream()
        with self.assertRaises(llm.RunawayGenerationError) as caught:
            llm._openai_from_stream(stream,'fixture')
        self.assertTrue(stream.closed)
        self.assertNotIsInstance(caught.exception,(llm.EmptyResponseError,llm.RelayTerminalError))
        self.assertIn('5000',str(caught.exception));self.assertNotIn('private',str(caught.exception))
        with self.assertRaises(llm.RunawayGenerationError) as transferred:
            model_step._raise(model_step._error(caught.exception))
        self.assertEqual(transferred.exception.metrics,caught.exception.metrics)

    def test_stale_progress_identity_is_rejected_before_observation(self):
        identity=dict(version=1,generation='g',run_id='r',lease='l',attempt='a')
        worker=model_step.Worker.__new__(model_step.Worker)
        worker.input=io.BytesIO();worker.output=io.BytesIO(model_step._encode(dict(identity,lease='stale',progress=metrics())))
        worker.lock=threading.RLock();worker.state='busy';worker.attempt='a'
        worker.pool=types.SimpleNamespace(run_id='r',base='/unused')
        stopped=[];worker.stop_tree=lambda:stopped.append(True)
        seen=[]
        with progress.observe(seen.append),patch.object(step_control,'requested',return_value=False):
            with self.assertRaises(model_step.ProviderOutcomeUnknown):worker.exchange(identity)
        self.assertEqual(seen,[]);self.assertEqual(stopped,[True])


class Fallback(terminal_fixture.Base):
    def test_runaway_uses_existing_fallback_without_replaying_same_endpoint(self):
        self._cfg(fallback_framework='anthropic',fallback_model='fixture')
        primary=terminal_fixture.FakeRelay([chunk(metrics(),terminal={'code':'runaway_generation'})])
        backup=terminal_fixture.FakeAnthropic('fallback')
        llm.use_test_client(primary,'openai');llm.use_test_client(backup,'anthropic')
        self.assertEqual(self._chat().text,'fallback')
        self.assertEqual(len(primary.calls),1);self.assertEqual(len(backup.calls),1)
        self.assertEqual(llm._ENDPOINT_HOLD,{})


@unittest.skipUnless(sys.platform.startswith('linux'),'Linux warm worker custody')
class RealProgress(unittest.TestCase):
    setUp=fixture.ForceControl.setUp
    stop_all=fixture.ForceControl.stop_all
    cancel=fixture.ForceControl.cancel
    wait_for=fixture.ForceControl.wait_for
    kwargs=fixture.ForceControl.kwargs

    @contextlib.contextmanager
    def provider(self, *, hold=False, runaway=False):
        bodies=[];release=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                packet={'id':'fixture','object':'chat.completion.chunk','model':'fixture','choices':[],'relay_generation':metrics()}
                try:
                    self.wfile.write(('data: '+json.dumps(packet)+'\n\n').encode());self.wfile.flush()
                    if hold:release.wait(12)
                    if runaway:
                        packet['relay_terminal']={'code':'runaway_generation'}
                        packet['relay_generation']['outcome']='runaway_generation'
                    else:
                        packet={'id':'fixture','object':'chat.completion.chunk','model':'fixture',
                            'choices':[{'index':0,'delta':{'content':'answer'},'finish_reason':'stop'}]}
                    self.wfile.write(('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode());self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        config={'frameworks':{'openai':{'api_key':'fixture','base_url':'http://127.0.0.1:%s/v1'%server.server_port}}}
        try:yield config,bodies,release
        finally:release.set();model_step.close_run(self.ctx.run_id,self.base);server.shutdown();server.server_close();thread.join(3)

    def invoke(self,config,callback):
        with progress.observe(callback):
            return model_step.call('openai','fixture',self.kwargs(),config,run_id=self.ctx.run_id,base=self.base)

    def test_real_progress_precedes_final_and_frozen_request_stays_identical(self):
        seen=[];arrived=threading.Event()
        def record(v):seen.append(v);arrived.set()
        with self.provider(hold=True) as (config,bodies,release),patch.dict(os.environ,{'PRAXIS_TEST':''}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.invoke,config,record)
                self.assertTrue(arrived.wait(7));self.assertFalse(future.done())
                release.set();self.assertEqual(future.result(3).text,'answer')
            self.assertEqual(self.invoke(config,lambda _:None).text,'answer')
            self.assertEqual(bodies[0],bodies[1]);self.assertEqual(len(seen),1)

    def test_reset_after_progress_kills_exact_attempt(self):
        arrived=threading.Event()
        with self.provider(hold=True) as (config,bodies,release),patch.dict(os.environ,{'PRAXIS_TEST':''}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.invoke,config,lambda _:arrived.set())
                self.assertTrue(arrived.wait(7));self.cancel()
                with self.assertRaises(step_control.StepCancelled):future.result(2)
            self.assertEqual(len(bodies),1)

    def test_typed_runaway_survives_real_worker_pipe(self):
        seen=[]
        with self.provider(runaway=True) as (config,bodies,_),patch.dict(os.environ,{'PRAXIS_TEST':''}):
            with self.assertRaises(llm.RunawayGenerationError) as caught:self.invoke(config,seen.append)
            self.assertEqual(caught.exception.metrics['argument_bytes'],1234)
            self.assertEqual(seen[-1]['outcome'],'runaway_generation');self.assertEqual(len(bodies),1)


if __name__=='__main__':unittest.main()
