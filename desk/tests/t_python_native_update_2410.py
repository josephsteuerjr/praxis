"""Native updater consumers on disposable profiles, never live Hélène."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1];sys.path.insert(0,str(DESK))
from localharness import python_updates as updates,python_source_updater as updater,app_migration
import python_app
import python_migrate


def put(root,name,data):
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p

class NativeUpdate(unittest.TestCase):
    def test_duplicate_http_install_request_cannot_launch_two_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner=SimpleNamespace(root=Path(tmp));entered=threading.Event();release=threading.Event();got=[]
            def prepare(*args):entered.set();release.wait(3);return {'updater_pid':123}
            with patch.object(updates,'_update_install',side_effect=prepare) as start:
                worker=threading.Thread(target=lambda:got.append(updates.update_install(owner,'unused')));worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    with self.assertRaisesRegex(RuntimeError,'already preparing'):updates.update_install(owner,'unused')
                    release.set();worker.join(3);self.assertFalse(worker.is_alive())
                    with self.assertRaisesRegex(RuntimeError,'already preparing'):updates.update_install(owner,'unused')
                    self.assertEqual(start.call_count,1)
                finally:
                    release.set();worker.join(3)
                    updates._update_targets.discard(os.path.normcase(str(owner.root.resolve())))

    def test_pre_handoff_failure_can_be_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner=SimpleNamespace(root=Path(tmp))
            with patch.object(updates,'_update_install',side_effect=ValueError('bad archive')) as start:
                for _ in range(2):
                    with self.assertRaises(ValueError):updates.update_install(owner,'unused')
                self.assertEqual(start.call_count,2)

    def test_startup_recovery_is_real_before_controller_import_and_execs_restored_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);old=base/'application';incoming=base/'incoming';recovery=base/'recovery'
            put(old,'app/python_app.py',b'print("RESTORED_BEFORE_CONTROLLER")\n')
            put(incoming,'app/python_app.py',(DESK/'python_app.py').read_bytes())
            updater.apply_files(incoming,old,recovery)
            updater.active_update(old).write_text(json.dumps({'target':str(old),'recovery':str(recovery)}))
            shutil.copytree(DESK/'localharness',old/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy2(DESK/'python_migrate.py',old/'app/python_migrate.py')
            (old/'app/localharness/python_controller.py').write_text('raise RuntimeError("MIXED_CONTROLLER_IMPORTED")')
            if os.name=='nt':
                try:(old/'runtime').symlink_to(Path(sys.executable).parent,target_is_directory=True)
                except OSError as exc:self.skipTest(str(exc))
            else:
                (old/'runtime/bin').mkdir(parents=True);(old/'runtime/bin/python3').symlink_to(sys.executable)
            got=subprocess.run([sys.executable,str(old/'app/python_app.py'),'--root',str(old)],capture_output=True,timeout=20)
            self.assertEqual(got.returncode,0,got.stderr.decode(errors='replace'))
            self.assertIn(b'RESTORED_BEFORE_CONTROLLER',got.stdout)
            self.assertNotIn(b'MIXED_CONTROLLER_IMPORTED',got.stderr)
            self.assertFalse(updater.active_update(old).exists())

    def test_update_new_generation_does_not_roll_back_its_own_prepared_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'app';root.mkdir()
            updater.active_update(root).write_text(json.dumps({'target':str(root),'migration_id':'one','recovery':str(root.parent/'recovery')}))
            (root/'python-migration.json').write_text(json.dumps({'id':'one','state':'prepared','new_root':str(root)}))
            self.assertFalse(updater.recover_startup(root,'one'))
            self.assertTrue(updater.active_update(root).exists())
            with python_migrate.installation_lock(root):
                with self.assertRaises(Exception):updater.recover_startup(root)

    def test_startup_is_excluded_while_prepare_has_no_overlay_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);old=base/'application';new=base/'incoming';new.mkdir()
            put(old,'app/python_app.py',(DESK/'python_app.py').read_bytes())
            shutil.copytree(DESK/'localharness',old/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy2(DESK/'python_migrate.py',old/'app/python_migrate.py')
            (old/'app/localharness/python_controller.py').write_text('raise RuntimeError("CONTROLLER_DURING_PREPARATION")')
            put(old,'helene.json',json.dumps({'mode':'remote','tree':str(old/'profile'),'code':str(old/'code')}).encode())
            entered=threading.Event();release=threading.Event();errors=[]
            def prepare(*args,**kwargs):entered.set();release.wait(5);raise ValueError('prepare sentinel')
            def work():
                try:
                    with python_migrate.installation_lock(old):updater.install(old,new)
                except ValueError as exc:errors.append(str(exc))
            with patch('localharness.update_profile.prepare',side_effect=prepare):
                thread=threading.Thread(target=work);thread.start()
                try:
                    self.assertTrue(entered.wait(2));self.assertFalse(updater.active_update(old).exists())
                    child=subprocess.run([sys.executable,'-B',str(old/'app/python_app.py'),'--root',str(old)],capture_output=True,timeout=5)
                    self.assertNotEqual(child.returncode,0)
                    self.assertNotIn(b'CONTROLLER_DURING_PREPARATION',child.stderr)
                finally:release.set();thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,['prepare sentinel'])
            self.assertFalse(updater.recover_startup(old))

    def test_bound_initial_migration_can_start_under_the_wizard_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'python-migration.json').write_text(json.dumps({'id':'fresh','new_root':str(root),'state':'prepared'}))
            with python_migrate.installation_lock(root):
                self.assertFalse(updater.recover_startup(root,'fresh'))
                with self.assertRaises(ValueError):updater.recover_startup(root,'wrong')

    def test_proven_ready_generation_is_not_rolled_back_after_helper_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';new=root/'new';recovery=root/'recovery'
            put(old,'value',b'old');put(new,'value',b'new');updater.apply_files(new,old,recovery)
            updater.active_update(old).write_text(json.dumps({'target':str(old),'migration_id':'one','recovery':str(recovery)}))
            (old/'python-migration.json').write_text('{"id":"one","state":"new_app_started"}')
            self.assertTrue(updater.recover_startup(old));self.assertEqual((old/'value').read_bytes(),b'new')

    def test_panic_never_waits_for_or_reads_update_journal(self):
        with patch.object(updater,'recover_startup',side_effect=AssertionError('must not run')):
            self.assertFalse(python_app.recover_before_imports(['--root','unused','--panic']))

    def test_browser_ui_confirmation_uses_real_migration_identity_and_keeps_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tree=root/'data';code=root/'code';tree.mkdir();code.mkdir()
            (root/'helene.json').write_text(json.dumps({'tree':str(tree),'code':str(code)}))
            note={'id':'one','new_root':str(root),'data_paths':[str(tree)],'source_paths':[{'selected':str(code)}],'state':'prepared'}
            app_migration.write_note(root,note)
            app=python_app.Application(root,root);app.migration_id='one';app.browser_mode=True
            try:
                from localharness import owner_stop
                with patch.object(owner_stop,'stopped',return_value=True):
                    self.assertTrue(app.invoke('python_ready',{})['confirmed'])
                recorded=json.loads((root/'python-migration.json').read_text())
                self.assertEqual(recorded['state'],'new_app_started');self.assertTrue(recorded['startup_proof']['stopped'])
            finally:app.server.server_close()

class UpdateHandoff(unittest.TestCase):
    def test_actual_helper_refuses_busy_target_before_old_owner_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);old=base/'application';old.mkdir();staging=base/'.helene-update-test';(staging/'Helene').mkdir(parents=True)
            argv=[sys.executable,str(Path(updater.__file__).resolve()),'--old',str(old),'--new',str(staging/'Helene'),'--pid',str(os.getpid()),'--staging',str(staging)]
            with python_migrate.installation_lock(old):
                child=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=dict(os.environ,PYTHONPATH=str(DESK)))
                try:
                    with self.assertRaisesRegex(RuntimeError,'refused admission'):updates.await_admission(child,staging,old,timeout=5)
                    self.assertFalse((staging/'updater-admitted.json').exists())
                finally:
                    if child.poll() is None:child.terminate()
                    child.communicate(timeout=5)
            self.assertTrue(old.is_dir());self.assertFalse(updater.active_update(old).exists())

    def test_rollback_reopen_waits_for_target_lease_then_execs_legacy_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);old=base/'application';staging=base/'.helene-update-test';new=staging/'Helene'
            old.mkdir();new.mkdir(parents=True)
            marker=old/'reopened'
            legacy=('import os,json,psutil\nfrom pathlib import Path\np=Path('+repr(str(marker))+')\nt=p.with_suffix(".tmp")\nt.write_text(json.dumps({"pid":os.getpid(),"birth":psutil.Process().create_time(),"cwd":os.getcwd()}))\nt.replace(p)\n')
            put(old,'app/python_app.py',legacy.encode())
            (new/'app').mkdir();shutil.copy2(DESK/'python_migrate.py',new/'app/python_migrate.py')
            shutil.copytree(DESK/'localharness',new/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
            for root in (old,new):
                if os.name=='nt':
                    try:(root/'runtime').symlink_to(Path(sys.executable).parent,target_is_directory=True)
                    except OSError as exc:self.skipTest(str(exc))
                else:
                    (root/'runtime/bin').mkdir(parents=True);(root/'runtime/bin/python3').symlink_to(sys.executable)
            args=SimpleNamespace(old=old,new=new,staging=staging,archive=[],pid=0,cleanup=False,browser=True)
            children=[];popen=subprocess.Popen
            def launch(*a,**k):
                child=popen(*a,**k);children.append(child);return child
            try:
                with python_migrate.installation_lock(old),patch.object(updater,'install',side_effect=ValueError('failed update')),patch.object(updater.subprocess,'Popen',side_effect=launch):
                    with self.assertRaisesRegex(ValueError,'failed update'):updater.perform(args)
                    self.assertEqual(len(children),1)
                    log=old/'python-update-recovery.log';deadline=time.monotonic()+8
                    while time.monotonic()<deadline and b'Waiting for update installation lease' not in log.read_bytes():
                        if children[0].poll() is not None:break
                        time.sleep(.02)
                    self.assertIn(b'Waiting for update installation lease',log.read_bytes())
                    self.assertFalse(marker.exists());self.assertIsNone(children[0].poll())
                self.assertEqual(children[0].wait(timeout=10),0,log.read_text(encoding='utf-8',errors='replace'))
                # Windows CRT exec can retire the trampoline PID before its
                # replacement exits; observe the actual legacy entry event.
                deadline=time.monotonic()+10
                while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
                record=json.loads(marker.read_text())
                self.assertEqual(Path(record['cwd']).resolve(),old.resolve())
                import psutil
                try:
                    process=psutil.Process(record['pid'])
                    if process.create_time()==record['birth']:process.wait(timeout=10)
                except psutil.NoSuchProcess:pass
            finally:
                for child in children:
                    if child.poll() is None:child.kill();child.wait(timeout=5)

    def test_cli_inplace_uses_the_existing_lease_without_child_reacquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'application';put(old,'python-app.json',b'{}');seen=[];order=[]
            def prepare(*args,**kwargs):order.append('prepare')
            def stop(*args):order.append('stop')
            def observe(transaction,receipt,ready):ready.set();return transaction()
            def perform(args):
                with self.assertRaises(Exception):
                    with python_migrate.installation_lock(old):pass
                seen.append(args.old)
                order.append('perform')
                (old/'python-migration.json').write_text(json.dumps({'id':'one','state':'completed','old_root':str(old),'new_root':str(old)}))
            from localharness import update_extensions
            argv=['migration','--dir',str(old),'--in-place','--confirmed']
            with patch.object(sys,'argv',argv),patch.object(python_migrate,'copy_program'),patch.object(update_extensions,'check'),patch.object(python_migrate.migration_windows,'stop',side_effect=stop),patch.object(python_migrate.migration_windows,'quiescent'),patch.object(updater,'prepare_install',side_effect=prepare),patch.object(updater,'validate_prepared'),patch('localharness.update_window.run',side_effect=observe),patch.object(updater,'perform',side_effect=perform):
                python_migrate.main()
            self.assertEqual(seen,[old])
            self.assertEqual(order,['prepare','stop','perform'])

    def test_two_ui_ready_requests_write_once_and_never_regress_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=python_app.Application(root,root);app.migration_id='one'
            calls=[];entered=threading.Event();release=threading.Event();errors=[]
            def confirm(*args,**kwargs):entered.set();release.wait(2);calls.append(1)
            from localharness import owner_stop
            def request():
                try:app.invoke('python_ready',{})
                except Exception as exc:errors.append(exc)
            try:
                with patch.object(owner_stop,'stopped',return_value=True),patch.object(app_migration,'confirm_started',side_effect=confirm):
                    first=threading.Thread(target=request);second=threading.Thread(target=request);first.start();self.assertTrue(entered.wait(1));second.start();release.set();first.join(3);second.join(3)
                    self.assertEqual(errors,[]);self.assertEqual(calls,[1])
                    app.invoke('python_ready',{});self.assertEqual(calls,[1])
            finally:release.set();app.server.server_close()

if __name__=='__main__':unittest.main()

