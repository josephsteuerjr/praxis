"""The agent submits once; application delivery survives its original turn."""
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import importlib.util
import json
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
from localharness import speech_bridge, speech_client, speech_protocol as ipc

class AsyncSpeechTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tree=Path(self.tmp.name);self.config=self.tree/'helene.json'
        self.cfg={'voice':{'speak':True,'voice':'xtts'}};self.config.write_text(json.dumps(self.cfg),encoding='utf8')
        self.state={'status':'running'};self.entered=threading.Event();self.release=threading.Event();self.delivered=threading.Event()
        self.sent=[];self.preflight=[];self.posts=[];self.blocked=False;self.call='call-1'
        self.agent=types.SimpleNamespace(tool_speak=lambda:None,TOOL_IMPL={},TOOLS=[{'name':'speak','description':'old'}],
            _TURN_CHANNEL=ContextVar('speech-channel',default=types.SimpleNamespace(chat_id='window')),
            _TURN_OUTBOUND=ContextVar('speech-outbound',default=[]),
            run_context=types.SimpleNamespace(current_run=lambda:types.SimpleNamespace(run_id='run-1')),
            _runs=lambda:types.SimpleNamespace(manifest=lambda rid:self.state),
            current_tool_execution=lambda:{'call_id':self.call},
            guard_outbound_reply=lambda text,*a,**k:self.preflight.append((text,len(self.posts))) or text,
            _stage_turn_media=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No per-turn staging or generated-audio review')))
        @contextmanager
        def delivery(*a):yield self.state.get('status')!='cancelled' and not self.blocked
        self.boundary=types.SimpleNamespace(cancelled=lambda state:state.get('status')=='cancelled',delivery=delivery)
        self.owner=types.SimpleNamespace(LOCK=threading.RLock())
        def produce(text,tree,output_dir,**kwargs):
            self.posts.append((text,kwargs));self.entered.set();self.release.wait(3)
            if kwargs['stopped']():raise InterruptedError()
            path=self.tree/(kwargs['request_id']+'.ogg');path.write_bytes(b'controlled audio; no acoustic acceptance')
            return types.SimpleNamespace(path=path,check=lambda *a:(_ for _ in ()).throw(AssertionError('No review after generation')))
        def send(job,audio,check):
            check();self.sent.append((dict(job),audio.path.read_bytes(),dict(self.state)));self.delivered.set();return 'accepted-once'
        self.blocker=patch.object(ipc,'blocked',side_effect=lambda tree:self.blocked);self.blocker.start();self.addCleanup(self.blocker.stop)
        self.producer=patch.object(speech_client,'synthesize',side_effect=produce);self.producer.start();self.addCleanup(self.producer.stop)
        self.service=speech_bridge.install(self.agent,self.tree,self.config,boundary=self.boundary,owner=self.owner,deliver=send)
        self.addCleanup(self.finish)
    def finish(self):
        self.release.set();self.service.close()
    def wait_done(self):
        end=time.monotonic()+4
        while self.service.pending.unfinished_tasks and time.monotonic()<end:time.sleep(.01)
        self.assertEqual(self.service.pending.unfinished_tasks,0)
    def receipt(self):
        identity=hashlib.sha256(('run-1:'+self.call).encode()).hexdigest()[:32]
        return ipc.read(self.service.home/(identity+'.json'))
    def test_return_precedes_generation_and_original_turn_completion_does_not_hold_delivery(self):
        start=time.monotonic();reply=self.agent.tool_speak('Speak and continue')
        self.assertLess(time.monotonic()-start,1);self.assertIn('принят',reply)
        self.assertTrue(self.entered.wait(2));self.assertFalse(self.delivered.is_set())
        self.state['status']='completed';self.release.set();self.assertTrue(self.delivered.wait(2));self.wait_done()
        self.assertEqual(self.sent[0][2]['status'],'completed');self.assertEqual(self.receipt()['status'],'delivered')
        self.assertEqual(self.preflight,[('Speak and continue',0)])
        self.assertEqual(self.agent._TURN_OUTBOUND.get(),[])
    def test_replayed_call_cannot_generate_or_send_twice(self):
        self.agent.tool_speak('Once');self.assertTrue(self.entered.wait(2))
        self.agent.tool_speak('Once');self.release.set();self.wait_done();self.agent.tool_speak('Once')
        self.assertEqual(len(self.posts),1);self.assertEqual(len(self.sent),1)
    def test_same_identity_cannot_change_recipient_or_content(self):
        self.agent.tool_speak('Original');self.assertTrue(self.entered.wait(2));reply=self.agent.tool_speak('Changed')
        self.assertIn('не принята',reply);self.assertEqual(len(self.posts),1)
    def test_cancel_while_producer_is_busy_prevents_late_delivery(self):
        self.agent.tool_speak('Cancelled');self.assertTrue(self.entered.wait(2));self.state['status']='cancelled'
        self.release.set();self.wait_done();self.assertEqual(self.sent,[]);self.assertEqual(self.receipt()['status'],'cancelled')
    def test_owner_pause_prevents_late_delivery(self):
        self.agent.tool_speak('Paused');self.assertTrue(self.entered.wait(2));self.blocked=True
        self.release.set();self.wait_done();self.assertEqual(self.sent,[])
    def test_pause_then_resume_between_producer_polls_still_cancels_completed_turn_audio(self):
        from localharness import owner_stop
        self.agent.tool_speak('Old request');self.assertTrue(self.entered.wait(2))
        self.state['status']='completed'
        owner_stop.change_pause(self.tree,True);owner_stop.change_pause(self.tree,False)
        self.release.set();self.wait_done();self.assertEqual(self.sent,[])
        self.assertEqual(self.receipt()['status'],'cancelled')
    def test_selection_change_prevents_old_voice_delivery(self):
        self.agent.tool_speak('Old voice');self.assertTrue(self.entered.wait(2));self.cfg['voice']['voice']='fish'
        self.config.write_text(json.dumps(self.cfg),encoding='utf8');self.release.set();self.wait_done();self.assertEqual(self.sent,[])
    def test_one_contract_for_every_provider_and_device(self):
        self.release.set()
        for i,(provider,device) in enumerate([('fish','cpu'),('xtts','cuda'),('xtts','cpu')]):
            self.call='call-'+str(i);self.cfg['voice'].update(voice=provider,device=device)
            self.config.write_text(json.dumps(self.cfg),encoding='utf8');self.agent.tool_speak('Provider '+str(i));self.wait_done()
        self.assertEqual(len(self.posts),3);self.assertEqual(len(self.sent),3)
    def test_tool_description_requires_no_followup_or_audio_review(self):
        self.assertIn('асинхронно',self.agent.TOOLS[0]['description']);self.assertIn('не проверяй аудио',self.agent.tool_speak.__doc__)
        self.assertTrue(self.service.accepted_for_run('run-1') is False)
    def test_actual_default_english_projection_and_pointer_advertise_async_contract(self):
        path=Path(__file__).resolve().parents[2]/'helene/core/tool_text_en.py'
        spec=importlib.util.spec_from_file_location('_speech_projection_fixture',path)
        projection=importlib.util.module_from_spec(spec);spec.loader.exec_module(projection)
        original=path.read_bytes();self.agent.tool_text_en=projection
        speech_bridge.install_projection(self.agent)
        tool={'name':'speak','description':speech_bridge.DESCRIPTION,'input_schema':{'type':'object'}}
        with patch.object(projection,'enabled',return_value=True):
            described=projection.apply([tool])[0]
            self.assertIn('asynchronously',described['description']);self.assertNotIn('outgoing-reply check',described['description'])
            self.assertIn('automatically',projection.pointer_purpose('speak','old'))
        with patch.object(projection,'enabled',return_value=False):
            self.assertIn('асинхронно',projection.apply([tool])[0]['description'])
        self.assertEqual(path.read_bytes(),original);self.assertEqual(described['input_schema'],tool['input_schema'])

if __name__=='__main__':unittest.main()
