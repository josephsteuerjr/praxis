"""Durable Restore admission/owner worker; private files and fake apply only.

The HTTP case uses the real authenticated Host and existing watcher with an
intentionally blocked synthetic restore. It proves admission, not installation.
"""
from contextlib import ExitStack,nullcontext
import json,os,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from localharness import desktop_update_request as jobs,session_host as session
from localharness import upstream_recovery as recovery,python_updates as updates,agents,program_state
from localharness.package_notice import read,write
import t_upstream_recovery_3110 as recovery_fixture


class RestoreJob(unittest.TestCase):
    def setUp(self):
        self.fx=recovery_fixture.ModernRecovery('runTest');self.addCleanup(self.fx.doCleanups);self.fx.setUp()
        self.owner=self.fx.owner;self.root=self.fx.root;self.tree=self.fx.data
        self.agent=SimpleNamespace(id='main',tree=self.tree,enabled=True,conflict=None)
        self.owner.current=Mock(return_value=self.agent);self.owner.selection=lambda _identity:nullcontext()
        self.owner.session_host=True
        self.enterContext(patch.object(recovery,'modern',return_value=True))
        self.enterContext(patch.object(session,'process_state',return_value='alive'))
        self.enterContext(patch.object(agents,'roster',return_value=[self.agent]))
        write(session.control_path(self.root),{'pid':os.getpid(),'birth':42.0,'generation':'private-host',
              'program':str(self.root),'state':'running'})

    def admit(self):return jobs.admit_restore(self.owner,confirmed=True)

    def test_admission_is_durable_before_stage_and_exposes_status_identity(self):
        with patch.object(recovery,'stage',side_effect=AssertionError('No staging in admission')) as stage:
            value=self.admit()
        stage.assert_not_called();record=read(jobs.path(self.tree))
        self.assertEqual(record['id'],value['operation_id'])
        self.assertEqual(value['status'],str(jobs.path(self.tree)))
        self.assertEqual((record['kind'],record['operation'],record['source_mode']),('install','restore_upstream','clean'))
        self.assertEqual(record['agent_id'],'main');self.assertEqual(record['tree'],str(self.tree.resolve()))
        self.assertEqual(record['admission']['generation'],'private-host')
        self.assertNotIn('staging',record);self.assertNotIn('archive',record)

    def test_repeated_restore_reuses_identity_and_ordinary_update_conflicts(self):
        first=self.admit();second=self.admit()
        self.assertEqual(first['job']['id'],second['job']['id']);self.assertTrue(second['reused'])
        record={**first['job'],'id':'e'*32,'operation':'ordinary-install','archive':'private.zip'}
        jobs._save(self.tree,record)
        with self.assertRaisesRegex(ValueError,'already owns'):self.admit()

    def test_current_restore_progress_ignores_previous_terminal_before_first_worker_receipt(self):
        from localharness.update_progress import Progress,pointer
        for terminal in ('completed','failed'):
            with self.subTest(previous=terminal):
                previous=Progress(program_state.progress(self.root,'f'*32),'f'*32,self.root)
                previous.phase(terminal,error='previous attempt')
                admitted=self.admit();before=pointer(self.root).read_bytes()
                row=updates.update_progress(self.owner)
                self.assertEqual(row['id'],admitted['operation_id'])
                self.assertEqual(row['stage'],'requested');self.assertEqual(row['job_state'],'confirmed')
                self.assertNotIn('source_update',row);self.assertEqual(row.get('error',''),'')
                self.assertEqual(pointer(self.root).read_bytes(),before,'observation does not repin progress')

    def test_current_restore_progress_uses_matching_receipt_even_when_overview_is_repointed(self):
        from localharness.update_progress import Progress
        admitted=self.admit();identity=admitted['operation_id']
        current=Progress(program_state.progress(self.root,identity),identity,self.root)
        current.phase('preparing',program_files_done=3,program_files_total=9)
        previous=Progress(program_state.progress(self.root,'f'*32),'f'*32,self.root)
        previous.phase('completed',files=999)
        with patch('localharness.source_update_activity.activity',side_effect=AssertionError('No foreign review attached')):
            row=updates.update_progress(self.owner)
        self.assertEqual(row['id'],identity);self.assertEqual(row['stage'],'preparing')
        self.assertEqual((row['program_files_done'],row['program_files_total']),(3,9))
        self.assertNotIn('source_update',row)

    def test_current_restore_cancel_and_attention_are_its_own_status(self):
        from localharness.update_progress import Progress
        admitted=self.admit();identity=admitted['operation_id']
        current=Progress(program_state.progress(self.root,identity),identity,self.root)
        current.phase('preparing')
        for state in ('cancelled','attention'):
            with self.subTest(state=state):
                jobs._save(self.tree,{**admitted['job'],'state':state,'error':'this restore needs observation'})
                row=updates.update_progress(self.owner)
                self.assertEqual(row['id'],identity);self.assertEqual(row['stage'],state)
                self.assertEqual(row['job_state'],state);self.assertEqual(row['error'],'this restore needs observation')

    def test_ordinary_progress_keeps_existing_receipt_and_activity_behavior(self):
        from localharness.update_progress import Progress
        previous=Progress(program_state.progress(self.root,'f'*32),'f'*32,self.root)
        previous.phase('completed',files=27)
        jobs._save(self.tree,{'id':'a'*32,'state':'confirmed','kind':'download','program':str(self.root),
                              'tree':str(self.tree),'from_version':updates.version(self.root)})
        with patch('localharness.source_update_activity.activity',return_value={'id':'ordinary-source'}):
            row=updates.update_progress(self.owner)
        self.assertEqual((row['id'],row['stage'],row['files']),('f'*32,'completed',27))
        self.assertEqual(row['source_update'],{'id':'ordinary-source'})

    def test_confirmation_owner_stop_and_selected_agent_are_admission_guards(self):
        with self.assertRaises(ValueError):jobs.admit_restore(self.owner)
        self.owner.exiting=True
        with self.assertRaises(InterruptedError):self.admit()
        self.owner.exiting=False;self.owner.stop.set()
        with self.assertRaises(InterruptedError):self.admit()
        self.owner.stop.clear();self.agent.enabled=False
        with self.assertRaises(ValueError):self.admit()
        self.agent.enabled=True;self.owner.session_host=False
        with self.assertRaises(RuntimeError):self.admit()
        self.assertFalse(jobs.path(self.tree).exists())

    def test_existing_watcher_consumes_restore_in_same_job_and_cancel_context(self):
        admitted=self.admit();seen=[]
        def run(owner,operation=''):
            seen.append(operation);self.assertEqual(owner.update_job['id'],operation)
            self.assertEqual(operation,admitted['operation_id']);self.assertFalse(owner.update_cancelled())
            return {'scope':'handoff boundary only'}
        with patch.object(recovery,'restore_owned',side_effect=run),patch.object(updates,'update_download') as download:
            jobs.consume(self.owner)
        download.assert_not_called();self.assertEqual(seen,[admitted['operation_id']])
        self.assertEqual(jobs.status(self.tree)['state'],'handed_off')
        self.assertIsNone(self.owner.update_job);self.assertIsNone(self.owner.update_cancelled)

    def test_queued_exit_stop_and_real_cancel_marker_never_enter_restore(self):
        admitted=self.admit()
        with patch.object(recovery,'restore_owned') as restore:
            self.owner.exiting=True;jobs.consume(self.owner);restore.assert_not_called()
            self.owner.exiting=False;self.owner.stop.set();jobs.consume(self.owner);restore.assert_not_called()
            self.owner.stop.clear()
            jobs.request(self.tree,'cancel',root=self.root,identity=admitted['operation_id'],by='window')
            self.assertTrue(jobs.cancelled(self.tree,admitted['operation_id']))
            jobs.consume(self.owner);restore.assert_not_called()
        self.assertEqual(jobs.status(self.tree)['state'],'cancelled')

    def test_other_ordinary_update_guard_blocks_stage_without_releasing_its_owner(self):
        self.admit();updates._update_targets.add(self.fx.target)
        with patch.object(recovery,'stage') as stage:
            jobs.consume(self.owner)
        stage.assert_not_called();self.assertIn(self.fx.target,updates._update_targets)
        self.assertEqual(jobs.status(self.tree)['state'],'attention')

    def test_stage_cancellation_cleans_only_its_directory_and_preserves_pause_data(self):
        admitted=self.admit();before=(self.tree/'memory/.control/autonomy-paused.json').read_bytes()
        original=recovery.os.link;links=[]
        def cancel_after_copy(source,target):
            original(source,target);links.append(str(target))
            jobs.request(self.tree,'cancel',root=self.root,identity=admitted['operation_id'],by='window')
        with patch.object(program_state,'staging',side_effect=self.fx.outside_stage), \
             patch.object(recovery.os,'link',side_effect=cancel_after_copy),patch.object(updates,'_prepare_handoff') as handoff:
            jobs.consume(self.owner)
        self.assertTrue(links);handoff.assert_not_called()
        record=jobs.status(self.tree);self.assertEqual(record['state'],'cancelled')
        self.assertEqual(record['staging'],str(self.fx.outside_stage(self.root,'recovery-'+admitted['operation_id'])))
        self.assertFalse(Path(record['staging']).exists());self.assertNotIn(self.fx.target,updates._update_targets)
        self.assertEqual((self.tree/'memory/.control/autonomy-paused.json').read_bytes(),before)
        self.assertEqual((self.tree/'memory/history.jsonl').read_bytes(),b'owner conversation exact bytes\n')

    def test_host_requires_the_registered_selected_observer_and_routes_to_admission(self):
        host=object.__new__(session.Host);host.stop=threading.Event();host.owner=self.owner
        host.root=self.root;host.observers={'registered':{'agent':'main','browser_mode':True}}
        self.owner._default=lambda:'main'
        with patch.object(agents,'find',return_value=self.agent),patch.object(jobs,'admit_restore',return_value={'state':'admitted'}) as admit:
            for observer,selected in (('missing','main'),('','main'),('registered','other')):
                with self.subTest(observer=observer,selected=selected),self.assertRaises(ValueError):
                    host.dispatch('invoke',{'agent':selected,'observer':observer,'command':'restore_upstream','arguments':{'confirmed':True}})
            result=host.dispatch('invoke',{'agent':'main','observer':'registered','command':'restore_upstream','arguments':{'confirmed':True}})
        self.assertEqual(result['state'],'admitted');admit.assert_called_once_with(self.owner,confirmed=True,browser_mode=True)

    def test_nonmodern_host_keeps_the_existing_owner_restore_route(self):
        host=object.__new__(session.Host);host.stop=threading.Event();host.owner=self.owner
        host.root=self.root;host.observers={};self.owner._default=lambda:'main'
        self.owner.invoke=Mock(return_value={'legacy':'existing owner route'})
        with patch.object(recovery,'modern',return_value=False),patch.object(agents,'find',return_value=self.agent), \
             patch.object(jobs,'admit_restore') as admit:
            result=host.dispatch('invoke',{'agent':'main','command':'restore_upstream','arguments':{'confirmed':True}})
        self.assertEqual(result,{'legacy':'existing owner route'});admit.assert_not_called()
        self.owner.invoke.assert_called_once_with('restore_upstream',{'confirmed':True})


