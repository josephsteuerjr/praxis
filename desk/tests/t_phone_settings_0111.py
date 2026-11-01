"""Actual agent settings: private values, patch boundaries and optimistic save."""
import copy,json,secrets,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
from deskd import phone_settings as settings
from localharness import config_cli


class Settings(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.path=self.root/'helene.json';self.tree=self.root/'data';self.tree.mkdir()
        self.api_secret=secrets.token_urlsafe(32);self.bot_secret=secrets.token_urlsafe(32)
        self.config={'tree':str(self.tree),'code':'tree','python':'runtime/python.exe','app':'app/deskapp.py',
            'runner':'app/localharness/runner.py','port':31234,'mode':'local',
            'agent':{'name':'Existing','custom':'keep'},'model':{'framework':'openai','model':'chosen',
            'key':self.api_secret,'base_url':'https://example.invalid/v1','custom':'keep'},
            'telegram':{'bot_token':self.bot_secret,'owner_id':42},'relay':{'enabled':False,'instructions':'preserve authored instructions'},
            'phone':{'enabled':True,'mode':'automatic'},'unknown_block':{'important':['keep']}}
        self.path.write_text(json.dumps(self.config),encoding='utf8')
        self.addCleanup(patch.stopall)
        patch.object(settings.readers,'config_path',return_value=self.path).start()
        patch.object(settings.readers,'tree',return_value=self.tree).start()

    def test_snapshot_never_returns_credentials_or_runtime_paths(self):
        view=settings.snapshot();serialized=json.dumps(view)
        self.assertNotIn(self.api_secret,serialized);self.assertNotIn(self.bot_secret,serialized)
        self.assertNotIn(str(self.tree),serialized)
        fields={row['path']:row for row in view['fields']}
        self.assertEqual(fields['model.key']['value'],'');self.assertTrue(fields['model.key']['stored'])
        self.assertEqual(fields['agent.name']['value'],self.config['agent']['name'])

    def test_declared_patch_preserves_unedited_secrets_nested_blocks_and_runtime(self):
        result=settings.prepare(self.config,{'agent.name':'Changed','model.model':'next model'})
        self.assertEqual(result['model']['key'],self.api_secret)
        self.assertEqual(result['telegram'],self.config['telegram']);self.assertEqual(result['relay']['instructions'],self.config['relay']['instructions'])
        self.assertEqual(result['unknown_block'],self.config['unknown_block'])
        for key in ('tree','code','python','runner','app','port','mode'):self.assertEqual(result[key],self.config[key])

    def test_paths_and_arbitrary_rpc_are_not_settings(self):
        for key in ('tree','python','port','model.__proto__.x','command','mode','relay.executable'):
            with self.subTest(key=key),self.assertRaises(settings.SettingsError):settings.prepare(self.config,{key:'injected'})

    def test_subscription_keeps_existing_loop_key_when_only_name_changes(self):
        config=copy.deepcopy(self.config);loop=secrets.token_urlsafe(32)
        config['relay'].update(enabled=True,key=loop,port=5019);config['model'].update(key=loop,base_url='http://127.0.0.1:5019')
        result=settings.prepare(config,{'agent.name':'Changed'})
        self.assertEqual(result['relay']['key'],loop);self.assertEqual(result['model']['key'],loop)
        with self.assertRaises(settings.SettingsError):settings.prepare(config,{'model.key':'an API key does not belong in subscription settings'})

    def test_switching_provider_keeps_the_other_providers_stored_key(self):
        subscription=settings.prepare(self.config,{'provider':'chatgpt'})
        self.assertNotEqual(subscription['model']['key'],self.api_secret)
        self.assertEqual(subscription['model']['keys']['api'],self.api_secret)
        restored=settings.prepare(subscription,{'provider':'openai'})
        self.assertEqual(restored['model']['key'],self.api_secret)

    def test_stable_phone_keeps_local_ingress_and_rejects_secret_or_path_urls(self):
        fixed=settings.prepare(self.config,{'phone.mode':'stable','phone.external':'https://stable.example/'})
        self.assertEqual(fixed['phone']['local_port'],self.config['port'])
        self.assertEqual(fixed['phone']['external'],'https://stable.example')
        for url in ('http://stable.example','https://user:password@stable.example','https://stable.example/path','https://stable.example/?key=secret'):
            with self.subTest(url=url),self.assertRaises(settings.SettingsError):settings.prepare(self.config,{'phone.external':url})

    def test_stale_form_cannot_write_and_fresh_save_uses_normal_writer(self):
        seen=settings.snapshot()['mtime_ns'];operation=uuid.uuid4().hex
        body={'changes':{'agent.name':'Changed'},'mtime_ns':seen,'scope_id':settings.scope_id(),'operation':operation}
        def commit(path,tree,value,stamp,op):
            self.assertEqual(path,self.path);self.assertEqual(tree,self.tree);self.assertEqual(op,operation)
            return config_cli.save(path,json.dumps(value),stamp)
        with patch.object(settings,'_desktop_commit',side_effect=commit) as writer:
            answer=settings.save(body);self.assertTrue(answer['ok'])
            with self.assertRaises(settings.SettingsError) as caught:settings.save(body)
        self.assertEqual(caught.exception.status,409);self.assertEqual(writer.call_count,1)
        actual=json.loads(self.path.read_text());self.assertEqual(actual['model']['key'],self.api_secret)

    def test_validation_happens_before_owner_rpc(self):
        body={'changes':{'owner_context.messages':0},'mtime_ns':settings.snapshot()['mtime_ns'],'scope_id':settings.scope_id(),'operation':uuid.uuid4().hex}
        with patch.object(settings,'_desktop_commit') as commit:
            with self.assertRaises(settings.SettingsError):settings.save(body)
        commit.assert_not_called();self.assertEqual(json.loads(self.path.read_text()),self.config)

    def test_computer_permissions_match_actual_product_contract_and_preserve_restrictions(self):
        contract=json.loads((Path(__file__).resolve().parents[1]/'ui-kit/contract.json').read_text(encoding='utf8'))
        spec=settings.BY_PATH['computer.scopes']
        self.assertEqual([row[0] for row in spec['choices']],contract['computer_scopes'])
        allowed=contract['computer_scopes'][:1]
        value=settings.prepare(self.config,{'computer.scopes':allowed})
        self.assertEqual(value['computer']['scopes'],allowed)
        self.assertEqual(settings.prepare(value,{'agent.name':'Still restricted'})['computer']['scopes'],allowed)
        self.assertEqual(settings.prepare(value,{'computer.scopes':[]})['computer']['scopes'],[])
        for bad in ('computer.read',['computer.admin'],[{}]):
            with self.subTest(bad=bad),self.assertRaises(settings.SettingsError):settings.prepare(self.config,{'computer.scopes':bad})

    def test_new_credentials_are_write_only_and_survive_unrelated_save(self):
        config=settings.prepare(self.config,{'telegram.api_hash':self.bot_secret,'relay.proxy.password':self.api_secret})
        self.path.write_text(json.dumps(config),encoding='utf8')
        view=settings.snapshot();encoded=json.dumps(view)
        self.assertNotIn(self.api_secret,encoded);self.assertNotIn(self.bot_secret,encoded)
        self.assertTrue(next(row for row in view['fields'] if row['path']=='relay.proxy.password')['stored'])
        result=settings.prepare(config,{'owner.name':'Owner'})
        self.assertEqual(result['relay']['proxy']['password'],self.api_secret)
        self.assertEqual(result['telegram']['api_hash'],self.bot_secret)

    def test_settings_use_desktop_keys_and_validate_proxy_before_commit(self):
        value=settings.prepare(self.config,{'telegram.mode':'account','telegram.status_message':True,
            'relay.proxy.mode':'proxy','relay.proxy.host':'proxy.example','relay.proxy.port':'1080',
            'relay.proxy.protocol':'socks5h','relay.proxy.username':'owner','relay.proxy.password':self.api_secret})
        self.assertTrue(value['telegram']['status_message'])
        self.assertEqual(value['relay']['proxy']['protocol'],'socks5h')
        self.assertEqual(value['model']['provider'],'api')
        for changes in ({'relay.proxy.mode':'proxy'}, {'relay.proxy.mode':'proxy','relay.proxy.host':'bad/host','relay.proxy.port':'1080'},
                        {'relay.proxy.mode':'proxy','relay.proxy.host':'host','relay.proxy.port':'65536'}):
            with self.subTest(changes=changes),self.assertRaises(settings.SettingsError):settings.prepare(self.config,changes)
        fields={row['path']:row for row in settings.snapshot()['fields']}
        self.assertEqual(fields['backup.keep']['value'],8)
        self.assertEqual(fields['backup.every_days']['value'],7)
        self.assertTrue(all(row['section'] for row in fields.values()))


if __name__=='__main__':unittest.main()
