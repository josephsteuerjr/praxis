"""Clean admission is extraction completion, not a directory comparison/trial."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import clean_update as clean,parallel_files as files
from localharness import python_updates,python_source_updater,portable_switch,source_merge,update_profile,source_admission
from localharness import migration_lifecycle,first_start
from localharness.python_platform import runtime_target
from localharness.package_notice import read,write

class CleanSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.old=self.base/'previous';self.new=self.base/'incoming';self.data=self.base/'owner-data'
        for path in (self.old/'app',self.old/'tree',self.new/'app',self.new/'tree',self.data/'memory'):
            path.mkdir(parents=True,exist_ok=True)
        write(self.old/'python-app.json',{'version':'2.4.0','target':runtime_target()})
        write(self.new/'python-app.json',{'version':'2.5.0-rc.1','target':runtime_target()})
        (self.old/'app/python_app.py').write_bytes(b'# previous entry')
        (self.old/'tree/authored.py').write_bytes(b'owner_changed_this = True')
        (self.new/'tree/authored.py').write_bytes(b'pristine = True')
        (self.data/'memory/history').write_bytes(b'irreplaceable owner history')
        write(self.old/'helene.json',{'tree':str(self.data),'code':'tree','setup_complete':True,
            'model':{'key':'fixture-only'},'autonomy':{'paused':True},'voice':{'speak':True}})
        self.events=[]
        for module,name in ((source_merge,'inventory'),(update_profile,'selection'),(portable_switch,'payload'),(source_admission,'activate')):
            p=patch.object(module,name,side_effect=AssertionError('Clean must not verify directory bytes: '+name));p.start();self.addCleanup(p.stop)
        for name in ('stop','quiescent','cleanup_installation','install_shortcuts'):
            p=patch.object(migration_lifecycle,name,side_effect=lambda *a,_n=name,**k:self.events.append(_n) or {});p.start();self.addCleanup(p.stop)
        p=patch('localharness.portable_profile.default_data',return_value=self.base/'permanent/data');p.start();self.addCleanup(p.stop)
    def prepare(self):return clean.prepare(self.old,self.new)
    def test_explicit_folder_preparation_api_skips_all_directory_verification(self):
        value=python_source_updater.prepare_install(self.old,self.new,folder_switch=True)
        self.assertEqual(value['format'],clean.FORMAT)
        self.assertTrue(self.old.is_dir());self.assertTrue(self.new.is_dir())
        self.assertEqual((self.data/'memory/history').read_bytes(),b'irreplaceable owner history')
    def test_normal_clean_archive_install_uses_independent_unmocked_staging(self):
        from localharness import program_state
        archive=self.base/'incoming.zip'
        with zipfile.ZipFile(archive,'w') as package:
            package.writestr('Helene/python-app.json',json.dumps(read(self.new/'python-app.json')))
            package.writestr('Helene/tree/agent.py','EXTRACTED = True\n')
        owner=types.SimpleNamespace(root=self.old,event=lambda *a:None,update_job=None)
        def handoff(current,destination,verified,progress,force,sourceMode=None):
            self.assertIs(current,owner);self.assertEqual(sourceMode,'clean')
            self.assertFalse(destination.is_relative_to(self.old))
            self.assertTrue(program_state.staging_belongs(self.old,destination))
            self.assertEqual((destination/'Helene/tree/agent.py').read_text(),'EXTRACTED = True\n')
            return {'staged':str(destination)}
        with patch.object(python_updates,'_prepare_handoff',side_effect=handoff) as common:
            result=python_updates._update_install(owner,archive,sourceMode='clean')
        common.assert_called_once();self.assertTrue(Path(result['staged']).is_dir())
        self.assertTrue(self.old.is_dir())
        self.assertEqual((self.data/'memory/history').read_bytes(),b'irreplaceable owner history')
    def test_default_staging_retains_in_place_policy(self):
        from localharness import program_state
        default=program_state.staging(self.old,'legacy-policy')
        self.assertEqual(default,program_state.staging(self.old,'legacy-policy',folder_switch=False))
        if os.name!='nt':self.assertTrue(default.is_relative_to(self.old))
        independent=program_state.staging(self.old,'folder-policy',folder_switch=True)
        self.assertFalse(independent.is_relative_to(self.old))
        self.assertTrue(program_state.staging_belongs(self.old,independent))
    def acknowledge(self,value):
        note=dict(value['note'],state='new_app_started',startup_proof={'tree':str(self.data),'channel_pid':123,'runner_pid':124,'code':str(self.new/'tree')})
        write(self.new/'python-migration.json',note);return note
    def test_clean_preparation_and_dispatch_never_inventory_either_programme(self):
        value=self.prepare()
        self.assertEqual(value['format'],clean.FORMAT)
        self.assertEqual(python_source_updater.prepare_install(self.old,self.new),value)
        self.assertEqual(python_source_updater.validate_prepared(self.old,self.new),value)
        self.assertEqual(portable_switch.prepare(self.old,self.new),value)
        config=read(self.new/'helene.json')
        self.assertEqual(config['code'],str(self.new/'tree'));self.assertEqual(config['tree'],str(self.data))
        self.assertEqual(config['model']['key'],'fixture-only');self.assertTrue(config['autonomy']['paused'])
        self.assertEqual((self.data/'memory/history').read_bytes(),b'irreplaceable owner history')
        self.assertTrue(self.old.is_dir());self.assertFalse(value['moves'])
    def test_changed_old_and_new_programme_files_do_not_reintroduce_comparison(self):
        value=self.prepare();clean.relocate(self.old,self.new,value)
        (self.old/'tree/authored.py').write_bytes(b'changed after explicit clean choice')
        (self.new/'tree/authored.py').write_bytes(b'new bytes are owner choice too')
        clean.finish(self.old,self.new,self.acknowledge(value))
        self.assertFalse(self.old.exists());self.assertTrue(self.new.is_dir())
        self.assertEqual((self.data/'memory/history').read_bytes(),b'irreplaceable owner history')
    def test_internal_legacy_relay_selects_new_bundled_transport(self):
        from localharness.standalone_relay import executable
        binary = 'praxis-relay-headless.exe' if os.name == 'nt' else 'praxis-relay-headless'
        bundled = self.new/'relay-product'/binary
        bundled.parent.mkdir(); bundled.write_bytes(b'new controlled relay fixture')
        legacy = self.old/'helene-relay.exe'; legacy.write_bytes(b'old controlled relay fixture')
        (self.new/'helene-relay.exe').write_bytes(b'old compatibility entry')
        cfg = read(self.old/'helene.json'); cfg['relay'] = {'enabled':True, 'executable':str(legacy)}
        write(self.old/'helene.json',cfg)
        value = self.prepare()
        selected = read(self.new/'helene.json')
        self.assertEqual(executable(self.new, selected),bundled)
        self.assertFalse(any(row['source'] == str(legacy) for row in value['moves']))
    def test_independent_external_relay_choice_survives_new_bundle(self):
        external = self.base/'independent-relay.exe'; external.write_bytes(b'external fixture')
        binary = 'praxis-relay-headless.exe' if os.name == 'nt' else 'praxis-relay-headless'
        bundled = self.new/'relay-product'/binary; bundled.parent.mkdir(); bundled.write_bytes(b'new fixture')
        cfg = read(self.old/'helene.json'); cfg['relay'] = {'enabled':True, 'executable':str(external)}
        write(self.old/'helene.json',cfg); self.prepare()
        self.assertEqual(read(self.new/'helene.json')['relay']['executable'],str(external))
    def test_relative_external_relay_keeps_original_address_in_deeper_candidate(self):
        self.new = self.base/'staging/transaction/incoming'
        (self.new/'app').mkdir(parents=True); (self.new/'tree').mkdir()
        write(self.new/'python-app.json',{'version':'2.5.0-rc.2'})
        external = self.base/'independent-relay.exe'; external.write_bytes(b'external fixture')
        binary = 'praxis-relay-headless.exe' if os.name == 'nt' else 'praxis-relay-headless'
        bundled = self.new/'relay-product'/binary; bundled.parent.mkdir(); bundled.write_bytes(b'new fixture')
        cfg = read(self.old/'helene.json'); cfg['relay'] = {'enabled':True, 'executable':'../independent-relay.exe'}
        write(self.old/'helene.json',cfg); self.prepare()
        self.assertEqual(read(self.new/'helene.json')['relay']['executable'],str(external))
    def test_one_startup_acknowledgement_retires_old_without_five_minute_trial(self):
        value=self.prepare();stages=[]
        def spawn(*a,**k):self.acknowledge(value);return types.SimpleNamespace(poll=lambda:None)
        args=types.SimpleNamespace(old=self.old,new=self.new,browser=False,job=None)
        with patch.object(clean.subprocess,'Popen',side_effect=spawn),patch.object(portable_switch,'health',side_effect=AssertionError('No trial')):
            self.assertEqual(clean.run(args,value,lambda *a,**k:stages.append(a),lambda:False),0)
        self.assertFalse(self.old.exists());self.assertEqual(read(self.new/'python-migration.json')['state'],'completed')
        self.assertEqual((self.data/'memory/history').read_bytes(),b'irreplaceable owner history')
    def test_stop_before_retirement_keeps_old_folder(self):
        value=self.prepare();clean.relocate(self.old,self.new,value)
        with self.assertRaises(InterruptedError):clean.finish(self.old,self.new,self.acknowledge(value),stopped=lambda:True)
        self.assertTrue(self.old.is_dir());self.assertEqual(self.events,[])
    def test_internal_data_and_unpinned_legacy_component_are_renamed_not_copied(self):
        internal=self.old/'profile';shutil.move(str(self.data),str(internal));self.data=internal
        cfg=read(self.old/'helene.json');cfg['tree']=str(internal);write(self.old/'helene.json',cfg)
        component=self.old/'voice/model.bin';component.parent.mkdir();component.write_bytes(b'legacy component')
        history=internal/'memory/history';history_inode=history.stat().st_ino;model_inode=component.stat().st_ino
        value=self.prepare();clean.relocate(self.old,self.new,value)
        target=Path(read(self.new/'helene.json')['tree'])
        self.assertFalse(target.is_relative_to(self.old));self.assertFalse(target.is_relative_to(self.new))
        self.assertEqual((target/'memory/history').stat().st_ino,history_inode)
        self.assertEqual((self.new/'voice/model.bin').stat().st_ino,model_inode)
        clean.restore(value)
        self.assertEqual((internal/'memory/history').stat().st_ino,history_inode)
        self.assertEqual(component.stat().st_ino,model_inode)
    def test_failed_new_start_returns_internal_data_before_leaving_old_programme(self):
        internal=self.old/'profile';shutil.move(str(self.data),str(internal));self.data=internal
        cfg=read(self.old/'helene.json');cfg['tree']=str(internal);write(self.old/'helene.json',cfg)
        value=self.prepare();args=types.SimpleNamespace(old=self.old,new=self.new,browser=False,job=None)
        with patch.object(clean.subprocess,'Popen',return_value=types.SimpleNamespace(poll=lambda:1)):
            with self.assertRaisesRegex(RuntimeError,'failed'):clean.run(args,value,lambda *a,**k:None,lambda:False)
        self.assertTrue(self.old.is_dir());self.assertEqual((internal/'memory/history').read_bytes(),b'irreplaceable owner history')
    def test_replaced_directory_and_nested_programme_cannot_be_retired(self):
        with self.assertRaises(ValueError):clean.roots(self.old,self.old/'new')
        value=self.prepare();note=self.acknowledge(value)
        self.old.rename(self.base/'original');self.old.mkdir()
        (self.old/'owner-file').write_bytes(b'new unrelated directory')
        with self.assertRaisesRegex(ValueError,'directory was replaced'):clean.finish(self.old,self.new,note)
        self.assertEqual((self.old/'owner-file').read_bytes(),b'new unrelated directory')
    def test_existing_pinned_component_is_reused_without_copying_payload(self):
        from localharness import component_store as store
        object_dir=self.base/'store/objects'/('c'*64);(object_dir/'payload').mkdir(parents=True)
        ref={'store':str(self.base/'store'),'identity':'c'*64,'compatibility':{}}
        write(object_dir/'receipt.json',{'identity':ref['identity'],'compatibility':{}})
        write(self.old/store.PINS,{'format':store.FORMAT,'pins':{'voice':ref}})
        with patch.object(store,'import_directory',side_effect=AssertionError('No component reimport')):
            self.prepare()
        self.assertEqual(store.export_references(self.new)['pins']['voice'],ref)
        self.assertFalse((self.new/'voice').exists())
    def test_store_inside_old_and_external_data_pins_are_moved_and_remapped_without_hashing(self):
        from localharness import component_store as store
        object_dir=self.old/'custom-store/objects'/('d'*64);(object_dir/'payload').mkdir(parents=True)
        model=object_dir/'payload/model.bin';model.write_bytes(b'actual retained model');inode=model.stat().st_ino
        ref={'store':str(self.old/'custom-store'),'identity':'d'*64,'compatibility':{}}
        write(object_dir/'receipt.json',{'identity':ref['identity'],'compatibility':{}})
        write(self.old/store.PINS,{'format':store.FORMAT,'pins':{'voice':ref}})
        write(self.data/store.PINS,{'format':store.FORMAT,'pins':{'models/whisper':ref}})
        with patch.object(store,'import_directory',side_effect=AssertionError('No component copy or hash')):
            value=self.prepare()
            self.assertEqual(store.export_references(self.data)['pins']['models/whisper']['store'],ref['store'])
            clean.relocate(self.old,self.new,value)
        new_ref=store.export_references(self.new)['pins']['voice']
        self.assertFalse(Path(new_ref['store']).is_relative_to(self.old))
        self.assertEqual((store._reference(new_ref)/'model.bin').stat().st_ino,inode)
        self.assertEqual(store.export_references(self.data)['pins']['models/whisper']['store'],new_ref['store'])
        clean.restore(value)
        self.assertEqual(model.stat().st_ino,inode)
        self.assertEqual(store.export_references(self.data)['pins']['models/whisper']['store'],ref['store'])
    def test_preserve_choice_cannot_be_silently_changed_to_clean(self):
        from localharness.update_source import choice_path
        write(choice_path(self.new),{'mode':'preserve','old':str(self.old),'new':str(self.new)})
        with self.assertRaisesRegex(ValueError,'Another source policy'):self.prepare()
        self.assertTrue(self.old.is_dir());self.assertFalse((self.new/'helene.json').exists())
    def test_incoming_helper_adopts_old_host_clean_overlay_without_revalidating_folders(self):
        clean.choose(self.old,self.new)
        legacy={'format':'helene-prepared-install.v1','old':str(self.old),'new':str(self.new),
            'note':{'source_policy':'clean'},'selection':{'historical':'receipt only'}}
        path=python_source_updater.prepared_install_path(self.new);write(path,legacy)
        value=clean.adopt_legacy(self.old,self.new)
        self.assertEqual(value['format'],clean.FORMAT)
        self.assertEqual(read(path.with_name('legacy-'+path.name)),legacy)
        self.assertTrue(self.old.is_dir())
    def test_focused_in_app_clean_handoff_skips_source_admission_and_review(self):
        import threading
        from localharness import owner_stop,session_exit
        staged=self.base/'stage/Helene';staged.parent.mkdir();self.new.rename(staged);self.new=staged
        owner=types.SimpleNamespace(root=self.old,event=lambda *a:None,update_job=None,stop=threading.Event())
        progress=types.SimpleNamespace(path=self.base/'progress.json',phase=lambda *a,**k:None)
        with patch.object(owner_stop,'stopped',return_value=False),patch.object(session_exit,'requested',return_value=False),\
             patch.object(python_updates,'prepare_code',side_effect=AssertionError('No merge or review')),\
             patch.object(python_updates,'_launch_handoff',return_value={'handed_off':True}) as launch:
            result=python_updates._prepare_handoff(owner,staged.parent,{},progress,False,'clean')
        self.assertTrue(result['handed_off']);launch.assert_called_once()
        self.assertEqual(read(python_source_updater.prepared_install_path(staged))['format'],clean.FORMAT)
    def test_manual_archive_ready_callback_removes_old_without_its_own_locked_file(self):
        from localharness import owner_stop,session_exit
        value=self.prepare();value['note']['app_retirement']=True;clean.relocate(self.old,self.new,value)
        self.acknowledge(value)
        with patch.object(owner_stop,'stopped',return_value=False),patch.object(session_exit,'requested',return_value=False):
            started=clean.start_retirement(self.new)
            clean._RETIREMENTS[str(self.new)].join(3)
        self.assertTrue(started['started']);self.assertFalse(self.old.exists())
        self.assertEqual(read(self.new/'python-migration.json')['state'],'completed')
    def test_known_python_archive_adoption_does_not_scan_siblings_or_make_retirement_inventory(self):
        write(self.data/'memory/.control/desktop-session.json',{'program':str(self.old)})
        write(self.data/'memory/.control/profile-identity.json',{'agent_name':'Fixture'})
        places=types.SimpleNamespace(data=self.data)
        backend=types.SimpleNamespace(exclusive=lambda _:contextlib.nullcontext(),stop=lambda *a:self.events.append('bounded-stop'),assert_quiet=lambda *a:None)
        with patch.object(first_start,'legacy_plan',side_effect=AssertionError('No programme discovery')):
            result=first_start.prepare(self.new,places=places,backend=backend)
        self.assertTrue(result['clean_switch']);self.assertTrue(self.old.is_dir())
        self.assertTrue(read(self.new/'python-migration.json')['app_retirement'])
    def test_clickable_clean_programme_uses_its_configured_data_without_first_start_sweep(self):
        import windows_start
        value=self.prepare();clean.relocate(self.old,self.new,value)
        with patch.dict(os.environ),patch.object(first_start,'handover',side_effect=AssertionError('No generic adoption sweep')),patch.object(windows_start,'ordinary') as launch:
            windows_start.main(['--root',str(self.new)],places=types.SimpleNamespace(data=self.base/'different-default'))
            self.assertEqual(Path(os.environ['HELENE_USER_DATA']),self.data)
        launch.assert_called_once_with(self.new,['--migration',value['note']['id']])
    def test_active_data_writer_prevents_relocation_and_old_retirement(self):
        value=self.prepare()
        with patch.object(migration_lifecycle,'owner_lease_available',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'still has a writer'):clean.relocate(self.old,self.new,value)
        self.assertTrue(self.old.is_dir());self.assertEqual((self.data/'memory/history').read_bytes(),b'irreplaceable owner history')
    def test_manual_archive_cleanup_is_owned_by_session_host_not_a_ui_window(self):
        value=self.prepare();value['note']['app_retirement']=True;write(self.new/'python-migration.json',value['note'])
        from localharness import portable_retirement
        host=types.SimpleNamespace(retirement=lambda:{'session_owned':True})
        with patch.object(clean,'start_retirement',side_effect=AssertionError('UI cannot own deletion')):
            self.assertEqual(portable_retirement.start(self.new,host),{'session_owned':True})
    def test_clean_helper_does_not_revive_old_hosts_five_minute_retirement_journal(self):
        self.prepare()
        from localharness import portable_retirement
        with patch.object(portable_retirement,'path',side_effect=AssertionError('Ignore superseded old trial')):
            result=portable_retirement.start(self.new)
        self.assertTrue(result['helper_owned'])
    def test_clean_helper_lease_covers_admission_and_cutover_and_lives_in_new(self):
        import python_migrate
        value=self.prepare();args=types.SimpleNamespace(new=self.new,cleanup=False)
        held=[]
        @contextlib.contextmanager
        def lease(target):
            held.append(Path(target));yield;held.pop()
        def perform(current):
            self.assertEqual(held,[self.new]);return 'admitted-under-lease'
        with patch.object(python_migrate,'installation_lock',side_effect=lease),patch.object(python_source_updater,'_perform',side_effect=perform):
            self.assertEqual(python_source_updater.perform(args),'admitted-under-lease')
        self.assertFalse(held)
    def internal_fixture(self):
        internal=self.old/'profile';shutil.move(str(self.data),str(internal));self.data=internal
        cfg=read(self.old/'helene.json');cfg['tree']=str(internal);write(self.old/'helene.json',cfg)
        value=self.prepare();args=types.SimpleNamespace(old=self.old,new=self.new,browser=False,job=None)
        def spawn(*a,**k):self.acknowledge(value);return types.SimpleNamespace(poll=lambda:None)
        return value,args,spawn
    def test_completed_helper_retry_never_stops_new_or_recreates_old_data_path(self):
        value,args,spawn=self.internal_fixture()
        with patch.object(clean.subprocess,'Popen',side_effect=spawn):clean.run(args,value,lambda *a,**k:None,lambda:False)
        target=Path(read(self.new/'helene.json')['tree']);self.assertFalse(self.old.exists())
        with patch.object(clean.subprocess,'Popen',side_effect=AssertionError('No second launch')),\
             patch.object(migration_lifecycle,'stop',side_effect=AssertionError('Never stop committed NEW')),\
             patch.object(migration_lifecycle,'owner_lease_available',return_value=False):
            self.assertEqual(clean.run(args,value,lambda *a,**k:None,lambda:False),0)
        self.assertFalse(self.old.exists());self.assertTrue(target.is_dir())
        self.assertEqual((target/'memory/history').read_bytes(),b'irreplaceable owner history')
    def test_partial_retirement_retry_only_finishes_cleanup_and_never_restores_data(self):
        value,args,spawn=self.internal_fixture()
        def partial(path,**kwargs):
            self.assertEqual(Path(path),self.old);(self.old/'app/python_app.py').unlink();raise OSError('controlled partial retirement')
        with patch.object(clean.subprocess,'Popen',side_effect=spawn),patch.object(clean.shutil,'rmtree',side_effect=partial):
            with self.assertRaisesRegex(OSError,'partial retirement'):clean.run(args,value,lambda *a,**k:None,lambda:False)
        target=Path(read(self.new/'helene.json')['tree'])
        self.assertEqual(read(self.new/'python-migration.json')['state'],'retiring_old');self.assertTrue(target.is_dir())
        with patch.object(clean.subprocess,'Popen',side_effect=AssertionError('No retry launch')),\
             patch.object(migration_lifecycle,'stop',side_effect=AssertionError('Never stop committed NEW')),\
             patch.object(migration_lifecycle,'owner_lease_available',return_value=False):
            self.assertEqual(clean.run(args,value,lambda *a,**k:None,lambda:False),0)
        self.assertFalse(self.old.exists());self.assertTrue(target.is_dir())
        self.assertEqual((target/'memory/history').read_bytes(),b'irreplaceable owner history')
    def test_writer_refusal_before_this_helper_launch_never_stops_existing_new(self):
        value=self.prepare();args=types.SimpleNamespace(old=self.old,new=self.new,browser=False,job=None)
        calls=[]
        with patch.object(migration_lifecycle,'stop',side_effect=lambda path:calls.append(Path(path))),\
             patch.object(migration_lifecycle,'owner_lease_available',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'still has a writer'):clean.run(args,value,lambda *a,**k:None,lambda:False)
        self.assertNotIn(self.new,calls);self.assertTrue(self.old.is_dir())
    def test_remote_profile_cache_is_retained_and_rebound_with_local_profiles(self):
        cache=self.old/'remote-cache';cache.mkdir();(cache/'private-state').write_bytes(b'remote local state')
        write(self.old/'agents/remote/helene.json',{'mode':'remote','tree':str(cache),'base':'https://example.invalid','token':'fixture'})
        value=self.prepare();clean.relocate(self.old,self.new,value)
        remote=read(self.new/'agents/remote/helene.json');target=Path(remote['tree'])
        self.assertTrue(target.is_absolute());self.assertTrue(target.is_dir())
        self.assertIn(str(target),value['note']['data_paths'])
        self.assertFalse(target.is_relative_to(self.old));self.assertEqual((target/'private-state').read_bytes(),b'remote local state')
        self.assertEqual(remote['token'],'fixture');self.assertEqual(remote['base'],'https://example.invalid')
    def test_remote_only_relative_existing_cache_and_missing_cache_get_independent_data(self):
        for existing in (True,False):
            with self.subTest(existing=existing):
                cache=self.old/('cache-'+str(existing))
                if existing:cache.mkdir()
                write(self.old/'helene.json',{'mode':'remote','tree':cache.name,'base':'https://example.invalid'})
                candidate=self.base/('remote-incoming-'+str(existing));candidate.mkdir();(candidate/'tree').mkdir()
                write(candidate/'python-app.json',{'version':'2.5.0-rc.1'})
                value=clean.prepare(self.old,candidate);clean.relocate(self.old,candidate,value)
                target=Path(read(candidate/'helene.json')['tree'])
                self.assertTrue(target.is_absolute());self.assertTrue(target.is_dir())
                self.assertIn(str(target),value['note']['data_paths'])
                self.assertFalse(target.is_relative_to(self.old));self.assertFalse(target.is_relative_to(candidate))
    def test_manual_archive_retry_resumes_incomplete_retirement_after_startup_ack(self):
        from localharness import owner_stop,session_exit
        value=self.prepare();value['note']['app_retirement']=True;clean.relocate(self.old,self.new,value);self.acknowledge(value)
        with patch.object(owner_stop,'stopped',return_value=False),patch.object(session_exit,'requested',return_value=False):
            with patch.object(clean.shutil,'rmtree',side_effect=OSError('controlled cleanup failure')):
                clean.start_retirement(self.new);clean._RETIREMENTS[str(self.new)].join(3)
            self.assertEqual(read(self.new/'python-migration.json')['state'],'retirement_incomplete')
            clean.start_retirement(self.new);clean._RETIREMENTS[str(self.new)].join(3)
        self.assertEqual(read(self.new/'python-migration.json')['state'],'completed');self.assertFalse(self.old.exists())
    def test_separate_archive_target_copies_into_its_owned_real_installation_lease(self):
        from localharness import python_archive_update
        target=self.base/'separate-target'
        note=python_archive_update.prepare(self.new,target,self.old,self.data,progress=lambda *a:None)
        self.assertEqual((target/'tree/authored.py').read_bytes(),b'pristine = True')
        self.assertEqual(Path(read(target/'helene.json')['tree']),self.data)
        self.assertEqual(note['new_root'],str(target));self.assertTrue(self.old.is_dir())
    def test_nested_archive_source_and_destination_refuse_before_copy_or_stop(self):
        from localharness import python_archive_update
        target=self.new/'nested'
        with self.assertRaisesRegex(ValueError,'independent'):
            python_archive_update.prepare(self.new,target,self.old,self.data,progress=lambda *a:None)
        self.assertFalse(target.exists());self.assertFalse(self.events)
    def test_replaced_old_directory_refuses_before_first_stop_or_data_move(self):
        value=self.prepare();self.old.rename(self.base/'original-old');self.old.mkdir()
        (self.old/'owner-material').write_bytes(b'unrelated new directory')
        args=types.SimpleNamespace(old=self.old,new=self.new,browser=False,job=None)
        with patch.object(clean.subprocess,'Popen',side_effect=AssertionError('No launch')):
            with self.assertRaisesRegex(ValueError,'directory was replaced'):
                clean.run(args,value,lambda *a,**k:None,lambda:False)
        self.assertFalse(self.events);self.assertEqual((self.old/'owner-material').read_bytes(),b'unrelated new directory')
        self.assertEqual((self.data/'memory/history').read_bytes(),b'irreplaceable owner history')

class CleanExtraction(unittest.TestCase):
    def test_clean_extract_uses_all_available_cores_and_no_per_member_sha(self):
        available=max(1,(getattr(os,'process_cpu_count',os.cpu_count)() or os.cpu_count() or 1))
        self.assertEqual(files.WORKERS,available)
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);archive=folder/'input.zip'
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as output:
                output.writestr('Helene/tree/agent.py',b'fresh source')
                output.writestr('Helene/runtime/python.exe',b'controlled runtime fixture')
            with patch.object(files.hashlib,'sha256',side_effect=AssertionError('No per-member SHA')):
                proof=files.extract(archive,folder/'out',verify=False)
            self.assertEqual(proof,{})
            self.assertEqual((folder/'out/Helene/tree/agent.py').read_bytes(),b'fresh source')
    def test_crc_failure_does_not_replace_output_and_path_escape_precedes_any_output(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);archive=folder/'input.zip';target=folder/'out/Helene/file';target.parent.mkdir(parents=True);target.write_bytes(b'old')
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_STORED) as output:output.writestr('Helene/file',b'fresh')
            with zipfile.ZipFile(archive) as source:entry=source.getinfo('Helene/file')
            raw=bytearray(archive.read_bytes());raw[entry.header_offset+30+len(entry.filename.encode())+len(entry.extra)]^=1;archive.write_bytes(raw)
            with self.assertRaises(zipfile.BadZipFile):files.extract(archive,folder/'out',verify=False)
            self.assertEqual(target.read_bytes(),b'old');self.assertFalse(list(folder.rglob('*.part')))
            with zipfile.ZipFile(archive,'w') as output:output.writestr('Helene/../escape',b'bad')
            with self.assertRaises(ValueError):files.extract(archive,folder/'out',verify=False)
            self.assertFalse((folder/'out/escape').exists())

if __name__=='__main__':unittest.main()
