import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import model_catalog
from deskd import control, readers

class Catalog(unittest.TestCase):
    def test_server_catalog_uses_saved_relay_key_without_exposing_it(self):
        from localharness import python_desktop_commands as commands
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);config=tree/'helene.json'
            config.write_text(json.dumps({'relay':{'enabled':True,'port':18999,'key':'private-relay-key'},
                                         'model':{'provider':'chatgpt','key':'different-provider-key'}}))
            with patch.object(readers,'tree',return_value=tree),patch.object(readers,'config_path',return_value=config), \
                 patch.object(commands,'probe_model',return_value={'ok':True,'models':['sol']}) as fetch:
                value=control.brain_models(refresh=True)
                self.assertTrue(value['ok']);self.assertNotIn('private-relay-key',json.dumps(value))
                self.assertEqual(fetch.call_args.args,('http://127.0.0.1:18999','private-relay-key','openai'))

    def test_desk_revision_uses_owner_socket_when_auth_is_root_private(self):
        import os
        from deskd import relay_control
        with patch.object(model_catalog,'local_relay_revision',side_effect=PermissionError), \
             patch.dict(os.environ,{'HELENE_SUPERVISOR':'serverboot'}), \
             patch.object(relay_control,'request',return_value={'catalog_revision':'a'*64}) as request:
            self.assertEqual(model_catalog.relay_revision(Path('/fixture')),'a'*64)
            self.assertEqual(request.call_args.args,(Path('/fixture'),{'action':'status'}))
            request.return_value={}
            with self.assertRaises(ValueError):model_catalog.relay_revision(Path('/fixture'))

    def test_actual_window_v1_url_is_account_scoped_and_shares_root_catalog(self):
        from localharness import python_desktop_commands as commands
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);config=tree/'helene.json'
            config.write_text(json.dumps({'relay':{'enabled':True,'port':18999}}))
            owner=SimpleNamespace(current=lambda:SimpleNamespace(tree=tree,config=config))
            args={'baseUrl':'http://127.0.0.1:18999/v1','key':'','framework':'openai'}
            auth=tree/'relay/local_auth/auth.json';auth.parent.mkdir(parents=True)
            auth.write_text('{"fixture":"a"}')
            with patch.object(commands,'probe_model',return_value={'ok':True,'models':['sol']}) as fetch:
                self.assertTrue(commands.invoke(owner,'probe_model',args)['live'])
                self.assertTrue(commands.invoke(owner,'probe_model',{**args,'baseUrl':'http://127.0.0.1:18999'})['cached'])
                self.assertEqual(fetch.call_count,1)
                auth.write_text('{"fixture":"b"}')
                fetch.return_value={'ok':True,'models':['astra']}
                result=commands.invoke(owner,'probe_model',args)
                self.assertTrue(result['live']);self.assertEqual(result['models'],['astra'])
                self.assertEqual(fetch.call_count,2)

    def test_credentials_generation_and_framework_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json'
            fetch=Mock(return_value={'ok':True,'models':['sol']})
            model_catalog.read(path,'http://localhost:1/','','openai',fetch,revision='account-a')
            fetch.return_value={'ok':False,'note':'offline'}
            self.assertTrue(model_catalog.read(path,'http://localhost:1','','openai',fetch,revision='account-a')['cached'])
            self.assertFalse(model_catalog.read(path,'http://localhost:1','','openai',fetch,revision='account-b')['ok'])
            self.assertFalse(model_catalog.read(path,'http://localhost:1','','anthropic',fetch,revision='account-a')['ok'])

    def test_interrupted_refresh_retains_cache_and_failed_save_reports_live_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json'
            fetch=Mock(return_value={'ok':True,'models':['sol']})
            model_catalog.read(path,'http://localhost:1','','openai',fetch)
            original=path.read_bytes()
            fetch.side_effect=OSError('private detail should not escape')
            result=model_catalog.read(path,'http://localhost:1','','openai',fetch,refresh=True)
            self.assertTrue(result['refresh_failed']);self.assertEqual(result['models'],['sol'])
            self.assertNotIn('private detail',json.dumps(result))
            self.assertEqual(path.read_bytes(),original)
            fetch.side_effect=None;fetch.return_value={'ok':True,'models':['new']}
            with patch('localharness.package_notice.write',side_effect=PermissionError()):
                result=model_catalog.read(path,'http://localhost:1','','openai',fetch,refresh=True)
            self.assertTrue(result['live']);self.assertFalse(result['persisted'])
            self.assertEqual(path.read_bytes(),original)

    def test_corrupt_cache_entry_cannot_break_other_catalogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json'
            path.write_text(json.dumps({'broken':{'ok':True,'models':['bad'],'fetched_at':'yesterday'}}))
            result=model_catalog.read(path,'http://localhost:1','','openai',lambda:{'ok':True,'models':['sol']})
            self.assertTrue(result['live']);self.assertEqual(len(json.loads(path.read_text())),1)

    def test_auth_file_and_explicit_account_switch_invalidate_generation(self):
        from localharness import python_relay
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);auth=tree/'relay/local_auth/accounts/primary/auth.json'
            auth.parent.mkdir(parents=True);auth.write_text('{"fixture":"account-a"}')
            first=model_catalog.relay_revision(tree)
            auth.write_text('{"fixture":"account-b"}')
            second=model_catalog.relay_revision(tree)
            self.assertNotEqual(first,second)
            python_relay.changed(tree/'relay')
            self.assertNotEqual(second,model_catalog.relay_revision(tree))

    def test_persistent_refresh_failure_and_endpoint_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json'
            fetch=Mock(return_value={'ok':True,'models':['sol'],'model_details':[{'id':'sol','reasoning_efforts':['high']}],'note':'live'})
            self.assertTrue(model_catalog.read(path,'http://localhost:1','secret','openai',fetch)['live'])
            self.assertTrue(model_catalog.read(path,'http://localhost:1','secret','openai',fetch)['cached'])
            self.assertEqual(fetch.call_count,1)
            fetch.return_value={'ok':False,'note':'connection failed'}
            failed=model_catalog.read(path,'http://localhost:1','secret','openai',fetch,refresh=True)
            self.assertTrue(failed['refresh_failed']);self.assertEqual(failed['models'],['sol'])
            self.assertNotIn('secret',path.read_text())
            self.assertFalse(model_catalog.read(path,'http://localhost:2','secret','openai',fetch)['ok'])
            self.assertFalse(model_catalog.read(path,'http://localhost:1','other','openai',fetch)['ok'])

    def test_desktop_phone_catalog_uses_selected_profile_not_container_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'helene.json';path.write_text(json.dumps({'model':{'provider':'chatgpt','framework':'openai'},'relay':{'enabled':True,'port':18999}}))
            with patch.object(readers,'config_path',return_value=path),patch.object(readers,'tree',return_value=Path(tmp)), \
                 patch.object(control,'deskctl_call',side_effect=AssertionError('no server service')), \
                 patch('localharness.python_desktop_commands.probe_model',return_value={'ok':True,'models':['sol'],'model_details':[{'id':'sol'}]}) as probe:
                self.assertTrue(control.brain_models()['by_framework']['primary']['codex'])
                probe.assert_called_once_with('http://127.0.0.1:18999','','openai')
                self.assertTrue(control.brain_models()['by_framework']['primary']['cached'])

if __name__=='__main__':unittest.main()
