"""Destructive first-start policy in entirely private synthetic Windows folders."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import first_start as start


class Backend:
    def __init__(self):self.actions=[]
    @contextmanager
    def exclusive(self,target):yield
    def stop(self,roots,protected=()):self.actions.append(('stop',list(roots)))
    def release(self,path):self.actions.append(('release',path))
    def retire(self,path):self.actions.append(('retire',path))
    def remove(self,path):
        self.actions.append(('remove',path))
        if path.is_dir():shutil.rmtree(path)
        else:path.unlink()
    def shortcuts(self,roots,new):self.actions.append(('shortcuts',new))
    def assert_quiet(self,roots,protected=()):self.actions.append(('quiet',list(roots)))


class FirstStart(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='helene-first-start-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.profile=self.base/'User — Юзер';self.profile.mkdir()
        self.local=self.profile/'AppData/Local';self.roaming=self.profile/'AppData/Roaming'
        self.pf=self.base/'Program Files';self.downloads=self.profile/'Downloads'
        for path in (self.local,self.roaming,self.pf,self.downloads):path.mkdir(parents=True)
        self.root=self.downloads/'Новая Helene';self.root.mkdir();(self.root/'tree').mkdir()
        (self.root/'tree/llm.py').write_text('LLM_ENDPOINTS_API=1\n')
        for name in ('runtime/python.exe','app/python_app.py','app/deskapp.py','app/windows_start.py','app/resources/first-start.html','tree/agent.py','python-app.json'):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
        # Declared symbolic runtime bytes exercise actual payload custody while
        # the fake backend keeps all process/registry authority out of this test.
        import hashlib
        (self.root/'helene.exe').write_bytes(b'MZ symbolic private fixture')
        (self.root/'code-upstream.zip').write_bytes(b'pristine symbolic source fixture')
        (self.root/'python-package-files.json').write_text(json.dumps({'files':{
            name:{'sha256':hashlib.sha256((self.root/name).read_bytes()).hexdigest()}
            for name in ('runtime/python.exe','helene.exe')}}),encoding='utf8')
        (self.root/'python-app.json').write_text(json.dumps({'format':'helene-python-app.v1','version':'2.3.0-test'}),encoding='utf8')
        self.places=start.Places(self.profile,(self.local,self.roaming),(self.pf,),self.downloads)
        self.backend=Backend()
    def old(self,base=None,name='Programs/Helene'):
        old=(base or self.local)/name;data=old/'data';(data/'memory/groups').mkdir(parents=True)
        (data/'memory/groups/window.jsonl').write_bytes(b'old complete conversation\n')
        (data/'memory/.state').mkdir();(data/'memory/.state/harness.lock').write_text('{"pid":91}')
        (data/'memory/.control/desk_inbox').mkdir(parents=True)
        (data/'memory/.control/desk_inbox/old.md').write_text('do not run an old queued turn')
        (data/'memory/.control/profile-identity.json').write_text('{"id":"keep-profile"}')
        (old/'app').mkdir();(old/'app/old.py').write_text('old_code = 1')
        (old/'helene.json').write_text(json.dumps({'tree':'data','mode':'local','setup_complete':True,
            'model':{'framework':'openai','model':'fixture','key':'private-fixture'},
            'agent':{'name':'Original agent'},'autonomy':{'paused':True},'relay':{'enabled':False}}))
        return old,data
    def prepare(self):return start.prepare(self.root,places=self.places,backend=self.backend)
    def test_legacy_python_mixed_code_and_data_keeps_state_and_discards_modules(self):
        old,data=self.old()
        shutil.move(str(data/'memory'),str(old/'memory'));shutil.rmtree(data)
        (old/'agent.py').write_text('old mutation');(old/'llm.py').write_text('old mutation')
        (old/'workspace/project').mkdir(parents=True);(old/'workspace/project/user.py').write_text('owner work')
        cfg=start.load(old/'helene.json');cfg.update(tree='.',code='.')
        (old/'helene.json').write_text(json.dumps(cfg))
        result=self.prepare();self.assertTrue(result['agent']);self.assertFalse(old.exists())
        self.assertEqual((self.places.data/'workspace/project/user.py').read_text(),'owner work')
        self.assertFalse((self.places.data/'agent.py').exists());self.assertFalse((self.places.data/'app').exists())
        self.assertEqual(start.load(self.root/'helene.json')['agent']['name'],'Original agent')
    def test_mixed_child_tree_does_not_keep_old_python_source(self):
        old,data=self.old();code=old/'tree';data.rename(code)
        (code/'agent.py').write_text('mutation');(code/'llm.py').write_text('mutation')
        cfg=start.load(old/'helene.json');cfg.update(tree='tree',code='tree')
        (old/'helene.json').write_text(json.dumps(cfg))
        self.prepare();self.assertTrue((self.places.data/'memory/groups/window.jsonl').exists())
        self.assertFalse((self.places.data/'agent.py').exists());self.assertFalse(old.exists())
    def test_manual_new_upstream_keeps_other_receipted_python_folder_and_queues(self):
        import datetime
        old,data=self.old(base=self.downloads,name='Helene previous')
        self.places.data.parent.mkdir(parents=True);data.rename(self.places.data)
        cfg=start.load(old/'helene.json');cfg['tree']=str(self.places.data)
        (old/'helene.json').write_text(json.dumps(cfg))
        start._write(start.receipt(old),{'format':start.FORMAT,'state':'completed',
            'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'data':str(self.places.data)})
        before=(old/'app/old.py').read_bytes();self.prepare()
        self.assertTrue(old.is_dir());self.assertEqual((old/'app/old.py').read_bytes(),before)
        self.assertTrue((self.places.data/'memory/.control/desk_inbox/old.md').exists())
        self.assertEqual(start.load(self.root/'helene.json')['agent']['name'],'Original agent')
        self.assertNotIn(('retire',old),self.backend.actions)
    def test_archive_outer_directory_is_protected_by_inner_programme_receipt(self):
        import datetime
        old,data=self.old(base=self.downloads,name='Helene previous/Helene')
        self.places.data.parent.mkdir(parents=True);data.rename(self.places.data)
        cfg=start.load(old/'helene.json');cfg['tree']=str(self.places.data)
        (old/'helene.json').write_text(json.dumps(cfg))
        start._write(start.receipt(old),{'format':start.FORMAT,'state':'completed',
            'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'data':str(self.places.data)})
        self.prepare();self.assertTrue(old.is_dir());self.assertTrue(start.ready(old,self.places))
        self.assertTrue(start.ready(self.root,self.places))
    def test_receipted_empty_copy_does_not_preserve_stale_legacy_execution(self):
        import datetime
        old,data=self.old()
        other=self.downloads/'Helene earlier/Helene';other.mkdir(parents=True)
        start._write(start.receipt(other),{'format':start.FORMAT,'state':'completed',
            'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'data':str(self.places.data)})
        self.prepare();self.assertTrue(other.is_dir());self.assertFalse(old.exists())
        self.assertFalse((self.places.data/'memory/.state/harness.lock').exists())
        self.assertFalse((self.places.data/'memory/.control/desk_inbox').exists())
    def test_exact_receipted_owner_on_desktop_is_preserved_without_expanding_data_search(self):
        import datetime
        old,data=self.old(base=self.profile/'Desktop',name='Helene')
        self.places.data.parent.mkdir(parents=True);data.rename(self.places.data)
        cfg=start.load(old/'helene.json');cfg['tree']=str(self.places.data)
        (old/'helene.json').write_text(json.dumps(cfg))
        start._write(start.receipt(old),{'format':start.FORMAT,'state':'completed',
            'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'data':str(self.places.data)})
        start._write(self.places.data/'memory/.control/desktop-session.json',{'program':str(old)})
        self.prepare();self.assertTrue(old.is_dir())
        self.assertTrue((self.places.data/'memory/.control/desk_inbox/old.md').exists())
        self.assertEqual(start.load(self.root/'helene.json')['agent']['name'],'Original agent')
    def test_reopening_an_admitted_version_keeps_latest_data_model_and_settings(self):
        import datetime
        from localharness import windows_first_start,session_host
        old,data=self.old(base=self.profile/'Desktop',name='Helene')
        self.places.data.parent.mkdir(parents=True);data.rename(self.places.data)
        cfg=start.load(old/'helene.json');cfg['tree']=str(self.places.data);cfg['agent']['name']='Latest name'
        (old/'helene.json').write_text(json.dumps(cfg))
        start._write(start.receipt(old),{'format':start.FORMAT,'state':'completed',
            'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'data':str(self.places.data)})
        start._write(self.places.data/'memory/.control/desktop-session.json',{'program':str(old),'pid':12,'generation':'test'})
        brain={'frameworks':{'openai':{'base_url':'http://127.0.0.1:9/v1','api_key':'latest-fixture-key'}},
            'roles':{'voice':{'framework':'openai','model':'latest-data-model'}}}
        start._write(self.places.data/'memory/llm.json',brain)
        with patch.object(session_host,'process_state',return_value='absent'),patch.object(windows_first_start,'Backend',return_value=self.backend):
            changed=start.handover(self.root,places=self.places)
        self.assertTrue(changed)
        start.ensure_config(self.root,places=self.places,refresh_profile=changed)
        actual=start.load(self.root/'helene.json')
        self.assertEqual(actual['agent']['name'],'Latest name')
        self.assertEqual(actual['model']['model'],'latest-data-model')
        self.assertEqual(actual['model']['key'],'latest-fixture-key')
        self.assertEqual(start.load(self.places.data/'memory/llm.json'),brain)
    def test_secondary_legacy_credentials_are_retained_without_copying_code(self):
        one,_=self.old(name='Programs/Helene one');two,_=self.old(name='Programs/Helene two')
        first=start.load(one/'helene.json');second=start.load(two/'helene.json')
        first['model']['key']='first-private-fixture';second['model']['key']='second-private-fixture'
        (one/'helene.json').write_text(json.dumps(first));(two/'helene.json').write_text(json.dumps(second))
        self.prepare()
        saved=start.load(self.places.data/'memory/.state/imported-profiles.json')['profiles']
        self.assertEqual({row['config']['model']['key'] for row in saved.values()},
                         {first['model']['key'],second['model']['key']})
        self.assertFalse(one.exists());self.assertFalse(two.exists())
        self.assertFalse((self.places.data/'app/old.py').exists())
    def test_whole_contract_preserves_agent_moves_data_and_clears_every_old_copy(self):
        old,data=self.old();history=(data/'memory/groups/window.jsonl').read_bytes()
        orphan=self.local/'Vendor/not-a-prefix-hElEnE-remnant';orphan.mkdir(parents=True);(orphan/'junk').write_text('old')
        portable=self.downloads/'old-Helene-portable';portable.mkdir();(portable/'helene.exe').write_bytes(b'old')
        marker=self.downloads/'update-job-ref-fixture.json';marker.write_text('{}')
        import zipfile
        archive=self.downloads/'Helene-previous.zip'
        with zipfile.ZipFile(archive,'w') as saved:saved.writestr('Helene/python-app.json',json.dumps({'format':'helene-python-app.v1'}))
        personal=self.downloads/'My other project';personal.mkdir();(personal/'keep').write_text('personal')
        outside=self.profile/'Documents/Helene personal notes';outside.mkdir(parents=True);(outside/'keep').write_text('personal')
        result=self.prepare();target=self.places.data
        self.assertTrue(result['agent']);self.assertEqual((target/'memory/groups/window.jsonl').read_bytes(),history)
        self.assertFalse((target/'memory/.state/harness.lock').exists())
        self.assertFalse((target/'memory/.control/desk_inbox').exists())
        self.assertEqual(json.loads((target/'memory/.control/profile-identity.json').read_text())['id'],'keep-profile')
        self.assertTrue(all(not path.exists() for path in (old,orphan,portable,marker,archive)))
        self.assertTrue((personal/'keep').exists());self.assertTrue((outside/'keep').exists())
        config=start.load(self.root/'helene.json')
        self.assertEqual(config['agent']['name'],'Original agent');self.assertTrue(config['autonomy']['paused'])
        self.assertEqual(config['model']['key'],'private-fixture');self.assertEqual(config['tree'],str(target))
        self.assertTrue(start.ready(self.root,self.places))
        receipt=start.load(start.receipt(self.root));self.assertEqual(set(receipt),{'format','state','completed_at','data','retirement'})
        from localharness import portable_retirement
        pending=start.load(portable_retirement.path(self.root))
        self.assertEqual(receipt['retirement'],{'id':pending['id'],'sha256':portable_retirement.authority(pending)})
        self.assertEqual(self.backend.actions[0][0],'stop')
        self.assertLess(next(i for i,row in enumerate(self.backend.actions) if row[0]=='retire'),
                        next(i for i,row in enumerate(self.backend.actions) if row[0]=='shortcuts'))
    def test_valid_receipt_is_fast_path_and_never_runs_cleanup_again(self):
        self.old();self.prepare();self.backend.actions.clear()
        sibling=self.downloads/'Helene-created-later';sibling.mkdir()
        result=self.prepare();self.assertTrue(result['agent']);self.assertEqual(self.backend.actions,[])
        self.assertTrue(sibling.exists())
    def test_missing_data_or_config_after_success_never_restarts_destructive_scan(self):
        self.old();self.prepare();self.backend.actions.clear()
        shutil.rmtree(self.places.data);(self.root/'helene.json').unlink()
        self.assertTrue(start.ready(self.root,self.places))
        start.ensure_config(self.root,places=self.places)
        self.assertTrue(self.places.data.is_dir());self.assertFalse(start.agent_exists(self.places.data))
        self.assertEqual(self.backend.actions,[])
    def test_first_install_has_one_data_home_and_minimal_creation_saves_private_credentials(self):
        result=self.prepare();self.assertFalse(result['agent']);self.assertTrue(self.places.data.is_dir())
        with self.assertRaises(ValueError):start.create_agent(self.root,{'name':'x'},places=self.places)
        start.create_agent(self.root,{'name':'My agent','owner':'Owner','provider':'openai','model':'fixture','key':'dummy'},places=self.places)
        config=start.load(self.root/'helene.json')
        self.assertEqual(config['agent']['name'],'My agent');self.assertEqual(config['model']['key'],'dummy')
        self.assertTrue(config['setup_complete']);self.assertFalse((self.root/'data').exists())
    def test_failed_cleanup_never_publishes_success_and_retry_uses_moved_data(self):
        old,data=self.old();original=(data/'memory/groups/window.jsonl').read_bytes()
        actual=self.backend.remove
        def blocked(path):
            if path==old:raise OSError('held old directory')
            return actual(path)
        with patch.object(self.backend,'remove',side_effect=blocked):
            with self.assertRaises(OSError):self.prepare()
        self.assertFalse(start.ready(self.root,self.places));self.assertEqual(start.load(start.receipt(self.root))['state'],'preparing')
        self.assertEqual((self.places.data/'memory/groups/window.jsonl').read_bytes(),original)
        self.prepare();self.assertTrue(start.ready(self.root,self.places));self.assertFalse(old.exists())
    def test_downloads_data_is_not_searched_or_silently_deleted_outside_allowed_scope(self):
        old,data=self.old(self.downloads,'Helene-old-with-data')
        with self.assertRaisesRegex(ValueError,'вне AppData'):self.prepare()
        self.assertTrue((data/'memory/groups/window.jsonl').exists());self.assertEqual(self.backend.actions,[])
    def test_conflicting_sources_are_retained_without_overwriting_existing_history(self):
        old,data=self.old();target=self.places.data;(target/'memory/groups').mkdir(parents=True)
        (target/'memory/groups/window.jsonl').write_bytes(b'canonical conversation\n')
        self.prepare()
        self.assertEqual((target/'memory/groups/window.jsonl').read_bytes(),b'canonical conversation\n')
        recovered=list((target/'recovered').rglob('window.jsonl'))
        self.assertEqual(len(recovered),1);self.assertEqual(recovered[0].read_bytes(),b'old complete conversation\n')
    def test_appdata_substring_and_accent_matching_are_explicit(self):
        for name in ('helene','prefixHELENEcopy','.hélène-recovery','элен-остаток'):
            self.assertTrue(start.branded(name))
        self.assertFalse(start.branded('Other programme'))
    def test_no_receipt_for_failed_supervisor_shutdown(self):
        self.old()
        with patch.object(self.backend,'stop',side_effect=RuntimeError('respawning bridge')):
            with self.assertRaisesRegex(RuntimeError,'respawning'):self.prepare()
        self.assertFalse(start.receipt(self.root).exists());self.assertFalse((self.root/'helene.json').exists())
    def test_blank_failed_installer_config_recovers_model_from_actual_agent_brain(self):
        old,data=self.old();config=start.load(old/'helene.json')
        config.update(setup_complete=False,model={'framework':'openai','model':'','key':''})
        (old/'helene.json').write_text(json.dumps(config))
        brain={'frameworks':{'openai':{'base_url':'https://fixture.invalid/v1','api_key':'retained-key'}},
               'roles':{'voice':{'framework':'openai','model':'retained-model','vision_prepass':False}}}
        original=(json.dumps(brain)+'\n').encode();(data/'memory/llm.json').write_bytes(original)
        self.prepare();actual=start.load(self.root/'helene.json')
        self.assertTrue(actual['setup_complete']);self.assertEqual(actual['model']['model'],'retained-model')
        self.assertEqual(actual['model']['key'],'retained-key')
        self.assertEqual((self.places.data/'memory/llm.json').read_bytes(),original)
    def test_owner_stop_survives_appdata_wipe_and_lives_in_data(self):
        self.old();legacy=self.local/'Helene';legacy.mkdir();stop=b'{"reason":"owner explicitly stopped"}'
        (legacy/'owner-stop.json').write_bytes(stop)
        self.prepare();self.assertFalse(legacy.exists())
        self.assertEqual((self.places.data/'owner-stop.json').read_bytes(),stop)
        self.assertFalse((self.root/'owner-stop.json').exists())


if __name__=='__main__':unittest.main()
