"""Authenticated expressive preview controls, no real provider or Telegram calls."""
from pathlib import Path
import json,os,tempfile,time
import unittest
from unittest import mock

from fish_tts_client import FishProfile,FishTTSClient
from fish_voice_ui import FishVoiceUI
from test_fish_tts import Response,ogg_fixture


class UICase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='fish-ui-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        env=mock.patch.dict(os.environ,{'PRAXIS_BASE':str(self.root)});env.start();self.addCleanup(env.stop)
        legacy=mock.patch('xtts_tts_client.selected',return_value=None);legacy.start();self.addCleanup(legacy.stop)
        key=self.root/'memory/.secrets/fish/api-key';key.parent.mkdir(parents=True);key.write_text('offline-ui-token');key.chmod(0o600)
        self.transport=mock.Mock();self.transport.open.side_effect=lambda *a,**k:Response(ogg_fixture())
        self.factory=lambda p:FishTTSClient(self.root,p,key_file=key,opener=self.transport)
        self.ui=FishVoiceUI(self.root,self.root/'preview-state',client_factory=self.factory)
        self.addCleanup(self.ui.close)
        self.body={'request_id':'c'*32,'voice_id':'test-voice','model':'drama-3','text':'Привет.',
                   'delivery':'Тепло, с улыбкой.'}

    def wait(self,identity):
        end=time.monotonic()+3
        while time.monotonic()<end:
            result=self.ui.job(identity)
            if result['state'] not in {'queued','running'}:return result
            time.sleep(.01)
        self.fail('preview did not finish')

    def test_private_api_rejects_other_roles_before_provider_contact(self):
        for role in ('guest','public','family',''):
            self.assertEqual(self.ui.dispatch('submit',{},self.body,role),{'ok':False,'error':'unauthorized'})
        self.transport.open.assert_not_called()

    def test_preview_is_expressive_but_never_changes_selection(self):
        result=self.ui.dispatch('submit',{},self.body,'owner');self.assertTrue(result['ok'])
        result=self.wait(self.body['request_id']);self.assertEqual(result['state'],'ready')
        self.assertEqual(result['delivery'],'preview_only_no_telegram')
        self.assertEqual(json.loads(self.transport.open.call_args.args[0].data)['text'],'[Тепло, с улыбкой.] Привет.')
        self.assertFalse((self.root/'memory/.state/speech/selection.json').exists())
        audio=self.ui.dispatch('audio',{'voice_job':self.body['request_id']},None,'device')
        self.assertTrue(audio['ok']);self.assertEqual(audio['mime'],'audio/ogg')

    def test_same_id_after_ui_restart_reuses_saved_audio_without_second_charge(self):
        self.ui.submit(self.body,'owner');self.wait(self.body['request_id']);self.ui.close()
        other=FishVoiceUI(self.root,self.root/'preview-state',client_factory=self.factory);self.addCleanup(other.close)
        self.assertEqual(other.submit(self.body,'owner')['state'],'ready')
        self.assertEqual(self.transport.open.call_count,1)

    def test_changed_direction_with_same_id_is_rejected(self):
        self.ui.submit(self.body,'owner');self.wait(self.body['request_id'])
        result=self.ui.dispatch('submit',{},dict(self.body,delivery='Холодно.'),'owner')
        self.assertEqual(result,{'ok':False,'error':'generation_conflict'})
        self.assertEqual(self.transport.open.call_count,1)

    def test_unknown_paid_preview_is_not_retried_on_repeat(self):
        self.transport.open.side_effect=TimeoutError()
        self.ui.submit(self.body,'owner');self.assertEqual(self.wait(self.body['request_id'])['state'],'unknown')
        self.assertEqual(self.ui.submit(self.body,'owner')['state'],'unknown')
        self.assertEqual(self.transport.open.call_count,1)

    def test_selection_and_pause_are_independent_of_preview(self):
        body={'request_id':'a'*32,'provider':'fish','voice_id':'test-voice','model':'drama-3',
              'enabled':True,'expected_revision':'legacy-xtts'}
        selected=self.ui.dispatch('selection',{},body,'device');self.assertTrue(selected['ok'])
        paused=self.ui.dispatch('selection',{}, {'request_id':'b'*32,'provider':'fish','enabled':False,
                                                'expected_revision':selected['revision']},'owner')
        self.assertTrue(paused['ok']);self.assertFalse(paused['enabled'])
        self.assertEqual(paused['reference_id'],'test-voice')
        self.transport.open.assert_not_called()

    def test_malformed_body_does_not_raise_or_call_provider(self):
        self.assertFalse(self.ui.dispatch('submit',{},[], 'owner')['ok'])
        self.assertFalse(self.ui.dispatch('catalog',{'catalog_page':'nope'},None,'owner')['ok'])
        self.transport.open.assert_not_called()


if __name__=='__main__':unittest.main()
