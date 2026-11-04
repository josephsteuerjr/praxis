from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import threading
import time
import unittest
import uuid
import wave

import numpy as np

from xtts_engine import XttsEngine
from xtts_profiles import VoiceStore
from xtts_protocol import XttsError, file_digest
from xtts_service import SpeechServer
from xtts_tts_client import XttsTTSClient


class Chunk:
    def detach(self):return self
    def cpu(self):return self
    def numpy(self):return np.full(2400,.2,dtype=np.float32)


class Model:
    def __init__(self):
        self.speaker_manager=SimpleNamespace(speakers={name:{'gpt_cond_latent':name,'speaker_embedding':name} for name in ['Alice','Beatrice']})
        self.tokenizer=SimpleNamespace(encode=lambda text,lang:list(text))
        self.args=SimpleNamespace(gpt_max_text_tokens=400)
        self.slow=False
    def get_conditioning_latents(self,**kwargs):return ('reference','embedding')
    def inference_stream(self,*args,**kwargs):
        for _ in range(200 if self.slow else 1):
            if self.slow:time.sleep(.02)
            yield Chunk()


def reference(path):
    with wave.open(str(path),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000)
        audio.writeframes(b'\1\0'*24000*4)
    return path


def decoder(source,destination,stopped):
    if Path(source).read_bytes()[:4]!=b'RIFF':raise XttsError('invalid_reference')
    if stopped():raise XttsError('cancelled')
    reference(destination)
    return 4.0


def encoder(source,destination,stopped):
    if stopped():raise XttsError('cancelled')
    destination.write_bytes(b'OggS'+b'\0'*20+b'OpusHead'+b'\0'*90)


