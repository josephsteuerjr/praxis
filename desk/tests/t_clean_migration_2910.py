"""Clean migration: dead old owner, retirement, then attached data, no model."""
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,os,subprocess,sys,tempfile,threading,time,unittest
from unittest.mock import Mock,patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from localharness import windows_installation as install,migration_lifecycle as lifecycle
from localharness import migration_windows as windows,migration_job,desktop_update_request as jobs,session_host
from localharness import source_update_host,desktop_supervisor,owner_stop,update_process
from localharness.package_notice import read,write
from localharness.update_progress import Progress,status
import t_migration_clean_windows_2810 as fixtures


class CleanAttached(unittest.TestCase):
    def test_old_code_is_retired_without_copying_optional_engines_or_touching_model_data(self):
        from localharness import component_downloads
        code=self.old/'code/old/agent.py';code.parent.mkdir(parents=True);code.write_text('owner mutation')
        selected=Path(read(self.old/'helene.json')['code'])
        if not selected.is_absolute():selected=self.old/selected
        runtime=self.old/'voice-xtts/python.exe';runtime.parent.mkdir();runtime.write_bytes(b'old engine')
        model=self.old/'components/xtts/model/weights.pth';model.parent.mkdir(parents=True);model.write_bytes(b'model data')
        nested=self.old/'code/old/memory/history.jsonl';nested.parent.mkdir();nested.write_bytes(b'nested history')
        with patch.object(component_downloads,'preserve_installed',side_effect=AssertionError('no old runtime copy')):
            note=self.run_clean()
        self.assertFalse(code.exists());self.assertFalse(runtime.exists())
        if selected.is_relative_to(self.old):self.assertFalse((selected/'agent.py').exists())
        self.assertFalse((self.package/'voice-xtts').exists());self.assertFalse((self.package/'code-backup').exists())
        self.assertEqual(model.read_bytes(),b'model data');self.assertEqual(nested.read_bytes(),b'nested history')
        self.assertEqual(note['optional_runtime_policy'],'install-current-packs;existing-model-data-retained')
        stages=[row['stage'] for row in self.progress.value['stages']]
        self.assertLess(stages.index('checking_programme'),stages.index('stopping_old'))
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='helene-clean-contract-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.old,self.package=fixtures.CleanMigration().fixture(self.base)
        (self.old/'helene.exe').write_bytes(b'old native supervisor')
        (self.old/'helene-body.exe').write_bytes(b'old native companion')
        write(self.package/'source-inputs.json',{'files':{},'version':'2.3.0-rc.6'})
        self.data=self.old/'data';self.original=(self.old/'helene.json').read_bytes()
        self.history=(self.data/'memory/history.jsonl').read_bytes()
        self.progress=Progress(self.base/'.helene-update-test.progress.json','test-clean',self.package)

    def run_clean(self,**kwargs):
        # Only OS service/shortcut operations and full-package custody are
        # substituted. Config/data/code/retirement/progress stay real.
        with patch.object(lifecycle,'stop',return_value={'service':None}), \
             patch.object(lifecycle,'quiescent',return_value=True), \
             patch.object(lifecycle,'cleanup_installation',return_value={}), \
             patch.object(source_update_host,'verified_runtime',return_value={'kind':'fixture-custody'}):
            return install.run(self.package,self.package,old=self.old,data=self.data,
                source_mode='clean',launch=False,progress=self.progress,**kwargs)

    def test_real_clean_migrator_accepts_real_progress_and_attaches_same_data(self):
        from localharness import code_transition,source_update_host
        with patch.object(code_transition,'prepare',side_effect=AssertionError('no merge')), \
             patch.object(source_update_host,'review',side_effect=AssertionError('no model review')):
            note=self.run_clean()
        self.assertEqual(note['state'],'prepared');self.assertTrue(note['old_retired_before_attach'])
        self.assertFalse((self.old/'helene.exe').exists())
        self.assertFalse((self.old/'helene-body.exe').exists())
        cfg=read(self.package/'helene.json')
        self.assertEqual(Path(cfg['tree']),self.data);self.assertEqual(Path(cfg['code']),self.package/'tree')
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)
        self.assertEqual((self.old/'helene.json').read_bytes(),self.original)
        self.assertEqual(cfg['model']['key'],'fixture-private-key');self.assertTrue(cfg['autonomy']['paused'])
        self.assertEqual(status(self.package)['stage'],'prepared')

    def test_supervisor_death_precedes_every_data_lease_and_retirement_precedes_config(self):
        dead=False;retired=False;events=[]
        def stop(old):
            nonlocal dead;dead=True;events.append('dead');return {'service':None}
        def lease(path):self.assertTrue(dead);events.append('lease');return True
        remove=lifecycle.remove_program
        def retire(*args,**kwargs):
            nonlocal retired
            self.assertTrue(dead);self.assertFalse((self.package/'helene.json').exists())
            value=remove(*args,**kwargs);retired=True;events.append('removed');return value
        with patch.object(lifecycle,'stop',side_effect=stop),patch.object(lifecycle,'quiescent'), \
             patch.object(lifecycle,'cleanup_installation',return_value={}),patch.object(lifecycle,'remove_program',side_effect=retire), \
             patch.object(windows,'owner_lease_available',side_effect=lease), \
             patch.object(source_update_host,'verified_runtime',return_value={}):
            note=install.run(self.package,self.package,old=self.old,data=self.data,source_mode='clean',launch=False,progress=self.progress)
        self.assertTrue(retired);self.assertEqual(events[0],'dead');self.assertEqual(note['copied_data'],[])
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)

    def test_failed_old_stop_keeps_program_data_and_no_new_config(self):
        with patch.object(lifecycle,'stop',side_effect=RuntimeError('old supervisor still alive')), \
             patch.object(source_update_host,'verified_runtime',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'still alive'):install.run(self.package,self.package,old=self.old,data=self.data,launch=False,progress=self.progress)
        self.assertTrue((self.old/'helene.exe').is_file());self.assertFalse((self.package/'helene.json').exists())
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)

    def test_incoming_activation_runs_before_the_target_lease_is_taken(self):
        import contextlib,python_migrate
        held=[]
        @contextlib.contextmanager
        def lock(path,**kwargs):
            self.assertNotIn(Path(path),held);held.append(Path(path))
            try:yield
            finally:held.remove(Path(path))
        def verify(path):self.assertEqual(held,[]);return {}
        with patch.object(python_migrate,'installation_lock',side_effect=lock), \
             patch.object(source_update_host,'verified_runtime',side_effect=verify), \
             patch.object(lifecycle,'stop',return_value={}),patch.object(lifecycle,'quiescent'), \
             patch.object(lifecycle,'cleanup_installation',return_value={}):
            install.run(self.package,self.package,old=self.old,data=self.data,source_mode='clean',launch=False,progress=self.progress)
        self.assertEqual(held,[])

    def test_held_data_lease_refuses_removal_and_new_owner(self):
        with patch.object(lifecycle,'stop',return_value={}),patch.object(lifecycle,'quiescent'), \
             patch.object(lifecycle,'cleanup_installation',return_value={}), \
             patch.object(windows,'owner_lease_available',return_value=False), \
             patch.object(source_update_host,'verified_runtime',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'lease'):install.run(self.package,self.package,old=self.old,data=self.data,launch=False,progress=self.progress)
        self.assertTrue((self.old/'helene.exe').is_file());self.assertFalse((self.package/'helene.json').exists())

    def test_preserving_code_is_rejected_before_any_stop_or_data_write(self):
        with patch.object(lifecycle,'stop') as stop:
            with self.assertRaisesRegex(ValueError,'preserving'):install.run(self.package,self.package,old=self.old,data=self.data,source_mode='preserve')
        stop.assert_not_called();self.assertFalse(jobs.path(self.data).exists())

    def test_admission_coordination_stays_outside_existing_data(self):
        child=Mock(pid=89431,handle=object());captured=[]
        def spawn(command,**kwargs):
            control=Path(command[command.index('--control-tree')+1]);captured.append(control)
            self.assertFalse(control.is_relative_to(self.data));self.assertFalse(jobs.path(self.data).exists())
            value=jobs.status(control);value.update(state='running',admission={'pid':child.pid,'birth':12,'generation':'fixture'})
            jobs._save(control,value);return child
        with patch.object(owner_stop,'stopped',return_value=False), \
             patch.object(update_process,'interpreter_for',return_value=Path(sys.executable)), \
             patch.object(session_host,'process_state',return_value='alive'), \
             patch.object(desktop_supervisor.JobChild,'spawn',side_effect=spawn):
            value=migration_job.admit(self.package,self.package,old=self.old,data=self.data)['job']
        self.assertEqual(value['source_mode'],'clean');self.assertTrue(value['migration']['remove_old'])
        self.assertEqual(value['program'],str(self.package));self.assertEqual(value['tree'],str(captured[0]))
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)
        self.assertFalse(jobs.path(self.data).exists())

    def test_failure_is_durable_with_traceback_and_terminal_progress(self):
        control=self.base/'bootstrap';identity='a'*32
        value={'id':identity,'kind':'migration','program':str(self.package),'tree':str(control),'state':'running',
               'migration':{'package':str(self.package),'target':str(self.package),'old':str(self.old),
                            'data':str(self.data),'fresh':False,'remove_old':True,'source_mode':'clean'}}
        jobs._save(control,value);host=SimpleNamespace(stop=threading.Event())
        owner=SimpleNamespace(stop=threading.Event(),event=Mock(),close=Mock())
        with patch.object(install,'run',side_effect=ValueError('actual fixture preparation failure')):
            migration_job._execute(host,owner,value)
        failed=jobs.status(control);self.assertEqual(failed['state'],'attention')
        self.assertIn('ValueError: actual fixture',Path(failed['diagnostic']).read_text('utf8'))
        self.assertEqual(status(self.package)['stage'],'failed');self.assertTrue(host.stop.is_set())
        self.assertFalse(jobs.path(self.data).exists());owner.close.assert_called_once()

    def test_progress_abi_accepts_string_and_dictionary_without_losing_counters(self):
        self.progress('checking_extensions');self.progress({'stage':'connecting_data','bytes':128})
        self.assertEqual(self.progress.value['bytes'],128);self.progress.save()
        value=status(self.package);self.assertEqual(value['stage'],'connecting_data');self.assertEqual(value['bytes'],128)
        before=self.progress.path.read_bytes()
        for invalid in (None,123,[], ''):
            with self.assertRaises((TypeError,ValueError)):self.progress(invalid)
        self.assertEqual(self.progress.path.read_bytes(),before)

    def test_confirmed_completion_is_published_into_a_profile_without_previous_jobs(self):
        self.assertEqual(jobs.status(self.data),{'state':'none'})
        identity='b'*32
        value={'id':identity,'kind':'migration','tree':str(self.base/'control'),'state':'completed',
               'migration':{'data':str(self.data),'old':str(self.old)},'result':{'migration_id':'proven'}}
        write(self.package/'python-migration.json',{'id':'proven','startup_proof':{'root':str(self.package)}})
        migration_job.publish_completed_migration(self.package,value)
        self.assertEqual(jobs.status(self.data)['id'],identity)
        self.assertEqual(jobs.status(self.data)['state'],'completed')
        self.assertEqual(status(self.package)['stage'],'completed')
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)

    def test_confirmed_completion_replaces_only_the_settled_failed_clean_pointer(self):
        control=self.base/'control';identity='b'*32;prior_id='a'*32
        prior={'id':prior_id,'kind':'migration','program':str(self.old),'tree':str(self.data),
               'state':'attention','source_mode':'clean','legacy_recovery':{'released':True},'admission':{'pid':123}}
        jobs._save(self.data,prior)
        value={'id':identity,'kind':'migration','tree':str(control),'state':'completed',
               'migration':{'data':str(self.data),'old':str(self.old)},'result':{'migration_id':'proven'}}
        write(self.package/'python-migration.json',{'id':'proven','startup_proof':{'root':str(self.package)}})
        with patch.object(session_host,'process_state',return_value='absent'):
            migration_job.publish_completed_migration(self.package,value)
        self.assertEqual(jobs.status(self.data)['id'],identity)
        self.assertEqual(jobs.status(self.data)['state'],'completed')
        history=read(jobs.path(self.data).parent/'desktop-update-jobs'/('a'*32+'.json'))
        self.assertEqual(history['state'],'superseded');self.assertEqual(status(self.package)['stage'],'completed')
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)

    def test_completion_never_overwrites_unrelated_work_or_invents_startup_proof(self):
        value={'id':'b'*32,'tree':str(self.base/'control'),'state':'completed',
               'migration':{'data':str(self.data),'old':str(self.old)},'result':{'migration_id':'proven'}}
        with self.assertRaises(ValueError):migration_job.publish_completed_migration(self.package,value)
        write(self.package/'python-migration.json',{'id':'proven','startup_proof':{'root':str(self.package)}})
        other={'id':'c'*32,'kind':'install','state':'running','program':'another owner','tree':str(self.data)}
        jobs._save(self.data,other);before=jobs.path(self.data).read_bytes()
        migration_job.publish_completed_migration(self.package,value)
        self.assertEqual(jobs.path(self.data).read_bytes(),before)

    def test_old_removal_cannot_be_authorized_by_an_arbitrary_early_note(self):
        for note in ({},{'source_policy':'preserve','old_quiescent':True}):
            with self.assertRaises(ValueError):windows.remove_program(self.old,self.package,note,before_start=True)
        self.assertTrue((self.old/'helene.exe').is_file())

    def test_windows_process_tree_includes_external_children_and_excludes_foreign_apps(self):
        root=str(self.old/'helene.exe');external=str(self.base/'shared/python.exe')
        rows=[{'ProcessId':101,'ParentProcessId':90,'ExecutablePath':root,'CommandLine':root,'Birth':'123'},
              {'ProcessId':102,'ParentProcessId':101,'ExecutablePath':external,'CommandLine':'python tool.py','Birth':'124'},
              {'ProcessId':103,'ParentProcessId':91,'ExecutablePath':external,'CommandLine':'python other.py','Birth':'125'},
              {'ProcessId':104,'ParentProcessId':101,'ExecutablePath':external,'CommandLine':'python orphan.py','Birth':'100'}]
        stopped=[]
        def terminate(row):
            stopped.append(int(row['ProcessId']));self.assertTrue(row['Birth']);return True
        with patch.object(windows,'process_snapshot',return_value=rows),patch.object(windows,'stop_generation',side_effect=terminate):
            windows.stop_owned_processes(self.old)
        self.assertEqual(set(stopped),{101,102})
        self.assertEqual(stopped[0],101)  # A guardian must die before its child.

    def test_reused_pid_never_authorizes_terminating_its_new_generation(self):
        import psutil
        process=Mock();process.create_time.return_value=999
        row={'ProcessId':12345,'Birth':111,'ExecutablePath':'old.exe','CommandLine':'old.exe'}
        with patch.object(psutil,'Process',return_value=process):self.assertFalse(windows.stop_generation(row))
        process.terminate.assert_not_called();process.wait.assert_not_called()

    def test_clean_form_has_no_code_preservation_choice(self):
        text=(Path(__file__).resolve().parents[1]/'resources/python-migration.html').read_text('utf8')
        self.assertNotIn('id="source-mode"',text);self.assertNotIn("$('source-mode')",text)
        self.assertNotIn('value="preserve"',text);self.assertIn("sourceMode:pythonUpdate?'preserve':'clean'",text)

    @unittest.skipUnless(os.name=='nt','Windows early bootstrap diagnostic')
    def test_preimport_error_is_logged_even_before_the_main_handler_exists(self):
        import contextlib,ctypes,io,runpy
        from localharness import source_admission,python_source_updater
        entry=Path(__file__).resolve().parents[1]/'python_app.py'
        with patch.object(sys,'argv',[str(entry),'--root',str(self.package)]), \
             patch.object(python_source_updater,'recover_startup',return_value=False), \
             patch.object(source_admission,'activate',side_effect=ValueError('fixture early admission failure')), \
             patch.object(ctypes.windll.user32,'MessageBoxW'),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):runpy.run_path(str(entry),run_name='__main__')
        self.assertIn('fixture early admission failure',(self.package/'.helene/logs/startup-error.log').read_text('utf8'))

    @unittest.skipUnless(os.name=='nt','Windows process tree and held data lease')
    def test_real_old_process_tree_dies_and_releases_lock_without_touching_foreign_python(self):
        import psutil
        entry=self.old/'app/python_app.py';entry.parent.mkdir(exist_ok=True)
        ready=self.old/'ready.json'
        entry.write_text("import sys,subprocess,time,json,msvcrt\nfrom pathlib import Path\nr=Path(sys.argv[1]);lock=(r/'data/.desktop-owner.lock').open('a+b');lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)\np=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(120)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)\n(r/'ready.json').write_text(json.dumps({'child':p.pid}))\ntime.sleep(120)\n",encoding='utf8')
        old=subprocess.Popen([sys.executable,'-B',str(entry),str(self.old)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
        foreign=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(120)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
        child=None
        try:
            deadline=time.monotonic()+10
            while not ready.exists() and time.monotonic()<deadline:time.sleep(.025)
            self.assertTrue(ready.is_file());child=psutil.Process(read(ready)['child'])
            self.assertFalse(windows.owner_lease_available(self.data/'.desktop-owner.lock'))
            windows.stop_owned_processes(self.old)
            old.wait(timeout=10)
            self.assertFalse(child.is_running());self.assertIsNone(foreign.poll())
            self.assertTrue(windows.owner_lease_available(self.data/'.desktop-owner.lock'))
            self.assertTrue((self.data/'.desktop-owner.lock').is_file())
            self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)
        finally:
            for process in (old,foreign):
                if process.poll() is None:process.terminate()
                process.wait(timeout=10)
            if child is not None:
                try:
                    if child.is_running():child.kill();child.wait(timeout=10)
                except psutil.NoSuchProcess:pass


if __name__=='__main__':unittest.main()
