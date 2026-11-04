"""Host namespaces and actual backend descendant custody, isolated Linux only."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import forge
import forge_roots
import forge_remote_run
import test_forge_wave2_server as fixtures

def hostproc_module():
    spec=importlib.util.spec_from_file_location('isolated_hostproc',Path(__file__).parent/'serverd/hostproc.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def bound(root,namespace='host:test'):
    root=Path(root).resolve();ancestors=[*reversed(root.parents),root]
    ids=[f'{p.stat().st_dev}:{p.stat().st_ino}' for p in ancestors]
    return {'schema':1,'namespace':namespace,'root_id':ids[-1],'ancestors':ids,'verified':True,'canonical':str(root)}

class HostCustody(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ServerWave2();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.tid=self.fixture.tid;self.base=self.fixture.base

    def test_backend_proof_is_bound_to_operation_and_receipt_bytes(self):
        import hashlib
        receipt={'nonce':'owned','settled':True,'exit_code':0}
        row={'tree_exit':{'contract':'command-tree-v1','settled':True,'operation_id':'op-own',
             'receipt':receipt,'receipt_sha256':hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()).hexdigest()}}
        self.assertTrue(forge_remote_run.tree_settled(row,'op-own'))
        self.assertFalse(forge_remote_run.tree_settled(row,'op-other'))
        receipt['exit_code']=7
        self.assertFalse(forge_remote_run.tree_settled(row,'op-own'))

    def test_captured_host_binding_is_context_local_and_sent_to_actual_client(self):
        import serverd_client
        binding=bound(self.fixture.root);seen=[]
        def exchange(request,**kw):seen.append(request['args']);return {'ok':True}
        with mock.patch.object(serverd_client,'available',return_value=True),mock.patch.object(serverd_client,'_token',return_value='fixture'),mock.patch.object(serverd_client,'_exchange',side_effect=exchange):
            with serverd_client.root_context(binding):
                serverd_client.call('workspace.edit',{'root':str(self.fixture.root),'action':'write'})
            serverd_client.call('workspace.edit',{'root':str(self.fixture.root),'action':'write'})
        self.assertEqual(seen[0]['expected_root_binding'],binding)
        self.assertNotIn('expected_root_binding',seen[1])

    def test_legacy_host_binding_adoption_keeps_preimage_and_is_idempotent(self):
        task=forge.get(self.tid);task['scope']='host';forge._save_task(task)
        raw=forge._task_file(self.tid).read_bytes()
        with mock.patch.object(forge.serverd_client,'workspace_inspect_result',return_value={'ok':True,'root_binding':bound(task['root'])}):
            self.assertEqual(forge_roots.adopt_host_binding(forge,self.tid),'root binding adopted')
            first=forge._task_file(self.tid).read_bytes()
            self.assertEqual(forge_roots.adopt_host_binding(forge,self.tid),'root binding already adopted')
            self.assertEqual(forge._task_file(self.tid).read_bytes(),first)
        preimages=list((forge._task_dir(self.tid)/'migrations').glob('*.preimage'))
        self.assertEqual(len(preimages),1);self.assertEqual(preimages[0].read_bytes(),raw)
        self.fixture.unit(status='running')
        self.assertIn('unsettled',forge_roots.adopt_host_binding(forge,self.tid))

    def test_binding_adoption_crash_after_task_replace_repairs_prepared_receipt(self):
        task=forge.get(self.tid);task['scope']='host';forge._save_task(task)
        original=forge._task_file(self.tid).read_bytes();write=forge._atomic_json
        def crash(path,value):
            if path.name.startswith('host-root-') and value.get('state')=='committed':raise OSError('injected receipt publication crash')
            return write(path,value)
        with mock.patch.object(forge.serverd_client,'workspace_inspect_result',return_value={'ok':True,'root_binding':bound(task['root'])}):
            with mock.patch.object(forge,'_atomic_json',side_effect=crash):
                with self.assertRaises(OSError):forge_roots.adopt_host_binding(forge,self.tid)
            after=forge._task_file(self.tid).read_bytes()
            self.assertNotEqual(after,original)
            self.assertEqual(forge_roots.adopt_host_binding(forge,self.tid),'root binding already adopted')
            self.assertEqual(forge._task_file(self.tid).read_bytes(),after)
        folder=forge._task_dir(self.tid)/'migrations'
        self.assertEqual(next(folder.glob('*.preimage')).read_bytes(),original)
        receipt=json.loads(next(folder.glob('host-root-*.json')).read_text())
        self.assertEqual(receipt['state'],'committed');self.assertTrue(receipt['recovered_at'])

    @unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('PRAXIS_TEST_FORGE_PROCESS')=='1','isolated Linux process gate')
    def test_actual_adoption_process_death_before_current_after_current_after_receipt(self):
        for number,stage in enumerate(('preimage','current','receipt')):
            tid=f'hcode-1122334{number}'
            forge._save_task({**forge.get(self.tid),'id':tid,'scope':'host','root_binding':{}})
            original=forge._task_file(tid).read_bytes()
            proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--adoption-crash',str(self.base),tid,stage])
            self.assertEqual(proc.wait(timeout=8),92)
            after=forge._task_file(tid).read_bytes()
            with mock.patch.object(forge.serverd_client,'workspace_inspect_result',return_value={'ok':True,'root_binding':bound(self.fixture.root)}):
                self.assertIn('adopted',forge_roots.adopt_host_binding(forge,tid))
            if stage!='preimage':self.assertEqual(forge._task_file(tid).read_bytes(),after)
            folder=forge._task_dir(tid)/'migrations'
            self.assertEqual(next(folder.glob('*.preimage')).read_bytes(),original)
            self.assertEqual(json.loads(next(folder.glob('host-root-*.json')).read_text())['state'],'committed')

    @unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('PRAXIS_TEST_FORGE_PROCESS')=='1','isolated Linux process gate')
    def test_two_host_task_processes_cannot_reserve_same_physical_writer(self):
        task=forge.get(self.tid);task.update(scope='host',root_binding=bound(task['root']));forge._save_task(task)
        other={**task,'id':'hcode-11223344'};forge._save_task(other)
        code="""import json,sys,time
