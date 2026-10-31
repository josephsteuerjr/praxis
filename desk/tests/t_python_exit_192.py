"""Updater rollback never races an unconfirmed retained Job; scoped window exit."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'desk'))
from localharness import python_source_updater as updater,migration_windows
import python_app
class Exit(unittest.TestCase):
    def test_actual_window_bridge_exit_and_descriptor_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=python_app.Application(root,root);app.serve();app.publish_control();app.relaunch=True
            try:
                value=json.loads(app.control_file.read_text(encoding='utf-8'));value['root']=str(root/'other');app.control_file.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'identity'):migration_windows.request_python_exit(root,os.getpid())
                app.publish_control();migration_windows.request_python_exit(root,os.getpid())
                self.assertTrue(app.stopping.is_set());self.assertTrue(app.exiting);self.assertFalse(app.relaunch)
            finally:app.close()
            self.assertFalse(app.control_file.exists())
    def test_legacy_without_control_requires_manual_exit_not_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'tray Exit'):migration_windows.request_python_exit(Path(tmp),123)
    @unittest.skipUnless(os.name == 'nt', 'Windows Job handle custody')
    def test_graceful_failure_cannot_skip_job_stop_or_race_rollback(self):
        from localharness.desktop_process import JobChild
        for unconfirmed in (False,True):
            with self.subTest(unconfirmed=unconfirmed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);old=root/'old';staging=root/'.helene-update-fixture';new=staging/'Helene';recovery=root/'recovery'
                for folder,text in ((old,'old'),(new,'new')):
                    (folder/'app').mkdir(parents=True);(folder/'app/python_app.py').write_text(text)
                (old/'python-migration.json').write_text('{"id":"fixture","state":"prepared"}')
                events=[]
                class Child:
                    handle=1;calls=0
                    def poll(self):
                        self.calls+=1
                        if self.calls==1:raise RuntimeError('startup failed')
                        return None
                    def stop(self):
                        events.append('stop')
                        if unconfirmed:raise RuntimeError('job emptiness unconfirmed')
                        self.handle=None
                child=Child()
                def install(a,b):
                    updater.apply_files(b,a,recovery)
                    updater.active_update(a).write_text(json.dumps({'target':str(a),'recovery':str(recovery)}))
                    return {'id':'fixture','data_paths':[]},recovery
                original=updater.rollback
                def rollback(a,b):
                    self.assertEqual(events,['stop']);events.append('rollback');return original(a,b)
                args=['updater','--old',str(old),'--new',str(new),'--pid','0','--staging',str(staging)]
                with patch.object(sys,'argv',args),patch.object(updater,'wait_owner'),patch.object(updater,'install',side_effect=install),patch.object(JobChild,'spawn',return_value=child),patch.object(migration_windows,'close_python',side_effect=TimeoutError('hidden window')),patch.object(updater,'rollback',side_effect=rollback),patch.object(updater.subprocess,'Popen') as reopened:
                    with self.assertRaisesRegex(RuntimeError,'unconfirmed' if unconfirmed else 'startup failed'):updater.main()
                    self.assertEqual(reopened.call_count,0 if unconfirmed else 1)
                self.assertEqual((old/'app/python_app.py').read_text(),'new' if unconfirmed else 'old')
                self.assertEqual(updater.active_update(old).exists(),unconfirmed)
if __name__=='__main__':unittest.main()
