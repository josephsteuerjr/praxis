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

    def test_status_export_denial_keeps_children_and_exact_refresh_ack(self):
        import threading,time
        with tempfile.TemporaryDirectory(prefix='controller-export-') as tmp:
            root=Path(tmp);data=root/'data';data.mkdir()
            write(root/'helene.json',{'tree':str(data),'mode':'local','setup_complete':False})
            controller=python_controller.Controller(root,lambda *a:None)
            denied=threading.Event();denied.set()
            original=supervisor._desktop_status
            def plans(*a,**kw):
                spec=supervisor.Spec('channel',(sys.executable,'-B','-c','import time;time.sleep(60)'),
                    root,dict(kw['environ']),root/'child.log',0)
                return {supervisor.BASE_ID:(data,{supervisor.BASE_ID+':channel':spec})}
            def export(*a,**kw):
                if denied.is_set():raise PermissionError('synthetic destination sharing denial')
                return original(*a,**kw)
            def wait_for(predicate):
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    if predicate():return
                    time.sleep(.02)
                self.fail('owner observation did not reach the required state')
            def status_ready():
                try:return bool(controller.status().get('agents'))
                except RuntimeError:return False
            with patch.object(owner_stop,'stopped',return_value=False), \
                 patch.object(supervisor,'_plans',side_effect=plans), \
                 patch.object(supervisor,'_desktop_status',side_effect=export), \
                 patch.object(controller,'sync_publishers'), \
                 patch('localharness.desktop_update_request.watch'):
                controller.start()
                try:
                    wait_for(status_ready)
                    first=controller.status()
                    pid=first['agents'][supervisor.BASE_ID]['pids']['channel']
                    self.assertIn('status_export_error',first)
                    self.assertFalse((data/'.python-desktop-status').exists())
                    self.assertTrue(controller.thread.is_alive())
                    # Same-process acknowledgement is an actual fresh observation,
                    # not a claim that the separate native/CLI export succeeded.
                    controller.refresh()
                    one=controller.status()['ack']
                    self.assertTrue(one)
                    self.assertFalse((data/'.python-desktop-status').exists())
                    second=threading.Event();errors=[]
                    def refresh():
                        try:controller.refresh();second.set()
                        except BaseException as exc:errors.append(exc)
                    reader=threading.Thread(target=refresh);reader.start()
                    reader.join(10)
                    self.assertFalse(reader.is_alive());self.assertFalse(errors)
                    self.assertTrue(second.is_set())
                    self.assertTrue(denied.is_set())
                    self.assertIn('status_export_error',controller.status())
                    self.assertFalse((data/'.python-desktop-status').exists())
                    denied.clear()
                    wait_for(lambda:'status_export_error' not in controller.status())
                    current=controller.status()
                    self.assertNotEqual(current['ack'],one)
                    self.assertEqual(current['agents'][supervisor.BASE_ID]['pids']['channel'],pid)
                    self.assertIsNone(controller.error)
                    self.assertEqual(read(data/'.python-desktop-status')['nonce'],controller.nonce)
                finally:
                    # Persistent failure must not prevent Stop or real child reap.
                    denied.set();controller.stop.set();controller.thread.join(10)
                self.assertFalse(controller.thread.is_alive());self.assertIsNone(controller.error)

    def test_native_requests_wait_for_successful_status_export(self):
        import threading
        from queue import Queue
        with tempfile.TemporaryDirectory(prefix='native-export-') as tmp:
            root=Path(tmp);data=root/'data';data.mkdir()
            config=root/'helene.json';write(config,{'tree':str(data)})
            commands=Queue();commands.put('first');commands.put('second')
            stop=threading.Event();attempts=[];published=[]
            original=supervisor._desktop_status
            def export(*args):
                attempts.append(args[3])
                if len(attempts)<=2:
                    self.assertEqual(commands.qsize(),1)
                    self.assertFalse((data/'.python-desktop-status').exists())
                    raise PermissionError('synthetic persistent export denial')
                original(*args)
                published.append(read(data/'.python-desktop-status')['ack'])
                if args[3]=='second':stop.set()
                if len(attempts)>8:stop.set();self.fail('native request did not advance')
            spec=supervisor.Spec('channel',(sys.executable,'-B','-c','import time;time.sleep(60)'),
                root,dict(os.environ),root/'child.log',0)
            inventory={supervisor.BASE_ID:(data,{supervisor.BASE_ID+':channel':spec})}
            with patch.object(owner_stop,'stopped',return_value=False), \
                 patch.object(supervisor,'_plans',return_value=inventory), \
                 patch.object(supervisor,'_desktop_status',side_effect=export):
                self.assertEqual(supervisor.run(config,stop=stop,desktop_roster=True,
                    status_nonce='a'*32,commands=commands),0)
            self.assertEqual(attempts,['first','first','first','second'])
            self.assertEqual(published,['first','second'])
            self.assertTrue(commands.empty())

    def test_normal_start_keeps_ambient_snapshot_and_removes_stale_bootstrap_binding(self):
        observed=self.exercise(False)
        self.assertTrue(all('HELENE_MIGRATION_SESSION_JOB' not in row and 'HELENE_MIGRATION_SESSION_TREE' not in row for row in observed))

    def test_migration_start_passes_only_the_actual_admitted_bootstrap_binding(self):
        observed=self.exercise(True)
        self.assertTrue(all(row['HELENE_MIGRATION_SESSION_JOB']=='admitted-job' and row['HELENE_MIGRATION_SESSION_TREE'].endswith('bootstrap') for row in observed))


if __name__=='__main__':unittest.main()
