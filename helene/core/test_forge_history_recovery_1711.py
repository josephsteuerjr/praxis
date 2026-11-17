"""Opaque shared history needs a witnessed later reboot, never skipped rows."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import forge, forge_recovery as recovery


class HistoryRecovery(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name);self.root=self.base/'workspace';self.root.mkdir()
        for name,value in [('BASE',self.base),('STATE_DIR',self.base/'memory/.forge'),('TASKS_DIR',self.base/'memory/.forge/tasks')]:
            p=patch.object(forge,name,value);p.start();self.addCleanup(p.stop)
        self.boundary={'boot_id':'boot-before','host_id':'fixture-host','boot_time':int(dt.datetime.now(dt.timezone.utc).timestamp())-900}
        p=patch.object(recovery,'kernel_boundary',side_effect=lambda:dict(self.boundary));p.start();self.addCleanup(p.stop)
        p=patch.object(sys,'platform','linux');p.start();self.addCleanup(p.stop)
        p=patch.object(forge,'_pid_alive',return_value=False);self.alive=p.start();self.addCleanup(p.stop)
        self.tid='code-history';self.uid='agent-old'
        forge._save_task({'id':self.tid,'root':str(self.root),'scope':'self','status':'active','goal':'fixture'})
        self.unit=forge._unit_dir(self.tid,'agents',self.uid);self.unit.mkdir(parents=True)
        old=dt.datetime.fromtimestamp(self.boundary['boot_time']-3600,dt.timezone.utc).isoformat()
        self.put(self.unit/'request.json',{'id':self.uid,'task_id':self.tid,'root':str(self.root),'scope':'self',
            'status':'running','role':'worker','created':old,'supervisor_pid':111,'supervisor_started_at':'fixture-birth'})
        self.put(self.unit/'stop.json',{'state':'unresolved','note':'original unknown outcome'})
        self.journal=self.unit.parents[1]/'events.jsonl'
        self.first=(json.dumps({'at':old,'kind':'agent_spawned','agent_id':self.uid})+'\n').encode()
        self.raw=self.first+b'{"kind":"agent_spawned","agent_id":"other-unit"\n'
        self.journal.write_bytes(self.raw)

    def put(self,path,value):path.write_text(json.dumps(value),encoding='utf8')
    def control(self,action,*,agent=True,**kw):
        return forge.control(self.tid,action,agent_id=self.uid if agent else '',**kw)
    def prepare(self,**kw):
        return self.control('prepare_recovery',host_binding='verified-local-execution-host',reason='owner verified original execution host',**kw)
    def reboot(self):self.boundary={**self.boundary,'boot_id':'boot-after','boot_time':self.boundary['boot_time']+600}
    def proof(self):return recovery.read_recovery(self.unit)

    def test_named_diagnostic_exact_location_and_no_new_proof(self):
        diagnostic=recovery.history_diagnostic(self.unit)
        self.assertEqual(diagnostic['code'],'forge_history_corrupt')
        self.assertEqual((diagnostic['line'],diagnostic['offset']),(2,len(self.first)))
        self.assertEqual(diagnostic['journal_sha256'],hashlib.sha256(self.raw).hexdigest())
        with self.assertRaises(recovery.HistoryRefusal):
            recovery.reconcile_unit(forge,self.tid,'agents',self.uid,actor='fixture',reason='fixture',host_binding='verified-local-execution-host')
        self.assertEqual(self.journal.read_bytes(),self.raw)
        self.assertFalse((self.unit/recovery.FILE).exists())

    def test_preparation_preserves_bytes_and_requires_actual_later_boot(self):
        original={name:(self.unit/name).read_bytes() for name in ('request.json','stop.json')}
        self.assertEqual(self.prepare()['status'],'prepared')
        witness=(self.unit/recovery.HISTORY_WITNESS).read_bytes()
        self.assertEqual((self.unit/recovery.HISTORY_SNAPSHOT).read_bytes(),self.raw)
        self.assertTrue(self.journal.read_bytes().startswith(self.raw))
        self.assertEqual(self.prepare()['status'],'prepared')
        self.assertEqual((self.unit/recovery.HISTORY_WITNESS).read_bytes(),witness)
        refused=self.control('recover');self.assertEqual(refused['status'],'refused')
        self.assertEqual(refused['diagnostic']['code'],'forge_history_reboot_required')
        self.assertIsNone(self.proof())
        self.assertEqual(original,{name:(self.unit/name).read_bytes() for name in original})

    def test_later_boot_releases_only_custody_and_retains_invalid_old_proof_and_failed_result(self):
        self.put(self.unit/'result.json',{'status':'failed','error':'historical failure'})
        (self.unit/recovery.FILE).write_bytes(b'{old invalid proof')
        original={name:(self.unit/name).read_bytes() for name in ('request.json','stop.json','result.json',recovery.FILE)}
        self.assertEqual(self.prepare()['status'],'prepared');self.reboot()
        self.assertEqual(self.control('recover')['status'],'settled');self.assertTrue(self.proof())
        self.assertEqual(forge._unit_state(self.unit)['status'],'failed')
        self.assertTrue(forge._unit_state(self.unit)['custody_recovered'])
        self.assertEqual(original,{name:(self.unit/name).read_bytes() for name in original})
        self.assertTrue(self.journal.read_bytes().startswith(self.raw))
        self.assertEqual(self.control('recover')['status'],'settled')

    def test_valid_other_unit_append_is_safe_but_new_corruption_rejects_proof(self):
        self.prepare();self.reboot();self.control('recover');self.assertTrue(self.proof())
        with self.journal.open('ab') as out:
            out.write((json.dumps({'kind':'agent_spawned','agent_id':'agent-other','at':dt.datetime.now(dt.timezone.utc).isoformat()})+'\n').encode())
        self.assertTrue(self.proof())
        proof=(self.unit/recovery.HISTORY_PROOF).read_bytes()
        with self.journal.open('ab') as out:out.write(b'\x00\x00\n')
        self.assertIsNone(self.proof());self.assertEqual((self.unit/recovery.HISTORY_PROOF).read_bytes(),proof)
        self.assertEqual(self.control('recover')['status'],'refused')

    def test_target_relaunch_after_snapshot_and_new_boot_blocks_recovery(self):
        self.prepare();self.reboot()
        with self.journal.open('ab') as out:
            out.write((json.dumps({'kind':'agent_resumed','agent_id':self.uid,'at':dt.datetime.now(dt.timezone.utc).isoformat()})+'\n').encode())
        refused=self.control('recover')
        self.assertEqual(refused['diagnostic']['code'],'forge_history_new_execution');self.assertIsNone(self.proof())

    def test_prefix_edit_and_subject_change_do_not_release_custody(self):
        self.prepare();self.reboot();original=self.journal.read_bytes()
        self.journal.write_bytes(b'X'+original[1:])
        self.assertEqual(self.control('recover')['diagnostic']['code'],'forge_history_prefix_changed')
        self.journal.write_bytes(original)
        self.put(self.unit/'result.json',{'status':'done','result':'unwitnessed new bytes'})
        self.assertEqual(self.control('recover')['diagnostic']['code'],'forge_history_witness_changed')
        self.assertIsNone(self.proof())

    def test_host_change_same_boot_or_reversed_chronology_are_refused(self):
        self.prepare();old=dict(self.boundary)
        for current in ({**old,'host_id':'copied-host','boot_id':'new','boot_time':old['boot_time']+600},
                        {**old,'boot_time':old['boot_time']+600},
                        {**old,'boot_id':'new','boot_time':old['boot_time']-1}):
            with self.subTest(current=current):
                self.boundary=current
                self.assertEqual(self.control('recover')['status'],'refused');self.assertIsNone(self.proof())

    def test_live_process_missing_stop_and_foreign_scope_refuse_preparation(self):
        self.alive.return_value=True
        self.assertEqual(self.prepare()['diagnostic']['code'],'forge_history_stop_required')
        self.alive.return_value=False;(self.unit/'stop.json').unlink()
        self.assertEqual(self.prepare()['diagnostic']['code'],'forge_history_stop_required')
        self.put(self.unit/'stop.json',{'state':'unresolved'})
        request=json.loads((self.unit/'request.json').read_bytes());self.put(self.unit/'request.json',{**request,'device_id':'foreign-device'})
        self.assertEqual(self.prepare()['status'],'refused')
        self.assertFalse((self.unit/recovery.HISTORY_WITNESS).exists())

    def test_branch_is_validated_before_first_witness_is_written(self):
        other=forge._unit_dir(self.tid,'agents','agent-other');other.mkdir()
        request=json.loads((self.unit/'request.json').read_bytes())
        self.put(other/'request.json',{**request,'id':other.name,'device_id':'foreign-device'})
        self.put(other/'stop.json',{'state':'unresolved'})
        self.assertEqual(self.prepare(agent=False)['status'],'refused')
        self.assertFalse((self.unit/recovery.HISTORY_WITNESS).exists())

    def test_scalar_invalid_utf8_and_blank_rows_are_not_silently_skipped(self):
        for bad in (b'null\n',b'[]\n',b'123\n',b'\xff\n',b'\n'):
            with self.subTest(bad=bad):
                self.journal.write_bytes(self.first+bad)
                self.assertEqual(recovery.history_diagnostic(self.unit)['code'],'forge_history_corrupt')

    def test_recover_without_witness_and_missing_participant_are_named_refusals(self):
        self.reboot();self.assertEqual(self.control('recover')['diagnostic']['code'],'forge_history_witness_required')
        answer=forge.control(self.tid,'recover',agent_id='agent-missing')
        self.assertEqual(answer['diagnostic']['code'],'forge_history_unit_changed')

    def test_recovered_custody_does_not_authorize_unsafe_continuation(self):
        self.prepare();self.reboot();self.assertEqual(self.control('recover')['status'],'settled')
        with patch.object(forge,'_launch_agent_unlocked',side_effect=AssertionError('unknown actions must not replay')):
            answer=self.control('resume')
        self.assertEqual(answer['status'],'refused')


if __name__ == '__main__':
    unittest.main()