class VoiceRPC(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='voices-')
        self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.model=Model()
        self.engine=XttsEngine(self.root/'model',self.root/'component/profile.json')
        self.engine.engine=self.model;self.engine.weights_ready=True
        self.engine.model_id='a'*64;self.engine.runtime_id='b'*64;self.engine.phase='awaiting_voice'
        self.store=VoiceStore(self.root/'component',self.root/'state',decoder=decoder)
        self.token=self.root/'token';self.token.write_text('c'*64)
        self.server=SpeechServer(self.root/'socket','c'*64,self.engine,scratch=self.root/'scratch',encoder=encoder,voice_store=self.store)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.client=XttsTTSClient(self.root/'out',socket_path=self.root/'socket',token_file=self.token,timeout=5)
        self.source=reference(self.root/'reference.wav')

    def close(self):
        self.server.close_requests();self.server.shutdown();self.server.server_close();self.thread.join(2)

    def voices(self):return self.client.voices()['voices']

    def select(self,voice):
        return self.client.command('voice_select',{'voice_id':voice['id'],'profile_id':voice['profile_id'],'model_id':voice['model_id']},actor='owner:miniapp')

    def test_stale_selection_retry_cannot_enable_after_owner_off(self):
        voice=self.voices()[0]
        revision=self.client.voices()['selection_revision']
        operation=uuid.uuid4().hex
        values={'voice_id':voice['id'],'profile_id':voice['profile_id'],'model_id':voice['model_id'],
                'operation_id':operation,'expected_revision':revision}
        self.client.command('voice_select',values,actor='owner-device:miniapp')
        self.client.command('voice_select',values,actor='owner-device:miniapp')
        self.client.command('voice_enabled',{'enabled':False},actor='owner:miniapp')
        with self.assertRaisesRegex(XttsError,'config_changed'):
            self.client.command('voice_select',values,actor='owner-device:miniapp')
        self.assertFalse(self.store.selection()['enabled'])

    def test_fresh_upload_attempt_keeps_canonical_profile_after_cancel(self):
        identity=uuid.uuid4().hex
        poisoned=uuid.uuid4().hex
        self.client.cancel(poisoned)
        with self.assertRaisesRegex(XttsError,'cancelled'):
            self.client.add_voice(self.source,'Voice',request_id=identity,attempt_id=poisoned)
        one=self.client.add_voice(self.source,'Voice',request_id=identity,attempt_id=uuid.uuid4().hex)
        two=self.client.add_voice(self.source,'Voice',request_id=identity,attempt_id=uuid.uuid4().hex)
        self.assertEqual(one['voice_id'],two['voice_id'])
        self.assertEqual(one['voice_id'],'clone-'+identity)

    def intent(self,voice,**extra):
        return {'request_id':uuid.uuid4().hex,'text':'Точная реплика.','profile_id':voice['profile_id'],'model_id':voice['model_id'],**extra}

    def test_preview_does_not_select_or_enable_voice_and_reuses_conditioning(self):
        voice=self.voices()[0]
        self.assertEqual(len(self.voices()),2)
        for _ in range(2):
            path,meta=self.client.preview(voice,'Проба голоса.')
            self.assertTrue(path.read_bytes().startswith(b'OggS'));path.unlink()
            self.assertEqual(meta['profile_id'],voice['profile_id'])
        self.assertFalse(self.store.selection()['enabled'])
        self.assertEqual(self.engine.profile_id,'')
        self.assertEqual(self.engine.conditioning_loads,1)

    def test_choice_is_explicit_and_old_admitted_voice_keeps_its_profile(self):
        old,new=self.voices()
        self.select(old)
        old_intent=self.intent(old)
        self.select(new)
        path,meta=self.client.synthesize_intent(old_intent)
        self.assertEqual(meta['profile_id'],old['profile_id']);path.unlink()
        self.assertEqual(self.store.selection()['voice_id'],new['id'])
        self.assertTrue(self.store.selection()['enabled'])

    def test_upload_is_idempotent_and_does_not_change_selected_voice(self):
        identity=uuid.uuid4().hex
        for _ in range(2):
            result=self.client.add_voice(self.source,'Мой голос',request_id=identity)
            self.assertEqual(result['voice_id'],'clone-'+identity)
        self.assertEqual(len(self.voices()),3)
        self.assertFalse(self.store.selection()['enabled'])
        self.assertEqual(self.store.spec(result['voice_id'])['reference_sha256'],file_digest(self.store.root/(result['voice_id']+'.wav')))

    def test_active_clone_can_be_erased_after_disabling_and_late_create_cannot_resurrect(self):
        identity=uuid.uuid4().hex
        key=self.client.add_voice(self.source,'Образец',request_id=identity)['voice_id']
        voice=next(v for v in self.voices() if v['id']==key)
        self.select(voice)
        with self.assertRaisesRegex(XttsError,'active_voice'):self.client.command('voice_delete',{'voice_id':key})
        self.client.command('voice_enabled',{'enabled':False})
        self.client.command('voice_delete',{'voice_id':key})
        self.assertFalse((self.store.root/(key+'.wav')).exists())
        tombstone=json.loads((self.store.root/(key+'.json')).read_bytes())
        self.assertTrue(tombstone['deleted']);self.assertNotIn('name',tombstone)
        self.assertEqual(self.engine.profile_id,'')
        with self.assertRaisesRegex(XttsError,'voice_deleted'):
            self.client.add_voice(self.source,'Образец',request_id=identity)

    def test_reference_changed_in_place_cannot_silently_change_voice(self):
        key=self.client.add_voice(self.source,'Образец')['voice_id']
        voice=next(v for v in self.voices() if v['id']==key)
        (self.store.root/(key+'.wav')).write_bytes(b'changed')
        with self.assertRaisesRegex(XttsError,'reference_changed'):self.client.preview(voice,'Проба.')

    def test_disabled_speech_is_rejected_but_preview_is_available(self):
        voice=self.voices()[0]
        with self.assertRaisesRegex(XttsError,'voice_disabled'):self.client.synthesize_intent(self.intent(voice))
        path,_=self.client.preview(voice,'Проба.');path.unlink()

    def test_cancellation_tombstone_does_not_poison_a_new_durable_attempt(self):
        voice=self.voices()[0];self.select(voice)
        canonical=self.intent(voice)
        first=uuid.uuid4().hex
        self.client.cancel(first)
        with self.assertRaisesRegex(XttsError,'cancelled'):
            self.client.synthesize_intent({**canonical,'attempt_id':first})
        second=uuid.uuid4().hex
        path,meta=self.client.synthesize_intent({**canonical,'attempt_id':second})
        self.assertEqual(meta['request_id'],canonical['request_id'])
        self.assertEqual(meta['transport_request_id'],second);path.unlink()

    def test_disable_is_responsive_while_inference_and_blocks_late_audio(self):
        voice=self.voices()[0];self.select(voice);self.model.slow=True
        result={}
        def synthesize():
            try:self.client.synthesize_intent(self.intent(voice));result['unexpected']=True
            except XttsError as error:result['error']=error.code
        thread=threading.Thread(target=synthesize);thread.start()
        deadline=time.monotonic()+2
        while not self.client.health()['busy'] and time.monotonic()<deadline:time.sleep(.01)
        before=time.monotonic();self.client.command('voice_enabled',{'enabled':False})
        self.assertLess(time.monotonic()-before,1)
        thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(result,{'error':'cancelled'})
        self.assertFalse(list((self.root/'out').glob('*')))

    def test_invalid_upload_and_path_do_not_leave_voice_files(self):
        bad=self.root/'bad.wav';bad.write_bytes(b'not audio')
        with self.assertRaisesRegex(XttsError,'invalid_reference'):self.client.add_voice(bad,'Проба')
        with self.assertRaisesRegex(XttsError,'unknown_voice'):self.client.command('voice_delete',{'voice_id':'../../etc/passwd'})
        self.assertFalse(list(self.store.root.glob('clone-*.wav')))
        self.assertFalse(list((self.root/'scratch').glob('voice-upload-*')))


if __name__=='__main__':unittest.main()
