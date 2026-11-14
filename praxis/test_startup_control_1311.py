import asyncio
import inspect
import os
from pathlib import Path
import tempfile
import time
import json
import unittest
from unittest.mock import patch

os.environ.setdefault('TELEGRAM_API_ID','1')
os.environ.setdefault('TELEGRAM_API_HASH','fixture')
os.environ.setdefault('TELEGRAM_SESSION',str(Path(tempfile.gettempdir())/'praxis-control-fixture'))
import mtproto_runner as runner
from run_context import RunContext
from run_manager import RunManager
import bufstore
import unanswered
import step_control


class StartupControl(unittest.IsolatedAsyncioTestCase):
    async def test_new_run_is_not_recovered_as_an_orphan(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager=RunManager(temporary)
            def begin():
                ctx=RunContext.create(kind='chat_turn',goal='fixture',principal_id='owner',scope='owner')
                manager.create(ctx,'fixture');manager.transition(ctx.run_id,'running');return ctx.run_id
            old=begin();startup_run_ids=tuple(manager.run_ids());fresh=begin()
            reports=manager.recover(run_ids=startup_run_ids)
            self.assertEqual(manager.manifest(old)['status'],'paused')
            self.assertEqual(manager.manifest(fresh)['status'],'running')
            self.assertEqual({r['run_id'] for r in reports},{old})
            self.assertEqual(manager.recover(run_ids=()),[])
            self.assertEqual(manager.manifest(fresh)['status'],'running')

    async def test_post_recovery_does_not_inspect_new_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager=RunManager(temporary)
            ctx=RunContext.create(kind='chat_turn',goal='fresh',principal_id='owner',scope='owner')
            manager.create(ctx,'fixture');manager.transition(ctx.run_id,'running')
            with patch.object(runner.agent,'_runs',return_value=manager),patch.object(runner.agent.work_store,'recover',return_value=[]),patch.object(runner.agent,'run_delivery_finalize_recovered',side_effect=AssertionError('fresh run inspected')):
                self.assertEqual(runner.agent.recover_durable_state(run_ids=()),[])
            self.assertEqual(manager.manifest(ctx.run_id)['status'],'running')

    async def test_direct_entrypoint_stays_stopped_until_explicit_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            sentinel = Path(temporary)/'.panic'
            sentinel.write_text('owner stop')
            with patch.object(runner.agent,'PANIC_SENTINEL',sentinel):
                task = asyncio.create_task(runner._wait_panic_release())
                await asyncio.sleep(.03)
                self.assertFalse(task.done())
                sentinel.unlink()
                await asyncio.wait_for(task,1)

    def test_startup_captures_recovery_scope_before_connect(self):
        main = inspect.getsource(runner.main)
        self.assertLess(main.index('startup_run_ids ='),main.index('client.connect()'))
        self.assertIn('agent.recover_durable_state, run_ids=startup_run_ids',main)

    def test_reset_input_is_not_rearmed_but_a_new_message_remains_eligible(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);now=time.time();manager=RunManager(base)
            ctx=RunContext.create(kind='chat_turn',goal='fixture',principal_id='owner',scope='owner',origin_chat_id='42')
            manager.create(ctx,'fixture')
            buffers=base/'memory/.buffers';buffers.mkdir(parents=True)
            history=buffers/'42.json';history.write_text(json.dumps(['Human: old input']))
            state=base/'memory/.state';state.mkdir(parents=True,exist_ok=True)
            meta=state/'buf_meta.json';meta.write_text(json.dumps({'42':{'is_dm':True,'last_author':'Human','last_ts':now-10}}))
            pending=state/'unanswered.json';pending.write_text(json.dumps({'42':{'name':'Human','since':now-20}}))
            with patch.multiple(bufstore,BASE=base,BUF_DIR=buffers,STATE_DIR=state,META_PATH=meta),patch.multiple(unanswered,BASE=base,STATE_DIR=state,PATH=pending):
                self.assertEqual(len(bufstore.missed_dm_candidates(now=now+1000)),1)
                self.assertEqual(len(unanswered.entries(now=now+1000)),1)
                step_control._write(base/'memory/.control/run-cancel'/(ctx.run_id+'.json'),{'id':'fixture','action':'cancel','run_id':ctx.run_id})
                before=(history.read_bytes(),meta.read_bytes(),pending.read_bytes())
                self.assertEqual(bufstore.missed_dm_candidates(now=now+1000),[])
                self.assertEqual(unanswered.entries(now=now+1000),[])
                self.assertEqual(before,(history.read_bytes(),meta.read_bytes(),pending.read_bytes()))
                meta.write_text(json.dumps({'42':{'is_dm':True,'last_author':'Human','last_ts':now+1}}))
                self.assertEqual(len(bufstore.missed_dm_candidates(now=now+1000)),1)
                self.assertEqual(len(unanswered.entries(now=now+1000)),1)


if __name__ == '__main__':
    unittest.main()
