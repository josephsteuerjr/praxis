"""One real profile/code copy, with Windows lifecycle calls isolated."""
import json
import os
from pathlib import Path
import sys
import tempfile
import contextlib
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_migrate
from localharness import app_migration, code_transition


class CleanMigration(unittest.TestCase):
    def setUp(self):
        from localharness import source_update_host
        for boundary in (patch.object(source_update_host,'verified_runtime',return_value={'kind':'fixture-runtime-boundary'}),
            patch.object(python_migrate.migration_windows,'stop',return_value={}),
            patch.object(python_migrate.migration_windows,'cleanup_installation',return_value={})):
            boundary.start();self.addCleanup(boundary.stop)
    def fixture(self, root):
        old=root/'old';(old/'data/memory').mkdir(parents=True);(old/'code').mkdir()
        (old/'data/memory/history.jsonl').write_bytes(b'{"private_history":"retained"}\n')
        (old/'code/agent.py').write_text('old_authored_code = True\n')
        (old/'helene.json').write_text(json.dumps({'tree':'data','code':'code','mode':'local',
            'model':{'key':'fixture-private-key'},'autonomy':{'paused':True},'runner':'custom.py','app':'custom.py'}))
        package=root/'unpacked';(package/'tree').mkdir(parents=True)
        for rel,data in [('tree/agent.py','release_code = True\n'),('python-app.json','{"version":"2.2.10","delivery":"portable"}'),
                         ('app/python_app.py','# owner'),('app/deskapp.py','# app'),
                         ('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3','fixture')]:
            p=package/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)
        code_transition.seal(package)
        return old,package

    def test_clean_button_keeps_data_and_runs_only_new_code_in_selected_folder(self):
        for same_folder in (True,False):
            with self.subTest(same_folder=same_folder),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);old,package=self.fixture(root);new=package if same_folder else root/'chosen'
                original=(old/'helene.json').read_bytes()
                with patch.object(python_migrate.migration_windows,'quiescent'),patch.object(python_migrate.migration_windows,'service',return_value=None):
                    note=python_migrate.migrate(old,package,new,new/'profile',prepared=same_folder,
                        portable=True,source_mode='clean',launch=False)
                cfg=json.loads((new/'helene.json').read_text());selected=app_migration.code_path(new/'helene.json',cfg)
                self.assertEqual(note['state'],'prepared');self.assertEqual(note['source_policy'],'clean')
                self.assertEqual((selected/'agent.py').read_text(),'release_code = True\n')
                tree=Path(cfg['tree']).resolve()
                self.assertEqual((tree/'memory/history.jsonl').read_bytes(),b'{"private_history":"retained"}\n')
                self.assertEqual(cfg['model']['key'],'fixture-private-key');self.assertTrue(cfg['autonomy']['paused'])
                self.assertEqual((new/cfg['app']).resolve(),new/'app/deskapp.py')
                self.assertEqual((new/cfg['runner']).resolve(),new/'app/localharness/runner.py')
                self.assertEqual((old/'helene.json').read_bytes(),original)
                self.assertFalse((old/'code/agent.py').exists())
                self.assertEqual(tree,old/'data');self.assertFalse((new/'profile').exists())
                self.assertEqual(note['copied_data'],[]);self.assertTrue(note['old_retired_before_attach'])

    def test_bad_release_is_rejected_before_old_owner_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            old,package=self.fixture(Path(tmp));(package/'tree/agent.py').write_text('changed')
            with patch.object(python_migrate.migration_windows,'stop') as stop:
                with self.assertRaises(ValueError):python_migrate.migrate(old,package,package,package/'profile',prepared=True,source_mode='clean',launch=False)
                stop.assert_not_called()

    def test_data_only_after_old_program_was_uninstalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,package=self.fixture(root)
            (old/'code/agent.py').unlink();(old/'code').rmdir()
            with patch.object(python_migrate.migration_windows,'quiescent'),patch.object(python_migrate.migration_windows,'service',return_value=None):
                note=python_migrate.migrate(old,package,package,package/'profile',prepared=True,
                    portable=True,source_mode='clean',launch=False)
            cfg=json.loads((package/'helene.json').read_text());tree=(package/cfg['tree']).resolve()
            self.assertTrue((tree/'memory/history.jsonl').is_file())
            self.assertTrue(note['old_retired_before_attach']);self.assertEqual(note['copied_data'],[])
            self.assertEqual((app_migration.code_path(package/'helene.json',cfg)/'agent.py').read_text(),'release_code = True\n')
            # A real startup proof is still required before marking completion.
            note.update(state='new_app_started',startup_proof={'stopped':True,'root':str(package)})
            with patch.object(python_migrate.migration_windows,'quiescent'), \
                 patch.object(python_migrate.migration_windows,'registered_uninstall',side_effect=AssertionError('No old program to uninstall')), \
                 patch.object(python_migrate.migration_windows,'retire',return_value={}) as retire, \
                 patch.object(python_migrate.migration_windows,'install_shortcuts',return_value=[]):
                result=python_migrate.finish_retirement(old,package,package,note,lambda _:None)
            self.assertEqual(result['state'],'completed');self.assertFalse(result['retirement']['purge'])
            self.assertEqual((old/'data/memory/history.jsonl').read_bytes(),b'{"private_history":"retained"}\n')
            retire.assert_not_called()  # old program retired before attachment

    def test_windows_button_passes_clean_choice_and_selected_folder(self):
        import python_migration_window
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,package=self.fixture(root);target=root/'chosen'
            window=SimpleNamespace(root=package,running=threading.Lock(),progress=lambda _:None)
            with patch.object(python_migrate.migration_windows,'service',return_value=None), \
                 patch.object(python_migrate,'installation_lock',return_value=contextlib.nullcontext()), \
                 patch('localharness.migration_job.admit',return_value={'state':'admitted','job':{'id':'fixture'}}) as migrate:
                result=python_migration_window.MigrationWindow.invoke(window,'migration_run',
                    {'old':str(old),'target':str(target),'sourceMode':'clean'})
                self.assertEqual(result['state'],'admitted')
                self.assertEqual(migrate.call_args.args,(package,target))
                self.assertEqual(migrate.call_args.kwargs['source_mode'],'clean')
                self.assertEqual(migrate.call_args.kwargs['old'],old);self.assertTrue(migrate.call_args.kwargs['remove_old'])


if __name__=='__main__':unittest.main()
