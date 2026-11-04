"""Durable authority capture: creation owns context; later admission cannot borrow it."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import forge
import forge_worker
import run_context
import test_forge_wave2_server as fixtures


class CapturedContext(unittest.TestCase):
    setUp = fixtures.ServerWave2.setUp
    unit = fixtures.ServerWave2.unit

    def context(self, name):
        contexts=self.__dict__.setdefault('_contexts',{})
        if name not in contexts:contexts[name]=run_context.RunContext.create(
            kind='chat', goal='request '+name, principal_id='principal-'+name,
            scope='private', origin_chat_id='origin-'+name, delivery_chat_id='delivery-'+name,
            model_profile='profile-'+name, run_id='run-'+name)
        return contexts[name]

    def save_context(self, value):
        task=forge.get(self.tid)
        task['run_context']=value
        forge._save_task(task)

    def binder(self, path, wait=True):
        # A real fresh interpreter consumes the on-disk producer request. Start
        # with unrelated B to exercise the worker's ambient-context clearing.
        evidence=path.parent/'binding-probe.json'
        script='''import json,sys
from pathlib import Path
from unittest import mock
import forge,forge_worker,run_context
p=Path(sys.argv[1]); base=Path(sys.argv[2])
forge.BASE=base;forge.STATE_DIR=base/'memory/.forge';forge.TASKS_DIR=p.parents[3]
ambient=run_context.RunContext.from_dict(json.loads(sys.argv[3]))
class Adapter:
 tools=[]
 def prepare(self,llm,request):
  ctx=run_context.current_run()
  (p.parent/'binding-probe.json').write_text(json.dumps(ctx.to_dict() if ctx else None))
  raise RuntimeError('probe ends before model or tool execution')
with run_context.bind_run(ambient),mock.patch.object(forge_worker.llm,'chat',side_effect=AssertionError('NO MODEL')) as model:
 forge_worker._run_request(p,json.loads(p.read_text()),adapter=Adapter())
 model.assert_not_called()
 assert run_context.current_run()==ambient
'''
        process=subprocess.Popen([sys.executable,'-B','-c',script,str(path),str(self.base),
                               json.dumps(self.context('B').to_dict())],
                              cwd=Path(forge.__file__).parent,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        if not wait:return process,evidence
        return self.binding_result((process,evidence))

    def binding_result(self, pending):
        process,evidence=pending
        try:out,err=process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill();process.communicate();raise
        self.assertEqual(process.returncode,0,err)
        self.assertTrue(evidence.exists(),err)
        return json.loads(evidence.read_text())

    def spawn_and_bind(self, expected, **options):
        observed=[]
        def boundary(script, path, log):
            pending=self.binder(Path(path),wait=False)
            observed.append(pending)
            return pending[0].pid
        ambient=self.context('B')
        with run_context.bind_run(ambient),mock.patch.object(forge,'_spawn_runner',side_effect=boundary) as spawn:
            answer=forge.agent(self.tid,'spawn',role='scout',brief='context regression',
                               agent_id='agent-bbbbbbbb',**options)
            self.assertEqual(run_context.current_run(),ambient)
        self.assertEqual(spawn.call_count,1,answer)
        observed=[self.binding_result(pending) for pending in observed]
        path=forge._unit_dir(self.tid,'agents','agent-bbbbbbbb')/'request.json'
        captured=json.loads(path.read_text()).get('run_context')
        self.assertEqual(captured,expected)
        bound=replace(run_context.RunContext.from_dict(expected),forge_task_id=self.tid).to_dict() if expected else None
        self.assertEqual(observed,[bound])

    def test_missing_context_stays_unattributed_in_real_worker(self):
        self.spawn_and_bind(None)

    def test_empty_context_stays_unattributed_in_real_worker(self):
        self.save_context({});self.spawn_and_bind({})

    def test_null_context_stays_unattributed_in_real_worker(self):
        self.save_context(None);self.spawn_and_bind(None)

    def test_saved_A_is_not_replaced_by_B_in_real_worker(self):
        saved=self.context('A').to_dict();self.save_context(saved);self.spawn_and_bind(saved)

    def test_new_task_captures_A_once_then_admits_under_B(self):
        with run_context.bind_run(self.context('A')),mock.patch.object(forge,'_resolve_target',return_value=(self.root,'fixture')):
            answer=forge.start('new task capture',target='fixture',isolation='direct')
        tasks=[p for p in forge.TASKS_DIR.glob('*/task.json') if p.parent.name!=self.tid]
        self.assertEqual(len(tasks),1,answer)
        self.tid=tasks[0].parent.name
        expected=replace(self.context('A'),forge_task_id=self.tid).to_dict()
        self.assertEqual(forge.get(self.tid)['run_context'],expected)
        self.spawn_and_bind(expected)

    def test_nested_inherits_verified_parent_A_not_task_C_or_ambient_B(self):
        self.save_context(self.context('C').to_dict())
        saved=self.context('A').to_dict()
        directory,_=self.unit(run_context=saved)
        self.spawn_and_bind(saved,_parent_request_path=str(directory/'request.json'))

    def test_nested_missing_parent_does_not_promote_task_or_ambient(self):
        self.save_context(self.context('C').to_dict())
        directory,_=self.unit()
        self.spawn_and_bind(None,_parent_request_path=str(directory/'request.json'))

    def test_queued_disk_reload_and_tick_preserve_captured_A(self):
        saved=self.context('A').to_dict();self.save_context(saved)
        task=forge.get(self.tid);task['max_active_workers']=1;forge._save_task(task)
        blocker,_=self.unit()
        with run_context.bind_run(self.context('B')),mock.patch.object(forge,'_spawn_runner') as spawn:
            answer=forge.agent(self.tid,'spawn',role='scout',brief='queue context',agent_id='agent-bbbbbbbb')
        spawn.assert_not_called();self.assertIn('Queued',answer)
        path=forge._unit_dir(self.tid,'agents','agent-bbbbbbbb')/'request.json'
        self.assertEqual(json.loads(path.read_text())['run_context'],saved)
        forge._atomic_json(blocker/'result.json',{'status':'done'});forge.release_worker_root(blocker/'request.json')
        self.save_context(self.context('C').to_dict())
        seen=[]
        def boundary(script, request_path, log):
            pending=self.binder(Path(request_path),wait=False)
            seen.append(pending)
            return pending[0].pid
        with run_context.bind_run(self.context('B')),mock.patch.object(forge,'_spawn_runner',side_effect=boundary) as spawn:
            forge.tick_queued(self.tid)
        self.assertEqual(spawn.call_count,1)
        seen=[self.binding_result(pending) for pending in seen]
        self.assertEqual(json.loads(path.read_text())['run_context'],saved)
        self.assertEqual(seen,[replace(run_context.RunContext.from_dict(saved),forge_task_id=self.tid).to_dict()])

    def test_stopped_never_started_resume_keeps_durable_A(self):
        saved=self.context('A').to_dict()
        directory,_=self.unit(status='queued',run_context=saved)
        forge.agent(self.tid,'stop',agent_id=directory.name)
        old=(directory/'result.json').read_bytes()
        self.save_context(self.context('C').to_dict())
        with run_context.bind_run(self.context('B')):
            receipt=forge.control(self.tid,'resume',directory.name)
            repeat=forge.control(self.tid,'resume',directory.name)
        self.assertEqual(receipt['status'],'queued',receipt)
        self.assertEqual(receipt['agent_id'],repeat['agent_id'])
        successor=forge._unit_dir(self.tid,'agents',receipt['agent_id'])/'request.json'
        self.assertEqual(json.loads(successor.read_text())['run_context'],saved)
        self.assertEqual(self.binder(successor),replace(run_context.RunContext.from_dict(saved),forge_task_id=self.tid).to_dict())
        self.assertEqual((directory/'result.json').read_bytes(),old)

    def test_dag_rework_of_legacy_task_does_not_capture_B(self):
        import forge_swarm
        with mock.patch.object(forge,'_spawn_runner',side_effect=RuntimeError('INTERCEPTED_NO_PROCESS')):
            forge.swarm(self.tid,'plan',plan=json.dumps([{'id':'A','role':'scout','brief':'inspect'}]))
            with run_context.bind_run(self.context('B')):forge.swarm(self.tid,'start')
        node=forge_swarm.load(forge._task_dir(self.tid))['nodes'][0]
        old=forge._unit_dir(self.tid,'agents',node['agent_id'])
        forge._atomic_json(old/'result.json',{'status':'done','result':'first attempt'})
        forge.release_worker_root(old/'request.json')
        digest=forge._result_identity(old/'result.json')
        forge.swarm(self.tid,'rework',node_id='A',message='new attempt',result_sha256=digest)
        with run_context.bind_run(self.context('B')),mock.patch.object(forge,'_spawn_runner',side_effect=RuntimeError('INTERCEPTED_NO_PROCESS')):
            forge.swarm(self.tid,'tick')
        replacement=forge_swarm.load(forge._task_dir(self.tid))['nodes'][0]['agent_id']
        self.assertNotEqual(replacement,old.name)
        request=forge._read_json(forge._unit_dir(self.tid,'agents',replacement)/'request.json')
        self.assertFalse(request.get('run_context'))


if __name__=='__main__':unittest.main()