from pathlib import Path
import forge
base=Path(sys.argv[1]);forge.BASE=base;forge.STATE_DIR=base/'memory/.forge';forge.TASKS_DIR=forge.STATE_DIR/'tasks'
tid=sys.argv[2];unit='agent-00112233'
with forge._mutation_lock(tid):
 reason=forge._agent_capacity(tid,{'id':unit,'role':'worker'})
 if not reason:forge._atomic_json(forge._unit_dir(tid,'agents',unit)/'request.json',{'id':unit,'task_id':tid,'role':'worker','status':'running'})
 print(json.dumps({'refused':reason}),flush=True)
"""
        a=subprocess.Popen([sys.executable,'-B','-c',code,str(self.base),self.tid],stdout=subprocess.PIPE,text=True)
        b=subprocess.Popen([sys.executable,'-B','-c',code,str(self.base),other['id']],stdout=subprocess.PIPE,text=True)
        rows=[json.loads(p.communicate(timeout=8)[0]) for p in (a,b)]
        self.assertEqual(sum(not r['refused'] for r in rows),1)
        self.assertTrue(any('occupied' in r['refused'] for r in rows))

    def test_terminal_labels_without_backend_tree_proof_retain_custody(self):
        task=forge.get(self.tid);task['scope']='host';forge._save_task(task)
        directory=forge._unit_dir(self.tid,'processes','proc-old')
        request={'id':'proc-old','task_id':self.tid,'scope':'host','remote':True,'remote_id':'op-existing'}
        forge._atomic_json(directory/'request.json',request)
        original={'status':'done','remote_result':{'ok':True,'status':'done','exit':0}}
        forge._atomic_json(directory/'result.json',original);raw=(directory/'result.json').read_bytes()
        self.assertEqual(forge._unit_state(directory)['status'],'unknown')
        remote=mock.Mock();remote.call.return_value={'ok':True,'status':'done','exit':0}
        with mock.patch.object(forge,'_remote_client',return_value=remote):
            self.assertEqual(forge_remote_run.refresh(forge,directory,task)['status'],'unknown')
        self.assertEqual((directory/'result.json').read_bytes(),raw)
        self.assertIn('proc-old',forge._shared_writer_reason(self.tid,''))

    def test_scope_less_legacy_remote_result_is_preserved_when_exit_reconciles(self):
        from test_forge_remote_admission_2710 import tree_proof
        task=forge.get(self.tid);task['scope']='host';forge._save_task(task)
        directory=forge._unit_dir(self.tid,'processes','proc-old')
        request={'id':'proc-old','task_id':self.tid,'remote':True,'remote_id':'op-existing'}
        forge._atomic_json(directory/'request.json',request)
        original={'status':'done','remote_result':{'ok':True,'status':'done','exit':0},'owner_evidence':'retained'}
        forge._atomic_json(directory/'result.json',original);raw=(directory/'result.json').read_bytes()
        self.assertEqual(forge._unit_state(directory)['status'],'unknown')
        import forge_reader
        self.assertEqual(forge_reader.task_detail(self.base,self.tid)['processes'][0]['status'],'unknown')
        remote=mock.Mock();remote.call.return_value={'ok':True,'id':'op-existing','status':'stopped','exit':0,'tree_exit':tree_proof('op-existing')}
        with mock.patch.object(forge,'_remote_client',return_value=remote):
            observed=forge_remote_run.refresh(forge,directory,task)
        self.assertEqual(observed['status'],'stopped')
        self.assertEqual((directory/'result.json').read_bytes(),raw)
        self.assertEqual(forge._unit_state(directory)['status'],'stopped')
        self.assertTrue(forge_reader.task_detail(self.base,self.tid)['processes'][0]['exit_reconciliation'])
        # Changing the original invalidates the reconciliation instead of lending
        # an old proof to different bytes.
        forge._atomic_json(directory/'result.json',{**original,'owner_evidence':'changed'})
        self.assertEqual(forge._unit_state(directory)['status'],'unknown')

    def test_contradictory_remote_scope_never_signals_another_backend(self):
        task=forge.get(self.tid);task['scope']='host';forge._save_task(task)
        directory=forge._unit_dir(self.tid,'processes','proc-conflict')
        forge._atomic_json(directory/'request.json',{'id':directory.name,'task_id':self.tid,
                          'scope':'windows','remote':True,'remote_id':'op-foreign-body'})
        with mock.patch.object(forge,'_remote_client') as client:
            result=forge_remote_run.refresh(forge,directory,task,stop=True)
        self.assertEqual(result['status'],'unknown');client.assert_not_called()

    def test_verified_roots_and_unknown_legacy_namespaces(self):
        root=self.base/'roots';root.mkdir();a=root/'a';b=root/'b';a.mkdir();b.mkdir()
        left={'scope':'host','root':str(a),'root_binding':bound(a)}
        alias=self.base/'alias';alias.symlink_to(a,target_is_directory=True)
        right={'scope':'host','root':str(alias),'root_binding':bound(alias)}
        self.assertTrue(forge_roots.tasks_overlap(left,right))
        self.assertFalse(forge_roots.tasks_overlap(left,{**left,'root':str(b),'root_binding':bound(b)}))
        self.assertFalse(forge_roots.tasks_overlap(left,{**right,'root_binding':bound(alias,'host:other')}))
        self.assertTrue(forge_roots.tasks_overlap(left,{'scope':'host','root':'/unknown-alias'}))
        # Windows device IDs isolate machines; aliases within an unverified device
        # deliberately retain a coarse domain until canonical identity exists.
        self.assertTrue(forge_roots.tasks_overlap({'scope':'windows','device_id':'a','root':'C:/a'},
                                                 {'scope':'windows','device_id':'a','root':'D:/alias'}))
        self.assertFalse(forge_roots.tasks_overlap({'scope':'windows','device_id':'a','root':'C:/a'},
                                                  {'scope':'windows','device_id':'b','root':'C:/a'}))

    def test_same_host_root_writer_blocks_both_worker_and_general_admission(self):
        task=forge.get(self.tid);task.update(scope='host',root_binding=bound(task['root']));forge._save_task(task)
        directory,request=self.fixture.unit(status='running')
        other={**task,'id':'hcode-11223344'};forge._save_task(other)
        reason=forge._shared_writer_reason(other['id'],'');self.assertIn(request['id'],reason)
        self.assertIn(request['id'],forge._agent_capacity(other['id'],{'id':'agent-new','role':'worker'}))

    @unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('PRAXIS_TEST_FORGE_PROCESS')=='1','isolated Linux process gate')
    def test_cross_process_same_host_alias_serializes_independent_roots_parallel(self):
        root=self.base/'roots';root.mkdir();a=root/'a';b=root/'b';a.mkdir();b.mkdir()
        task={'scope':'host','root':str(a),'root_binding':bound(a)}
        marker=self.base/'held';release=self.base/'release'
        code="""import json,sys,time
