"""Builder shape and optional-component boundaries, not final binary qualification."""
import json
import itertools
import types
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from installer import python_app_bundle
from localharness import app_components
from localharness import app_migration, python_source_updater, xtts_install
import python_migrate


def put(root,name,data=b'fixture'):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path


class DeliveryTests(unittest.TestCase):
    def mechanical(self,old,new,staging):
        from localharness.program_state import root
        root(old)
        return python_source_updater.perform(types.SimpleNamespace(old=old,new=new,staging=staging,
            pid=0,cleanup=False,browser=False,archive=[]))
    def test_update_journal_retries_transient_windows_replace_denial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';recovery=root/'recovery'
            put(old,'app/value',b'old');put(new,'app/value',b'new')
            original=Path.replace;attempts=[]
            def replace(source,target):
                if source.name=='update-files.tmp' and not attempts:
                    attempts.append(True)
                    raise PermissionError('sharing violation')
                return original(source,target)
            with patch.object(Path,'replace',replace),patch.object(python_source_updater.time,'sleep'):
                python_source_updater.apply_files(new,old,recovery)
            self.assertEqual((old/'app/value').read_bytes(),b'new')
            self.assertEqual(len(json.loads((recovery/'update-files.json').read_text())),1)

    def test_permanent_journal_denial_restores_previous_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';recovery=root/'recovery'
            put(old,'app/value',b'old');put(new,'app/value',b'new')
            original=Path.replace
            def replace(source,target):
                if source.name=='update-files.tmp':raise PermissionError('denied')
                return original(source,target)
            with patch.object(Path,'replace',replace),patch.object(python_source_updater.time,'monotonic',side_effect=itertools.count(0,4)):
                with self.assertRaises(PermissionError):python_source_updater.apply_files(new,old,recovery)
            self.assertEqual((old/'app/value').read_bytes(),b'old')

    def test_python_close_refuses_unclean_owner_and_never_forces_it(self):
        from localharness import migration_windows
        import psutil
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);tree=root/'data'
            put(root,'helene.json',json.dumps({'tree':str(tree)}).encode())
            record=put(tree,'.desktop-owner.lock',b'active\n')
            rows=[{'ProcessId':123,'Birth':1.0,'ExecutablePath':str(root/'runtime/python.exe'),
                   'CommandLine':'"'+str(root/'app/python_app.py')+'" --root "'+str(root)+'"'}]
            windows_os=types.SimpleNamespace(**{**vars(os),'name':'nt'})
            process=types.SimpleNamespace(create_time=lambda:1.0,wait=lambda **kwargs:0)
            with patch.object(migration_windows,'request_python_exit') as request,patch.object(migration_windows,'os',windows_os),patch.object(migration_windows,'process_snapshot',return_value=rows),patch.object(psutil,'Process',return_value=process),patch.object(migration_windows,'owner_lease_available',return_value=False),patch.object(migration_windows,'powershell') as commands:
                with self.assertRaisesRegex(RuntimeError,'data lease'):migration_windows.close_python(root)
                request.assert_called_once_with(root,123);commands.assert_not_called()
            with patch.object(migration_windows,'request_python_exit'),patch.object(migration_windows,'os',windows_os),patch.object(migration_windows,'process_snapshot',return_value=rows),patch.object(psutil,'Process',return_value=process),patch.object(migration_windows,'owner_lease_available',return_value=True):
                migration_windows.close_python(root)
            self.assertEqual(record.read_bytes(),b'active\n','the diagnostic word is not a lease and must not be erased')

    def test_prepare_failure_reopens_old_application_without_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'application';staging=root/'.helene-update-fixture';new=staging/'Helene'
            put(old,'app/python_app.py',b'previous');new.mkdir(parents=True)
            args=['updater','--old',str(old),'--new',str(new),'--pid','0','--staging',str(staging)]
            with patch.object(sys,'argv',args),patch.object(python_source_updater,'wait_owner'),patch.object(python_source_updater,'install',side_effect=OSError('disk full')),patch.object(python_source_updater.subprocess,'Popen') as start:
                with self.assertRaisesRegex(OSError,'disk full'):self.mechanical(old,new,staging)
                self.assertEqual(start.call_count,1)
                self.assertIn(str(old/'app/python_app.py'),start.call_args.args[0])
            self.assertEqual((old/'app/python_app.py').read_bytes(),b'previous')

    def test_exited_new_owner_rolls_back_without_polling_released_handle(self):
        from localharness.desktop_supervisor import JobChild
        class Exited:
            handle=1
            def poll(self):
                if self.handle is None:raise TypeError('released handle')
                self.handle=None
                return 1
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'application';staging=root/'.helene-update-fixture';new=staging/'Helene'
            put(old,'app/python_app.py',b'previous');put(new,'app/python_app.py',b'updated')
            put(old,'python-migration.json',b'{"state":"prepared"}')
            recovery=root/'recovery';note={'id':'fixture','data_paths':[]}
            def install(previous,incoming,**kwargs):
                python_source_updater.apply_files(incoming,previous,recovery)
                return note,recovery
            args=['updater','--old',str(old),'--new',str(new),'--pid','0','--staging',str(staging)]
            with patch.object(sys,'argv',args),patch.object(python_source_updater,'wait_owner'),patch.object(python_source_updater,'install',side_effect=install),patch.object(JobChild,'spawn',return_value=Exited()),patch.object(python_source_updater.subprocess,'Popen') as restart:
                with self.assertRaisesRegex(RuntimeError,'exited before'):self.mechanical(old,new,staging)
                restart.assert_called_once()
            self.assertEqual((old/'app/python_app.py').read_bytes(),b'previous')

    def test_migration_matches_path_components_not_sibling_names(self):
        from localharness.migration_windows import references_root
        self.assertTrue(references_root('"C:/apps/Helene/runtime/python.exe" --root "C:/apps/Helene"','C:/apps/Helene'))
        self.assertFalse(references_root('"C:/apps/HeleneBeta/runtime/python.exe" --root "C:/apps/HeleneBeta"','C:/apps/Helene'))

    def test_preserved_cleanup_is_scheduled_from_observed_startup_not_prepared_note(self):
        from localharness.desktop_supervisor import JobChild
        from localharness.program_state import recovery as recovery_path
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'application';staging=root/'.helene-update-fixture';new=staging/'Helene'
            put(old,'app/python_app.py',b'previous');put(new,'app/python_app.py',b'updated')
            recovery=recovery_path(old,'fixture');recovery.mkdir(parents=True)
            note={'id':'fixture','state':'prepared','source_policy':'preserve','data_paths':[]}
            def install(previous,incoming,**kwargs):return note,recovery
            def started(*args,**kwargs):
                put(old,'python-migration.json',json.dumps({**note,'state':'new_app_started',
                    'startup_proof':{'fixture_only':True}}).encode())
                return Mock(pid=123)
            with patch.object(python_source_updater,'wait_owner'),patch.object(python_source_updater,'install',side_effect=install),\
                 patch.object(JobChild,'spawn',side_effect=started),patch.object(python_source_updater.subprocess,'Popen') as cleanup:
                self.assertEqual(self.mechanical(old,new,staging),0)
            self.assertNotIn('startup_proof',note)
            self.assertEqual(cleanup.call_count,1)
            command=cleanup.call_args.args[0]
            self.assertIn('--discard-preimages',command)
            self.assertEqual(command[command.index('--discard-preimages')+1],'fixture')

    def test_interrupted_overlay_recovers_original_before_new_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new'
            from localharness.program_state import recovery as recovery_path
            recovery=recovery_path(old,'fixture')
            put(old,'app/value',b'original');put(new,'app/value',b'candidate')
            python_source_updater.apply_files(new,old,recovery)
            python_source_updater.active_update(old).write_text(json.dumps({'target':str(old),'recovery':str(recovery),'migration_id':'fixture'}))
            python_source_updater.recover_interrupted(old)
            self.assertEqual((old/'app/value').read_bytes(),b'original')
            self.assertFalse(python_source_updater.active_update(old).exists())
            self.assertTrue((recovery/'update-files.json').is_file())

    def test_extension_preflight_receives_external_config_and_refuses_incompatible(self):
        from localharness import update_extensions
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';tree=root/'external'
            put(old,'helene.json',json.dumps({'tree':str(tree),'code':str(root/'self-code')}).encode())
            put(tree,'extensions/example/extension.json',b'{}');put(new,'python-app.json',b'{"version":"1.9.1"}')
            def checked(command,*args):
                Path(command[command.index('--extension-report')+1]).write_text('{"ok":false,"items":[]}')
                return type('Result',(),{'stdout':b'extension diagnostics\n', 'returncode':2})()
            with patch.object(update_extensions,'run_check',side_effect=checked) as run:
                with self.assertRaisesRegex(RuntimeError,'incompatible'):update_extensions.check(old,new)
                self.assertIn(str(old/'helene.json'),run.call_args.args[0])
                self.assertIn(str(tree),run.call_args.args[0])
                self.assertEqual(len(update_extensions.check(old,new,force=True)),1)

    def test_retry_preserves_both_file_directory_transitions(self):
        for source_directory in (False,True):
            with self.subTest(source_directory=source_directory),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);source=root/'source';destination=root/'copy';delta=root/'delta'
                source_name='node/child' if source_directory else 'node'
                previous_name='node' if source_directory else 'node/child'
                put(source,source_name,b'current');put(destination,previous_name,b'previous')
                app_migration.refresh_preserved(source,destination,delta)
                self.assertEqual((destination/source_name).read_bytes(),b'current')
                self.assertEqual((delta/previous_name).read_bytes(),b'previous')

    def test_update_spawn_failure_rolls_back_actual_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'application';staging=root/'.helene-update-fixture';new=staging/'Helene'
            put(old,'app/python_app.py',b'previous');put(new,'app/python_app.py',b'updated')
            note={'id':'fixture','data_paths':[]};recovery=root/'recovery'
            def install(previous,incoming,**kwargs):
                python_source_updater.apply_files(incoming,previous,recovery)
                return note,recovery
            from localharness.desktop_supervisor import JobChild
            args=['updater','--old',str(old),'--new',str(new),'--pid','0','--staging',str(staging)]
            with patch.object(sys,'argv',args),patch.object(python_source_updater,'wait_owner'),patch.object(python_source_updater,'install',side_effect=install),patch.object(JobChild,'spawn',side_effect=OSError('spawn refused')),patch.object(python_source_updater.subprocess,'Popen') as restored:
                with self.assertRaisesRegex(OSError,'spawn refused'):self.mechanical(old,new,staging)
                restored.assert_called_once()
            self.assertEqual((old/'app/python_app.py').read_bytes(),b'previous')
            self.assertTrue(recovery.is_dir())

    def test_update_prepares_xtts_only_when_selected_payload_is_in_new_stage(self):
        for selected in (False,True):
            with self.subTest(selected=selected),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);old=root/'old';new=root/'new';tree=root/'data';new.mkdir();tree.mkdir()
                put(old,'components/xtts/xtts-bundle.json',b'{}')
                if selected:put(new,'components/xtts/xtts-bundle.json',b'{}')
                note={'id':'fixture','data_paths':[str(tree)]}
                from localharness import update_overlay,program_state
                recovery=program_state.recovery(old,'fixture')
                prepared={'note':note,'recovery':str(recovery),'progress':''}
                python_source_updater.prepared_install_path(new).write_text(json.dumps(prepared))
                with patch.object(app_migration,'prepare') as prepare,patch.object(python_source_updater,'validate_prepared',return_value=prepared),patch.object(update_overlay,'apply'),patch.object(xtts_install,'install') as install:
                    python_source_updater.install(old,new)
                prepare.assert_not_called()  # Mechanical cutover consumes the already prepared snapshot.
                self.assertEqual(install.call_count,int(selected))

    def test_changed_runtime_wheel_refuses_before_any_installer_executes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);wheels=root/'wheels';runtime=root/'runtime'
            put(wheels,'psutil.whl',b'changed')
            record={'packages':{name:{'filename':name+'.whl','sha256':'0'*64}
                                for name in ('psutil','PySocks')}}
            put(wheels,'wheels.json',json.dumps(record).encode())
            with patch.object(python_app_bundle.subprocess,'run') as install:
                with self.assertRaisesRegex(ValueError,'wheel changed'):
                    python_app_bundle.install_runtime_dependencies(runtime,wheels)
                install.assert_not_called()

    def test_program_copy_keeps_dependency_data_but_not_agent_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);package=root/'package';new=root/'application';new.mkdir()
            put(package,'runtime/Lib/vendor/data/resource');put(package,'app/agents/roles.py')
            put(package,'data/private');put(package,'agents/private/helene.json')
            python_migrate.copy_program(package,new,lambda value:None)
            self.assertTrue((new/'runtime/Lib/vendor/data/resource').is_file())
            self.assertTrue((new/'app/agents/roles.py').is_file())
            self.assertFalse((new/'data').exists());self.assertFalse((new/'agents').exists())

    def test_clean_stage_builds_without_broker_or_compiler_and_keeps_vendor_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);stage=root/'stage';desk=root/'desk';source=root/'relay-source'
            put(stage,'helene-svc.exe');put(stage,'helene-host.exe');put(stage,'data/private');put(stage,'agents/another/private')
            put(stage,'helene.json',b'private settings');put(stage,'local-build.json',b'private builder paths')
            put(stage,'components/toolchain/large');put(stage,'components/xtts/model/large')
            put(stage,'runtime/python314._pth',b'.\nimport site\n')
            put(stage,'runtime/psutil/__init__.py');put(stage,'runtime/socks.py')
            put(stage,'tree/fixture.py',b'# Synthetic stock code\n')
            put(stage,'runtime/Lib/site-packages/vendor/data/needed')
            put(stage,'runtime/Lib/site-packages/faster_whisper/module.py')
            for name in ('python_app.py','python_tray.py','python_migrate.py','deskapp.py','deskd/__init__.py','localharness/__init__.py','app/dist/index.html','resources/entry','shell/icons/icon.ico'):put(desk,name)
            binary=put(root,'helene-relay.exe');put(source,'Cargo.toml');put(source,'src/main.rs');put(source,'target/disposable');put(source,'.git/config')
            output=root/'application'
            put(root,'LICENSE');put(desk,'installer/ЛИЦЕНЗИЯ.md');put(desk,'installer/THIRD-PARTY.md');put(desk,'installer/PYTHON-START-HERE.md')
            downloads=(python_app_bundle.DESK/'component-downloads.json').read_bytes()
            put(desk,'component-downloads.json',downloads)
            put(root,'helene/core/xtts_tts_worker.py',(python_app_bundle.DESK.parent/'helene/core/xtts_tts_worker.py').read_bytes())
            with patch.object(python_app_bundle,'DESK',desk):
                python_app_bundle.assemble(stage,output,version='1.9.1')
            self.assertEqual((output/'app/component-downloads.json').read_bytes(),downloads)
            self.assertFalse((output/'helene-svc.exe').exists());self.assertFalse((output/'helene-host.exe').exists())
            self.assertFalse((output/'data').exists());self.assertFalse((output/'agents').exists())
            self.assertFalse((output/'helene.json').exists());self.assertFalse((output/'local-build.json').exists())
            self.assertFalse((output/'components/toolchain').exists());self.assertFalse((output/'components/xtts').exists())
            self.assertTrue((output/'runtime/Lib/site-packages/vendor/data/needed').is_file())
            self.assertFalse((output/'runtime/Lib/site-packages/faster_whisper').exists())
            self.assertTrue((stage/'helene-svc.exe').is_file())
            self.assertEqual(json.loads((output/'components/components.json').read_text())['components'],{})
            self.assertFalse((output/'components/relay/source/.git').exists())
            manifest=json.loads((output/'python-app.json').read_text())
            self.assertEqual(manifest['version'],'1.9.1');self.assertFalse(manifest['extensions']['xtts']['default'])
            self.assertFalse(manifest['extensions']['whisper']['default'])
            self.assertNotIn('complete',json.loads((output/'helene-build.json').read_text()))

    def test_nested_output_refuses_before_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with self.assertRaises(ValueError):python_app_bundle.assemble(root,root/'out',root/'helene-relay.exe',root,version='1.9.1')
            self.assertFalse((root/'out').exists())


if __name__=='__main__':unittest.main()
