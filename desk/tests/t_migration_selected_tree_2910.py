"""Selected control data and old running profile remain independently bound."""
from pathlib import Path
import json,os,subprocess,sys,tempfile,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_job,session_host,desktop_update_request as jobs,desktop_supervisor,owner_stop
from localharness.python_controller import Controller
from localharness.package_notice import read,write


class SelectedTree(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='helene-selected-tree-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'old program';self.root.mkdir()
        self.old_tree=self.base/'old configured profile';self.old_tree.mkdir()
        self.selected=self.base/'selected AppData';self.selected.mkdir()
        self.package=self.base/'incoming';self.package.mkdir()
        write(self.root/'helene.json',{'tree':str(self.old_tree),'code':'authored','port':18742,'runner':'','setup_complete':True})
        write(self.root/'python-app.json',{'version':'2.1.1'})
        self.original=(self.root/'helene.json').read_bytes()
        self.identity='b'*32
        self.value={'id':self.identity,'kind':'migration','program':str(self.root),'tree':str(self.selected),
            'state':'admitting','migration':{'old':str(self.root),'package':str(self.package),'data':str(self.selected)}}

    def descriptor(self):
        import psutil
        descriptor={'format':session_host.FORMAT,'program':str(self.root),'tree':str(self.selected),
            'pid':os.getpid(),'birth':psutil.Process().create_time(),'generation':'fixture','state':'running'}
        self.value['admission']={key:descriptor[key] for key in ('pid','birth','generation')}
        jobs._save(self.selected,self.value);write(self.selected/'memory/.control/desktop-session.json',descriptor)
        return descriptor

    def test_explicit_selected_tree_never_falls_back_to_old_configured_tree(self):
        descriptor=self.descriptor()
        self.assertEqual(migration_job.observer_descriptor(self.root,self.package,self.identity,tree=self.selected),descriptor)
        with self.assertRaises(ValueError):migration_job.observer_descriptor(self.root,self.package,self.identity)
        for field in ('tree','program','birth','generation'):
            changed=dict(descriptor);changed[field]='foreign'
            write(self.selected/'memory/.control/desktop-session.json',changed)
            with self.assertRaises(ValueError):migration_job.observer_descriptor(self.root,self.package,self.identity,tree=self.selected)
        self.assertEqual((self.root/'helene.json').read_bytes(),self.original)

    def test_migration_attach_cannot_launch_a_replacement_owner(self):
        self.descriptor()
        with patch.object(session_host,'SessionClient',return_value='fixture-client') as client,patch.object(desktop_supervisor.JobChild,'spawn') as spawn:
            self.assertEqual(session_host.attach_migration(self.root,self.package,self.identity,Mock(),tree=self.selected),'fixture-client')
            self.assertEqual(client.call_args.kwargs['session_job'],self.identity)
            with self.assertRaises(ValueError):session_host.attach_migration(self.root,self.package,'c'*32,Mock(),tree=self.selected)
        spawn.assert_not_called()

    def test_scoped_observer_rejects_replacement_job_before_any_action(self):
        self.descriptor();host=object.__new__(session_host.Host)
        host.stop=threading.Event()
        host.control_tree=self.selected;host.migration_request=jobs.path(self.selected)
        self.assertEqual(host.dispatch('job_status',{'session_job':self.identity})['id'],self.identity)
        # Whole-programme Exit deliberately survives stale observer bindings;
        # its separate lifecycle suite proves terminal process custody.
        for action in ('job_status','cancel_job','invoke','attach','retirement_start'):
            with self.assertRaisesRegex(ValueError,'identity changed'):host.dispatch(action,{'session_job':'c'*32})

    def test_bootstrap_does_not_consume_an_unrelated_old_profile_update(self):
        value={'id':'a'*32,'kind':'install','state':'confirmed','program':str(self.root),'tree':str(self.old_tree),'from_version':'2.1.1'}
        jobs._save(self.old_tree,value);before=jobs.path(self.old_tree).read_bytes()
        owner=SimpleNamespace(root=self.root,bootstrap=True,stop=threading.Event())
        with patch.object(owner_stop,'stopped',return_value=False),patch.object(jobs,'_run') as run:jobs.consume(owner)
        run.assert_not_called();self.assertEqual(jobs.path(self.old_tree).read_bytes(),before)

    def test_transition_binding_is_channel_only_and_does_not_rebind_old_agent(self):
        from localharness import update_process
        (self.package/'app').mkdir();(self.package/'app/deskapp.py').write_text('# new channel')
        runtime=self.package/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
        runtime.parent.mkdir(parents=True);runtime.write_bytes(b'fixture interpreter')
        environment={'HELENE_MIGRATION_SESSION_TREE':str(self.selected),'HELENE_MIGRATION_SESSION_JOB':self.identity}
        tree,specs=desktop_supervisor._specs(self.root/'helene.json',runtime_program=self.package,environ=environment)
        self.assertEqual(tree,self.old_tree)
        self.assertEqual(specs['main:channel'].env['HELENE_CONFIG'],str(self.root/'helene.json'))
        self.assertEqual(specs['main:channel'].env['HELENE_TREE'],str(self.old_tree))
        self.assertEqual(specs['main:channel'].env['HELENE_MIGRATION_SESSION_TREE'],str(self.selected))
        with patch.object(update_process,'interpreter_for',return_value=runtime):
            with self.assertRaisesRegex(ValueError,'Incomplete'):desktop_supervisor._specs(self.root/'helene.json',runtime_program=self.package,environ={'HELENE_MIGRATION_SESSION_JOB':self.identity})
        self.assertEqual((self.root/'helene.json').read_bytes(),self.original)

    def test_controller_supplies_selected_control_without_changing_its_profile(self):
        owner=Controller(self.root,Mock());owner.bootstrap=True;owner.update_job=dict(self.value)
        owner.runtime_program=self.package;captured=[]
        def supervise(config,**kwargs):captured.append((config,kwargs))
        with patch.object(owner_stop,'stopped',return_value=False),patch.object(desktop_supervisor,'run',side_effect=supervise):
            owner.start();owner.thread.join(timeout=5);owner.close()
        self.assertEqual(len(captured),1)
        self.assertEqual(captured[0][0],self.root/'helene.json')
        environment=captured[0][1]['environ']
        self.assertEqual(environment['HELENE_MIGRATION_SESSION_TREE'],str(self.selected))
        self.assertEqual(environment['HELENE_MIGRATION_SESSION_JOB'],self.identity)
        self.assertEqual(owner.current().tree,self.old_tree)
        self.assertEqual((self.root/'helene.json').read_bytes(),self.original)

    def test_normal_generation_drops_stale_migration_origin_environment(self):
        (self.root/'deskapp.py').write_text('# synthetic local channel')
        _,specs=desktop_supervisor._specs(self.root/'helene.json',environ={
            'HELENE_MIGRATION_SESSION_TREE':str(self.selected),'HELENE_MIGRATION_SESSION_JOB':self.identity})
        for spec in specs.values():
            self.assertNotIn('HELENE_MIGRATION_SESSION_TREE',spec.env)
            self.assertNotIn('HELENE_MIGRATION_SESSION_JOB',spec.env)

    def test_selected_tree_flag_never_bypasses_job_admission(self):
        import python_app
        with patch.object(python_app,'Application') as application,patch.object(desktop_supervisor.JobChild,'spawn') as spawn:
            with self.assertRaisesRegex(ValueError,'requires an admitted'):
                python_app._main(['--root',str(self.root),'--session-tree',str(self.selected)])
        application.assert_not_called();spawn.assert_not_called()

    def test_real_host_selected_tree_rejoin_cors_and_shutdown_leave_other_profile_untouched(self):
        jobs._save(self.selected,self.value)
        jobs._save(self.old_tree,{'id':'a'*32,'kind':'install','state':'confirmed','program':str(self.root),'tree':str(self.old_tree)})
        old_request=jobs.path(self.old_tree).read_bytes()
        script=r'''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import session_host,migration_job,desktop_update_request as jobs
from localharness.python_controller import Controller
from localharness.package_notice import write
session_host.owner_stop.stopped=lambda:False
class Fixture(Controller):
 def start(self):raise AssertionError('No agent or model in the deferred bootstrap')
 def close(self):pass
 def status(self):return {'fixture':True,'agents':{}}
 def channel(self):return {'tree':str(self.current().tree)}
def execute(host,owner,value):
 write(Path(value['tree'])/'work-started.json',{'id':value['id']})
 while not host.stop.wait(.05):
  if jobs.cancelled(value['tree'],value['id']):
   jobs.checkpoint(owner,value,state='cancelled');return
migration_job.execute=execute
session_host.Host(Path(sys.argv[2]),controller_factory=Fixture,control_tree=Path(sys.argv[3]),migration_request=jobs.path(sys.argv[3])).run()
'''
        process=subprocess.Popen([sys.executable,'-B','-c',script,str(Path(__file__).resolve().parents[1]),str(self.root),str(self.selected)],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def cleanup():
            if process.poll() is None:process.terminate();process.wait(timeout=8)
            process.stderr.close()
        self.addCleanup(cleanup)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if (self.selected/'work-started.json').exists():break
            if process.poll() is not None:self.fail(process.stderr.read().decode('utf-8','replace'))
            time.sleep(.03)
        else:self.fail('Selected-tree bootstrap not admitted')
        origin='http://127.0.0.1:39403'
        client=session_host.attach_migration(self.root,self.package,self.identity,lambda *args:None,origin,tree=self.selected)
        self.addCleanup(client.close)
        self.assertEqual(client.channel()['tree'],str(self.old_tree),'old agent keeps its old data until cutover')
        self.assertEqual(client._request('job_status')['tree'],str(self.selected))
        environment={'HELENE_MIGRATION_SESSION_TREE':str(self.selected),'HELENE_MIGRATION_SESSION_JOB':self.identity}
        with patch.dict(os.environ,environment):
            self.assertTrue(session_host.configured_origin(self.root/'helene.json',origin))
            self.assertFalse(session_host.configured_origin(self.root/'helene.json','http://127.0.0.1:39404'))
            with patch.dict(os.environ,{'HELENE_MIGRATION_SESSION_JOB':'c'*32}):self.assertFalse(session_host.configured_origin(self.root/'helene.json',origin))
            client.close();self.assertFalse(session_host.configured_origin(self.root/'helene.json',origin))
        self.assertIsNone(process.poll())
        again=session_host.attach_migration(self.root,self.package,self.identity,lambda *args:None,origin,tree=self.selected)
        self.addCleanup(again.close);again.shutdown();process.wait(timeout=8)
        self.assertEqual(process.returncode,0)
        self.assertTrue(jobs.status(self.selected).get('cancel_requested'))
        self.assertEqual(jobs.path(self.old_tree).read_bytes(),old_request)
        self.assertEqual((self.root/'helene.json').read_bytes(),self.original)


if __name__=='__main__':unittest.main()