from pathlib import Path
import forge_roots
with forge_roots.task_transaction(json.loads(sys.argv[1]),Path(sys.argv[2])):
 Path(sys.argv[3]).write_text('held')
 while not Path(sys.argv[4]).exists():time.sleep(.01)
"""
        proc=subprocess.Popen([sys.executable,'-B','-c',code,json.dumps(task),str(forge.STATE_DIR),str(marker),str(release)])
        try:
            deadline=time.monotonic()+5
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(marker.exists())
            with self.assertRaises(TimeoutError):
                with forge_roots.task_transaction(task,forge.STATE_DIR,.15):pass
            with forge_roots.task_transaction({**task,'root':str(b),'root_binding':bound(b)},forge.STATE_DIR,.3):pass
        finally:release.write_text('release');proc.wait(timeout=5)

    @unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('PRAXIS_TEST_FORGE_PROCESS')=='1','isolated Linux process gate')
    def test_actual_host_backend_waits_escaped_descendant_then_proves_settlement(self):
        hp=hostproc_module();unit=self.base/'host-op';marker=self.base/'descendant-finished'
        import shlex
        child=f"import time;from pathlib import Path;time.sleep(.7);Path({str(marker)!r}).write_text('done')"
        hp.spawn(unit,f'setsid {shlex.quote(sys.executable)} -c {shlex.quote(child)} & exit 0',str(self.base))
        time.sleep(.3);self.assertNotIn(hp.poll(unit)['status'],hp.TERMINAL_STATUSES)
        row=self.wait_terminal(hp,unit);self.assertEqual(row['status'],'done');self.assertTrue(marker.exists())
        self.assertTrue(forge_remote_run.tree_settled(row))

    @unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('PRAXIS_TEST_FORGE_PROCESS')=='1','isolated Linux process gate')
    def test_actual_stop_settles_between_supervisor_poll_iterations(self):
        hp=hostproc_module();unit=self.base/'host-barrier';unit.mkdir()
        marker=self.base/'command-started'
        hp._write(unit/'request.json',{'command':f'touch {marker}; setsid sleep 30 & exit 0',
                'cwd':str(self.base),'tree_contract':hp.TREE_CONTRACT})
        proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--gated-host-supervise',str(unit)],start_new_session=True)
        try:
            end=time.monotonic()+6
            while (not (unit/'poll-barrier').exists() or not marker.exists()) and time.monotonic()<end:time.sleep(.01)
            self.assertTrue((unit/'poll-barrier').exists());self.assertTrue(marker.exists())
            state=hp._read(unit/'state.json');nonce=state['command_identity']['nonce']
            hp._write(unit/'stop-request.json',{'at':hp._now()})
            (unit/'command-custody/stop').write_text(nonce)
            end=time.monotonic()+5
            while not (unit/'command-custody/settled.json').exists() and time.monotonic()<end:time.sleep(.01)
            self.assertTrue(hp._read(unit/'command-custody/settled.json')['stopped'])
            (unit/'poll-release').write_text('release');proc.wait(timeout=5)
            result=hp.poll(unit);self.assertEqual(result['status'],'stopped',result)
            raw=(unit/'result.json').read_bytes();hp.stop(unit)
            self.assertEqual((unit/'result.json').read_bytes(),raw)
        finally:
            (unit/'poll-release').write_text('release')
            hp.stop(unit)
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=5)

    @unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('PRAXIS_TEST_FORGE_PROCESS')=='1','isolated Linux process gate')
    def test_actual_host_timeout_and_lost_supervisor_stop_reconcile_from_custodian(self):
        hp=hostproc_module();unit=self.base/'host-timeout'
        hp.spawn(unit,'setsid sleep 30 & exit 0',str(self.base),timeout=1)
        row=self.wait_terminal(hp,unit);self.assertEqual(row['status'],'timed_out');self.assertTrue(forge_remote_run.tree_settled(row))
        unit=self.base/'host-lost';marker=self.base/'admitted'
        hp.spawn(unit,f'touch {marker}; setsid sleep 30 & wait',str(self.base))
        deadline=time.monotonic()+6
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue(marker.exists());state=hp._read(unit/'state.json')
        os.kill(state['supervisor_pid'],signal.SIGKILL)
        time.sleep(.1);self.assertNotIn(hp.poll(unit)['status'],hp.TERMINAL_STATUSES)
        hp.stop(unit);row=self.wait_terminal(hp,unit)
        self.assertTrue(row['tree_exit']['settled']);self.assertEqual(row['status'],'stopped')

    def wait_terminal(self,hp,unit):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            row=hp.poll(unit)
            if row.get('status') in hp.TERMINAL_STATUSES:return row
            time.sleep(.03)
        hp.stop(unit);self.fail('backend never settled: '+repr(row))

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--gated-host-supervise':
        unit=Path(sys.argv[2]);hp=hostproc_module();original=hp.CommandProcess
        class BarrierCommand(original):
            def poll(self):
                if not (unit/'poll-release').exists():
                    (unit/'poll-barrier').write_text('before condition poll')
                    while not (unit/'poll-release').exists():time.sleep(.01)
                return super().poll()
        hp.CommandProcess=BarrierCommand
        raise SystemExit(hp._supervise(unit))
    if len(sys.argv)==5 and sys.argv[1]=='--adoption-crash':
        base=Path(sys.argv[2]);tid=sys.argv[3];stage=sys.argv[4]
        forge.BASE=base;forge.STATE_DIR=base/'memory/.forge';forge.TASKS_DIR=forge.STATE_DIR/'tasks'
        atomic=forge._atomic_json;save=forge._save_task
        def write(path,value):
            if path.name.startswith('host-root-') and stage=='preimage':os._exit(92)
            atomic(path,value)
            if path.name.startswith('host-root-') and value.get('state')=='committed' and stage=='receipt':os._exit(92)
        def replace(task):
            save(task)
            if stage=='current':os._exit(92)
        with mock.patch.object(forge.serverd_client,'workspace_inspect_result',return_value={'ok':True,'root_binding':bound(base/'project')}),mock.patch.object(forge,'_atomic_json',side_effect=write),mock.patch.object(forge,'_save_task',side_effect=replace):
            forge_roots.adopt_host_binding(forge,tid)
        raise AssertionError('crash boundary not reached')
    unittest.main()
