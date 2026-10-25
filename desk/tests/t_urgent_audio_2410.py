"""Urgent audio boundaries: real producer/idle/replay/route; no agent or subprocess."""
import asyncio
from contextlib import nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import deskapp
import runner
import voice_admission as va

spec=importlib.util.spec_from_file_location('urgent_media_audio',DESK.parent/'helene/core/media_audio.py')
audio=importlib.util.module_from_spec(spec);sys.modules[spec.name]=audio;spec.loader.exec_module(audio)

class RecognitionOnly(unittest.TestCase):
    def test_disabled_unsupported_and_malformed_synthesis_do_not_block_stt(self):
        for env in ({'PRAXIS_TTS_BACKEND':'disabled'}, {'PRAXIS_TTS_BACKEND':'unknown-backend'},
                    {'PRAXIS_EDGE_TIMEOUT':'bad','PRAXIS_TTS_MAX_CHARS':'bad','PRAXIS_PIPER_USE_CUDA':'bad'}):
            with self.subTest(env=env),patch.dict(os.environ,env,clear=True):
                backend=audio.MediaAudio(stt=SimpleNamespace(transcribe=lambda p:'слова'))
                self.assertEqual(backend.transcribe('unused'),'слова')
                with self.assertRaises(audio.AudioConfigurationError):backend.synthesize('речь')

    def test_explicit_tts_object_is_not_initialized_by_recognition(self):
        calls=[]
        with patch.dict(os.environ,{'PRAXIS_TTS_BACKEND':'disabled'},clear=True):
            b=audio.MediaAudio(stt=SimpleNamespace(transcribe=lambda p:'ок'),tts=SimpleNamespace(synthesize=lambda t:calls.append(t)))
            self.assertEqual(b.transcribe('unused'),'ок');self.assertEqual(calls,[])
            b.synthesize('ответ');self.assertEqual(calls,['ответ'])

    def test_apply_stt_does_not_even_inspect_broken_synthesis(self):
        from localharness import voice
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);model=tree/'model';model.mkdir()
            with patch.object(voice,'library',return_value={'present':True}), \
                 patch.object(voice,'manifest',return_value={'model':'turbo','path':str(model)}), \
                 patch.object(voice,'speech_state',side_effect=AssertionError('TTS inspection during recognition')), \
                 patch.dict(os.environ,{},clear=True):
                said=voice.apply_stt(tree,{'voice':{'enabled':True,'model':'turbo'}})
                self.assertTrue(said['ready']);self.assertNotIn('speech',said)
                self.assertEqual(os.environ['PRAXIS_STT_MODEL'],str(model))

