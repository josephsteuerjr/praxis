"""Regressions from the actual payload/checkpoint/orientation failures."""
import contextlib
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import agent
import forge
import run_context
from run_manager import RunManager


class CodingReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name);self.root=self.base/'project';self.root.mkdir()
        for name,value in [('BASE',self.base),('STATE_DIR',self.base/'memory/.forge'),
                           ('TASKS_DIR',self.base/'memory/.forge/tasks')]:
            patch=mock.patch.object(forge,name,value);patch.start();self.addCleanup(patch.stop)
        self.task='code-read';self.unit='agent-read'
        forge._save_task({'id':self.task,'root':str(self.root),'status':'active','scope':'self',
                         'max_active_workers':8,'goal':'read exact result'})
        self.directory=forge._unit_dir(self.task,'agents',self.unit);self.directory.mkdir(parents=True)
        self.raw=json.dumps({'status':'done','result':'full result'}).encode()
        (self.directory/'result.json').write_bytes(self.raw)
        self.sha=hashlib.sha256(self.raw).hexdigest()

    def test_actual_result_sha_argument_reads_exact_record(self):
        value=json.loads(agent.tool_coding_agent(self.task,'payload',agent_id=self.unit,
                                                kind='result',result_sha256=self.sha))
        self.assertEqual(value['text'],self.raw.decode())
        self.assertEqual(value['sha256'],self.sha)

    def test_missing_hash_is_repairable_without_claiming_the_file_changed(self):
        value=json.loads(agent.tool_coding_agent(self.task,'payload',agent_id=self.unit,kind='result'))
        self.assertFalse(value['ok']);self.assertIn('payload_sha256',value['error'])
        self.assertNotIn('record changed',value['error'])

    def test_conflicting_hashes_are_not_silently_selected(self):
        value=json.loads(forge.agent(self.task,'payload',agent_id=self.unit,kind='result',
                                    payload_sha256=self.sha,result_sha256='f'*64))
        self.assertFalse(value['ok']);self.assertIn('Conflicting',value['error'])

    def test_wrong_hash_never_returns_content(self):
        value=json.loads(forge.agent(self.task,'payload',agent_id=self.unit,kind='result',payload_sha256='f'*64))
        self.assertFalse(value['ok']);self.assertNotIn('text',value)

    def test_checkpoint_accepts_actual_message_field_and_preserves_structure(self):
        data={'next_action':'review exact result','unresolved_reviews':['one']}
        value=json.loads(forge.swarm(self.task,'checkpoint',message=json.dumps(data)))
        self.assertEqual(value['next_action'],data['next_action'])
        self.assertEqual(value['unresolved_reviews'],data['unresolved_reviews'])

    def test_conflicting_checkpoint_objects_are_refused(self):
        value=forge.swarm(self.task,'checkpoint',plan=json.dumps({'next_action':'one'}),
                          message=json.dumps({'next_action':'two'}))
        self.assertIn('conflicting',value)
        self.assertFalse((self.directory.parents[1]/'coordinator-checkpoint.json').exists())

    def exercise_loop(self,action):
        manager=RunManager(self.base)
        ctx=run_context.RunContext.create(run_id='run-coding-read',kind='wake',goal='repairable read',
                                          principal_id=agent.PRAXIS_SELF_PRINCIPAL,scope='owner',model_profile='test')
        manager.create(ctx,'immutable context');manager.transition(ctx.run_id,'running')
        block={'type':'tool_use','id':'call-read','name':'coding_agent',
               'input':{'task_id':self.task,'action':action,'agent_id':self.unit,'kind':'result'}}
        responses=[types.SimpleNamespace(stop_reason='tool_use',blocks=[block],text=''),
                   types.SimpleNamespace(stop_reason='end_turn',blocks=[],text='observed error')]
        def broken(**kwargs):raise ValueError('corrupt read input')
        with run_context.bind_run(ctx),mock.patch.object(agent,'_RUN_MANAGER',manager),\
             mock.patch.object(agent,'_model_call',side_effect=responses),\
             mock.patch.object(agent,'_work_loop_continue',return_value=False),\
             mock.patch.object(agent,'_durable_model_text',side_effect=lambda text,*a,**k:text),\
             mock.patch.object(agent,'TOOL_CEILING_SEC',0),\
             mock.patch.dict(agent.TOOL_IMPL,{'coding_agent':broken}):
            value=agent._terminal_tool_loop(system='system',messages=[{'role':'user','content':'read'}],
                                           tools=[{'name':'coding_agent'}],max_iters=2)
        return manager,ctx,value

    def test_read_exception_closes_call_and_returns_to_model(self):
        manager,ctx,value=self.exercise_loop('payload')
        self.assertEqual(value,'observed error')
        status=manager.status(ctx.run_id)
        self.assertEqual(status['status'],'running')
        self.assertFalse(status['outstanding_call_ids'])
        self.assertTrue(status['terminalizable'])

    def test_mutating_exception_still_retains_uncertain_call(self):
        with self.assertRaises(agent.RunStopped):self.exercise_loop('spawn')


class ActiveOrientation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.manager=RunManager(self.base)

    def create(self,name):
        ctx=run_context.RunContext.create(run_id='run-'+name,kind='wake',goal=name,
                                          principal_id='praxis:self',scope='owner')
        self.manager.create(ctx,'context');self.manager.transition(ctx.run_id,'running')
        return ctx.run_id

    def test_history_is_not_locked_or_ledger_reduced_for_orientation(self):
        old=self.create('old');self.manager.transition(old,'done')
        live=self.create('live');original=self.manager._locked;locked=[]
        @contextlib.contextmanager
        def observe(path,**kw):
            locked.append(path.name)
            with original(path,**kw):yield
        with mock.patch.object(self.manager,'_locked',side_effect=observe),\
             mock.patch.object(self.manager,'_tool_ledger_locked',side_effect=AssertionError('full ledger')):
            rows=self.manager.live_run_summaries()
        self.assertEqual([r['run_id'] for r in rows],[live]);self.assertNotIn(old,locked)

    def test_busy_live_manifest_stays_visible_as_unknown(self):
        live=self.create('busy')
        with mock.patch.object(self.manager,'_locked',side_effect=TimeoutError('busy')):
            rows=self.manager.live_run_summaries()
        self.assertEqual(rows[0]['run_id'],live);self.assertEqual(rows[0]['status'],'unknown')
        self.assertIn('busy',rows[0]['observation_error'])

    def test_wal_only_reopen_is_not_hidden(self):
        live=self.create('reopen');before=self.manager.manifest(live)
        self.manager.transition(live,'done');self.manager.live_run_summaries()
        # Simulate a crash after a valid reversible guard append, before publish.
        terminal=self.manager.manifest(live);directory=self.manager.path(live)
        row={'seq':terminal['event_seq']+1,'kind':'status_changed','at':before['updated_at'],
             'from_status':'done','to_status':'running'}
        with (directory/'events.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        self.assertIn(live,[r['run_id'] for r in self.manager.live_run_summaries()])

    def test_tool_includes_coding_tasks_even_without_durable_runs(self):
        with mock.patch.object(agent,'_RUN_MANAGER',self.manager),\
             mock.patch.object(agent,'_coding_tasks_line',return_value='\nCoding tasks: one'):
            value=agent.tool_list_active_runs()
        self.assertIn('Coding tasks: one',value)
