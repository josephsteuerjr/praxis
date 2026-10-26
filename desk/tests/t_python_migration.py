"""Migration's real source/data/Git consumers in disposable installations."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import app_migration
import python_migrate


def git(path,*args):
    try:
        return subprocess.check_output(['git','-C',str(path),*args],stderr=subprocess.PIPE).decode().strip()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.decode('utf-8',errors='replace')) from error


class MigrationTests(unittest.TestCase):
    def test_portable_archive_migrates_profile_without_copying_program(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';(old/'data/memory').mkdir(parents=True)
            (old/'data/memory/history.txt').write_text('authored memory')
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'data','runner':''}))
            new=root/'portable'
            for rel in ('python-app.json','app/python_app.py','runtime/python.exe'):
                p=new/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('prepared program')
            with patch.object(python_migrate,'copy_program',side_effect=AssertionError('Portable program must not be copied')),patch.object(python_migrate.migration_windows,'quiescent'),patch.object(python_migrate.migration_windows,'service',return_value=None):
                note=python_migrate.migrate(old,new,new,new/'profile',prepared=True,launch=False)
            self.assertEqual(note['state'],'prepared')
            self.assertEqual((new/'app/python_app.py').read_text(),'prepared program')
            self.assertTrue(list((new/'profile').rglob('history.txt')))
            self.assertEqual((old/'data/memory/history.txt').read_text(),'authored memory')

    def test_migration_preserves_default_agent_preferences_and_legacy_voice_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';(old/'data').mkdir(parents=True)
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'data','runner':''}))
            (old/'agent-default.json').write_text('{"id":"second"}')
            (old/'window-preferences.json').write_text('{"frame.panel":"1"}')
            runtime=old/'voice-xtts';runtime.mkdir()
            executable='python.exe' if sys.platform=='win32' else 'bin/python3'
            (runtime/executable).parent.mkdir(parents=True,exist_ok=True)
            (runtime/executable).write_bytes(b'legacy-runtime')
            new=root/'new';app_migration.prepare(old,new,root/'saved')
            self.assertEqual((new/'agent-default.json').read_bytes(),(old/'agent-default.json').read_bytes())
            self.assertEqual((new/'window-preferences.json').read_bytes(),(old/'window-preferences.json').read_bytes())
            self.assertEqual((new/'voice-xtts'/executable).read_bytes(),b'legacy-runtime')

    def test_fresh_config_uses_the_actual_model_contract_and_retains_edits_on_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);package=root/'package';package.mkdir();new=root/'new'
            python_migrate.fresh(package,new,root/'data',launch=False)
            config=new/'helene.json';value=json.loads(config.read_text())
            self.assertIn('model',value['model']);self.assertNotIn('name',value['model'])
            value['model']['model']='selected-model'
            config.write_text(json.dumps(value))
            python_migrate.fresh(package,new,root/'data',launch=False)
            self.assertEqual(json.loads(config.read_text())['model']['model'],'selected-model')

    def test_incomplete_retirement_resumes_without_second_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';(old/'data').mkdir(parents=True)
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'data','runner':''}))
            package=root/'package';(package/'migration').mkdir(parents=True)
            (package/'migration/legacy-package-files.json').write_text('{"files":{}}')
            new=root/'new';note=app_migration.prepare(old,new,root/'saved')
            note.update(state='retirement_incomplete',startup_proof={'stopped':True,'root':str(new)})
            app_migration.write_note(new,note)
            with patch('localharness.owner_stop.stopped',return_value=True),patch.object(python_migrate.migration_windows,'quiescent'),patch.object(python_migrate.migration_windows,'retire',return_value={'service_removed':False}) as retire,patch('localharness.desktop_supervisor.JobChild.spawn') as spawn:
                result=python_migrate.migrate(old,package,new,root/'saved')
                self.assertEqual(result['state'],'completed')
                self.assertEqual(python_migrate.migrate(old,package,new,root/'saved')['state'],'completed')
                spawn.assert_not_called();retire.assert_called_once()

    def test_immutable_model_reuses_storage_but_memory_has_independent_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';(source/'models').mkdir(parents=True);(source/'memory').mkdir()
            (source/'models/checkpoint.bin').write_bytes(b'immutable-model')
            (source/'memory/event').write_bytes(b'authored-state')
            app_migration.preserve_source(source,root/'copy')
            self.assertTrue((source/'models/checkpoint.bin').samefile(root/'copy/models/checkpoint.bin'))
            self.assertFalse((source/'memory/event').samefile(root/'copy/memory/event'))
            (source/'models/checkpoint.bin').unlink()
            self.assertEqual((root/'copy/models/checkpoint.bin').read_bytes(),b'immutable-model')

    def test_runtime_lease_is_not_transferred_but_git_and_pause_are(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir()
            (source/'.desktop-owner.lock').write_bytes(b'active\n')
            (source/'.python-desktop-status').write_text('{"pid":123}')
            (source/'owner-stop.json').write_text('{"by":"owner"}')
            (source/'.git').mkdir();(source/'.git/index').write_bytes(b'staged bytes')
            app_migration.preserve_source(source,root/'copy')
            self.assertEqual((source/'.desktop-owner.lock').read_bytes(),b'active\n')
            self.assertFalse((root/'copy/.desktop-owner.lock').exists())
            self.assertFalse((root/'copy/.python-desktop-status').exists())
            self.assertTrue((root/'copy/owner-stop.json').is_file())
            self.assertEqual((root/'copy/.git/index').read_bytes(),b'staged bytes')

    def test_only_matching_transaction_or_global_latch_can_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';new=root/'new';new.mkdir()
            latch=new/'owner-stop.json';latch.write_text('{}')
            with patch('localharness.owner_stop.client_file',return_value=latch):
                note=python_migrate.transaction(old,new)
            self.assertTrue(latch.exists())
            self.assertEqual(python_migrate.transaction(old,new)['id'],note['id'])
            with self.assertRaises(ValueError):python_migrate.transaction(root/'foreign',new)
            unknown=root/'unknown';unknown.mkdir();(unknown/'unique.txt').write_text('keep')
            with self.assertRaises(FileExistsError):python_migrate.transaction(old,unknown)
            self.assertEqual((unknown/'unique.txt').read_text(),'keep')

    def test_copy_resume_reuses_bytes_and_keeps_modified_previous(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);package=root/'package';package.mkdir();new=root/'new';new.mkdir()
            (package/'code.py').write_text('new');(new/'code.py').write_text('old self edit')
            (new/'helene.json').write_text('private config')
            events=[];python_migrate.copy_program(package,new,events.append)
            self.assertEqual((new/'.migration-program-previous/code.py').read_text(),'old self edit')
            self.assertEqual((new/'helene.json').read_text(),'private config')
            events=[];python_migrate.copy_program(package,new,events.append)
            self.assertTrue(events[-1]['reused'])
            self.assertEqual(events[-1]['bytes'],events[-1]['total_bytes'])

    def test_failed_prepared_attempt_can_resnapshot_without_losing_prior_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';(old/'data').mkdir(parents=True)
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'data','runner':''}))
            (old/'data/last').write_text('first')
            package=root/'package';package.mkdir();new=root/'new'
            with patch.object(python_migrate.migration_windows,'service',return_value=None),patch.object(python_migrate.migration_windows,'quiescent'):
                first=python_migrate.migrate(old,package,new,root/'saved',launch=False)
                (old/'data/last').write_text('latest')
                second=python_migrate.migrate(old,package,new,root/'saved',launch=False)
            self.assertNotEqual(first['id'],second['id'])
            self.assertEqual(first['data_paths'],second['data_paths'])
            retained=list((root/'saved'/second['id']).glob('previous-delta-*/last'))
            self.assertEqual(len(retained),1);self.assertEqual(retained[0].read_text(),'first')
            self.assertEqual((Path(second['data_paths'][0])/'last').read_text(),'latest')
            self.assertTrue(list((new/'.migration-attempts').glob('*/helene.json')))

    def test_stopped_controller_admits_no_children_but_keeps_controls(self):
        from localharness.python_controller import Controller
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'helene.json').write_text(json.dumps({'tree':'data','runner':''}))
            owner=Controller(root,lambda *args:None)
            with patch('localharness.owner_stop.stopped',return_value=True):
                owner.start()
                self.assertIsNone(owner.thread)
                self.assertTrue(owner.invoke('owner_state',{})['stopped'])

    def test_migration_of_stopped_agent_never_claims_runner_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';(old/'data').mkdir(parents=True)
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'data','runner':'runner.py'}))
            new=root/'new';note=app_migration.prepare(old,new,root/'saved')
            proof={'stopped':True,'root':str(new.resolve())}
            with patch('localharness.owner_stop.stopped',return_value=False):
                with self.assertRaises(ValueError):app_migration.confirm_started(new,note['id'],proof=proof)
            with patch('localharness.owner_stop.stopped',return_value=True):
                note=app_migration.confirm_started(new,note['id'],proof=proof)
            self.assertTrue(note['startup_proof']['stopped'])
            self.assertNotIn('runner_pid',note['startup_proof'])

    def test_neighbor_runner_requirement_matches_supervisor(self):
        from localharness.desktop_supervisor import runner_required, BASE_ID
        cfg={'runner':'runner.py','model':{'key':''}}
        self.assertTrue(runner_required(cfg,BASE_ID))
        self.assertFalse(runner_required(cfg,'neighbor'))
        self.assertTrue(runner_required(dict(cfg,setup_complete=True),'neighbor'))

    @unittest.skipUnless(sys.platform=='win32','Windows Job transaction')
    def test_retirement_failure_keeps_proven_new_owner(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';old.mkdir();package=root/'package';package.mkdir()
            (old/'helene.json').write_text(json.dumps({'tree':'data','runner':''}))
            new=root/'new'
            def prepared(*args,**kwargs):
                note={'id':'test','state':'new_app_started','old_root':str(old),'new_root':str(new)}
                app_migration.write_note(new,note);return note
            child=MagicMock();child.handle=123
            with patch.object(python_migrate.migration_windows,'service',return_value=None),patch.object(python_migrate.migration_windows,'quiescent'),patch.object(app_migration,'prepare',side_effect=prepared),patch('localharness.desktop_process.JobChild.spawn',return_value=child),patch.object(python_migrate.migration_windows,'retire',side_effect=PermissionError('shortcut')):
                with self.assertRaises(PermissionError):python_migrate.migrate(old,package,new,root/'data')
            child.release.assert_called_once();child.stop.assert_not_called()
            self.assertEqual(json.loads((new/'python-migration.json').read_text())['state'],'retirement_incomplete')

    def test_panic_shortcut_does_not_start_window_or_touch_real_latch(self):
        import python_app
        with patch('localharness.owner_stop.latch_client') as latch,patch.object(python_app,'Application') as window:
            self.assertEqual(python_app.main(['--root','unused','--panic']),0)
            latch.assert_called_once();window.assert_not_called()

    def test_shared_linked_git_and_imported_dirty_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repository=root/'repo';repository.mkdir()
            git(repository,'init');(repository/'agent.py').write_text('VALUE="birth"')
            git(repository,'add','.');git(repository,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','birth')
            old=root/'old';old.mkdir();code=old/'code'
            git(repository,'worktree','add','-b','self-edits',str(code))
            (code/'agent.py').write_text('VALUE="staged"');git(code,'add','agent.py')
            (code/'agent.py').write_text('VALUE="dirty"');(code/'new.py').write_text('VALUE=42')
            data=old/'data';data.mkdir();(data/'memory').mkdir();(data/'memory/event').write_text('last write')
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'code','runner':''}))
            neighbor=old/'agents/second';neighbor.mkdir(parents=True)
            (neighbor/'helene.json').write_text(json.dumps({'tree':'../../data','code':'../../code','runner':''}))
            note=app_migration.prepare(old,root/'new',root/'saved')
            cfg=json.loads((root/'new/helene.json').read_text());other=json.loads((root/'new/agents/second/helene.json').read_text())
            selected=Path(cfg['code'])
            self.assertEqual(cfg['tree'],other['tree']);self.assertEqual(cfg['code'],other['code'])
            self.assertTrue((selected/'.git').is_dir())
            self.assertEqual(git(selected,'rev-parse','HEAD'),git(code,'rev-parse','HEAD'))
            self.assertEqual(git(selected,'show',':agent.py'),'VALUE="staged"')
            self.assertEqual(subprocess.check_output([sys.executable,'-c','import agent;print(agent.VALUE)'],cwd=selected).strip(),b'dirty')
            self.assertEqual((selected/'new.py').read_text(),'VALUE=42')
            self.assertEqual(len(note['copied_data']),2)

    def test_linked_worktree_split_index_preserves_staged_and_dirty_views(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repository=root/'repo';repository.mkdir()
            git(repository,'init');(repository/'agent.py').write_text('VALUE="birth"')
            git(repository,'add','.');git(repository,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','birth')
            code=root/'code';git(repository,'worktree','add','-b','authored',str(code))
            (code/'agent.py').write_text('VALUE="staged"');git(code,'add','agent.py')
            git(code,'update-index','--split-index')
            self.assertTrue(git(code,'rev-parse','--shared-index-path'))
            (code/'agent.py').write_text('VALUE="dirty"')
            selected=root/'copy';app_migration.preserve_source(code,selected)
            self.assertEqual(git(selected,'show',':agent.py'),'VALUE="staged"')
            self.assertEqual(git(selected,'diff','--cached','--binary'),git(code,'diff','--cached','--binary'))
            self.assertEqual(git(selected,'diff','--binary'),git(code,'diff','--binary'))

    def test_final_snapshot_follows_quiescence_not_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';(old/'data').mkdir(parents=True);package=root/'package';package.mkdir()
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'data','runner':''}))
            event=old/'data/last';event.write_text('early')
            def stop_writes(path):event.write_text('final');return True
            with patch.object(python_migrate.migration_windows,'service',return_value=None), patch.object(python_migrate.migration_windows,'quiescent',side_effect=stop_writes):
                note=python_migrate.migrate(old,package,root/'new',root/'saved',launch=False)
            cfg=json.loads((root/'new/helene.json').read_text())
            self.assertEqual((Path(cfg['tree'])/'last').read_text(),'final')
            self.assertEqual(note['state'],'prepared')

    def test_remote_connection_and_completed_receipt_are_not_window_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';(old/'data').mkdir(parents=True)
            (old/'helene.json').write_text(json.dumps({'tree':'data','mode':'remote','base':'https://example.invalid','runner':''}))
            new=root/'new';note=app_migration.prepare(old,new,root/'saved')
            with self.assertRaisesRegex(ValueError,'connection'):
                app_migration.confirm_started(new,note['id'],proof={'remote':True})
            proof={'remote':True,'base':'https://example.invalid','connected':True}
            note=app_migration.confirm_started(new,note['id'],proof=proof)
            note['state']='completed';app_migration.write_note(new,note)
            self.assertEqual(app_migration.confirm_started(new,note['id'],proof=proof)['state'],'completed')

    def test_remote_migration_keeps_stop_without_contacting_the_server(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';(old/'data').mkdir(parents=True)
            (old/'helene.json').write_text(json.dumps({'tree':'data','code':'data','mode':'remote','base':'https://example.invalid','runner':''}))
            new=root/'new';note=app_migration.prepare(old,new,root/'saved')
            proof={'stopped':True,'root':str(new.resolve())}
            with patch('localharness.owner_stop.stopped',return_value=True):
                result=app_migration.confirm_started(new,note['id'],proof=proof)
            self.assertTrue(result['startup_proof']['stopped'])
            self.assertNotIn('connected',result['startup_proof'])

    @unittest.skipUnless(sys.platform=='win32','Windows Job transfer')
    def test_failure_after_stop_restores_previous_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';old.mkdir();package=root/'package';package.mkdir()
            (old/'helene.json').write_text(json.dumps({'tree':'data','runner':''}))
            before={'State':'Running'}
            with patch.object(python_migrate.migration_windows,'service',return_value=before), patch.object(python_migrate.migration_windows,'stop',return_value={'service':before}), patch.object(python_migrate.migration_windows,'quiescent',side_effect=RuntimeError('old window')), patch.object(python_migrate.migration_windows,'restore_service',return_value=True) as restore:
                with self.assertRaisesRegex(RuntimeError,'old window'):
                    python_migrate.migrate(old,package,root/'new',root/'saved',stop_service=True)
                restore.assert_called_once_with(old,before)
            note=json.loads((root/'new/python-migration.json').read_text())
            self.assertEqual(note['state'],'failed_before_retirement')
            self.assertTrue(note['old_service_restored'])

    @unittest.skipUnless(sys.platform=='win32','Windows Job transfer')
    def test_installer_job_release_retains_process(self):
        import os,time
        from localharness.desktop_process import JobChild
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            child=JobChild.spawn([sys.executable,'-c','import time;time.sleep(1)'],cwd=root,env=dict(os.environ),log=root/'log',stopped=lambda:False)
            pid=child.pid;child.release()
            # Query a retained PID only for observation, never for a kill.
            import ctypes
            handle=ctypes.windll.kernel32.OpenProcess(0x100000,False,pid)
            self.assertTrue(handle)
            self.assertEqual(ctypes.windll.kernel32.WaitForSingleObject(handle,0),258)
            self.assertEqual(ctypes.windll.kernel32.WaitForSingleObject(handle,5000),0)
            ctypes.windll.kernel32.CloseHandle(handle)

if __name__=='__main__':unittest.main()
