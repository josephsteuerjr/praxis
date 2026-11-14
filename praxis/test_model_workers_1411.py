"""Real private pipes, SDK loopback and process lifetime; no paid calls."""
import concurrent.futures
import dataclasses
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import llm
import model_step
import step_control
import test_step_control_1311 as fixture


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux provider custody')
class WarmWorkers(unittest.TestCase):
    setUp = fixture.ForceControl.setUp
    stop_all = fixture.ForceControl.stop_all
    cancel = fixture.ForceControl.cancel
    wait_for = fixture.ForceControl.wait_for
    provider = fixture.ForceControl.provider
    kwargs = fixture.ForceControl.kwargs

    def invoke(self, config, **kwargs):
        return model_step.call('openai', 'fixture', self.kwargs(), config,
            run_id=self.ctx.run_id, base=self.base, **kwargs)

    def pool(self):return model_step._pools[(str(self.base.resolve()), self.ctx.run_id)]

    def test_twenty_warm_calls_reuse_one_generation_and_clean_terminal(self):
        with self.provider() as (config, bodies, arrived), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            self.assertEqual(self.invoke(config).text, 'answer')
            first = self.pool().workers[0]
            for _ in range(20):self.assertEqual(self.invoke(config).text, 'answer')
            self.assertEqual(self.pool().workers, [first])
            self.assertEqual(first.state, 'idle')
            self.assertEqual(len(bodies), 21)
            self.manager.transition(self.ctx.run_id, 'done')
            self.wait_for(lambda: first.poll() is not None)
            self.assertNotIn((str(self.base.resolve()), self.ctx.run_id), model_step._pools)

    def test_new_config_and_model_seen_without_restarting_generation(self):
        with self.provider() as (one, bodies1, _), self.provider() as (two, bodies2, _), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            self.invoke(one)
            first = self.pool().workers[0]
            two['frameworks']['openai']['api_key'] = 'second-fixture-key'
            model_step.call('openai', 'changed-model', self.kwargs(), two,
                run_id=self.ctx.run_id, base=self.base)
            self.assertIs(self.pool().workers[0], first)
            self.assertEqual(len(bodies1), 1)
            self.assertEqual(bodies2[0]['model'], 'changed-model')

    def test_parallel_same_run_not_serialized_and_both_cancel(self):
        with self.provider(hang=True) as (config, bodies, arrived), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                calls = [executor.submit(self.invoke, config) for _ in range(2)]
                self.wait_for(lambda: len(bodies) == 2, 8)
                self.assertEqual(len(self.pool().workers), 2)
                self.assertEqual(len({w.generation for w in self.pool().workers}), 2)
                self.cancel()
                for call in calls:
                    with self.assertRaises(step_control.StepCancelled):call.result(2)

    def test_cancel_idle_closes_admission_and_preserves_other_run(self):
        with self.provider() as (config, bodies, _), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            self.invoke(config)
            first = self.pool().workers[0]
            other = 'run-other-fixture'
            self.addCleanup(lambda: model_step.close_run(other, self.base))
            model_step.call('openai', 'fixture', self.kwargs(), config, run_id=other, base=self.base)
            neighbor = model_step._pools[(str(self.base.resolve()), other)].workers[0]
            self.cancel()
            self.wait_for(lambda: first.poll() is not None)
            self.assertIsNone(neighbor.poll())
            with self.assertRaises(step_control.StepCancelled):self.invoke(config)
            self.assertEqual(len(bodies), 2)

    def test_cancel_during_spawn_publication_never_acknowledges_early(self):
        created, release = threading.Event(), threading.Event()
        original = model_step.Worker.__init__
        def hold(worker, pool):
            original(worker, pool)
            created.set();release.wait(8)
        self.addCleanup(release.set)
        with self.provider() as (config, bodies, _), patch.dict(os.environ, {'PRAXIS_TEST':''}), patch.object(model_step.Worker, '__init__', hold):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                call = executor.submit(self.invoke, config)
                self.assertTrue(created.wait(4))
                self.cancel()
                receipt = self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
                self.wait_for(receipt.exists)
                self.assertFalse(json.loads(receipt.read_text())['ok'])
                release.set()
                with self.assertRaises(step_control.StepCancelled):call.result(3)
                self.wait_for(lambda: receipt.exists() and json.loads(receipt.read_text())['ok'])
                self.assertEqual(bodies, [])

    def test_worker_death_has_unknown_outcome_and_never_replays(self):
        with self.provider(hang=True) as (config, bodies, arrived), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                call = executor.submit(self.invoke, config)
                self.assertTrue(arrived.wait(6))
                self.pool().workers[0].process.request_stop()
                with self.assertRaises(model_step.ProviderOutcomeUnknown):call.result(3)
                time.sleep(.1)
                self.assertEqual(len(bodies), 1)

    def test_stale_generation_response_retires_lease_without_replay(self):
        with self.provider() as (config, bodies, _), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            self.invoke(config)
            with patch.object(model_step, '_read', return_value={'version':-1}):
                with self.assertRaises(model_step.ProviderOutcomeUnknown):self.invoke(config)
            self.assertLessEqual(len(bodies), 2)
            self.assertNotIn((str(self.base.resolve()), self.ctx.run_id), model_step._pools)

    def test_paused_idle_worker_reaped(self):
        with self.provider() as (config, _, _), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            self.invoke(config)
            first = self.pool().workers[0]
            self.manager.request_pause(self.ctx.run_id, actor='fixture')
            self.wait_for(lambda: first.poll() is not None)

    def test_parent_death_lifeline_ends_provider_tree(self):
        with self.provider() as (config, _, _), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            code = ("import json,model_step,time;from pathlib import Path;"
                "model_step.call('openai','fixture',%r,%r,run_id='run-lifeline',base=%r);"
                "w=model_step._pools[(str(Path(%r).resolve()),'run-lifeline')].workers[0];"
                "print(w.pid,flush=True);time.sleep(60)") % (self.kwargs(), config, str(self.base), str(self.base))
            parent = subprocess.Popen([sys.executable, '-B', '-c', code], stdout=subprocess.PIPE, text=True)
            self.addCleanup(lambda: parent.poll() is None and (parent.kill(), parent.wait()))
            helper = int(parent.stdout.readline())
            fd = os.pidfd_open(helper)
            try:
                parent.kill();parent.wait(3)
                self.wait_for(lambda: bool(select.select([fd], [], [], 0)[0]), 4)
            finally:os.close(fd);parent.stdout.close()

    def test_slow_stop_does_not_delay_signalling_other_resources(self):
        release = threading.Event()
        self.addCleanup(release.set)
        class Slow:
            pid = 0
            def stop_tree(self):release.wait(5);return True, 'fixture ended'
            def poll(self):return 0 if release.is_set() else None
        self.registry.track(self.ctx.run_id, Slow())
        from command_process import CommandProcess
        with open(os.devnull, 'wb') as out:
            process = CommandProcess([sys.executable, '-c', 'import time;time.sleep(60)'], stdout=out, stderr=out)
        self.handles.append(process)
        self.registry.track(self.ctx.run_id, process)
        start = time.monotonic();self.cancel()
        self.wait_for(lambda: process.poll() is not None, 1.5)
        self.assertLess(time.monotonic()-start, .5)
        receipt = self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        self.wait_for(receipt.exists)
        self.assertFalse(json.loads(receipt.read_text())['ok'])
        release.set()
        self.wait_for(lambda: json.loads(receipt.read_text())['ok'])

    def test_repeated_request_has_new_identity_and_owned_settlement(self):
        with self.provider() as (config, _, _), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            self.invoke(config);self.cancel()
            directory = self.base/'memory/.control/run-cancel'
            receipt = directory/(self.ctx.run_id+'.receipt')
            self.wait_for(lambda: receipt.exists() and json.loads(receipt.read_text())['ok'])
            step_control._write(directory/(self.ctx.run_id+'.json'),
                {'id':'second-reset', 'run_id':self.ctx.run_id, 'action':'cancel'})
            self.wait_for(lambda: json.loads(receipt.read_text()).get('request_id') == 'second-reset')
            self.assertTrue(json.loads(receipt.read_text())['ok'])

    def test_reset_during_stream_does_not_return_partial_as_success(self):
        with self.provider(streaming=True) as (config, bodies, arrived), patch.dict(os.environ, {'PRAXIS_TEST':''}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                call=executor.submit(self.invoke,config)
                self.assertTrue(arrived.wait(6))
                start=time.monotonic();self.cancel()
                with self.assertRaises(step_control.StepCancelled):call.result(1.5)
                self.assertLess(time.monotonic()-start,.5)
                self.assertEqual(len(bodies),1)

    def test_reset_after_response_before_parent_admission_rejects_late_result(self):
        received, release=threading.Event(),threading.Event()
        original=model_step.Worker.exchange
        def hold(worker,request):
            result=original(worker,request)
            received.set();release.wait(8)
            return result
        self.addCleanup(release.set)
        with self.provider() as (config, bodies, _), patch.dict(os.environ, {'PRAXIS_TEST':''}), patch.object(model_step.Worker,'exchange',hold):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                call=executor.submit(self.invoke,config)
                self.assertTrue(received.wait(6));self.cancel();release.set()
                with self.assertRaises(step_control.StepCancelled):call.result(2)
                self.assertEqual(len(bodies),1)

    def test_explicit_background_forge_context_outlives_origin_turn(self):
        import run_context
        background=dataclasses.replace(self.ctx,forge_task_id='code-fixture')
        self.manager.transition(self.ctx.run_id,'done')
        with self.provider() as (config,bodies,_),patch.dict(os.environ,{'PRAXIS_TEST':''}),run_context.bind_run(background):
            self.assertEqual(self.invoke(config).text,'answer')
            pool=model_step._pools[(str(self.base.resolve()),self.ctx.run_id,'code-fixture')]
            worker=pool.workers[0]
            self.assertIsNone(worker.poll())
            self.assertEqual(self.invoke(config).text,'answer')
            self.assertEqual(pool.workers,[worker])
            self.assertEqual(len(bodies),2)
            model_step.close_run(self.ctx.run_id,self.base)
            self.assertIsNotNone(worker.poll())

    def test_another_owner_reservation_prevents_early_green(self):
        from command_process import CommandProcess
        other=step_control.Registry(self.base);other.close()
        self.addCleanup(other.close)
        with open(os.devnull,'wb') as out:
            held=CommandProcess([sys.executable,'-c','import time;time.sleep(60)'],stdout=out,stderr=out)
            selected=CommandProcess([sys.executable,'-c','import time;time.sleep(60)'],stdout=out,stderr=out)
        self.handles.extend([held,selected]);other.track(self.ctx.run_id,held)
        self.registry.track(self.ctx.run_id,selected);self.cancel()
        receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        self.wait_for(lambda:selected.poll() is not None and receipt.exists())
        self.assertIsNone(held.poll())
        self.assertFalse(json.loads(receipt.read_text())['ok'])
        self.assertTrue(json.loads(receipt.read_text())['pending_resources'])
        self.wait_for(lambda:(other.once() or json.loads(receipt.read_text())['ok']))
        self.assertIsNotNone(held.poll())
        self.assertEqual(len(json.loads(receipt.read_text())['processes']),2)

    def test_failed_reservation_publication_does_not_leave_running_phantom(self):
        original=step_control.track
        def fail_after_publication(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('fixture publication failure')
        with patch.object(step_control,'track',fail_after_publication):
            with self.assertRaises(OSError):
                with step_control.starting(self.ctx.run_id,self.base):self.fail('effect was admitted')
        self.wait_for(lambda:not list(self.registry.resource_directory(self.ctx.run_id).glob('*.json')))

    def test_prior_control_conflict_does_not_delay_owned_process_signal(self):
        from command_process import CommandProcess
        self.manager.start_tool(self.ctx.run_id,'pending-fixture','fixture',{},side_effect=True)
        self.manager.request_cancel(self.ctx.run_id,actor='fixture:prior',reason='prior immutable cancellation')
        with open(os.devnull,'wb') as out:
            process=CommandProcess([sys.executable,'-c','import time;time.sleep(60)'],stdout=out,stderr=out)
        self.handles.append(process);self.registry.track(self.ctx.run_id,process)
        self.cancel();self.wait_for(lambda:process.poll() is not None,1.5)
        manifest=self.manager.manifest(self.ctx.run_id)
        self.assertEqual(manifest['control']['requested_by'],'fixture:prior')
        receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        self.wait_for(receipt.exists)
        self.assertFalse(json.loads(receipt.read_text())['ok'])
        self.assertEqual(json.loads(receipt.read_text())['error'],'RunConflict')

    def test_real_foreign_executor_must_attest_its_resource(self):
        from command_process import CommandProcess
        code=r'''
import json,sys,time
from pathlib import Path
import step_control
from command_process import CommandProcess
base=Path(sys.argv[1]);run=sys.argv[2]
owner=step_control.Registry(base);owner.close()
process=CommandProcess([sys.executable,'-c','import time;time.sleep(60)'],stdout=__import__('subprocess').DEVNULL,stderr=__import__('subprocess').DEVNULL,parent_lifeline=True)
owner.track(run,process);(base/'foreign-ready').write_text('ready')
try:
 end=time.monotonic()+10
 while not (base/'foreign-release').exists() and time.monotonic()<end:time.sleep(.01)
 while time.monotonic()<end:
  owner.once()
  receipt=base/'memory/.control/run-cancel'/(run+'.receipt')
  if receipt.exists() and json.loads(receipt.read_text()).get('ok'):break
  time.sleep(.02)
 assert process.poll() is not None
finally:process.stop_tree();owner.close()
'''
        with open(os.devnull,'wb') as out:
            foreign=CommandProcess([sys.executable,'-B','-c',code,str(self.base),self.ctx.run_id],stdout=out,stderr=out)
            selected=CommandProcess([sys.executable,'-c','import time;time.sleep(60)'],stdout=out,stderr=out)
        self.handles.extend([foreign,selected])
        self.wait_for((self.base/'foreign-ready').exists)
        self.registry.track(self.ctx.run_id,selected);self.cancel()
        receipt=self.base/'memory/.control/run-cancel'/(self.ctx.run_id+'.receipt')
        self.wait_for(lambda:selected.poll() is not None and receipt.exists())
        self.assertFalse(json.loads(receipt.read_text())['ok'])
        (self.base/'foreign-release').write_text('release')
        self.assertEqual(foreign.wait(5),0)
        self.wait_for(lambda:json.loads(receipt.read_text())['ok'])


class Framing(unittest.TestCase):
    def test_corrupt_typed_payload_and_bool_version_are_rejected(self):
        identity={'version':model_step.VERSION,'generation':'g','run_id':'run-fixture','lease':'l','attempt':'a'}
        good=dict(identity,response=dataclasses.asdict(llm.LLMResponse(text='fixture')))
        model_step._validate_result(good,identity)
        for result in (dict(good,version=True),dict(identity,response={}),dict(identity,error=None),
                       dict(good,response=dict(good['response'],text=None))):
            with self.assertRaises(ValueError):model_step._validate_result(result,identity)

    def test_partial_header_and_partial_body_are_failures(self):
        for data in (b'', b'\0'*3, model_step._encode({'x':1})[:-1]):
            with self.assertRaises(EOFError):model_step._read(io.BytesIO(data))

    def test_large_unicode_frame_roundtrip_without_context_cap(self):
        value = {'text':'\u03b1\n'*1500000, 'image':'A'*2000000}
        self.assertEqual(model_step._read(io.BytesIO(model_step._encode(value))), value)


if __name__ == '__main__':unittest.main()
