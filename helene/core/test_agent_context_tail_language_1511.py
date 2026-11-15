"""Actual context bodies, media augmentation and model-loop language boundaries."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import wave

import agent
import frame_language as fl
import media
import people

LANGS=('ru','en','fr','zh')
RAW='Authored пусто 中文 Français {owner}'


class ContextTailLanguage(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.path=self.root/'helene.json'
        self.enterContext(patch.dict(os.environ,{'HELENE_CONFIG':str(self.path),
            'PRAXIS_DOSSIER_ALL':'1','PRAXIS_FRAME_HEAD_STABLE':'off','PRAXIS_FRAME_EPOCH':'off'}))
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.path.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def test_actual_dossier_files_keep_authored_bytes_and_identity_across16_axes(self):
        directory=self.root/'people';directory.mkdir()
        path=directory/'literal.md'
        body=f'# {RAW}\n\ntelegram_id: 123\n\n- {RAW}\n'
        path.write_text(body,encoding='utf-8');before=path.read_bytes();by_frame={}
        ctx=agent.ChannelContext(chat_id='123',principal_id='123',owner=True,is_dm=True)
        with patch.object(people,'PEOPLE_DIR',directory),patch.object(agent.frame_epoch,'active',return_value=False):
            for ui in LANGS:
                for frame in LANGS:
                    self.choose(ui,frame)
                    block=agent._participant_memory_block('forged name',ctx)
                    self.assertIn(RAW,block);self.assertIn('memory/people/literal.md',block)
                    self.assertIn('123',block);self.assertNotIn('forged name',block)
                    self.assertEqual(by_frame.setdefault(frame,block),block)
                    self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(set(by_frame.values())),4)

    def test_unknown_principal_is_named_without_promoting_a_display_name(self):
        directory=self.root/'people';directory.mkdir()
        (directory/'literal.md').write_text(f'# {RAW}\n\ntelegram_id: 123\n\n- {RAW}\n',encoding='utf-8')
        self.choose('zh','en')
        ctx=agent.ChannelContext(chat_id='999',principal_id='999',owner=True,is_dm=True)
        with patch.object(people,'PEOPLE_DIR',directory),patch.object(agent.frame_epoch,'active',return_value=False):
            block=agent._participant_memory_block(RAW,ctx)
        self.assertIn('NOT IDENTIFIED',block);self.assertIn('tg 999',block)
        self.assertIn('does not establish identity',block)

    def audio(self):
        data=io.BytesIO()
        with wave.open(data,'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(b'\0\0'*80)
        spool=media.MediaSpool(self.root/'spool')
        ref=spool.ingest_bytes(data.getvalue(),kind='audio',filename='literal.wav',scope='owner',chat_id='123',message_id=11)
        return spool,ref

    def test_actual_audio_transcript_is_literal_and_decoded_once_across16_axes(self):
        spool,ref=self.audio();before=ref.path.read_bytes();by_frame={}
        ctx=agent.ChannelContext(chat_id='123',owner=True,is_dm=True)
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame)
                with patch.object(agent,'_media_spool',return_value=spool),patch.object(agent.media_audio,'transcribe',return_value=RAW) as transcribe:
                    full,model=agent._media_prompt('FULL '+RAW,(ref,),ctx,model_text='MODEL '+RAW)
                transcribe.assert_called_once_with(ref.path)
                self.assertIn(RAW,full);self.assertIn(RAW,model)
                self.assertNotIn('FULL',model);self.assertNotIn('MODEL',full)
                caption=model.split('\n\n',1)[1]
                self.assertEqual(by_frame.setdefault(frame,caption),caption)
                self.assertEqual(ref.path.read_bytes(),before)
        self.assertEqual(len(set(by_frame.values())),4)

    def test_audio_language_is_pinned_before_transcriber_changes_settings(self):
        spool,ref=self.audio();self.choose('zh','en')
        def transcribe(path):self.choose('en','fr');return RAW
        with patch.object(agent,'_media_spool',return_value=spool),patch.object(agent.media_audio,'transcribe',side_effect=transcribe):
            _,model=agent._media_prompt(RAW,(ref,),agent.ChannelContext(chat_id='123',owner=True))
        self.assertIn('transcript',model);self.assertNotIn('transcription',model)
        self.assertEqual(fl.language(),'fr')

    def test_command_preflight_refusal_does_not_start_or_create_safety_point(self):
        for frame in LANGS:
            self.choose('zh',frame)
            with patch.object(agent.stewardship,'check',return_value=None),patch.object(agent,'_core_edit_check_on',return_value=False), \
                 patch.object(agent.selfgit,'safety_point',side_effect=AssertionError('late effect')) as safety:
                answer=agent._home_command('fixture',cwd=str(self.root/'missing'),program_argv=['literal-no-program'])
            self.assertIn(str(self.root/'missing'),answer);self.assertIn('"started":false',answer.replace(' ',''))
            safety.assert_not_called()

    def test_loop_observation_reaches_provider_without_changing_arguments_or_automatic_stop(self):
        for frame in LANGS:
            self.choose('zh',frame);messages=[{'role':'user','content':RAW}];calls=[];observed=[]
            def chat(*a,**kw):
                observed.append(copy.deepcopy(kw['messages']))
                if len(observed)<=3:
                    return SimpleNamespace(stop_reason='tool_use',text='',blocks=[{'type':'tool_use','id':str(len(observed)),'name':'probe','input':{'value':RAW}}])
                return SimpleNamespace(stop_reason='end_turn',text=RAW,blocks=[])
            def probe(**kw):calls.append(kw);self.choose('en','ru' if frame!='ru' else 'fr');return RAW
            with patch.object(agent.llm,'chat',side_effect=chat),patch.dict(agent.TOOL_IMPL,{'probe':probe}):
                answer=agent._terminal_tool_loop(system=RAW,messages=messages,tools=[{'name':'probe'}],max_iters=None)
            self.assertEqual(answer,RAW);self.assertEqual(calls,[{'value':RAW}]*3)
            text=fl.catalog(frame)['helper.agent._terminal_tool_loop.18011'].format(p0='probe',p1='3')
            self.assertIn(text,str(observed[-1]))
            self.assertEqual(len(observed),4)