class RestoreHttpAdmission(unittest.TestCase):
    def test_real_authenticated_reply_returns_while_existing_worker_is_blocked(self):
        with tempfile.TemporaryDirectory(prefix='restore-http-owner-') as temporary,ExitStack() as resources:
            base=Path(temporary);root=base/'Helene';tree=base/'profile';root.mkdir();tree.mkdir()
            write(root/'python-app.json',{'version':'private'});write(root/'helene.json',{'tree':str(tree),'code':'tree','setup_complete':False})
            app=Path(__file__).resolve().parents[1]
            script=r'''import sys,time,threading
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import session_host as session,upstream_recovery as recovery
from localharness.python_controller import Controller
from localharness.package_notice import read,write
session.owner_stop.stopped=lambda:False
recovery.modern=lambda _root:True
def slow_restore(owner,operation=''):
 tree=owner.current().tree
 prior=read(tree/'worker-entered.json');write(tree/'worker-entered.json',{'operation':operation,'count':prior.get('count',0)+1})
 while not (tree/'release-worker').exists():
  if owner.update_cancelled():raise InterruptedError('private owner cancellation')
  time.sleep(.02)
 return {'scope':'blocked synthetic worker; no apply'}
recovery.restore_owned=slow_restore
class PrivateController(Controller):
 def start(self):
  from localharness.desktop_update_request import watch
  threading.Thread(target=watch,args=(self,self.stop),name='helene-update-requests',daemon=True).start()
 def close(self):self.stop.set()
 def status(self):return {'agents':{},'fixture':True}
session.Host(Path(sys.argv[2]),controller_factory=PrivateController).run()
'''
            output=(base/'host.log').open('wb');resources.callback(output.close)
            process=subprocess.Popen([sys.executable,'-B','-c',script,str(app),str(root)],
                stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            def stop():
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=8)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            resources.callback(stop)
            deadline=time.monotonic()+10
            while True:
                descriptor=read(session.control_path(root))
                if descriptor.get('pid')==process.pid and descriptor.get('state')=='running':break
                if process.poll() is not None:self.fail('Private host failed: '+(base/'host.log').read_text(errors='replace'))
                if time.monotonic()>deadline:self.fail('Private host admission timeout')
                time.sleep(.03)
            first=session.SessionClient(root,descriptor,lambda *args:None,agent='main')
            resources.callback(first.close)
            # Shorter TEST observation only; product SessionClient180 is unchanged.
            started=time.monotonic()
            admitted=first._request('invoke',{'command':'restore_upstream','arguments':{'confirmed':True},'observer':first.observer},timeout=3)
            admission_seconds=time.monotonic()-started
            self.assertEqual(admitted['state'],'admitted');self.assertFalse((tree/'release-worker').exists())
            deadline=time.monotonic()+5
            while not (tree/'worker-entered.json').exists():
                if time.monotonic()>deadline:self.fail('Existing watcher did not consume admitted Restore')
                time.sleep(.02)
            self.assertEqual(read(tree/'worker-entered.json')['operation'],admitted['operation_id'])
            first.close()
            second=session.SessionClient(root,descriptor,lambda *args:None,agent='main');resources.callback(second.close)
            repeated=second.invoke('restore_upstream',{'confirmed':True})
            self.assertEqual(repeated['operation_id'],admitted['operation_id']);self.assertTrue(repeated['reused'])
            self.assertEqual(read(tree/'worker-entered.json')['count'],1)
            (tree/'release-worker').write_bytes(b'release synthetic worker')
            deadline=time.monotonic()+5
            while jobs.status(tree).get('state')!='handed_off':
                if time.monotonic()>deadline:self.fail('Worker did not retain its admitted completion identity')
                time.sleep(.02)
            print('RESTORE_JOB_HTTP '+json.dumps({'scope':'actual private authenticated Host/watcher; blocked synthetic work, no apply',
                'admission_seconds':admission_seconds,'accepted_before_worker_release':True,
                'same_job_after_observer_rejoin':repeated['operation_id']==admitted['operation_id'],
                'worker_execution_count':read(tree/'worker-entered.json')['count'],
                'kind':admitted['job']['kind'],'operation':admitted['job']['operation'],
                'product_rpc_timeout_unchanged':True}),flush=True)
            second.shutdown();process.wait(timeout=8);self.assertEqual(process.returncode,0)


if __name__=='__main__':unittest.main()
