"""Actual async adapter schemas/results stay frame-local and keep literal call data."""
import copy
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
from localharness import speech_bridge
import agent
import frame_language as fl
import tool_language as tl
import tool_text_en
from test_general_tool_language_1511 import stock
from test_tool_language_1511 import structural

RAW='Literal Русский 中文 {id} </script>'


class SpeechLanguages(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.config=self.root/'helene.json';self.choose('en','ru')
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.config)))
        self.enterContext(patch.dict(tl._runtime,{},clear=True))

    def choose(self,ui,frame):
        self.config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf8')

    def install(self,tool=None):
        tool=copy.deepcopy(stock()['speak'] if tool is None else tool)
        fake=SimpleNamespace(FRAME_TEXT_BOUNDARIES_API=1,frame_language=fl,tool_language=tl,tool_text_en=tool_text_en,
            TOOLS=[tool],TOOL_IMPL={},tool_speak=lambda:None,_TURN_CHANNEL=ContextVar('language-speech',default=SimpleNamespace(chat_id='chat-literal')),
            run_context=SimpleNamespace(current_run=lambda:SimpleNamespace(run_id='run-literal')),_runs=lambda:None,
            current_tool_execution=lambda:{'call_id':'call-literal'})
        service=Mock();service.submit.return_value={'id':RAW,'status':'queued'}
        with patch.object(speech_bridge,'SpeechJobs',return_value=service):
            self.assertIs(speech_bridge.install(fake,self.root,self.config,boundary=object(),owner=object(),deliver=Mock()),service)
        return fake,service,tool

    def test_actual_async_schema_catalog_and_pointer_all16_axes_preserve_callable_shape(self):
        original=copy.deepcopy(stock()['speak']);before_projection=tool_text_en.apply
        fake,service,native=self.install();saved=copy.deepcopy(native)
        self.assertIs(tool_text_en.apply,before_projection)
        for ui in fl.LANGUAGES:
            for frame in fl.LANGUAGES:
                self.choose(ui,frame)
                projected=tl.apply([native],fallback=tool_text_en.apply)[0]
                self.assertEqual(projected['description'],fl.text('native.speech.description'))
                self.assertEqual(structural(projected),structural(original))
                self.assertEqual(agent._hand_purpose(projected),fl.text('native.speech.purpose'))
                with patch.object(agent,'BASE_TOOLS',[native]),patch.object(agent,'OWNER_TOOLS',[]),patch.object(agent,'SHARED_CONTEXT_TOOLS',[]):
                    actual={tool['name']:tool for tool in agent.catalog_tools_for(agent.ChannelContext(owner=True))}
                self.assertEqual(actual['speak'],projected)
                self.assertEqual(speech_bridge.turn_note(fake),fl.text('native.speech.turn_note'))
        self.assertEqual(native,saved);service.submit.assert_not_called()

    def test_authored_description_or_callable_change_is_not_replaced(self):
        for mutate in (lambda tool:tool.update(description=RAW),lambda tool:tool['input_schema'].update(required=['caption'])):
            original=copy.deepcopy(stock()['speak']);mutate(original);fake,service,native=self.install(original)
            self.assertEqual(native,original);self.assertIs(tl.apply([native],fallback=tool_text_en.apply)[0],native)
            self.assertIsNone(tl.purpose(native));service.submit.assert_not_called()

    def test_actual_submission_keeps_one_identity_payload_and_language_across_settings_change(self):
        fake,service,native=self.install();by_frame={}
        for ui in fl.LANGUAGES:
            for frame in fl.LANGUAGES:
                self.choose(ui,frame);service.submit.reset_mock()
                def admit(*args,**kwargs):
                    self.choose('zh','fr' if frame!='fr' else 'en');return {'id':RAW,'status':'queued'}
                service.submit.side_effect=admit
                answer=fake.tool_speak(RAW,RAW)
                service.submit.assert_called_once_with(RAW,RAW,identity=hashlib.sha256(b'run-literal:call-literal').hexdigest()[:32],run_id='run-literal',chat_id='chat-literal')
                with fl.use(frame):self.assertEqual(answer,fl.text('native.speech.accepted',id=RAW,status='queued'))
                self.assertEqual(by_frame.setdefault(frame,answer),answer);self.assertIn(RAW,answer)
        self.assertEqual(len(set(by_frame.values())),4)

    def test_missing_admission_and_failed_submission_never_claim_delivery(self):
        fake,service,native=self.install()
        for frame in fl.LANGUAGES:
            self.choose('en',frame);service.submit.reset_mock()
            with patch.object(fake,'current_tool_execution',return_value={}):
                self.assertEqual(fake.tool_speak(RAW),fl.text('native.speech.missing'))
            service.submit.assert_not_called();service.submit.side_effect=OSError('RAW diagnostic')
            self.assertEqual(fake.tool_speak(RAW),fl.text('native.speech.refused',error='OSError'))
            service.submit.assert_called_once()


if __name__=='__main__':unittest.main()
