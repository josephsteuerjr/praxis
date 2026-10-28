"""Migration consumes durable session admission, not a long-lived wizard loop."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_job,desktop_update_request as jobs,session_host
from localharness.package_notice import read,write
from localharness import source_update_host


class MigrationJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='helene-migration-job-test-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'program';self.root.mkdir();self.data=self.base/'data'

    def test_fresh_seed_is_only_own_bootstrap_controls_not_a_hidden_profile(self):
        job={'id':'a'*32,'kind':'migration','tree':str(self.data),'state':'admitting','migration':{'fresh':True}}
        jobs._save(self.data,job)
        self.assertTrue(migration_job.owns_empty_seed(self.data,job['id']))
        self.assertFalse(migration_job.owns_empty_seed(self.data,'b'*32))
        note=self.data/'memory/private-note.md';note.write_text('real previous memory')
        self.assertFalse(migration_job.owns_empty_seed(self.data,job['id']))

    def test_existing_fresh_data_refuses_before_any_bootstrap_spawn(self):
        self.data.mkdir();(self.data/'keep.txt').write_text('existing data')
        from localharness import desktop_supervisor,owner_stop
        with patch.object(owner_stop,'stopped',return_value=False),patch.object(desktop_supervisor.JobChild,'spawn') as spawn:
            with self.assertRaisesRegex(ValueError,'empty selected data'):
                migration_job.admit(self.root,self.root,data=self.data,fresh=True)
        spawn.assert_not_called();self.assertEqual((self.data/'keep.txt').read_text(),'existing data')
        self.assertFalse(jobs.path(self.data).exists())

    def test_window_calls_only_durable_admit_not_the_migration_worker(self):
        from python_migration_window import MigrationWindow
        from localharness import windows_installation
        app=object.__new__(MigrationWindow)
        app.root=self.root;app.window=None;app.running=threading.Lock();app.review_cancelled=threading.Event()
        app.force_extensions=False;app.progress=Mock();app.stopping=threading.Event();app.exiting=False
        with patch.object(migration_job,'admit',return_value={'state':'admitted','job':{'id':'m'}}) as admit, \
             patch.object(windows_installation,'run') as work,patch('python_migration_window.threading.Timer'):
            result=app.invoke('migration_install',{'mode':'fresh','target':str(self.root),'data':str(self.data),'sourceMode':'clean'})
        self.assertEqual(result['state'],'admitted');work.assert_not_called();admit.assert_called_once()
        self.assertEqual(admit.call_args.kwargs['fresh'],True)

    def test_deferred_real_host_admits_before_work_and_stop_needs_no_window(self):
        job={'format':'helene-desktop-update-request.v2','id':'b'*32,'kind':'migration',
             'program':str(self.root),'tree':str(self.data),'state':'admitting','phase':'requested',
             'source_mode':'clean','migration':{'fresh':True}}
        jobs._save(self.data,job)
        script=r'''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import session_host,migration_job,desktop_update_request as jobs
from localharness.python_controller import Controller
from localharness.package_notice import write
session_host.owner_stop.stopped=lambda:False
class FixtureController(Controller):
 def start(self):raise RuntimeError('Deferred bootstrap must not start an agent')
 def close(self):pass
 def status(self):return {'agents':{},'deferred':True}
def execute(host,owner,value):
 write(Path(value['tree'])/'work-started.json',{'job':value['id']})
 while not host.stop.wait(.05):
  if jobs.cancelled(value['tree'],value['id']):
   jobs.checkpoint(owner,value,state='cancelled',phase='cancelled');return
migration_job.execute=execute
session_host.Host(Path(sys.argv[2]),controller_factory=FixtureController,control_tree=Path(sys.argv[3]),migration_request=jobs.path(sys.argv[3])).run()
'''
        proc=subprocess.Popen([sys.executable,'-B','-c',script,str(Path(__file__).resolve().parents[1]),str(self.root),str(self.data)],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def cleanup():
            if proc.poll() is None:proc.terminate();proc.wait(timeout=8)
            proc.stderr.close()
        self.addCleanup(cleanup)
        descriptor_path=self.data/'memory/.control/desktop-session.json'
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            current=jobs.status(self.data);descriptor=read(descriptor_path)
            if current.get('admission',{}).get('pid')==proc.pid and (self.data/'work-started.json').exists():break
            if proc.poll() is not None:self.fail(proc.stderr.read().decode('utf8','replace'))
            time.sleep(.03)
        else:self.fail('no durable bootstrap admission')
        self.assertEqual(current['state'],'running')
        session_host._rpc(descriptor,'cancel_job')
        deadline=time.monotonic()+5
        while jobs.status(self.data)['state']!='cancelled' and time.monotonic()<deadline:time.sleep(.03)
        self.assertEqual(jobs.status(self.data)['state'],'cancelled')
        self.assertIsNone(proc.poll())
        session_host._rpc(descriptor,'exit');proc.wait(timeout=8);self.assertEqual(proc.returncode,0)

    def test_named_coordinator_is_rejoined_never_respawned_while_alive(self):
        import psutil
        directory=self.base/'plan';directory.mkdir()
        descriptor={'pid':os.getpid(),'birth':psutil.Process().create_time(),'invocation':'same',
                    'cancel':str(directory/'cancel.json'),'log':str(directory/'output.log')}
        write(directory/'coordinator-process.json',descriptor)
        with patch.object(source_update_host.subprocess,'Popen') as spawn:
            process,cancel,log=source_update_host._producer(['not-executed'],self.root,directory,directory/'different-cancel',directory/'different-log')
        spawn.assert_not_called();self.assertIsNone(process.poll())
        self.assertEqual(cancel,directory/'cancel.json');self.assertEqual(log,directory/'output.log')

    def test_unknown_coordinator_admission_never_buys_a_replacement(self):
        directory=self.base/'plan';directory.mkdir()
        write(directory/'coordinator-process.json',{'invocation':'admission-unknown','state':'starting'})
        with patch.object(source_update_host.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError,'unknown'):
                source_update_host._producer(['not-executed'],self.root,directory,directory/'cancel',directory/'log')
        spawn.assert_not_called()

    def test_transition_supervisor_uses_incoming_harness_without_rewriting_old_configuration(self):
        from localharness import desktop_supervisor,update_process
        incoming=self.base/'incoming';(incoming/'app/localharness').mkdir(parents=True)
        (incoming/'runtime').mkdir()
        executable=incoming/('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
        executable.parent.mkdir(parents=True,exist_ok=True);executable.write_bytes(b'fixture-runtime')
        (incoming/'app/deskapp.py').write_text('# current channel')
        (incoming/'app/localharness/runner.py').write_text('# current runner')
        self.data.mkdir();code=self.base/'authored-code';code.mkdir()
        cfg=self.root/'helene.json'
        original_python=self.root/'old-python';original_python.write_bytes(b'original-runtime')
        original_runner=self.root/'old-runner.py';original_runner.write_text('# compatible original agent harness')
        write(cfg,{'tree':str(self.data),'code':str(code),'python':str(original_python),
            'app':str(self.root/'missing-old-channel'),'runner':str(original_runner),
            'installed':{'service':True},'setup_complete':True,'relay':{'enabled':False}})
        original=cfg.read_bytes()
        with self.assertRaises(ValueError):desktop_supervisor._specs(cfg,session_host=False)
        tree,specs=desktop_supervisor._specs(cfg,session_host=False,runtime_program=incoming)
        self.assertEqual(tree,self.data)
        self.assertEqual(specs['main:runner'].argv[0],str(original_python))
        self.assertIn(str(original_runner),specs['main:runner'].argv)
        self.assertEqual(specs['main:channel'].argv[0],str(update_process.interpreter_for(incoming)))
        self.assertEqual(specs['main:runner'].env['HELENE_CONFIG'],str(cfg))
        self.assertEqual(cfg.read_bytes(),original)

    def test_legacy_finish_only_hand_is_not_the_new_protocol_and_transition_env_is_not_permanent(self):
        from localharness.agent_source_update import require_patch_tool,AgentResolutionUnavailable
        from localharness import update_process,source_update
        path=self.root/'app/localharness/source_update.py';path.parent.mkdir(parents=True)
        tool={'input_schema':{'properties':{'action':{'enum':['diff','patch','finish']}}}}
        path.write_text('TOOL='+repr(tool))
        with self.assertRaises(AgentResolutionUnavailable):require_patch_tool(self.root)
        path.write_text('TOOL='+repr(source_update.TOOL));require_patch_tool(self.root)
        with patch.dict(os.environ,{'HELENE_SOURCE_UPDATE_RUNTIME':'temporary-update-runtime'}):
            self.assertNotIn('HELENE_SOURCE_UPDATE_RUNTIME',update_process.permanent_environment())

    def test_legacy_browser_update_delegates_to_durable_job_before_any_perform(self):
        from localharness import python_source_updater as updater
        stage=self.base/'.helene-update-legacy';new=stage/'Helene';new.mkdir(parents=True)
        argv=['python_source_updater.py','--old',str(self.root),'--new',str(new),'--staging',str(stage),
              '--pid','123','--browser']
        with patch.object(sys,'argv',argv),patch.object(sys,'path',list(sys.path)), \
             patch.object(migration_job,'legacy_update',return_value={'state':'admitted'}) as legacy, \
             patch.object(updater,'perform',side_effect=AssertionError('legacy UI must not own semantic work')):
            self.assertEqual(updater.main(),0)
        legacy.assert_called_once_with(self.root,new,source_mode='preserve',admission=stage/'updater-admitted.json')

    def test_staged_setup_update_does_not_open_a_long_lived_migration_window(self):
        import helene_setup
        stage=self.base/'.helene-update-legacy';new=stage/'Helene';new.mkdir(parents=True)
        with patch.object(migration_job,'legacy_update',return_value={'state':'admitted'}) as legacy, \
             patch('python_migration_window.main',side_effect=AssertionError('normal Update is not a migration wizard')):
            self.assertEqual(helene_setup.main(['--root',str(new),'--old',str(self.root),'--target',str(self.root)]),0)
        legacy.assert_called_once_with(self.root,new,source_mode='preserve',admission=None,force_extensions=False)

    def test_legacy_descriptor_is_honest_job_admission_not_a_shown_window(self):
        import psutil
        stage=self.base/'.helene-update-legacy';new=stage/'Helene';new.mkdir(parents=True)
        value={'id':'c'*32,'tree':str(self.data),'admission':{'pid':os.getpid(),'birth':psutil.Process().create_time(),'generation':'fixture'}}
        with patch.object(migration_job,'admit',return_value={'state':'admitted','job':value}):
            migration_job.legacy_update(self.root,new,admission=stage/'updater-admitted.json')
        receipt=read(new/f'.python-migrator-{os.getpid()}.json')
        self.assertEqual(receipt['admission_kind'],'durable-agent-job');self.assertFalse(receipt['wizard_opened'])
        self.assertEqual(read(stage/'updater-admitted.json')['durable_owner'],value['admission'])

    def test_temporary_hand_reload_uses_the_owning_controller_not_an_old_window(self):
        from localharness import legacy_update_tool
        owner=SimpleNamespace(root=self.root,close=Mock(),start=Mock())
        with legacy_update_tool.controller_scope(owner), \
             patch.object(legacy_update_tool,'restart_engine',side_effect=AssertionError('no old UI owner')):
            legacy_update_tool._restart_selected(self.root)
        owner.close.assert_called_once();owner.start.assert_called_once()


if __name__=='__main__':unittest.main()
