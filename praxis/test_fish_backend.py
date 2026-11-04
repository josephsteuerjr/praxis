"""Provider snapshots, intonation provenance and duplicate-cost prevention."""
from dataclasses import replace
import json,os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fish_tts_client import FishProfile,FishTTSClient,render_speech
import speech_backend
from speech_backend import SpeechStore
from xtts_generations import Generations,process_owner
from xtts_protocol import XttsError
from test_fish_tts import Response,ogg_fixture


class BackendCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='fish-backend-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.env=mock.patch.dict(os.environ,{'PRAXIS_BASE':str(self.base)});self.env.start();self.addCleanup(self.env.stop)
        self.legacy=mock.patch('xtts_tts_client.selected',return_value={'model_id':'a'*64,'profile_id':'b'*64,'enabled':True})
        self.legacy.start();self.addCleanup(self.legacy.stop)
        self.store=SpeechStore()
        self.profile=FishProfile('test-voice',model='drama-3')

    def select(self,identity='a'*32,**kwargs):
        return self.store.mutate(provider='fish',profile=self.profile,request_id=identity,actor='owner:test',**kwargs)

    def test_absence_keeps_exact_previous_xtts_selection(self):
        self.assertEqual(speech_backend.selected(),{'model_id':'a'*64,'profile_id':'b'*64,'enabled':True})
        self.assertFalse((self.store.root/'selection.json').exists())

    def test_explicit_selection_is_durable_and_replays_without_new_revision(self):
        first=self.select(expected_revision='legacy-xtts')
        self.assertEqual(self.select(expected_revision='legacy-xtts'),first)
        self.assertEqual(speech_backend.selected()['profile_id'],self.profile.profile_id)
        self.assertEqual(len((self.store.root/'events.jsonl').read_text().splitlines()),1)

    def test_same_key_different_voice_is_conflict(self):
        self.select()
        with self.assertRaisesRegex(XttsError,'voice_conflict'):
            self.store.mutate(provider='fish',profile=replace(self.profile,reference_id='other'),
                              request_id='a'*32,actor='owner:test')

    def test_stale_revision_does_not_overwrite_selection(self):
        first=self.select()
        with self.assertRaisesRegex(XttsError,'config_changed'):
            self.store.mutate(provider='xtts',expected_revision='legacy-xtts',request_id='b'*32,actor='owner:test')
        self.assertEqual(self.store.state()['revision'],first['revision'])

    def test_missing_operation_receipt_is_repaired_from_atomic_selection(self):
        first=self.select();path=self.store.root/'operations'/('a'*32+'.json');path.unlink()
        self.assertEqual(self.store.state()['revision'],first['revision'])
        self.assertTrue(path.is_file())
        self.assertEqual(self.select(),first)

    def test_missing_audit_after_configuration_commit_is_repaired_once(self):
        self.select();path=self.store.root/'events.jsonl';path.unlink()
        self.store.state();self.store.state()
        self.assertEqual(len(path.read_text().splitlines()),1)

    def test_legacy_engine_cannot_silently_ignore_requested_intonation(self):
        g=Generations(self.base/'gen')
        with self.assertRaisesRegex(XttsError,'expressive_speech_requires_fish'):
            g.submit('Привет.',delivery='Тепло.',run_id='r',call_id='c',
                     channel={'chat_id':'owner','scope':'owner'},selection={'model_id':'a'*64,'profile_id':'b'*64})

    def test_pause_and_resume_preserve_voice_and_model(self):
        first=self.select()
        paused=self.store.mutate(enabled=False,request_id='b'*32,actor='owner:test')
        self.assertIsNone(speech_backend.selected())
        resumed=self.store.mutate(enabled=True,request_id='c'*32,actor='owner:test')
        self.assertEqual(resumed['profile_id'],first['profile_id'])
        self.assertEqual(paused['model'],'drama-3')

    def test_old_profiles_keep_original_voice_after_new_selection(self):
        first=self.select()
        self.store.mutate(provider='fish',profile=replace(self.profile,reference_id='other'),
                          request_id='b'*32,actor='owner:test')
        client=speech_backend.client_for({'provider':'fish',**self.profile.selection()},self.base)
        self.assertEqual(client.profile.reference_id,'test-voice')
        self.assertEqual(client.profile.profile_id,first['profile_id'])

    def test_old_request_stays_xtts_even_when_fish_is_selected(self):
        self.select()
        with mock.patch('xtts_tts_client.XttsTTSClient') as cls:
            speech_backend.client_for({'model_id':'a'*64,'profile_id':'b'*64},self.base)
            cls.assert_called_once()

    def test_explicit_global_pause_stops_an_already_created_fish_client(self):
        self.select();client=speech_backend.client_for(self.profile.selection(),self.base)
        self.store.mutate(enabled=False,request_id='b'*32,actor='owner:test')
        g=Generations(self.base/'gen')
        value=g.submit('Привет.',run_id='r',call_id='c',channel={'chat_id':'owner','scope':'owner'},selection=self.profile.selection())
        with mock.patch.object(client.client.opener,'open') as provider:
            with self.assertRaisesRegex(XttsError,'cancelled'):
                client.stream_intent(value['request'],self.base/'paused.ogg')
            provider.assert_not_called()

    def test_corrupt_profile_never_selects_default_provider(self):
        self.select();(self.store.root/'profiles'/(self.profile.profile_id+'.json')).write_text('{}')
        with self.assertRaises(XttsError):speech_backend.selected()

    def test_xtts_native_pause_is_reflected_in_shared_state(self):
        self.store.mutate(provider='xtts',request_id='a'*32,actor='owner:test')
        with mock.patch('xtts_tts_client.selected',return_value=None):
            self.assertFalse(self.store.state()['enabled'])
            self.assertIsNone(speech_backend.selected())

    def test_original_words_and_direction_are_distinct_and_replay_conflicts(self):
        self.select();g=Generations(self.base/'gen')
        args={'run_id':'r','call_id':'c','channel':{'chat_id':'owner','scope':'owner'},'selection':self.profile.selection()}
        value=g.submit('Точные слова.',delivery='Тепло, последнюю фразу тише.',**args)
        self.assertEqual(value['request']['text'],'Точные слова.')
        self.assertEqual(value['request']['delivery'],'Тепло, последнюю фразу тише.')
        with self.assertRaisesRegex(XttsError,'generation_conflict'):
            g.submit('Точные слова.',delivery='Холодно и громко.',**args)

    def test_provider_receives_rendered_direction_but_never_origin(self):
        self.select();g=Generations(self.base/'gen')
        value=g.submit('Привет.',delivery='Тепло.',run_id='r',call_id='c',
            channel={'chat_id':'owner','scope':'owner'},selection=self.profile.selection())
        key=self.base/'key';key.write_text('offline-token');key.chmod(0o600)
        transport=mock.Mock();transport.open.side_effect=lambda *a,**k:Response(ogg_fixture())
        client=FishTTSClient(self.base,self.profile,key_file=key,opener=transport)
        result=g.execute(value['generation_id'],client,owner=process_owner())
        self.assertEqual(result['state'],'ready')
        self.assertEqual(json.loads(transport.open.call_args.args[0].data)['text'],'[Тепло.] Привет.')
        self.assertEqual(result['result']['provider'],'fish')
        self.assertEqual(result['result']['provider_model'],'drama-3')
        self.assertEqual(g.observation(result)['performance_direction'],'Тепло.')
        self.assertEqual(g.observation(result)['delivery'],'not_requested_by_generation')

    def test_send_intent_cannot_change_direction_or_provider(self):
        from xtts_stream_upload import verify_request
        self.select();g=Generations(self.base/'gen')
        value=g.submit('Привет.',delivery='Тепло.',run_id='r',call_id='c',
            channel={'chat_id':'owner','scope':'owner'},selection=self.profile.selection())
        request={**value['request'],'generation_id':value['generation_id']}
        verify_request(request,value)
        for field,new in [('provider','xtts'),('delivery','Холодно.')]:
            with self.assertRaisesRegex(XttsError,'generation_authority_mismatch'):
                verify_request({**request,field:new},value)

    def test_direction_is_free_form_and_inline_script_is_preserved(self):
        script='[вздох] Ну что ж. <soft>Привет.</soft>'
        self.assertEqual(render_speech(script,'с лёгкой иронией, замедляясь'),'[с лёгкой иронией, замедляясь] '+script)


if __name__=='__main__':unittest.main()
