import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
from localharness import upstream_recovery as recovery,code_transition,python_source_updater as updater
from localharness.package_notice import read,write


class Recovery(unittest.TestCase):
    def fixture(self,root):
        program=root/'Helene';data=root/'existing-data'
        (data/'memory').mkdir(parents=True);(data/'memory/history.jsonl').write_bytes(b'private-history\n')
        for name,text in [('tree/agent.py','upstream = True\n'),('code/local/agent.py','self_modified = True\n'),
            ('app/python_app.py','# fixture app'),('app/helene_setup.py','# fixture entry'),
            ('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3','fixture runtime'),
            ('python-app.json','{"version":"2.2.10","delivery":"portable"}')]:
            path=program/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        write(program/'helene.json',{'tree':str(data),'code':str(program/'code/local'),'mode':'local',
            'model':{'key':'fixture-key'},'autonomy':{'paused':True},'setup_complete':True})
        code_transition.seal(program)
        files={p.relative_to(program).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in program.rglob('*') if p.is_file() and p.relative_to(program).parts[0] not in ('tree','code') and p.name!='helene.json'}
        # An old/malformed passport must still not copy a mutable data entry.
        (program/'data').mkdir();(program/'data/private.txt').write_text('must not copy')
        files['data/private.txt']=hashlib.sha256(b'must not copy').hexdigest()
        write(program/'python-package-files.json',{'files':files})
        return program,data

    def test_pristine_stage_uses_only_program_inventory_and_no_data_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            program,data=self.fixture(Path(tmp));new=recovery.stage(program,'a'*32)
            from localharness.program_state import staging_belongs
            self.assertTrue(staging_belongs(program,new.parent))
            self.assertTrue(new.is_relative_to(program/'.helene'))
            self.assertFalse(list(program.parent.glob('.helene-update-*')))
            self.assertFalse((new/'data').exists());self.assertFalse((new/'helene.json').exists())
            self.assertFalse((new/'code').exists())
            self.assertEqual((new/'tree/agent.py').read_text(),'upstream = True\n')
            self.assertEqual((data/'memory/history.jsonl').read_bytes(),b'private-history\n')

    def test_restore_composes_existing_prepare_install_and_overlay_with_same_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            program,data=self.fixture(Path(tmp));before=read(program/'helene.json');events=[]
            def perform(args):
                events.append('perform')
                note,saved=updater.install(args.old,args.new)
                from localharness.app_migration import code_path
                cfg=read(program/'helene.json');code=code_path(program/'helene.json',cfg)
                self.assertEqual((code/'agent.py').read_text(),'upstream = True\n')
                self.assertEqual(Path(cfg['tree']),data)
                self.assertEqual(cfg['model'],before['model']);self.assertEqual(cfg['autonomy'],before['autonomy'])
                self.assertEqual((program/'code/local/agent.py').read_text(),'self_modified = True\n')
                self.assertTrue((saved/'helene.json').is_file())
                return 0
            with patch.object(recovery,'receipt',wraps=recovery.receipt), \
                 patch('localharness.migration_lifecycle.service',return_value=None), \
                 patch('localharness.migration_lifecycle.stop',side_effect=lambda _:events.append('stop')), \
                 patch('localharness.migration_lifecycle.quiescent'),patch.object(updater,'perform',side_effect=perform):
                self.assertEqual(recovery.restore(program,operation='b'*32),0)
            self.assertEqual(events,['stop','perform'])
            self.assertEqual(read(recovery.receipt(program,'b'*32))['state'],'completed')
            self.assertEqual((data/'memory/history.jsonl').read_bytes(),b'private-history\n')

    def test_bad_upstream_refuses_before_any_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            program,data=self.fixture(Path(tmp));(program/'code-upstream.zip').write_bytes(b'changed')
            cfg=(program/'helene.json').read_bytes()
            with patch('localharness.migration_windows.stop') as stop:
                with self.assertRaises(ValueError):recovery.restore(program,operation='c'*32)
                stop.assert_not_called()
            self.assertEqual((program/'helene.json').read_bytes(),cfg)
            self.assertEqual(read(recovery.receipt(program,'c'*32))['state'],'failed')

    def test_named_entry_routes_normal_wizard_and_explicit_emergency(self):
        import helene_setup
        with patch('python_migration_window.main',return_value=0) as wizard:
            self.assertEqual(helene_setup.main(['--root','C:/Helene','--old','C:/Old']),0)
            self.assertEqual(wizard.call_args.args[0],['--root',str(Path('C:/Helene')),'--old','C:/Old'])
            helene_setup.main(['--root','C:/Helene','--source-mode','clean','--force-extensions'])
            self.assertEqual(wizard.call_args.args[0],['--root',str(Path('C:/Helene')),'--force-extensions'])
        with patch.object(recovery,'restore',return_value=0) as restore:
            self.assertEqual(helene_setup.main(['--root','C:/Helene','--restore-upstream','--confirmed']),0)
            restore.assert_called_once()

    def test_window_recovery_requires_a_bound_helper_receipt_not_only_a_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            program,data=self.fixture(Path(tmp))
            child=SimpleNamespace(pid=123,poll=lambda:0)
            with patch.object(recovery.subprocess,'Popen',return_value=child):
                with self.assertRaisesRegex(RuntimeError,'не принял операцию'):recovery.launch(program)
            def admit(command,**kwargs):
                operation=command[command.index('--operation')+1]
                write(recovery.receipt(program,operation),{'operation':operation,'root':str(program),'state':'preparing'})
                return child
            with patch.object(recovery.subprocess,'Popen',side_effect=admit):
                result=recovery.launch(program)
            self.assertEqual(result['state'],'preparing');self.assertEqual(result['pid'],123)

    @unittest.skipUnless(os.name=='nt','Windows launcher')
    def test_real_helene_exe_forwards_to_bundled_python_without_native_shell(self):
        import windows_archive_entry
        compiler=shutil.which('rustc')
        if not compiler or subprocess.run([compiler,'--version'],capture_output=True).returncode:
            self.skipTest('A runnable native Rust toolchain is not provisioned in this isolated executor')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();(root/'app').mkdir()
            # Use only this disposable runtime entry and an inert app script.
            shutil.copyfile(sys.executable,root/'runtime/pythonw.exe')
            dll=Path(sys.base_prefix)/('python'+str(sys.version_info.major)+str(sys.version_info.minor)+'.dll')
            shutil.copyfile(dll,root/'runtime'/dll.name)
            # This inert fixture explicitly acknowledges readiness; real UI
            # acknowledgement is covered by packaged browser/native tests.
            from localharness import bootstrap_ready
            shutil.copyfile(bootstrap_ready.__file__,root/'app/bootstrap_ready.py')
            (root/'app/python_app.py').write_text("import json,sys,bootstrap_ready\nfrom pathlib import Path\nPath('entry-proof.json').write_text(json.dumps(sys.argv))\nbootstrap_ready.signal()\n")
            windows_archive_entry.write(root)
            self.assertTrue((root/'helene.exe').is_file())
            self.assertFalse((root/'helene.json').exists(),'Packaging must not create an agent profile or bypass first setup')
            assembled=root/'separate-assembly';assembled.mkdir()
            windows_archive_entry.write(assembled,launcher_binary=root/'helene.exe')
            self.assertEqual((assembled/'helene.exe').read_bytes(),(root/'helene.exe').read_bytes())
            self.assertFalse((assembled/'helene.json').exists())
            self.assertFalse(list(root.glob('*.vbs')))
            self.assertIn('helene.exe --migration-wizard',(root/'START-HERE.md').read_text())
            env=dict(os.environ,PYTHONHOME=sys.base_prefix)
            result=subprocess.run([str(root/'helene.exe'),'--migration-wizard'],env=env,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0)
            proof=root/'entry-proof.json';deadline=time.monotonic()+10
            while not proof.is_file() and time.monotonic()<deadline:time.sleep(.05)
            self.assertTrue(proof.is_file())
            args=json.loads(proof.read_text());self.assertEqual(args[1:],['--root',str(root),'--migration-wizard'])


if __name__=='__main__':unittest.main()
