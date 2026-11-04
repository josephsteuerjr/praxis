"""Explicit reboot evidence releases custody without rewriting historical outcomes."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import forge
import forge_recovery
import forge_roots


class RebootRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'workspace';self.root.mkdir()
        for name,value in [('BASE',self.base),('STATE_DIR',self.base/'memory/.forge'),
                           ('TASKS_DIR',self.base/'memory/.forge/tasks')]:
            patch=mock.patch.object(forge,name,value);patch.start();self.addCleanup(patch.stop)
        self.boundary={'boot_id':'fixture-boot','host_id':'fixture-host',
                       'boot_time':int(dt.datetime.now(dt.timezone.utc).timestamp())-600}
        p=mock.patch.object(forge_recovery,'kernel_boundary',return_value=self.boundary);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(forge,'_pid_alive',return_value=False);p.start();self.addCleanup(p.stop)
        self.tid='code-old';self.uid='agent-old';self.kind='agents'
        forge._save_task({'id':self.tid,'root':str(self.root),'scope':'self','status':'done','goal':'historical'})
        self.directory=forge._unit_dir(self.tid,self.kind,self.uid);self.directory.mkdir(parents=True)
        old=dt.datetime.fromtimestamp(self.boundary['boot_time']-3600,dt.timezone.utc).isoformat()
        self.request={'id':self.uid,'task_id':self.tid,'role':'worker','root':str(self.root),
                      'status':'running','supervisor_pid':111,'created':old}
        self.put('request.json',self.request)
        self.event={'at':old,'kind':'agent_spawned','agent_id':self.uid}
        self.events=self.directory.parents[1]/'events.jsonl';self.events.write_text(json.dumps(self.event)+'\n')

    def put(self,name,value):
        (self.directory/name).write_text(json.dumps(value))

    def recover(self,**kw):
        return forge_recovery.reconcile_unit(forge,self.tid,self.kind,self.uid,
            actor='test-operator',reason='verified historical boundary',
            host_binding=kw.get('host_binding','verified-local-execution-host'))

    def test_unknown_stays_fenced_without_explicit_proof(self):
        self.assertFalse(forge._writer_ended(self.directory))
        forge._save_task({'id':'code-new','root':str(self.root/'child'),'status':'active'})
        self.assertIn(self.uid,forge_roots.foreign_writer_reason(forge,'code-new'))

    def test_receipt_releases_root_and_preserves_request_stop_and_missing_result(self):
        self.put('stop.json',{'state':'unresolved','note':'old stop outcome unknown'})
        before={n:(self.directory/n).read_bytes() for n in ('request.json','stop.json')}
        self.recover()
        self.assertTrue(forge._writer_ended(self.directory))
        state=forge._unit_state(self.directory)
        self.assertEqual(state['status'],'lost');self.assertTrue(state['custody_recovered'])
        self.assertFalse((self.directory/'result.json').exists())
        self.assertEqual(before,{n:(self.directory/n).read_bytes() for n in before})
        forge._save_task({'id':'code-new','root':str(self.root/'child'),'status':'active'})
        self.assertEqual(forge_roots.foreign_writer_reason(forge,'code-new'),'')

    def test_failed_host_verification_stays_failed_after_release(self):
        old_dir=self.directory;self.kind='verifications';self.uid='verify-old'
        self.directory=forge._unit_dir(self.tid,self.kind,self.uid);self.directory.mkdir(parents=True)
        req={**self.request,'id':self.uid,'scope':'host'};self.put('request.json',req)
        result={'status':'failed','checks':[{'exit':139,'status':'failed'}]};self.put('result.json',result)
        before=(self.directory/'result.json').read_bytes()
        row={'at':self.request['created'],'kind':'verification_started','verification_id':self.uid}
        with self.events.open('a') as f:f.write(json.dumps(row)+'\n')
        self.recover();state=forge._unit_state(self.directory)
        self.assertEqual(state['status'],'failed');self.assertTrue(state['custody_recovered'])
        self.assertEqual(before,(self.directory/'result.json').read_bytes())
        with mock.patch.object(forge,'_kill_tree',side_effect=AssertionError('must not signal old verification')):
            stopped=json.loads(forge.verify(self.tid,'stop',verification_id=self.uid))
        self.assertEqual(stopped['status'],'failed');self.assertTrue(stopped['custody_recovered'])
        self.assertEqual(before,(self.directory/'result.json').read_bytes())

    def test_proof_is_idempotent(self):
        first=self.recover();raw=(self.directory/forge_recovery.FILE).read_bytes()
        self.assertEqual(self.recover(),first)
        self.assertEqual((self.directory/forge_recovery.FILE).read_bytes(),raw)

    def test_absent_launch_binding_cannot_be_replaced_by_an_old_timestamp(self):
        self.events.write_text('')
        with self.assertRaisesRegex(ValueError,'launch event'):self.recover()

    def test_post_reboot_execution_evidence_is_refused(self):
        row={**self.event,'kind':'agent_dequeued','at':dt.datetime.now(dt.timezone.utc).isoformat()}
        with self.events.open('a') as f:f.write(json.dumps(row)+'\n')
        with self.assertRaisesRegex(ValueError,'after the reboot'):self.recover()

    def test_current_request_is_not_released(self):
        self.put('request.json',{**self.request,'created':dt.datetime.now(dt.timezone.utc).isoformat()})
        with self.assertRaisesRegex(ValueError,'predate'):self.recover()

    def test_alive_bound_supervisor_is_refused(self):
        self.put('request.json',{**self.request,'supervisor_started_at':'birth'})
        with mock.patch.object(forge,'_pid_alive',return_value=True),self.assertRaisesRegex(ValueError,'alive'):
            self.recover()

    def test_foreign_device_cannot_use_local_boot(self):
        self.put('request.json',{**self.request,'scope':'windows','device_id':'other'})
        with self.assertRaisesRegex(ValueError,'foreign-host'):self.recover()

    def test_missing_host_attestation_is_refused(self):
        with self.assertRaisesRegex(ValueError,'binding'):self.recover(host_binding='')

    def test_subject_change_invalidates_old_recovery(self):
        self.recover();self.put('request.json',{**self.request,'supervisor_pid':222})
        self.assertIsNone(forge_recovery.read_recovery(self.directory))
        self.assertFalse(forge._writer_ended(self.directory))

    def test_new_result_invalidates_recovery_without_relabelling_it(self):
        self.recover();self.put('result.json',{'status':'done','result':'later'})
        self.assertIsNone(forge_recovery.read_recovery(self.directory))

    def test_reused_or_malformed_receipt_is_untrusted(self):
        self.recover();path=self.directory/forge_recovery.FILE;proof=json.loads(path.read_text())
        for value in ([],{'schema':forge_recovery.SCHEMA}, {**proof,'unit_id':'other'},
                      {**proof,'kernel_boundary':{'boot_id':'future','boot_time':self.boundary['boot_time']+1000}}):
            with self.subTest(value=value):
                path.write_text(json.dumps(value));self.assertIsNone(forge_recovery.read_recovery(self.directory))

    def test_receipt_cannot_move_to_a_different_host(self):
        self.recover()
        with mock.patch.object(forge_recovery,'kernel_boundary',return_value={**self.boundary,'host_id':'other-host'}):
            self.assertIsNone(forge_recovery.read_recovery(self.directory))

    def test_task_root_change_invalidates_recovery(self):
        self.recover();task=forge.get(self.tid);task['root']=str(self.base/'different');forge._save_task(task)
        self.assertIsNone(forge_recovery.read_recovery(self.directory))

    def test_later_stop_cannot_signal_an_extinct_identity_or_rewrite_old_stop(self):
        self.put('stop.json',{'state':'unresolved','note':'historical'})
        self.recover();before=(self.directory/'stop.json').read_bytes()
        with mock.patch.object(forge,'_kill_tree',side_effect=AssertionError('must not signal old PID')):
            text,_=forge._stop_agent_branch(self.tid,self.uid)
        self.assertIn('extinct',text)
        self.assertEqual(before,(self.directory/'stop.json').read_bytes())
        self.assertTrue(forge_recovery.read_recovery(self.directory))
