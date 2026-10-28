"""Connected-data setup, cleanup boundaries and real Windows shutdown/shortcuts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import windows_installation as install,migration_windows as windows
import t_migration_clean_windows_2810 as clean_fixture


class Installation(unittest.TestCase):
    def fixture(self,root):
        old,package=clean_fixture.CleanMigration().fixture(root)
        (old/'python-app.json').write_text('{"version":"2.1.1"}')
        return old,package

    def test_attach_keeps_same_data_settings_pause_and_archives_old_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,package=self.fixture(root)
            (old/'components/whisper').mkdir(parents=True)
            (old/'components/whisper/fixture.dll').write_bytes(b'optional runtime')
            original=(old/'helene.json').read_bytes()
            with patch.object(windows,'stop') as stop,patch.object(windows,'quiescent'),patch.object(windows,'service',return_value=None):
                note=install.run(package,package,old=old,data=old/'data',launch=False,source_mode='clean')
            stop.assert_called_once_with(old)
            cfg=json.loads((package/'helene.json').read_text(encoding='utf-8'))
            self.assertEqual(Path(cfg['tree']),old/'data')
            self.assertEqual(cfg['model']['key'],'fixture-private-key')
            self.assertTrue(cfg['autonomy']['paused'])
            self.assertFalse(cfg['installed']['service'])
            self.assertEqual(Path(cfg['app']),package/'app/deskapp.py')
            self.assertEqual((Path(cfg['code'])/'agent.py').read_text(),'release_code = True\n')
            self.assertEqual((old/'helene.json').read_bytes(),original)
            self.assertEqual(note['copied_data'],[])
            self.assertEqual((package/'components/whisper/fixture.dll').read_bytes(),b'optional runtime')
            archive=Path(note['archives'][0]['path'])
            with zipfile.ZipFile(archive) as saved:
                self.assertEqual(saved.read('agent.py'),(old/'code/agent.py').read_bytes())
                self.assertFalse(any('memory/' in n for n in saved.namelist()))
            self.assertTrue(install.archived_version('2.1.1'))
            self.assertFalse(install.archived_version('2.2.1'))

    def test_data_only_after_uninstall_and_new_agent_in_chosen_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,package=self.fixture(root);target=root/'chosen'
            note=install.run(package,target,data=old/'data',launch=False)
            cfg=json.loads((target/'helene.json').read_text())
            self.assertEqual(Path(cfg['tree']),old/'data');self.assertFalse(cfg['setup_complete'])
            self.assertEqual((old/'data/memory/history.jsonl').read_bytes(),b'{"private_history":"retained"}\n')
            self.assertEqual(note['state'],'prepared')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,package=self.fixture(root);target=root/'fresh';data=root/'new-data'
            install.run(package,target,data=data,fresh=True,launch=False)
            self.assertEqual(Path(json.loads((target/'helene.json').read_text())['tree']),data)
            self.assertTrue(data.is_dir())

    def test_tampered_incoming_and_nonempty_fresh_data_refused_before_stop(self):
        for tamper in (True,False):
            with self.subTest(tamper=tamper),tempfile.TemporaryDirectory() as tmp:
                old,package=self.fixture(Path(tmp))
                if tamper:(package/'tree/agent.py').write_text('changed')
                with patch.object(windows,'stop') as stop:
                    with self.assertRaises(ValueError):install.run(package,package,old=old,data=old/'data',fresh=not tamper,launch=False)
                    stop.assert_not_called()

    def test_retirement_requires_proof_and_cleanup_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,new=self.fixture(root)
            note={'id':'id','state':'prepared','old_root':str(old),'new_root':str(new)}
            with patch.object(windows,'cleanup_installation') as cleanup:
                with self.assertRaises(ValueError):install.finish(new,note,remove_old=True,progress=lambda _:None)
                cleanup.assert_not_called()
            note.update(state='new_app_started',startup_proof={'root':str(new)})
            with patch.object(windows,'cleanup_installation',side_effect=RuntimeError('Windows refused')),patch.object(windows,'remove_program') as remove,patch.object(windows,'install_shortcuts',return_value=['one']):
                result=install.finish(new,note,remove_old=False,progress=lambda _:None)
                remove.assert_not_called()
            self.assertEqual(result['state'],'retirement_incomplete');self.assertEqual(result['cleanup_errors'],['Windows refused'])

    def test_own_retirement_keeps_all_data_and_never_calls_old_uninstaller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,new=self.fixture(root)
            (old/'app').mkdir();(old/'app/python_app.py').write_text('old app')
            (old/'helene.exe').write_bytes(b'fixture launcher')
            (old/'components/xtts/model').mkdir(parents=True)
            (old/'components/xtts/model/weights.pth').write_bytes(b'model-data')
            note={'id':'id','state':'new_app_started','startup_proof':{'root':str(new)},'old_root':str(old),
                  'data_paths':[str(old/'data')],'old_data_paths':[str(old/'data')],
                  'source_paths':[{'old':str(old/'code')} ]}
            with patch.object(windows,'uninstall_registered',side_effect=AssertionError('legacy uninstaller forbidden')):
                result=windows.remove_program(old,new,note)
            self.assertTrue(result['ok']);self.assertFalse((old/'helene.exe').exists())
            self.assertTrue((old/'helene.json').is_file());self.assertTrue((old/'code/agent.py').is_file())
            self.assertTrue((old/'data/memory/history.jsonl').is_file())
            self.assertEqual((old/'components/xtts/model/weights.pth').read_bytes(),b'model-data')

    def test_wizard_dispatches_data_remove_choice_without_elevating_itself(self):
        from python_migration_window import MigrationWindow
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,package=self.fixture(root)
            window=SimpleNamespace(root=package,running=threading.Lock(),progress=lambda _:None)
            with patch.object(install,'run',return_value={'state':'completed'}) as run:
                MigrationWindow.invoke(window,'migration_install',{'old':str(old),'target':str(package),
                    'data':str(old/'data'),'removeOld':True,'mode':'connect'})
                self.assertEqual(run.call_args.kwargs['data'],str(old/'data'))
                self.assertTrue(run.call_args.kwargs['remove_old'])
                self.assertFalse(run.call_args.kwargs['fresh'])

    def test_retry_fresh_keeps_recorded_data_and_manual_setup_defaults_to_this_program(self):
        from python_migration_window import MigrationWindow
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old,package=self.fixture(root);data=root/'new-data'
            first=install.run(package,package,data=data,fresh=True,launch=False)
            (data/'memory').mkdir();(data/'memory/history.txt').write_text('retained after failed startup')
            again=install.run(package,package,data=data,fresh=True,launch=False)
            self.assertEqual(first['id'],again['id'])
            self.assertEqual((data/'memory/history.txt').read_text(),'retained after failed startup')
            window=MigrationWindow(package)
            try:
                defaults=window.invoke('migration_defaults',{})
                self.assertEqual(defaults['old'],'');self.assertEqual(Path(defaults['data']),data)
                self.assertEqual(defaults['mode'],'fresh')
            finally:window.server.server_close()

    def test_large_privileged_retirement_uses_bound_file_not_a_giant_command(self):
        import base64
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';old.mkdir()
            files={f'runtime/lib/module-{n}.py':'a'*64 for n in range(5000)}
            seen=[]
            def elevated(script,env):
                self.assertNotIn('HELENE_RETIRE_FILES',env)
                request=Path(env['HELENE_RETIRE_MANIFEST']);value=json.loads(request.read_text())
                self.assertEqual(value['files'],files);self.assertEqual(Path(value['old']),old)
                self.assertEqual(len(env['HELENE_RETIRE_SHA256']),64);seen.append(request)
            with patch.object(windows,'admin',return_value=False),patch.object(windows,'powershell',side_effect=RuntimeError('access denied')),patch.object(windows,'run_privileged',side_effect=elevated):
                windows.system_action(old,'remove_files',files)
            self.assertEqual(len(seen),1);self.assertFalse(seen[0].exists())
            with patch.object(windows.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
                windows.run_privileged("$ErrorActionPreference='Stop'",{'HELENE_OLD_ROOT':str(old),'HELENE_PRIVATE_TOKEN':'fixture-secret-do-not-forward'})
                script=base64.b64decode(run.call_args.kwargs['env']['HELENE_ADMIN_CODE']).decode('utf-16le')
                self.assertNotIn('fixture-secret',script);self.assertNotIn('HELENE_PRIVATE_TOKEN',script)

    @unittest.skipUnless(os.name=='nt','Windows native retirement script')
    def test_actual_powershell_retirement_only_deletes_hash_bound_fixture_files(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';(old/'runtime').mkdir(parents=True);(old/'data').mkdir()
            (old/'runtime/a.dll').write_bytes(b'fixture-a');(old/'runtime/b.dll').write_bytes(b'fixture-b')
            (old/'data/history.jsonl').write_bytes(b'private retained data')
            files={name:hashlib.sha256((old/name).read_bytes()).hexdigest() for name in ('runtime/a.dll','runtime/b.dll')}
            with patch.object(windows,'admin',return_value=False),patch.object(windows,'run_privileged',side_effect=AssertionError('Per-user cleanup needs no UAC')):
                windows.system_action(old,'remove_files',files)
            self.assertFalse((old/'runtime/a.dll').exists());self.assertFalse((old/'runtime/b.dll').exists())
            self.assertEqual((old/'data/history.jsonl').read_bytes(),b'private retained data')

    @unittest.skipUnless(os.name=='nt','Windows integration')
    def test_real_shortcuts_unpin_api_and_exact_binding_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';old.mkdir();other=root/'other';other.mkdir()
            folder=root/'links';folder.mkdir()
            own=folder/'Old.lnk';foreign=folder/'Foreign.lnk'
            windows.shortcut(own,old/'helene.exe',old)
            windows.shortcut(foreign,other/'helene.exe',other)
            self.assertEqual(windows.remove_shortcuts(old,[folder]),[str(own)])
            self.assertTrue(foreign.is_file());self.assertFalse(own.exists())
            # Dedicated temp desktop, never the owner's real shortcuts.
            new=root/'new';new.mkdir();(new/'helene.exe').write_bytes(b'fixture')
            made=windows.install_shortcuts(new,folder,desktop_only=True)
            self.assertEqual(made,[str(folder/'Helene.lnk')])
            self.assertTrue(windows.link_belongs(windows.shortcut_binding(Path(made[0])),new))

    @unittest.skipUnless(os.name=='nt','Windows process integration')
    def test_real_old_app_without_control_descriptor_is_stopped_foreign_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';foreign=root/'old-other'
            processes=[]
            try:
                for folder in (old,foreign):
                    (folder/'app').mkdir(parents=True)
                    script=folder/'app/python_app.py';script.write_text('import time\ntime.sleep(120)\n')
                    processes.append(subprocess.Popen([sys.executable,str(script)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000))
                with patch.object(windows,'service',return_value=None),patch.object(windows,'run_privileged',side_effect=AssertionError('No service, no UAC')):
                    windows.stop(old)
                self.assertIsNotNone(processes[0].poll());self.assertIsNone(processes[1].poll())
            finally:
                for process in processes:
                    if process.poll() is None:process.terminate()
                    process.wait(timeout=5)


if __name__=='__main__':unittest.main()
