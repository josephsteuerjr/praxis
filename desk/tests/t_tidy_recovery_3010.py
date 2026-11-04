"""Real private paths, legacy observation, clean code removal, profile safety."""
from pathlib import Path
import ctypes,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from localharness import program_state as state,update_progress as progress,desktop_update_request as jobs
from localharness import migration_job,update_overlay,update_source,code_transition,authored_baseline
from localharness.package_notice import read,write

class PrivateRecovery(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='helene-tidy-');self.addCleanup(temp.cleanup)
        self.base=Path(temp.name);self.program=self.base/'Hélène новая';self.program.mkdir()

    def test_new_receipts_locks_and_coordination_stay_inside_program(self):
        from python_migrate import installation_lock
        before={p.name for p in self.base.iterdir()}
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.base/'Local')}):
            with installation_lock(self.program):
                report=progress.Progress(state.progress(self.program,'sample'),'sample',self.program)
                report.phase('prepared')
            self.assertFalse((self.base/'Local/Helene').exists())
        write(self.program/'source-inputs.json',{'fixture':True})
        tree=migration_job.coordination_tree(self.program,self.program,self.base/'data')
        self.assertTrue(tree.is_relative_to(self.program/'.helene'))
        self.assertEqual(progress.status(self.program)['id'],'sample')
        self.assertEqual({p.name for p in self.base.iterdir()},before)
        if os.name=='nt':self.assertTrue(ctypes.windll.kernel32.GetFileAttributesW(str(self.program/'.helene'))&2)

    def test_legacy_progress_is_observed_without_creating_state(self):
        receipt=self.base/'.helene-update-old.progress.json'
        write(receipt,{'id':'old','target':str(self.program),'stage':'prepared'})
        write(progress.legacy_pointer(self.program),{'id':'old','receipt':str(receipt)})
        self.assertEqual(progress.status(self.program)['id'],'old')
        self.assertFalse((self.program/'.helene').exists())

    def test_identity_guards_reject_escape_and_linked_state(self):
        for identity in ('../outside','','a/b','a\\b'):
            with self.assertRaises(ValueError):state.recovery(self.program,identity)
        outside=self.base/'outside';outside.mkdir()
        try:(self.program/'.helene').symlink_to(outside,target_is_directory=True)
        except OSError:
            if os.name!='nt':raise
            import subprocess
            subprocess.run(['cmd.exe','/c','mklink','/J',str(self.program/'.helene'),str(outside)],check=True,capture_output=True,creationflags=0x08000000)
        with self.assertRaisesRegex(ValueError,'Linked'):state.progress(self.program,'test')
        self.assertEqual(list(outside.iterdir()),[])

    def test_existing_legacy_lock_is_not_bypassed(self):
        import hashlib
        from python_migrate import installation_lock
        from localharness.phone_tunnel.platform_support import acquire_file_lock,release_file_lock
        identity=hashlib.sha256(os.path.normcase(str(self.program)).encode()).hexdigest()[:24]
        legacy=self.base/('.helene-install-'+identity+'.lock')
        held=acquire_file_lock(legacy,timeout_sec=0)
        try:
            with self.assertRaises(Exception):
                with installation_lock(self.program):self.fail('Legacy lease bypassed')
        finally:release_file_lock(held)
        with installation_lock(self.program):pass

    def test_modes_require_real_baseline_without_changing_a_stored_old_job(self):
        self.assertEqual(jobs.new_source_mode(),'clean')
        self.assertEqual(jobs.new_source_mode('clean'),'clean')
        with self.assertRaises(ValueError):jobs.new_source_mode('preserve')
        with self.assertRaises(ValueError):jobs.new_source_mode('unknown-policy')
        write(self.program/'python-app.json',{'version':'2.3.0'})
        self.assertEqual(jobs.new_source_mode(root=self.program),'clean')
        with self.assertRaisesRegex(ValueError,'чистая замена'):
            jobs.new_source_mode('preserve',root=self.program)
        modern=self.base/'modern'
        for name in ('tree/agent.py','app/python_app.py'):
            path=modern/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# pristine fixture\n')
        write(modern/'python-app.json',{'version':'fixture-modern'})
        code_transition.seal(modern);authored_baseline.seal(modern)
        self.assertEqual(jobs.new_source_mode(root=modern),'preserve')
        self.assertEqual(jobs.new_source_mode('preserve',root=modern),'preserve')
        self.assertEqual(jobs.new_source_mode('clean',root=modern),'clean')
        data=self.base/'data'
        old={'id':'previous','state':'attention','source_mode':'preserve','program':str(self.program),'tree':str(data)}
        jobs._save(data,old)
        with patch('localharness.owner_stop.stopped',return_value=False):
            resumed=jobs.request(data,'resume',root=self.program,identity='previous')
        self.assertEqual(resumed['source_mode'],'preserve')

    @unittest.skipUnless(os.name=='nt','Windows visibility attributes')
    def test_only_declared_payload_hidden_and_atomic_settings_keep_visibility(self):
        for name in ('runtime/python.exe','app/python_app.py','helene.exe','body.exe','my-file.txt'):
            path=self.program/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
        write(self.program/'python-package-files.json',{'files':dict.fromkeys(('runtime/python.exe','app/python_app.py','helene.exe','body.exe'))})
        write(self.program/'helene.json',{'tree':'data'})
        state.tidy_program(self.program)
        write(self.program/'helene.json',{'tree':'other-data'})
        from localharness.config_transaction import replace_config
        staged=self.program/'settings.tmp';staged.write_text('{"tree":"third-data"}')
        replace_config(staged,self.program/'helene.json')
        hidden=lambda path:bool(ctypes.windll.kernel32.GetFileAttributesW(str(path))&2)
        self.assertTrue(hidden(self.program/'helene.json'))
        self.assertTrue(hidden(self.program/'runtime'))
        self.assertTrue(hidden(self.program/'body.exe'))
        self.assertFalse(hidden(self.program/'helene.exe'))
        self.assertFalse(hidden(self.program/'my-file.txt'))

    def test_clean_overlay_retires_removed_modules_but_excludes_private_state_and_data(self):
        incoming=self.base/'incoming';incoming.mkdir()
        for root in (incoming,self.program):
            (root/'tree').mkdir();(root/'app').mkdir()
        (incoming/'tree/current.py').write_text('new=True')
        (self.program/'tree/obsolete.py').write_text('old=True')
        (self.program/'app/obsolete.py').write_text('old=True')
        data=self.program/'tree/profile';data.mkdir();(data/'owner.py').write_text('personal')
        write(self.program/'helene.json',{'tree':str(data),'code':'tree','mode':'local'})
        write(update_source.choice_path(incoming),{'old':str(self.program),'new':str(incoming),'mode':'clean'})
        private=state.root(incoming)/'must-not-copy';private.write_bytes(b'private job')
        recovery=state.recovery(self.program,'fixture')
        update_overlay.prepare(incoming,self.program,recovery)
        update_overlay.apply(incoming,self.program,recovery)
        self.assertFalse((self.program/'tree/obsolete.py').exists())
        self.assertFalse((self.program/'app/obsolete.py').exists())
        self.assertEqual((data/'owner.py').read_text(),'personal')
        self.assertFalse((self.program/'.helene/must-not-copy').exists())
        from localharness.python_source_updater import rollback
        rollback(self.program,recovery)
        self.assertEqual((self.program/'tree/obsolete.py').read_text(),'old=True')

    def test_confirmed_clean_update_retires_preimages_but_keeps_later_user_edits(self):
        import hashlib
        from localharness.python_source_updater import retire_preimages
        recovery=state.recovery(self.program,'fixture');recovery.mkdir()
        (recovery/'app').mkdir();(recovery/'app/old.py').write_bytes(b'old source')
        (recovery/'app/changed.py').write_bytes(b'later edit')
        (recovery/'owner-note.txt').write_bytes(b'unknown user file')
        rows=[{'path':'app/old.py','existed':True,'previous':hashlib.sha256(b'old source').hexdigest()},
              {'path':'app/changed.py','existed':True,'previous':hashlib.sha256(b'original preimage').hexdigest()}]
        (recovery/'update-files.json').write_text(json.dumps(rows))
        write(self.program/'python-migration.json',{'id':'fixture','state':'prepared','source_policy':'clean'})
        with self.assertRaisesRegex(ValueError,'committed'):retire_preimages(self.program,recovery,'fixture')
        write(self.program/'python-migration.json',{'id':'fixture','state':'completed','source_policy':'clean'})
        report=retire_preimages(self.program,recovery,'fixture')
        self.assertFalse((recovery/'app/old.py').exists())
        self.assertEqual((recovery/'app/changed.py').read_bytes(),b'later edit')
        self.assertEqual((recovery/'owner-note.txt').read_bytes(),b'unknown user file')
        self.assertEqual(report['retained'],['app/changed.py'])

    def test_clean_update_runs_while_agent_pause_remains_and_stop_still_binds(self):
        from types import SimpleNamespace
        from localharness import python_updates,owner_stop
        data=self.base/'data';marker=data/'memory/.control/autonomy-paused.json'
        write(marker,{'paused':True});before=marker.read_bytes()
        owner=SimpleNamespace(update_job={'tree':str(data),'source_mode':'clean'},update_cancelled=lambda:False)
        with patch.object(owner_stop,'stopped',return_value=False),patch.object(python_updates.time,'sleep',side_effect=AssertionError('Clean mechanical update must not wait for agent resume')):
            python_updates._wait_pause(owner)
        self.assertEqual(marker.read_bytes(),before)
        jobs._save(data,{'id':'fixture','state':'paused'})
        owner.update_cancelled=lambda:True
        with patch.object(owner_stop,'stopped',return_value=False),self.assertRaises(InterruptedError):python_updates._wait_pause(owner)

    def test_old_code_git_is_moved_without_copy_restored_on_failure_and_retired_on_commit(self):
        from localharness import clean_code_retirement as git
        from localharness.python_source_updater import retire_preimages
        target=self.program/'tree/.git';target.mkdir(parents=True);(target/'HEAD').write_bytes(b'original history')
        recovery=state.recovery(self.program,'fixture');recovery.mkdir()
        git.prepare(self.program,recovery,[str(self.base/'data')]);self.assertFalse(target.exists())
        target.mkdir();(target/'HEAD').write_bytes(b'new startup history')
        git.restore(self.program,recovery)
        self.assertEqual((target/'HEAD').read_bytes(),b'original history')
        self.assertTrue(list((recovery/'later-self-edits').iterdir()))
        git.prepare(self.program,recovery,[str(self.base/'data')]);target.mkdir();(target/'HEAD').write_bytes(b'fresh history')
        (recovery/'update-files.json').write_text('[]')
        write(self.program/'python-migration.json',{'id':'fixture','state':'completed','source_policy':'clean'})
        result=retire_preimages(self.program,recovery,'fixture')
        self.assertEqual(result['old_code_git']['state'],'retired')
        self.assertEqual((target/'HEAD').read_bytes(),b'fresh history')
        self.assertFalse((recovery/'retired-code-git').exists())

    def test_only_unchanged_selected_internal_code_is_retired_after_startup(self):
        from localharness.clean_code_retirement import retire_selected_sources
        from localharness.source_merge import inventory
        source=self.program/'code/previous';source.mkdir(parents=True)
        (source/'old.py').write_text('old_code=True\n')
        (source/'changed.py').write_text('before=True\n')
        before=inventory(source)
        (source/'changed.py').write_text('after=True\n')
        (source/'unknown-user-file').write_bytes(b'preserve')
        (source/'.git').mkdir();(source/'.git/HEAD').write_text('old history')
        outside=self.base/'external-code';outside.mkdir();(outside/'keep.py').write_text('external=True\n')
        write(self.program/'code-transition.json',{'sources':[{'source':str(source),'previous_files':before},
              {'source':str(outside),'previous_files':inventory(outside)}]})
        recovery=state.recovery(self.program,'selected');recovery.mkdir()
        note={'state':'prepared','source_policy':'clean','data_paths':[str(self.base/'data')]}
        with self.assertRaises(ValueError):retire_selected_sources(self.program,recovery,note)
        self.assertTrue((source/'old.py').exists())
        note['state']='completed';result=retire_selected_sources(self.program,recovery,note)
        self.assertFalse((source/'old.py').exists());self.assertFalse((source/'.git').exists())
        self.assertEqual((source/'changed.py').read_text(),'after=True\n')
        self.assertEqual((source/'unknown-user-file').read_bytes(),b'preserve')
        self.assertTrue((outside/'keep.py').is_file())
        self.assertTrue(result['retained'])

    def test_selected_code_overlapping_data_is_retained(self):
        from localharness.clean_code_retirement import retire_selected_sources
        from localharness.source_merge import inventory
        source=self.program/'code/with-data';source.mkdir(parents=True);(source/'keep.py').write_text('keep=True\n')
        write(self.program/'code-transition.json',{'sources':[{'source':str(source),'previous_files':inventory(source)}]})
        recovery=state.recovery(self.program,'overlap');recovery.mkdir()
        result=retire_selected_sources(self.program,recovery,{'state':'completed','source_policy':'clean','data_paths':[str(source)]})
        self.assertTrue((source/'keep.py').is_file());self.assertTrue(result['retained'])

    def test_preserved_internal_source_retires_only_when_old_code_and_git_are_unchanged(self):
        from localharness.clean_code_retirement import retire_selected_sources,git_signature
        from localharness.source_merge import inventory
        for changed in (False,True):
            with self.subTest(later_git_edit=changed):
                source=self.program/'code'/('old-'+str(changed));source.mkdir(parents=True)
                selected=self.program/'code'/('selected-'+str(changed));selected.mkdir()
                (source/'module.py').write_text('authored=True\n');(source/'.git').mkdir()
                (source/'.git/HEAD').write_text('accepted history')
                (selected/'module.py').write_text('authored=True\nupstream=True\n')
                row={'source':str(source),'selected':str(selected),'previous_files':inventory(source),
                     'git_signature':git_signature(source/'.git')}
                write(self.program/'code-transition.json',{'merges':[row]})
                write(self.program/'helene.json',{'tree':str(self.base/'data'),'code':str(selected)})
                recovery=state.recovery(self.program,'preserved-'+str(changed));recovery.mkdir()
                note={'state':'completed','source_policy':'preserve','startup_proof':{'fixture_only':True},
                      'data_paths':[str(self.base/'data')]}
                if changed:(source/'.git/HEAD').write_text('later authored history')
                result=retire_selected_sources(self.program,recovery,note)
                self.assertEqual((source/'module.py').exists(),changed)
                self.assertEqual((source/'.git').exists(),changed)
                self.assertEqual((selected/'module.py').read_text(),'authored=True\nupstream=True\n')
                self.assertEqual(bool(result['retained']),changed)

if __name__=='__main__':unittest.main()
