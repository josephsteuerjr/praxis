"""Real controller thread and supervisor agree on their startup/environment ABI."""
from pathlib import Path
import os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import desktop_supervisor as supervisor,python_controller,owner_stop
from localharness.package_notice import read,write


class ControllerSupervisor(unittest.TestCase):
    def exercise(self,bootstrap):
        with tempfile.TemporaryDirectory(prefix='controller-supervisor-') as tmp:
            root=Path(tmp);data=root/'data';data.mkdir()
            write(root/'helene.json',{'tree':str(data),'mode':'local','setup_complete':False})
            controller=python_controller.Controller(root,lambda *a:None)
            controller.bootstrap=bootstrap
            controller.update_job={'kind':'migration','id':'admitted-job','tree':str(root/'bootstrap')}
            observed=[];original=supervisor._desktop_status
            def plans(*a,**kw):
                observed.append(dict(kw['environ']))
                os.environ['HELENE_TEST_IMMUTABLE_AMBIENT']='changed-by-gui'
                spec=supervisor.Spec('channel',(sys.executable,'-B','-c','import time;time.sleep(60)'),
                    root,dict(kw['environ']),root/'child.log',0)
                return {supervisor.BASE_ID:(data,{supervisor.BASE_ID+':channel':spec})}
            def status(*a,**kw):
                original(*a,**kw);controller.stop.set()
            with patch.dict(os.environ,{'HELENE_TEST_IMMUTABLE_AMBIENT':'original',
                    'HELENE_MIGRATION_SESSION_TREE':'foreign-tree','HELENE_MIGRATION_SESSION_JOB':'foreign-job'}), \
                 patch.object(owner_stop,'stopped',return_value=False), \
                 patch.object(supervisor,'_plans',side_effect=plans), \
                 patch.object(supervisor,'_desktop_status',side_effect=status), \
                 patch.object(controller,'sync_publishers'), \
                 patch('localharness.desktop_update_request.watch'):
                controller.start()
                controller.thread.join(10)
                self.assertFalse(controller.thread.is_alive())
                self.assertIsNone(controller.error,'The real supervisor must accept the real controller call')
                self.assertEqual(os.environ['HELENE_MIGRATION_SESSION_JOB'],'foreign-job')
            self.assertGreaterEqual(len(observed),2)
            self.assertTrue(all(row['HELENE_TEST_IMMUTABLE_AMBIENT']=='original' for row in observed))
            status_value=read(data/'.python-desktop-status')
            self.assertEqual(status_value['nonce'],controller.nonce)
            self.assertTrue(status_value['agents'][supervisor.BASE_ID]['pids']['channel'])
            return observed

    def test_normal_start_keeps_ambient_snapshot_and_removes_stale_bootstrap_binding(self):
        observed=self.exercise(False)
        self.assertTrue(all('HELENE_MIGRATION_SESSION_JOB' not in row and 'HELENE_MIGRATION_SESSION_TREE' not in row for row in observed))

    def test_migration_start_passes_only_the_actual_admitted_bootstrap_binding(self):
        observed=self.exercise(True)
        self.assertTrue(all(row['HELENE_MIGRATION_SESSION_JOB']=='admitted-job' and row['HELENE_MIGRATION_SESSION_TREE'].endswith('bootstrap') for row in observed))


if __name__=='__main__':unittest.main()
