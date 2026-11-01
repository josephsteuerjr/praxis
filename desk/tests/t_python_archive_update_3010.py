"""Portable path custody/order; merge/full-review has separate real-child gates."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_archive_update as archive,python_updates,code_transition
from localharness import migration_source,migration_lifecycle,source_admission,source_update_host
from localharness.package_notice import read,write
_verify_upstream=migration_source.verify

class Archive(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='python-archive-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.old=self.root/'previous';self.new=self.root/'incoming';self.data=self.root/'agent-data'
        for p in (self.old/'app',self.old/'tree',self.new/'tree',self.data/'memory'):p.mkdir(parents=True)
        (self.old/'app/python_app.py').write_text('# previous Python programme\n')
        write(self.old/'python-app.json',{'version':'2.3.0'})
        write(self.new/'python-app.json',{'version':'2.3.1'})
        write(self.old/'helene.json',{'tree':str(self.data),'code':'tree','model':{'key':'fixture-only'},'autonomy':{'paused':True}})
        (self.data/'memory/history.jsonl').write_bytes(b'authored history\n')
        (self.old/'tree/module.py').write_bytes(b'authored=True\n')
        self.events=[]
        for target,method,kwargs in ((source_update_host,'verified_runtime',{'return_value':{'fixture_only':True}}),
            (source_admission,'activate',{}),(migration_source,'verify',{}),
            (migration_lifecycle,'stop',{'side_effect':lambda *a:self.events.append('stop') or {'stopped':True}}),
            (migration_lifecycle,'quiescent',{})):
            mocker=patch.object(target,method,**kwargs);mocker.start();self.addCleanup(mocker.stop)

    def test_prepare_keeps_data_and_old_programme_until_verified_new_start(self):
        def reviewed(*args,**kwargs):
            self.assertEqual(kwargs['sourceMode'],'clean');self.assertFalse(kwargs['require_review'])
            self.events.append('prepare-code')
        def selected(old,new,note,**kwargs):
            self.assertEqual(self.events,['prepare-code','stop'])
            target=new/'tree/module.py';target.write_bytes(b'pristine=True\n')
            cfg=read(new/'helene.json');cfg['code']=str(target.parent);write(new/'helene.json',cfg)
            (new/'code-transition-plan.json').write_text('{}')
        with patch.object(python_updates,'prepare_code',side_effect=reviewed),patch.object(code_transition,'prepare',side_effect=selected),patch.object(code_transition,'validate'):
            note=archive.prepare(self.new,self.new,self.old,self.data,progress=lambda *a:None)
        self.assertTrue(note['normal_python_update']);self.assertEqual(note['source_policy'],'clean')
        self.assertTrue((self.old/'app/python_app.py').is_file());self.assertEqual((self.old/'tree/module.py').read_bytes(),b'authored=True\n')
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),b'authored history\n')
        cfg=read(self.new/'helene.json');self.assertEqual(Path(cfg['tree']),self.data);self.assertTrue(cfg['autonomy']['paused'])
        self.assertFalse(any(self.new.glob('**/history.jsonl')))
        with self.assertRaisesRegex(ValueError,'confirmed startup'):archive.validate_retirement(self.old,self.new,note)

    def test_failed_pristine_preparation_does_not_stop_or_replace_previous_programme(self):
        with patch.object(python_updates,'prepare_code',side_effect=RuntimeError('pristine verification unresolved')):
            with self.assertRaisesRegex(RuntimeError,'pristine verification unresolved'):
                archive.prepare(self.new,self.new,self.old,self.data,progress=lambda *a:None)
        self.assertFalse(self.events);self.assertFalse((self.new/'helene.json').exists())
        self.assertEqual((self.old/'tree/module.py').read_bytes(),b'authored=True\n')

    def test_archive_rejects_preserve_before_runtime_or_process_actions(self):
        with patch.object(python_updates,'prepare_code') as prepare:
            with self.assertRaisesRegex(ValueError,'совмещение'):
                archive.prepare(self.new,self.new,self.old,self.data,progress=lambda *a:None,source_mode='preserve')
            prepare.assert_not_called()
        self.assertFalse(self.events)

    def test_data_inside_old_programme_requires_explicit_external_destination(self):
        inside=self.old/'data';inside.mkdir()
        with patch.object(python_updates,'prepare_code') as review:
            with self.assertRaisesRegex(ValueError,'permanent folder'):
                archive.prepare(self.new,self.new,self.old,inside,progress=lambda *a:None)
            review.assert_not_called()
        self.assertFalse(self.events)

    def test_private_ui_state_moves_without_copy_and_unknown_files_remain(self):
        import hashlib
        private=self.old/'.helene';(private/'webview').mkdir(parents=True)
        draft=private/'webview/draft';draft.write_bytes(b'owner draft')
        identity=draft.stat().st_ino
        settings=self.old/'helene.json'
        backup=private/'settings-backups/settings.zip';backup.parent.mkdir();backup.write_bytes(b'fixture settings receipt')
        personal=self.old/'personal.txt';personal.write_bytes(b'unknown personal file')
        (self.old/'licenses/empty').mkdir(parents=True)
        note={'id':'a'*32,'normal_python_update':True,'startup_proof':{'fixture_only':True},
              'backup':str(backup),'old_programme_roots':['licenses'],
              'old_settings_sha256':{'helene.json':hashlib.sha256(settings.read_bytes()).hexdigest()}}
        result=archive.retire_metadata(self.old,self.new,note)
        moved=Path(note['previous_programme_state'])/'webview/draft'
        self.assertEqual(moved.read_bytes(),b'owner draft');self.assertEqual(moved.stat().st_ino,identity)
        self.assertEqual(personal.read_bytes(),b'unknown personal file')
        self.assertFalse(result['old_folder_removed']);self.assertFalse(private.exists())
        self.assertTrue(Path(note['backup']).is_file())
        self.assertFalse((self.old/'licenses').exists())

    def test_declared_runtime_models_module_retires_but_extra_or_changed_bytes_remain(self):
        import hashlib
        names=('runtime/Lib/site-packages/pkg/models/schema.py','runtime/Lib/site-packages/pkg/models/changed.py')
        inventory={}
        for name in names:
            path=self.old/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'packaged=True\n')
            inventory[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.old/names[1]).write_bytes(b'later authored bytes\n')
        extra=self.old/'runtime/Lib/site-packages/pkg/models/private.bin';extra.write_bytes(b'unknown model data')
        weights=self.old/'models/weights.bin';weights.parent.mkdir();weights.write_bytes(b'owner model')
        write(self.old/'python-package-files.json',{'files':inventory})
        note={'id':'f'*32,'normal_python_update':True,'state':'new_app_started','source_policy':'preserve',
              'startup_proof':{'fixture_only':True},'data_paths':[str(self.data)],'old_data_paths':[],'source_paths':[]}
        with patch.object(archive,'validate_retirement'):
            migration_lifecycle.remove_program(self.old,self.new,note)
        self.assertFalse((self.old/names[0]).exists())
        self.assertEqual((self.old/names[1]).read_bytes(),b'later authored bytes\n')
        self.assertEqual(extra.read_bytes(),b'unknown model data');self.assertEqual(weights.read_bytes(),b'owner model')

    def test_active_data_update_blocks_separate_bootstrap_before_programme_copy(self):
        from localharness import desktop_update_request as jobs,migration_job,owner_stop
        previous={'id':'b'*32,'kind':'source-update','state':'handed_off','tree':str(self.data),'program':str(self.old)}
        jobs._save(self.data,previous)
        before=jobs.path(self.data).read_bytes()
        target=self.root/'not-created'
        with patch.object(owner_stop,'stopped',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'existing update'):
                migration_job.admit(self.new,target,old=self.old,data=self.data)
        self.assertFalse(target.exists());self.assertEqual(jobs.path(self.data).read_bytes(),before)

    def test_extraction_timestamps_do_not_repack_or_invalidate_bound_upstream(self):
        import os
        from localharness import update_source
        module=self.new/'tree/module.py';module.write_bytes(b'pristine=True\n')
        code_transition.seal(self.new)
        snapshot=(self.new/'code-upstream.zip').read_bytes()
        choice=update_source.select(self.old,self.new,'clean',by='updater')
        os.utime(module,(module.stat().st_atime,module.stat().st_mtime+3600))
        with patch.object(migration_source,'verify',side_effect=_verify_upstream):
            code_transition._keep_upstream(self.new)
        self.assertEqual((self.new/'code-upstream.zip').read_bytes(),snapshot)
        self.assertEqual(update_source.selected(self.old,self.new),choice)
        self.assertFalse((self.new.parent/'source-choice.json').exists())
        self.assertTrue(update_source.choice_path(self.new).is_relative_to(self.new/'.helene'))
        module.write_bytes(b'changed_after_admission=True\n')
        with patch.object(migration_source,'verify',side_effect=_verify_upstream),self.assertRaisesRegex(ValueError,'pristine'):
            code_transition._keep_upstream(self.new)
        self.assertEqual((self.new/'code-upstream.zip').read_bytes(),snapshot)

    def test_already_admitted_legacy_choice_is_rejoined_without_sibling_writes_for_new_jobs(self):
        from localharness import update_source
        (self.new/'tree/module.py').write_bytes(b'pristine=True\n');code_transition.seal(self.new)
        choice=update_source.select(self.old,self.new,'clean',by='updater')
        current=update_source.choice_path(self.new);legacy=self.new.parent/'source-choice.json'
        current.replace(legacy)
        self.assertEqual(update_source.selected(self.old,self.new),choice)
        self.assertEqual(update_source.choice_path(self.new),legacy)
        # Another archive beside it gets its own private choice; its authority
        # cannot be borrowed from this existing job.
        other=self.new.with_name('other-new');other.mkdir()
        self.assertEqual(update_source.choice_path(other),other/'.helene/source-choice.json')

    def test_cleanup_warning_is_committed_visible_and_cold_start_does_not_repeat_setup(self):
        from localharness import migration_job,portable_start,portable_profile
        import python_app,helene_setup
        note={'id':'e'*32,'state':'retirement_incomplete','startup_proof':{'fixture_only':True},
              'cleanup_errors':['Kept personal.txt']}
        write(self.new/'python-migration.json',note)
        write(self.new/'helene.json',{'tree':str(self.data),'code':str(self.new/'tree'),'setup_complete':True})
        ui=self.new/'app/static';ui.mkdir(parents=True);(ui/'index.html').write_text('<html></html>')
        result=migration_job.committed_result(note)
        self.assertEqual(result['effect'],'committed');self.assertEqual(result['cleanup_errors'],note['cleanup_errors'])
        with patch.object(helene_setup,'main',side_effect=AssertionError('confirmed startup must not repeat setup')),\
             patch.object(portable_start,'initialize'),patch.object(portable_profile,'bind_program'),\
             patch.object(python_app,'Application',side_effect=RuntimeError('cold app reached')):
            with self.assertRaisesRegex(RuntimeError,'cold app reached'):
                python_app._main(['--root',str(self.new)])

    def test_preserve_archive_observer_accepts_only_its_exact_incoming_bootstrap(self):
        import os,psutil
        from localharness import migration_job,desktop_update_request as jobs,session_host
        write(self.new/'source-inputs.json',{'files':{}})
        control=migration_job.coordination_tree(self.new,self.new,self.data)
        descriptor={'program':str(self.new),'tree':str(control),'pid':os.getpid(),
                    'birth':psutil.Process().create_time(),'generation':'archive-fixture'}
        value={'id':'f'*32,'kind':'migration','state':'running','program':str(self.new),'tree':str(control),
               'admission':{k:descriptor[k] for k in ('pid','birth','generation')},
               'migration':{'package':str(self.new),'target':str(self.new),'old':str(self.old),
                            'data':str(self.data),'python_update':True,'source_mode':'preserve'}}
        jobs._save(control,value);write(control/'memory/.control/desktop-session.json',descriptor)
        self.assertEqual(migration_job.observer_program(self.new,self.new,value['id'],tree=control),self.new)
        value['migration']['python_update']=False;jobs._save(control,value)
        with self.assertRaisesRegex(ValueError,'admitted session'):
            migration_job.observer_program(self.new,self.new,value['id'],tree=control)

if __name__=='__main__':unittest.main()
