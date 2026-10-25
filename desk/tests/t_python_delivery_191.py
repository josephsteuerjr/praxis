"""Builder shape and optional-component boundaries, not final binary qualification."""
import json
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
            with patch.object(Path,'replace',replace),patch.object(python_source_updater.time,'monotonic',side_effect=[0,4]):
                with self.assertRaises(PermissionError):python_source_updater.apply_files(new,old,recovery)
            self.assertEqual((old/'app/value').read_bytes(),b'old')

    def test_python_close_refuses_unclean_owner_and_never_forces_it(self):
        from localharness import migration_windows
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);tree=root/'data'
            put(root,'helene.json',json.dumps({'tree':str(tree)}).encode())
            record=put(tree,'.desktop-owner.lock',b'active\n')
            rows=json.dumps([{'ProcessId':123,'CommandLine':'"'+str(root/'app/python_app.py')+'" --root "'+str(root)+'"'}])
            with patch.object(migration_windows,'request_python_exit'),patch.object(migration_windows.os,'name','nt'),patch.object(migration_windows,'powershell',side_effect=[rows,'']) as commands:
                with self.assertRaisesRegex(RuntimeError,'child teardown'):migration_windows.close_python(root)
                self.assertFalse(any('Stop-Process' in call.args[0] for call in commands.call_args_list))
            record.write_bytes(b'clean\n')
            with patch.object(migration_windows,'request_python_exit'),patch.object(migration_windows.os,'name','nt'),patch.object(migration_windows,'powershell',side_effect=[rows,'']):
                migration_windows.close_python(root)

    def test_prepare_failure_reopens_old_application_without_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'application';staging=root/'.helene-update-fixture';new=staging/'Helene'
            put(old,'app/python_app.py',b'previous');new.mkdir(parents=True)
            args=['updater','--old',str(old),'--new',str(new),'--pid','0','--staging',str(staging)]
            with patch.object(sys,'argv',args),patch.object(python_source_updater,'wait_owner'),patch.object(python_source_updater,'install',side_effect=OSError('disk full')),patch.object(python_source_updater.subprocess,'Popen') as start:
                with self.assertRaisesRegex(OSError,'disk full'):python_source_updater.main()
                self.assertEqual(start.call_count,1)
                self.assertIn(str(old/'app/python_app.py'),start.call_args.args[0])
            self.assertEqual((old/'app/python_app.py').read_bytes(),b'previous')

    def test_exited_new_owner_rolls_back_without_polling_released_handle(self):
        from localharness.desktop_process import JobChild
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
            def install(previous,incoming):
                python_source_updater.apply_files(incoming,previous,recovery)
                return note,recovery
            args=['updater','--old',str(old),'--new',str(new),'--pid','0','--staging',str(staging)]
            with patch.object(sys,'argv',args),patch.object(python_source_updater,'wait_owner'),patch.object(python_source_updater,'install',side_effect=install),patch.object(JobChild,'spawn',return_value=Exited()),patch.object(python_source_updater.subprocess,'Popen') as restart:
                with self.assertRaisesRegex(RuntimeError,'exited before'):python_source_updater.main()
                restart.assert_called_once()
            self.assertEqual((old/'app/python_app.py').read_bytes(),b'previous')

    def test_migration_matches_path_components_not_sibling_names(self):
        from localharness.migration_windows import references_root
        self.assertTrue(references_root('"C:/apps/Helene/runtime/python.exe" --root "C:/apps/Helene"','C:/apps/Helene'))
        self.assertFalse(references_root('"C:/apps/HeleneBeta/runtime/python.exe" --root "C:/apps/HeleneBeta"','C:/apps/Helene'))

    def test_interrupted_overlay_recovers_original_before_new_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';recovery=root/'recovery'
            put(old,'app/value',b'original');put(new,'app/value',b'candidate')
            python_source_updater.apply_files(new,old,recovery)
            python_source_updater.active_update(old).write_text(json.dumps({'target':str(old),'recovery':str(recovery)}))
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
            def install(previous,incoming):
                python_source_updater.apply_files(incoming,previous,recovery)
                return note,recovery
            from localharness.desktop_process import JobChild
            args=['updater','--old',str(old),'--new',str(new),'--pid','0','--staging',str(staging)]
            with patch.object(sys,'argv',args),patch.object(python_source_updater,'wait_owner'),patch.object(python_source_updater,'install',side_effect=install),patch.object(JobChild,'spawn',side_effect=OSError('spawn refused')),patch.object(python_source_updater.subprocess,'Popen') as restored:
                with self.assertRaisesRegex(OSError,'spawn refused'):python_source_updater.main()
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
                with patch.object(app_migration,'prepare',return_value=note) as prepare,patch.object(python_source_updater,'apply_files'),patch.object(xtts_install,'install') as install:
                    python_source_updater.install(old,new)
                self.assertFalse(prepare.call_args.kwargs['preserve_voice'])
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
            put(stage,'runtime/Lib/site-packages/vendor/data/needed')
            put(stage,'runtime/Lib/site-packages/piper/module.py')
            for name in ('python_app.py','python_tray.py','python_migrate.py','deskapp.py','deskd/__init__.py','localharness/__init__.py','app/dist/index.html','resources/entry','shell/icons/icon.ico'):put(desk,name)
            binary=put(root,'helene-relay.exe');put(source,'Cargo.toml');put(source,'src/main.rs');put(source,'target/disposable');put(source,'.git/config')
            output=root/'application'
            put(root,'LICENSE');put(desk,'installer/ЛИЦЕНЗИЯ.md');put(desk,'installer/THIRD-PARTY.md');put(desk,'installer/PYTHON-START-HERE.md')
            downloads=(python_app_bundle.DESK/'component-downloads.json').read_bytes()
            put(desk,'component-downloads.json',downloads)
            with patch.object(python_app_bundle,'DESK',desk):
                python_app_bundle.assemble(stage,output,binary,source,version='1.9.1')
            self.assertEqual((output/'app/component-downloads.json').read_bytes(),downloads)
            self.assertFalse((output/'helene-svc.exe').exists());self.assertFalse((output/'helene-host.exe').exists())
            self.assertFalse((output/'data').exists());self.assertFalse((output/'agents').exists())
            self.assertFalse((output/'helene.json').exists());self.assertFalse((output/'local-build.json').exists())
            self.assertFalse((output/'components/toolchain').exists());self.assertFalse((output/'components/xtts').exists())
            self.assertTrue((output/'runtime/Lib/site-packages/vendor/data/needed').is_file())
            self.assertFalse((output/'runtime/Lib/site-packages/piper').exists())
            self.assertTrue((stage/'helene-svc.exe').is_file())
            entry=app_components.verify(output,'relay');self.assertTrue(entry['toolchain_optional'])
            self.assertFalse((output/'components/relay/source/.git').exists())
            manifest=json.loads((output/'python-app.json').read_text())
            self.assertEqual(manifest['version'],'1.9.1');self.assertTrue(manifest['extensions']['xtts']['default'])
            self.assertFalse(manifest['extensions']['whisper']['default'])
            self.assertNotIn('complete',json.loads((output/'helene-build.json').read_text()))

    def test_nested_output_refuses_before_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with self.assertRaises(ValueError):python_app_bundle.assemble(root,root/'out',root/'helene-relay.exe',root,version='1.9.1')
            self.assertFalse((root/'out').exists())


if __name__=='__main__':unittest.main()
