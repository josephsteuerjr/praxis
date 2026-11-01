"""Accepted old preparer -> incoming CLEAN, without any host OS authority."""
import ast
import copy
from contextlib import contextmanager
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from localharness import authored_baseline,code_transition,first_start as start
from localharness import first_start_assets,portable_retirement as retirement
from localharness import portable_switch as switch,python_source_updater as updater
from localharness import migration_lifecycle,program_state,update_profile,update_source
from localharness import vision_policy,windows_first_start,windows_hooks
from localharness.package_notice import read,write
from t_first_start_3010 import Backend

_FIXTURE=Path(__file__).with_name('fixtures')/'legacy_preparer_3010.py'
_SPEC=importlib.util.spec_from_file_location('localharness._legacy_preparer_3010',_FIXTURE)
legacy=importlib.util.module_from_spec(_SPEC);_SPEC.loader.exec_module(legacy)


class Socket:
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def bind(self,address):pass
    def getsockname(self):return ('127.0.0.1',32123)


class LegacyPreparer(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='helene-old-preparer-')
        self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name).resolve()
        self.profile=self.base/'User';self.downloads=self.profile/'Downloads'
        self.local=self.profile/'AppData/Local';self.pf=self.base/'Program Files'
        for folder in (self.downloads,self.local,self.pf):folder.mkdir(parents=True)
        self.places=start.Places(self.profile,(self.local,),(self.pf,),self.downloads)
        self.data=self.places.data
        self.old=self.downloads/'Helene old accepted'
        self.new=self.downloads/'Helene-update-clean-fixture/Helene'
        self.backend=Backend()
        def forbidden(*args,**kwargs):raise AssertionError('No host OS or process authority in preparer fixture')
        for name in ('kernel','terminate','restart_holders','user_sid'):
            self.enterContext(patch.object(windows_first_start,name,side_effect=forbidden))
        self.enterContext(patch.object(windows_hooks,'retire',side_effect=forbidden))
        self.enterContext(patch.object(subprocess,'Popen',side_effect=forbidden))
        self.enterContext(patch.object(subprocess,'run',side_effect=forbidden))
        self.backend_factory=self.enterContext(patch.object(windows_first_start,'Backend',return_value=self.backend))
        self.enterContext(patch.object(start.Places,'current',return_value=self.places))
        self.enterContext(patch.object(program_state,'hidden',side_effect=lambda path:Path(path)))
        self.enterContext(patch.object(start.socket,'socket',return_value=Socket()))
        def private_only(path):
            if not Path(path).resolve().is_relative_to(self.base):raise AssertionError('Private write leaves fixture')
        self.enterContext(patch.object(vision_policy,'_protect_private_temp',side_effect=private_only))
        fake_psutil=SimpleNamespace(process_iter=Mock(side_effect=forbidden),Process=Mock(side_effect=forbidden),
            pid_exists=Mock(side_effect=forbidden),pids=Mock(side_effect=forbidden))
        self.enterContext(patch.dict(sys.modules,{'psutil':fake_psutil}))
        self.put(self.data/'memory/.control/profile-identity.json',b'{"id":"owner","agent_name":"Owner"}\n')
        self.put(self.data/'memory/.control/desk_inbox/pending.md',b'pending owner turn\n')
        self.put(self.data/'workspace/project/Cargo.lock',b'owner dependency resolution\n')
        self.make_program(self.old,old=True)
        self.make_program(self.new,old=False)
        self.put(self.old/'assets/backup.bin',b'backup bytes\x00\xff')
        self.put(self.old/'assets/reference.wav',b'RIFF owner reference\x00\xfe')
        config=read(self.old/'helene.json')
        config.update(backup={'enabled':True,'dir':'assets'},voice={'tts_reference':'assets/reference.wav'})
        write(self.old/'helene.json',config)
        self.original=self.snapshot(self.old)

    def put(self,path,raw):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);return path

    def make_program(self,program,*,old):
        self.put(program/'tree/agent.py',b'agent = "old authored"\n' if old else b'agent = "incoming"\n')
        self.put(program/'tree/llm.py',b'LLM_ENDPOINTS_API=1\n')
        self.put(program/'app/python_app.py',b'application = "old authored"\n' if old else b'application = "incoming"\n')
        self.put(program/'app/deskapp.py',b'desk = 1\n')
        self.put(program/'app/localharness/runner.py',b'runner = 1\n')
        self.put(program/'app/localharness/python_source_updater.py',b'helper = "incoming"\n')
        self.put(program/'runtime/python.exe',b'MZ private symbolic runtime')
        self.put(program/'helene.exe',b'MZ private symbolic launcher')
        write(program/'python-app.json',{'format':'helene-python-app.v1','version':'old-simple12' if old else 'incoming-v2'})
        write(program/'python-package-files.json',{'files':{name:{'sha256':hashlib.sha256((program/name).read_bytes()).hexdigest()}
            for name in ('runtime/python.exe','helene.exe')}})
        code_transition.seal(program)
        if not old:authored_baseline.seal(program)
        write(program/'helene.json',{'mode':'local','tree':str(self.data),'code':str(program/'tree'),
            'agent':{'name':'Owner'},'relay':{'enabled':False},'setup_complete':True})
        self.admit(program)
        return program

    def admit(self,program):
        write(start.receipt(program),{'format':start.FORMAT,'state':'completed',
            'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'data':str(self.data)})

    def snapshot(self,root):
        return {path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()}

    def prepared(self,*,by='window'):
        update_source.select(self.old,self.new,'clean',by=by)
        # These two unchanged accepted function bodies run with incoming
        # dependencies. Only old selection's exact schema is reinstated.
        with patch.object(update_profile,'selection',side_effect=legacy.selection):
            value=legacy.prepare(self.old,self.new)
        self.assertEqual(set(value['selection']),{'settings','sources','git'})
        self.assertNotIn('retirement',value);self.assertEqual(len(value['relocations']),2)
        self.assertFalse(self.backend.actions)
        return value

    def fresh(self,value):
        return updater._prepared_identity(self.old,self.new)

    def promote(self,value):
        switch.identity(self.old,self.new,value)
        return switch.promote_legacy_clean(self.old,self.new,value)

    def test_accepted_source_fragments_remain_exact(self):
        text=_FIXTURE.read_text(encoding='utf8');tree=ast.parse(text)
        for filename,name in (('portable_switch.py','prepare'),('update_profile.py','selection'),('portable_switch.py','payload')):
            node=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name==name)
            fragment='\n'.join(text.splitlines()[node.lineno-1:node.end_lineno])+'\n'
            self.assertEqual(hashlib.sha256(fragment.encode()).hexdigest(),legacy.SOURCE_FRAGMENTS[filename+':'+name])

    def test_real_old_preparer_is_admitted_before_any_shutdown_and_persists_discard_proof(self):
        value=self.prepared();self.backend_factory.reset_mock()
        self.assertNotIn('baseline',value['payload'])
        self.assertIn('baseline',switch.payload(self.new))
        with patch.object(migration_lifecycle,'stop') as stop,patch.object(migration_lifecycle,'quiescent') as quiet:
            admitted=self.fresh(value)
        self.assertEqual(admitted['legacy_clean']['format'],switch.LEGACY_CLEAN)
        self.assertIn('app',admitted['legacy_clean']['selection'])
        self.assertNotIn('app',admitted['selection'])
        self.assertEqual(read(updater.prepared_install_path(self.new)),admitted)
        self.backend_factory.assert_not_called();stop.assert_not_called();quiet.assert_not_called()
        self.assertFalse(self.backend.actions)
        self.assertFalse((self.old/'source-update-baseline.json').exists())
        self.assertEqual(authored_baseline.mode(self.old),'clean')
        with self.assertRaises(ValueError):authored_baseline.mode(self.old,'preserve')
        switch.validate(self.old,self.new,admitted)

    def test_implicit_updater_clean_is_not_explicit_owner_consent(self):
        value=self.prepared(by='updater')
        with self.assertRaisesRegex(ValueError,'чистую замену'):switch.identity(self.old,self.new,value)
        self.assertNotIn('legacy_clean',read(updater.prepared_install_path(self.new)))
        self.assertFalse(self.backend.actions)

    def test_missing_or_preserve_choice_refuses_before_effects(self):
        value=self.prepared();choice=update_source.choice_path(self.new);saved=read(choice)
        choice.unlink()
        with self.assertRaises(ValueError):switch.identity(self.old,self.new,value)
        saved['mode']='preserve';write(choice,saved)
        with self.assertRaisesRegex(ValueError,'распакуй свежий архив'):switch.identity(self.old,self.new,value)
        self.assertNotIn('legacy_clean',read(updater.prepared_install_path(self.new)))
        self.assertFalse(self.backend.actions)

    def test_mismatched_clean_note_or_plan_cannot_silently_upgrade(self):
        value=self.prepared();value['note']['source_choice']['by']='updater'
        write(updater.prepared_install_path(self.new),value)
        with self.assertRaises(ValueError):switch.identity(self.old,self.new,value)
        self.assertFalse(self.backend.actions)

    def test_changed_old_source_or_settings_refuses_before_admission(self):
        value=self.prepared();self.put(self.old/'tree/agent.py',b'changed after clean consent\n')
        with self.assertRaisesRegex(ValueError,'changed'):switch.identity(self.old,self.new,value)
        self.assertNotIn('legacy_clean',read(updater.prepared_install_path(self.new)))

    def test_changed_old_settings_refuses_before_admission(self):
        value=self.prepared();config=read(self.old/'helene.json');config['agent']['name']='Changed after clean consent'
        write(self.old/'helene.json',config)
        with self.assertRaisesRegex(ValueError,'changed'):switch.identity(self.old,self.new,value)
        self.assertNotIn('legacy_clean',read(updater.prepared_install_path(self.new)))

    def test_changed_valid_old_receipt_after_admission_refuses(self):
        value=self.prepared();switch.identity(self.old,self.new,value)
        record=read(start.receipt(self.old));record['another_admission']='new receipt identity';write(start.receipt(self.old),record)
        with self.assertRaisesRegex(ValueError,'admission changed'):switch.validate(self.old,self.new,value)
        self.assertFalse(self.backend.actions)

    def test_changed_app_after_admission_refuses_without_manufacturing_baseline(self):
        value=self.prepared();switch.identity(self.old,self.new,value)
        self.put(self.old/'app/owner-added.py',b'authored after admission\n')
        with self.assertRaisesRegex(ValueError,'admission changed'):switch.validate(self.old,self.new,value)
        self.assertFalse((self.old/'app-upstream.zip').exists());self.assertFalse(self.backend.actions)

    def test_changed_prepared_receipt_is_not_overwritten_by_admission(self):
        value=self.prepared();tampered=copy.deepcopy(value);tampered['progress']='another owner'
        write(updater.prepared_install_path(self.new),tampered)
        with self.assertRaisesRegex(ValueError,'receipt changed'):switch.identity(self.old,self.new,value)
        self.assertEqual(read(updater.prepared_install_path(self.new)),tampered)

    def test_incoming_app_runtime_and_baseline_integrity_are_required(self):
        value=self.prepared();path=self.new/'app/deskapp.py';saved=path.read_bytes();path.write_bytes(b'changed app')
        with self.assertRaisesRegex(ValueError,'exact incoming application'):switch.identity(self.old,self.new,value)
        path.write_bytes(saved);path=self.new/'runtime/python.exe';saved=path.read_bytes();path.write_bytes(b'changed runtime')
        with self.assertRaisesRegex(ValueError,'runtime differs'):switch.identity(self.old,self.new,value)
        path.write_bytes(saved);(self.new/'app-upstream.zip').write_bytes(b'tampered baseline')
        with self.assertRaises(ValueError):switch.identity(self.old,self.new,value)
        self.assertFalse(self.backend.actions)

    def test_prepared_payload_and_asset_freshness_survive_pre_exit_admission(self):
        value=self.prepared();switch.identity(self.old,self.new,value)
        path=self.old/'assets/reference.wav';saved=path.read_bytes();path.write_bytes(b'new voice after admission')
        with self.assertRaisesRegex(ValueError,'admission changed'):switch.promote_legacy_clean(self.old,self.new,value)
        path.write_bytes(saved);self.put(self.new/'helene.json',b'{"changed":true}')
        with self.assertRaisesRegex(ValueError,'payload changed'):switch.promote_legacy_clean(self.old,self.new,value)
        self.assertFalse((self.data/'retained-assets').exists());self.assertFalse(self.backend.actions)

    def test_valid_replacement_incoming_baseline_cannot_replace_admitted_delivery(self):
        value=self.prepared();switch.identity(self.old,self.new,value)
        metadata=read(self.new/'python-app.json');metadata['unreviewed_delivery']='changed';write(self.new/'python-app.json',metadata)
        with self.assertRaises(ValueError):switch.validate(self.old,self.new,value)
        self.assertFalse(self.backend.actions)

    def test_promotion_coalesces_old_moves_into_verified_copies_and_captures_all_receipts(self):
        others=[self.make_program(self.local/'Programs/Helene other',old=True),
                self.make_program(self.pf/'Helene installed',old=True),
                self.make_program(self.downloads/'Helene old wrapper/Helene',old=True)]
        self.put(others[-1].parent/'owner-note.txt',b'old wrapper retained during trial')
        value=self.prepared();before={program:self.snapshot(program) for program in [self.old,*others]}
        old_payload=copy.deepcopy(value['payload']);self.promote(value)
        self.assertIn('app',value['selection']);self.assertFalse(value['relocations'])
        self.assertIn('baseline',value['payload'])
        self.assertNotEqual(value['payload']['settings'],old_payload['settings'])
        self.assertEqual(read(updater.prepared_install_path(self.new)),value)
        captured=read(retirement.path(self.new));retirement.validate(self.new,captured,self.places)
        targets={Path(row['root']) for row in captured['targets']}
        self.assertEqual(targets,{self.old,*others[:-1],others[-1].parent})
        self.assertEqual(len(captured['assets']),1)
        asset=captured['assets'][0];self.assertEqual(Path(asset['source']),self.old/'assets')
        self.assertEqual(first_start_assets.snapshot(asset['source']),first_start_assets.snapshot(asset['destination']))
        config=read(self.new/'helene.json');backup=Path(config['backup']['dir']);voice=Path(config['voice']['tts_reference'])
        self.assertEqual(voice,backup/'reference.wav');self.assertTrue(voice.is_file())
        for program,contents in before.items():self.assertEqual(self.snapshot(program),contents)
        self.assertEqual((self.data/'memory/.control/desk_inbox/pending.md').read_bytes(),b'pending owner turn\n')
        self.assertEqual((self.data/'workspace/project/Cargo.lock').read_bytes(),b'owner dependency resolution\n')
        switch.relocate_assets(value,restore=True)
        self.assertTrue((self.old/'assets/reference.wav').exists())
        switch.validate(self.old,self.new,value)

    def test_deleted_incoming_baseline_after_admission_refuses_legacy_promotion(self):
        value=self.prepared();switch.identity(self.old,self.new,value)
        (self.new/'app-upstream.zip').unlink()
        with self.assertRaises((OSError,ValueError)):switch.promote_legacy_clean(self.old,self.new,value)
        self.assertFalse((self.data/'retained-assets').exists());self.assertFalse(self.backend.actions)
        self.assertNotIn('retirement',read(updater.prepared_install_path(self.new)))

    def test_coherent_metadata_change_after_promotion_is_not_accepted_as_old_payload_abi(self):
        value=self.prepared();self.promote(value)
        metadata=read(self.new/'python-app.json');metadata['changed_delivery_label']='unreviewed';write(self.new/'python-app.json',metadata)
        authored_baseline.validate(self.new)  # Still valid internally, but no longer the admitted bytes.
        with self.assertRaisesRegex(ValueError,'payload changed'):switch.validate(self.old,self.new,value)
        self.assertTrue(self.old.is_dir())

    def test_selected_old_canonical_receipt_is_required_before_exit(self):
        value=self.prepared();start.receipt(self.old).unlink()
        with self.assertRaisesRegex(ValueError,'квитанцией'):switch.identity(self.old,self.new,value)
        self.assertFalse(self.backend.actions)

    def test_profile_outside_canonical_receipt_is_refused_before_exit(self):
        external=self.base/'Other profile';self.put(external/'memory/keep',b'other profile')
        config=read(self.old/'helene.json');config['tree']=str(external);write(self.old/'helene.json',config)
        value=self.prepared()
        with self.assertRaisesRegex(ValueError,'профиле'):switch.identity(self.old,self.new,value)
        self.assertFalse(self.backend.actions)

    def test_default_backup_directory_is_rebound_and_copied(self):
        config=read(self.old/'helene.json');config['backup']={'enabled':True};config.pop('voice',None)
        write(self.old/'helene.json',config);self.put(self.old/'backups/default.bin',b'default backup')
        update_source.select(self.old,self.new,'clean',by='window')
        with patch.object(update_profile,'selection',side_effect=legacy.selection):value=legacy.prepare(self.old,self.new)
        self.assertFalse(value['relocations']);self.promote(value)
        folder=Path(read(self.new/'helene.json')['backup']['dir'])
        self.assertEqual((folder/'default.bin').read_bytes(),b'default backup')
        self.assertEqual((self.old/'backups/default.bin').read_bytes(),b'default backup')

    def test_copy_failure_preserves_old_references_and_does_not_launch_or_upgrade(self):
        value=self.prepared();switch.identity(self.old,self.new,value)
        before=(self.old/'helene.json').read_bytes()
        with patch.object(first_start_assets,'copy_asset',side_effect=OSError('synthetic copy fault')):
            with self.assertRaisesRegex(OSError,'copy fault'):switch.promote_legacy_clean(self.old,self.new,value)
        self.assertEqual((self.old/'helene.json').read_bytes(),before)
        self.assertTrue((self.old/'assets/reference.wav').exists());self.assertNotIn('retirement',value)
        self.assertNotIn('retirement',read(updater.prepared_install_path(self.new)))

    def test_legacy_destination_collision_or_unused_row_refuses(self):
        value=self.prepared();destination=Path(value['relocations'][0]['destination'])
        self.put(destination/'foreign.bin',b'foreign existing data')
        with self.assertRaisesRegex(ValueError,'destination already exists'):switch.identity(self.old,self.new,value)
        self.assertEqual((destination/'foreign.bin').read_bytes(),b'foreign existing data')

    def test_unused_legacy_relocation_cannot_authorize_an_asset_transfer(self):
        value=self.prepared();row=copy.deepcopy(value['relocations'][0])
        row['destination']=str(Path(row['destination']).with_name('unreferenced'))
        value['relocations'].append(row);write(updater.prepared_install_path(self.new),value)
        switch.identity(self.old,self.new,value)
        with self.assertRaisesRegex(ValueError,'not referenced'):switch.promote_legacy_clean(self.old,self.new,value)
        self.assertFalse((self.data/'retained-assets').exists());self.assertFalse(self.backend.actions)

    def test_trial_quiesces_then_promotes_sweeps_and_enters_shared_owner_before_launch(self):
        value=self.prepared();switch.identity(self.old,self.new,value)
        events=[]
        @contextmanager
        def owner(args,prepared):
            events.append('owner-enter');self.assertIn('retirement',prepared)
            self.assertEqual(read(updater.prepared_install_path(self.new)),prepared)
            yield
            events.append('owner-exit')
        def tail(args,prepared,progress,stopped):events.append('launch-tail');return 0
        promote=switch.promote_legacy_clean
        def promoted(*args,**kwargs):
            self.assertEqual(events[-1],'quiet');self.assertFalse((self.data/'retained-assets').exists())
            result=promote(*args,**kwargs);events.append('promote');return result
        module=SimpleNamespace(owner=owner)
        args=SimpleNamespace(old=self.old,new=self.new,pid=0)
        with patch.object(migration_lifecycle,'stop',side_effect=lambda p:events.append('stop')),\
             patch.object(migration_lifecycle,'quiescent',side_effect=lambda p:events.append('quiet')),\
             patch.object(switch,'promote_legacy_clean',side_effect=promoted),\
             patch.object(start,'sweep_legacy',side_effect=lambda *a,**k:events.append('sweep')),\
             patch.object(switch,'_trial_admitted',side_effect=tail),patch.object(updater,'_job_report'),\
             patch.dict(sys.modules,{'localharness.retirement_update':module}):
            import localharness
            with patch.object(localharness,'retirement_update',module,create=True):
                self.assertEqual(switch._trial(args,value,Mock(),lambda:False),0)
        self.assertEqual(events,['stop','quiet','promote','sweep','owner-enter','launch-tail','owner-exit'])
        self.assertFalse(self.backend.actions)

    def test_quiescence_failure_cannot_copy_assets_or_enter_new_trial(self):
        value=self.prepared();switch.identity(self.old,self.new,value)
        with patch.object(migration_lifecycle,'stop'),patch.object(migration_lifecycle,'quiescent',side_effect=RuntimeError('still live')),\
             patch.object(switch,'promote_legacy_clean') as promote,patch.object(updater,'_job_report'):
            with self.assertRaisesRegex(RuntimeError,'still live'):
                switch._trial(SimpleNamespace(old=self.old,new=self.new),value,Mock(),lambda:False)
        promote.assert_not_called();self.assertFalse((self.data/'retained-assets').exists())

    def test_owner_stop_during_promotion_cannot_launch_new_programme(self):
        with self.assertRaises(InterruptedError):
            switch._trial_admitted(SimpleNamespace(old=self.old,new=self.new),{'note':{'id':'test'}},Mock(),lambda:True)

    def test_old_clean_preparer_completes_shared_trial_with_real_fake_os_retirement(self):
        from localharness import owner_stop,retirement_update,session_exit
        value=self.prepared();switch.identity(self.old,self.new,value)
        other=self.make_program(self.local/'Programs/Helene other',old=True)
        protected={self.old:self.snapshot(self.old),other:self.snapshot(other)}
        args=SimpleNamespace(old=self.old,new=self.new,staging=self.new.parent,
            admission_name='updater-admitted.json',pid=0)
        self.put(self.new.parent/'python_source_updater.py',(self.new/'app/localharness/python_source_updater.py').read_bytes())
        write(self.new.parent/args.admission_name,{'target':str(self.old),'pid':os.getpid()})
        @contextmanager
        def lease(*args,**kwargs):yield True
        class Clock:
            now=1.0
            def read(self):return self.now
            def sleep(self,seconds):self.now+=seconds
        clock=Clock();observed=[]
        def healthy(program):
            observed.append(clock.now)
            if clock.now<1+switch.TRIAL_SECONDS:
                for root,contents in protected.items():self.assertEqual(self.snapshot(root),contents)
            return 'synthetic-new-generation'
        def launch(*args,**kwargs):
            self.assertEqual(read(updater.prepared_install_path(self.new)),value)
            self.assertIn('completion',read(retirement.path(self.new)))
            note=read(self.new/'python-migration.json');note['state']='new_app_started';write(self.new/'python-migration.json',note)
            return SimpleNamespace(poll=lambda:None)
        with patch.object(migration_lifecycle,'stop'),patch.object(migration_lifecycle,'quiescent'),\
             patch.object(retirement,'lease',side_effect=lease),patch.object(retirement_update,'_self',return_value={'pid':1001,'birth':1.0}),\
             patch.object(switch.subprocess,'Popen',side_effect=launch),patch.object(switch,'health',side_effect=healthy),\
             patch.object(switch.time,'monotonic',side_effect=clock.read),patch.object(switch.time,'sleep',side_effect=clock.sleep),\
             patch.object(owner_stop,'stopped',return_value=False),patch.object(session_exit,'requested',return_value=False),\
             patch.object(updater,'_job_report'):
            self.assertEqual(switch._trial(args,value,Mock(),lambda:False),0)
        self.assertGreaterEqual(max(observed)-min(observed),switch.TRIAL_SECONDS)
        self.assertFalse(self.old.exists());self.assertFalse(other.exists());self.assertTrue(self.new.is_dir())
        complete=read(retirement.path(self.new));self.assertEqual(complete['state'],'completed')
        self.assertIn('finalized_at',complete)
        self.assertEqual(set(complete['removed']),{str(self.old),str(other)})
        config=read(self.new/'helene.json');reference=Path(config['voice']['tts_reference'])
        self.assertEqual(reference.read_bytes(),b'RIFF owner reference\x00\xfe')
        self.assertFalse(updater.prepared_install_path(self.new).exists())
        self.assertEqual((self.data/'workspace/project/Cargo.lock').read_bytes(),b'owner dependency resolution\n')


if __name__=='__main__':unittest.main()