class Admission(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tree=Path(self.tmp.name);self.inbox=self.tree/'memory/.control/desk_inbox'
        self.files=[{'name':'voice-test.webm','mime':'audio/webm','data':b'local synthetic audio'},
                    {'name':'report.txt','mime':'text/plain','data':b'report'}]
        self.paths=deskapp._write_attachments(self.inbox,'stamp',self.files)
        self.audio=self.inbox/self.paths[0]
        self.note='[вложения]\n'+''.join('- '+p+' · '+f['mime']+' · 12\n' for p,f in zip(self.paths,self.files))
        for p in (patch.object(runner,'_tree',self.tree),patch.object(runner,'_voice_config_path',None),
                  patch.object(runner,'_voice_signature','startup'),patch.object(runner,'_voice_state',{'ready':True}),
                  patch.object(runner,'_low_priority',nullcontext),
                  patch.dict(sys.modules,media_audio=SimpleNamespace(transcribe=lambda p:'Не устанавливать'))):
            p.start();self.addCleanup(p.stop)

    def test_payload_names_cannot_collide_with_generated_metadata_in_either_order(self):
        from deskd import artifacts
        names=['report.txt.media.json','report.txt','report.txt.transcription.json','.helene-media-state']
        for index,order in enumerate((names,list(reversed(names)))):
            files=[{'name':name,'mime':'application/octet-stream','data':('payload:'+name).encode()} for name in order]
            paths=deskapp._write_attachments(self.inbox,'collision-'+str(index),files)
            for relative,item in zip(paths,files):
                path=self.inbox/relative
                self.assertEqual(path.read_bytes(),item['data'])
                self.assertEqual(artifacts.metadata(self.tree,path.relative_to(self.tree).as_posix())['media_mime'],'application/octet-stream')

    def test_producer_mime_preserves_video_and_identifies_voice(self):
        self.assertTrue(va.is_audio(self.audio))
        p=self.audio.parent/'video.webm';p.write_bytes(b'video')
        from localharness.media_metadata import path_for
        path_for(p,'mime.json').write_text('{"mime":"video/webm"}')
        self.assertFalse(va.is_audio(p))

    def test_failure_ready_retry_and_hash_change(self):
        calls=[]
        def bad(p):calls.append(1);raise RuntimeError('ASR unavailable')
        with self.assertRaises(va.VoicePending):va.resolve(self.audio,bad,signature='one')
        with self.assertRaises(va.VoicePending):va.resolve(self.audio,bad,signature='one')
        self.assertEqual(len(calls),1)
        va.retry(self.audio)
        self.assertIn('слова',va.resolve(self.audio,lambda p:'слова',signature='one'))
        self.assertIn('слова',va.resolve(self.audio,bad,signature='one'))
        self.audio.write_bytes(b'new source')
        self.assertIn('новое',va.resolve(self.audio,lambda p:'новое',signature='one'))

    def test_idle_failure_never_reaches_life_or_model_and_success_has_no_raw_voice_files(self):
        life=[];model=[]
        desk=SimpleNamespace(archive=lambda *a,**k:None,life=lambda text,**k:life.append(text))
        def turn(*a,**kw):model.append((kw['origin_text'],runner._INITIAL_FILES.get()))
        with patch.object(runner,'_room',lambda room:desk),patch.object(runner,'_turn_in_window',turn):
            with patch.object(runner,'_voice_state',{'ready':False,'why':'backend down'}):
                with self.assertRaises(va.VoicePending):runner.handle_desk('',attachments=self.paths,ingress_id='note:one')
            self.assertEqual(life,[]);self.assertEqual(model,[])
            va.retry(self.audio)
            runner.handle_desk('',attachments=self.paths,ingress_id='note:one')
        self.assertIn('[Транскрипт голосового]: Не устанавливать',model[0][0])
        self.assertEqual(model[0][1]['paths'],[self.paths[1]])
        self.assertNotIn('backend down',life[0]);self.assertTrue(self.audio.is_file())

    def test_expired_pending_audio_and_note_are_retained_but_terminal_is_collected(self):
        processed=self.inbox/'processed';processed.mkdir();n=processed/'001.md';n.write_text(self.note,encoding='utf8')
        old=time.time()-30*86400
        for p in [n,self.audio.parent]:os.utime(p,(old,old))
        runner._sweep_processed(processed)
        self.assertTrue(n.is_file());self.assertTrue(self.audio.is_file())
        d=Path(str(n)+'.done');d.write_text('done');os.utime(d,(old,old))
        runner._sweep_processed(processed)
        self.assertFalse(n.exists());self.assertFalse(self.audio.exists())

    def test_replay_addressed_note_keeps_attachments_and_identity(self):
        processed=self.inbox/'processed';processed.mkdir();n=processed/'001__to__123.md';n.write_text(self.note,encoding='utf8')
        got=[]
        with patch.object(runner,'_seal_claim',lambda *a,**kw:(True,'')),patch.object(runner,'_inbox_target',lambda s:'123'),patch.object(runner,'_archive_inputs',lambda *a:None),patch.object(runner,'handle_owner_note',lambda *a,**kw:got.append((a,kw))):
            runner._replay_unclaimed_notes(processed)
        self.assertEqual(got[0][1],{'attachments':self.paths,'ingress_id':'note:001__to__123'})

    def test_status_and_retry_real_route_confines_original_source(self):
        from localharness import voice_admission
        source='memory/.control/desk_inbox/'+self.paths[0]
        voice_admission._write(self.audio,{'status':'error','error':'ASR offline'})
        with patch.object(deskapp.readers,'tree',lambda:self.tree):
            value=asyncio.run(deskapp._r_voice_attachment(SimpleNamespace(body={},query={'path':source})))
            self.assertTrue(value['can_retry']);self.assertEqual(value['status'],'error')
            value=asyncio.run(deskapp._r_voice_attachment(SimpleNamespace(body={'path':source},query={})))
            self.assertTrue(value['retry_requested'])
            with self.assertRaises(ValueError):asyncio.run(deskapp._r_voice_attachment(SimpleNamespace(body={},query={'path':'soul/private.md'})))
        self.assertIn('/api/voice/attachment',deskapp._DEVICE_PATHS)

    def test_old_failed_telegram_voice_does_not_block_new_text_or_trust_legacy_cache(self):
        base=self.tree/'memory/telegram-media';base.mkdir();p=base/'old.ogg';p.write_bytes(b'old')
        p.with_suffix('.ogg.transcript.json').write_text(json.dumps({'text':'[голосовое не расшифровано: broken TTS]'}))
        archive=self.tree/'memory/groups';archive.mkdir()
        rows=[{'media_kind':'audio','media_path':'memory/telegram-media/old.ogg'}, {'text':'новый вопрос'}]
        (archive/'123.jsonl').write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf8')
        with patch.object(runner,'_agent',SimpleNamespace(_media_spool=lambda:SimpleNamespace(max_turn_media=4))),patch.object(runner,'_last_n',lambda:20),patch.object(runner,'_transcribe_path',side_effect=AssertionError('historical inference forbidden')):
            refs,notes=runner._telegram_media_refs('123',SimpleNamespace(scope='owner',chat_id='123'))
        self.assertEqual(refs,[]);self.assertEqual(notes,[])

if __name__=='__main__':unittest.main()
