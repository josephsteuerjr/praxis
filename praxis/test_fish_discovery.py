"""Provider-wide discovery and share-link resolution without synthesis or mutation."""
from pathlib import Path
import json,tempfile,unittest,urllib.parse
from unittest import mock
from fish_tts_client import FishTTSClient,FishError
from fish_voice_ui import FishVoiceUI
from test_fish_tts import Response

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.base=Path(folder.name)
        key=self.base/'key';key.write_text('offline-discovery-key');key.chmod(0o600)
        self.transport=mock.Mock()
        self.client=FishTTSClient(self.base,key_file=key,opener=self.transport)
        self.identity='a2b3c4d5'*4
        self.model={'_id':self.identity,'title':'Voice outside first page','description':'Conversational',
                    'languages':['en','ru'],'tags':['Female','Calm'],'licensed':True,
                    'author':{'nickname':'Creator'},'task_count':21}

    def response(self,value):self.transport.open.side_effect=lambda *args,**kwargs:Response(json.dumps(value).encode())

    def test_search_filters_reach_provider_and_preserve_actual_window_metadata(self):
        data={'items':[self.model],'has_more':True,'total':1000,'total_is_exact':False,'window_limited':True}
        self.response(data)
        result=self.client.voices(own=False,language=None,page=3,title='Warm & natural',tag='Female, Calm',sort_by='task_count',licensed=True)
        request=self.transport.open.call_args.args[0]
        params=urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(params['title'],['Warm & natural']);self.assertEqual(params['tag'],['Female','Calm'])
        self.assertEqual(params['page_number'],['3']);self.assertEqual(params['sort_by'],['task_count'])
        self.assertEqual(params['licensed'],['true']);self.assertNotIn('language',params)
        self.assertIsNone(request.data,'search cannot synthesize')
        for key in ('has_more','total','total_is_exact','window_limited'):self.assertEqual(result[key],data[key])
        self.assertEqual(result['voices'][0]['description'],self.model['description'])

    def test_bad_filters_fail_before_network(self):
        for args in ({'page':False},{'page':0},{'title':[]},{'tag':None},{'licensed':'true'},
                     {'sort_by':'random'},{'language':'ru&self=true'}):
            with self.subTest(args=args),self.assertRaises(FishError):self.client.voices(**args)
        self.transport.open.assert_not_called()

    def test_id_and_real_share_link_only_contact_fixed_api(self):
        self.response(self.model)
        for ref in (self.identity,self.identity.upper(),'https://fish.audio/m/'+self.identity+'/voice-name?share=1',
                    'https://beta.fish.audio/m/'+self.identity+'/',
                    'https://fish.audio/app/m/'+self.identity+'/',
                    'https://fish.audio/zh-CN/app/m/'+self.identity+'/'):
            self.assertEqual(self.client.voice(ref)['_id'],self.identity)
            request=self.transport.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.fish.audio/model/'+self.identity)
            self.assertIsNone(request.data)

    def test_arbitrary_urls_and_invalid_ids_are_not_fetched(self):
        for ref in ('http://fish.audio/m/'+self.identity,'https://fish.audio.attacker.test/m/'+self.identity,
                    'https://user:password@fish.audio/m/'+self.identity,'https://fish.audio:444/m/'+self.identity,
                    'https://127.0.0.1/model/'+self.identity,'file:///etc/passwd','../model/private','not-an-id',None):
            with self.subTest(ref=ref),self.assertRaisesRegex(FishError,'fish_invalid_reference'):self.client.voice(ref)
        self.transport.open.assert_not_called()

    def test_resolved_model_identity_must_match_requested_id(self):
        self.response(dict(self.model,_id='b'*32))
        with self.assertRaisesRegex(FishError,'fish_invalid_response'):self.client.voice(self.identity)

    def test_authenticated_search_and_resolution_never_change_selection_or_generate(self):
        ui=FishVoiceUI(self.base,self.base/'ui',client_factory=lambda _:self.client);self.addCleanup(ui.close)
        self.response({'items':[self.model],'has_more':False})
        body={'query':'natural','language':'en','page':2,'model':'drama-3','tag':'Calm','sort_by':'score','licensed':False}
        result=ui.dispatch('search',{},body,'device');self.assertTrue(result['ok'])
        card=result['voices'][0];self.assertEqual(card['author'],'Creator');self.assertEqual(card['tags'],['Female','Calm'])
        self.response(self.model)
        self.assertEqual(ui.dispatch('resolve',{}, {'reference':self.identity},'owner')['voice']['id'],self.identity)
        self.assertFalse((self.base/'memory/.state/speech/selection.json').exists())
        self.assertEqual(ui.generations.list(),[])
        self.transport.open.reset_mock()
        for action in ('search','resolve'):
            self.assertEqual(ui.dispatch(action,{},body,'guest'),{'ok':False,'error':'unauthorized'})
        self.transport.open.assert_not_called()

    def test_ui_upgrade_replaces_extension_without_changing_base_application(self):
        from fish_miniapp_patch import assets,upgrade
        original='<head><script type="module" src="./assets/index-base.js"></script></head><body></body>'
        first,first_files=assets(original,b'original app',b'extension one',b'style one')
        current,files=upgrade(first,b'original app',b'extension two',b'style two')
        self.assertEqual(current.count('data-praxis-fish='),1)
        self.assertNotIn('extension one',str(files))
        self.assertIn(b'original app',files.values());self.assertIn(b'extension two',files.values())
        self.assertFalse(any(name.count('-fish-')>1 for name in files))
        with self.assertRaises(ValueError):upgrade(original,b'original app',b'new',b'new')

if __name__=='__main__':unittest.main()
