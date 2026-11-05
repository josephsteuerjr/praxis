"""Validated recovery retains historical outcomes through public desktop controls."""
import datetime as dt
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import forge
import forge_recovery
import llm


class DesktopRecovery(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name)
        self.root=self.base/'workspace';self.root.mkdir()
        for name,value in [('BASE',self.base),('STATE_DIR',self.base/'memory/.forge'),
                           ('TASKS_DIR',self.base/'memory/.forge/tasks')]:
            patch=mock.patch.object(forge,name,value);patch.start();self.addCleanup(patch.stop)
        self.boundary={'boot_id':'fixture-boot','host_id':'fixture-host',
            'boot_time':int(dt.datetime.now(dt.timezone.utc).timestamp())-600}
        patch=mock.patch.object(forge_recovery,'kernel_boundary',return_value=self.boundary)
        patch.start();self.addCleanup(patch.stop)
        for name,value in [('_pid_alive',False),('_group_live_members',None)]:
            patch=mock.patch.object(forge,name,return_value=value);patch.start();self.addCleanup(patch.stop)
        patch=mock.patch.object(forge,'_kill_tree',side_effect=AssertionError('must not signal a recovered identity'))
        patch.start();self.addCleanup(patch.stop)
        self.tid='code-desktop-recovery'
        forge._save_task({'id':self.tid,'root':str(self.root),'scope':'self','status':'active','goal':'fixture'})
        self.old=dt.datetime.fromtimestamp(self.boundary['boot_time']-3600,dt.timezone.utc).isoformat()

    def unit(self,kind,identity,*,result=None):
        directory=forge._unit_dir(self.tid,kind,identity);directory.mkdir(parents=True)
        request={'id':identity,'task_id':self.tid,'root':str(self.root),'scope':'self',
            'role':'worker','status':'running','supervisor_pid':111,'supervisor_started_at':'old-birth',
            'created':self.old}
        (directory/'request.json').write_text(json.dumps(request),encoding='utf8')
        (directory/'stop.json').write_text(json.dumps({'state':'unresolved','note':'historical stop'}),encoding='utf8')
        if result is not None:(directory/'result.json').write_text(json.dumps(result),encoding='utf8')
        event,field=forge_recovery.KINDS[kind]
        with (directory.parents[1]/'events.jsonl').open('a',encoding='utf8') as stream:
            stream.write(json.dumps({'at':self.old,'kind':event,field:identity})+'\n')
        return directory

    def recover(self,kind,identity):
        return forge_recovery.reconcile_unit(forge,self.tid,kind,identity,
            actor='fixture-operator',reason='verified old execution boundary',
            host_binding='verified-local-execution-host')

    def history(self,directory):
        return {name:(directory/name).read_bytes() for name in ('request.json','result.json','stop.json')
                if (directory/name).exists()}

    def test_public_agent_poll_stop_and_group_stop_preserve_historical_records(self):
        directory=self.unit('agents','agent-old')
        before=self.history(directory)
        self.assertIn('"status": "unknown"',forge.agent(self.tid,'poll',agent_id='agent-old'))
        self.assertFalse(forge._writer_ended(directory))
        self.recover('agents','agent-old')
        self.assertTrue(forge._writer_ended(directory))
        reply=forge.agent(self.tid,'poll',agent_id='agent-old')
        self.assertIn('"status": "lost"',reply)
        self.assertIn('"custody_recovered": true',reply)
        self.assertIn('extinct',forge.agent(self.tid,'stop',agent_id='agent-old'))
        self.assertEqual(forge.control(self.tid,'stop',agent_id='agent-old')['status'],'settled')
        self.assertEqual(self.history(directory),before)
        self.assertFalse((directory/'result.json').exists())

    def test_public_verification_keeps_failure_and_releases_only_custody(self):
        directory=self.unit('verifications','verify-old',result={'status':'failed','checks':[{'exit':139}]})
        before=self.history(directory)
        self.recover('verifications','verify-old')
        for action in ('poll','stop'):
            reply=json.loads(forge.verify(self.tid,action,verification_id='verify-old'))
            self.assertEqual(reply['status'],'failed');self.assertTrue(reply['custody_recovered'])
        self.assertEqual(forge._detached_writer_reason(self.tid),'')
        self.assertEqual(forge._shared_writer_reason(self.tid,''),'')
        self.assertEqual(self.history(directory),before)
        self.assertFalse((directory/'stop-request.json').exists())

    def test_public_process_preserves_unknown_outcome_and_discharges_writer(self):
        directory=self.unit('processes','proc-old')
        before=self.history(directory)
        self.recover('processes','proc-old')
        for action in ('poll','stop'):
            reply=json.loads(forge.process(self.tid,action,process_id='proc-old'))
            self.assertEqual(reply['status'],'lost');self.assertTrue(reply['custody_recovered'])
        self.assertEqual(forge._detached_writer_reason(self.tid),'')
        self.assertEqual(forge._shared_writer_reason(self.tid,''),'')
        self.assertEqual(forge.control(self.tid,'stop')['status'],'settled')
        self.assertEqual(self.history(directory),before)

    def test_recovered_process_allows_public_new_worker_admission(self):
        self.unit('processes','proc-old')
        request={'id':'agent-12345678','role':'worker'}
        self.assertIn('proc-old',forge._agent_capacity(self.tid,request))
        self.recover('processes','proc-old')
        with mock.patch.object(forge,'_launch_agent_unlocked',return_value=333) as launch:
            reply=forge.agent(self.tid,'spawn',agent_id=request['id'],brief='new independent fixture work')
        self.assertIn('Порождён',reply)
        launch.assert_called_once()

    def test_selected_stop_retains_recovered_owned_jobs_without_signalling(self):
        parent=self.unit('agents','agent-parent')
        request=json.loads((parent/'request.json').read_text(encoding='utf8'))
        request.update(status='queued',supervisor_pid=0,supervisor_started_at='')
        (parent/'request.json').write_text(json.dumps(request),encoding='utf8')
        job=self.unit('processes','proc-old')
        request=json.loads((job/'request.json').read_text(encoding='utf8'))
        request['parent_unit_id']='agent-parent'
        (job/'request.json').write_text(json.dumps(request),encoding='utf8')
        self.recover('processes','proc-old')
        before=self.history(job)
        with mock.patch.object(forge,'process',side_effect=AssertionError('recovered job must not be signalled')):
            forge.agent(self.tid,'stop',agent_id='agent-parent')
        self.assertEqual(forge._read_json(parent/'stop.json')['state'],'settled')
        self.assertEqual(self.history(job),before)

    def test_raw_recovered_flags_cannot_replace_a_validated_proof(self):
        for record in ('request','result','state'):
            with self.subTest(record=record):
                identity='proc-forged-'+record
                directory=self.unit('processes',identity)
                (directory/'stop.json').unlink()
                path=directory/(record+'.json')
                value=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
                value.update(status='lost',custody_recovered=True)
                path.write_text(json.dumps(value),encoding='utf8')
                if record=='state':
                    request=directory/'request.json'
                    value=json.loads(request.read_text(encoding='utf8'));value['remote']=True
                    request.write_text(json.dumps(value),encoding='utf8')
                before={p.name:p.read_bytes() for p in directory.glob('*.json')}
                self.assertIsNone(forge_recovery.read_recovery(directory))
                self.assertFalse(forge._unit_state(directory).get('custody_recovered'))
                self.assertIn(identity,forge._shared_writer_reason(self.tid,''))
                self.assertIn(identity,forge._detached_writer_reason(self.tid))
                self.assertIn(identity,forge._agent_capacity(self.tid,{'id':'agent-next','role':'worker'}))
                self.assertEqual(before,{p.name:p.read_bytes() for p in directory.glob('*.json')})
                shutil.rmtree(directory)

    def test_changed_subject_invalidates_recovery_and_keeps_uncertainty(self):
        directory=self.unit('agents','agent-old')
        self.recover('agents','agent-old')
        path=directory/'request.json';request=json.loads(path.read_text(encoding='utf8'))
        request['supervisor_pid']=222;path.write_text(json.dumps(request),encoding='utf8')
        self.assertIsNone(forge_recovery.read_recovery(directory))
        self.assertFalse(forge._writer_ended(directory))
        reply=forge.agent(self.tid,'poll',agent_id='agent-old')
        self.assertIn('"status": "unknown"',reply)
        self.assertIn('agent-old',forge._shared_writer_reason(self.tid,''))

    def test_scope_contradiction_never_mints_a_recovery_record(self):
        directory=self.unit('agents','agent-old')
        path=directory/'request.json';request=json.loads(path.read_text(encoding='utf8'))
        request['scope']='host';path.write_text(json.dumps(request),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'contradicts owning task'):
            self.recover('agents','agent-old')
        self.assertFalse((directory/forge_recovery.FILE).exists())


class SingleProviderAttempt(unittest.TestCase):
    def test_network_retry_is_scoped_and_keeps_the_exact_input(self):
        class APIConnectionError(Exception):
            pass
        messages=[{'role':'user','content':'exact retained provider input'}]
        original=json.dumps(messages)
        with mock.patch.object(llm,'_call',side_effect=APIConnectionError('unknown admission')) as call, \
             mock.patch.object(llm._time,'sleep'):
            with llm.single_provider_attempt(),self.assertRaises(APIConnectionError):
                llm._call_retrying_net('openai','fixture',messages=messages)
            self.assertEqual(call.call_count,1)
            self.assertIs(call.call_args.kwargs['messages'],messages)
            self.assertFalse(llm._SINGLE_PROVIDER_ATTEMPT.get())
            call.reset_mock()
            with self.assertRaises(APIConnectionError):
                llm._call_retrying_net('openai','fixture',messages=messages)
            self.assertEqual(call.call_count,llm.NET_RETRIES+1)
            self.assertTrue(all(row.kwargs['messages'] is messages for row in call.call_args_list))
        self.assertEqual(json.dumps(messages),original)


if __name__=='__main__':
    unittest.main()
