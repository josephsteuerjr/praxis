"""One real device gesture across both consumers, ledger and resource census."""
from dataclasses import replace
import datetime as dt
import json
from pathlib import Path
import sys
import os
import time
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'helene/core'), str(ROOT/'praxis'), str(ROOT/'desk'),str(ROOT/'desk/localharness')]
from run_context import RunContext
from run_manager import RunManager, RunConflict
import step_control, control_watch
from deskd import control, run_control


class Handle:
    pid=0;kind='test-resource'
    def __init__(self):self.ended=threading.Event()
    def stop_tree(self):self.ended.set();return True,'resource ended'
    def poll(self):return 0 if self.ended.is_set() else None


class Convergence(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.manager=RunManager(self.root)
        self.ctx=replace(RunContext.create(kind='computer',goal='fixture',principal_id='owner',scope='owner'),
            created_at=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=5)).isoformat())
        self.run=self.ctx.run_id;self.manager.create(self.ctx,'fixture');self.manager.transition(self.run,'running')
        self.manager.start_tool(self.run,'call-fixture','fixture.wait',{},side_effect=True)
        with patch.object(step_control.Registry,'_watch',return_value=None):
            self.registry=step_control.Registry(self.root)
        self.registry.thread.join(1);self.addCleanup(self.registry.closed.set)
        self.path=self.root/'memory/.control/run-cancel'/(self.run+'.json')
        self.receipt=self.path.with_suffix('.receipt')

    def consume(self):return control_watch.consume(self.root,self.manager,('running','paused','blocked','in_doubt'))
    def tick(self):
        self.registry.once()
        for handle in self.registry.handles.get(self.run,[]):handle.ended.wait(1)
        self.registry.once()
        return json.loads(self.receipt.read_bytes())

    def scenario(self,first):
        handle=self.registry.track(self.run,Handle())
        gesture=control.interrupt(self.root,by='device',scope=self.run,reason='device Stop')['request']
        if first=='registry':
            run_control.known_run(self.root,self.run,'device Stop')
            self.tick()
        observed=self.consume();self.assertFalse(observed['failed'])
        receipt=self.tick();self.assertTrue(receipt['ok']);self.assertTrue(handle.ended.is_set())
        canonical=json.loads(self.path.read_bytes());self.assertEqual(receipt['request_id'],canonical['id'])
        self.assertEqual(observed['source']['by'],'device')
        self.assertEqual(observed['cancel_requests'][self.run],canonical['id'])
        again=control.interrupt(self.root,by='device',scope=self.run,reason='another label')['request']
        self.assertNotEqual(gesture['id'],again['id'])
        self.assertFalse(self.consume()['failed']);self.assertTrue(self.tick()['ok'])
        self.assertEqual(json.loads(self.path.read_bytes())['id'],canonical['id'])
        self.assertEqual(self.manager.manifest(self.run)['status'],'paused')
        self.manager.store_result(self.run,'finished',call_id='call-fixture')
        self.tick();self.assertEqual(self.manager.manifest(self.run)['status'],'cancelled')

    def test_consumer_first_pending_tool_repeated_gesture_and_settled_result(self):self.scenario('consumer')
    def test_registry_first_pending_tool_repeated_gesture_and_settled_result(self):self.scenario('registry')

    def test_direct_reset_first_keeps_canonical_reason_and_device_provenance(self):
        _,canonical=run_control.request(self.root,self.run,'first reason')
        self.registry.track(self.run,Handle());self.tick()
        control.interrupt(self.root,by='device',scope=self.run,reason='second reason')
        observed=self.consume();self.assertFalse(observed['failed']);self.assertTrue(self.tick()['ok'])
        self.assertEqual(self.manager.manifest(self.run)['control']['reason'],'first reason')
        manifest_path=next((self.root/'memory/runs').glob('*/'+self.run+'/events.jsonl'))
        events=[json.loads(row) for row in manifest_path.read_text(encoding='utf8').splitlines()]
        audit=[row for row in events if row['kind']=='desk_cancel_observed']
        self.assertEqual(audit[-1]['cancel_request_id'],canonical['id'])
        self.assertEqual(audit[-1]['source']['by'],'device')
        self.assertEqual(audit[-1]['source']['reason'],'second reason')

    def test_observer_cannot_confirm_empty_census_late_handle_is_stopped(self):
        control.interrupt(self.root,by='device',scope=self.run)
        self.assertFalse(self.consume()['failed']);self.registry.once()
        self.assertFalse(self.receipt.exists())
        handle=self.registry.track(self.run,Handle());self.assertTrue(self.tick()['ok'])
        self.assertTrue(handle.ended.is_set())

    def test_distinct_pending_cancellation_still_conflicts(self):
        self.manager.request_cancel(self.run,actor='other-authority',reason='different decision')
        with self.assertRaises(RunConflict):self.manager.request_cancel(self.run,actor='desk:owner',reason='device Stop')

    def test_old_gesture_never_cancels_newer_run(self):
        control.interrupt(self.root,by='device',scope=self.run)
        path=self.root/'memory/.control/interrupt.json';value=json.loads(path.read_bytes())
        value['at']='2000-01-01T00:00:00Z';path.write_text(json.dumps(value),encoding='utf8')
        observed=self.consume();self.assertEqual(observed['requested'],[])
        self.assertFalse(self.path.exists());self.assertEqual(self.manager.manifest(self.run)['status'],'running')

    @unittest.skipUnless(sys.platform.startswith('linux'),'Real Linux process custody integration')
    def test_device_stop_really_ends_selected_process_and_preserves_sibling(self):
        from command_process import CommandProcess
        with open(os.devnull,'wb') as output:
            selected=CommandProcess([sys.executable,'-c','import time; time.sleep(90)'],stdout=output,stderr=output)
            self.addCleanup(selected.stop_tree)
            sibling=CommandProcess([sys.executable,'-c','import time; time.sleep(90)'],stdout=output,stderr=output)
            self.addCleanup(sibling.stop_tree)
        self.registry.track(self.run,selected)
        control.interrupt(self.root,by='device',scope=self.run,reason='device Stop')
        self.assertFalse(self.consume()['failed'])
        deadline=time.monotonic()+5
        receipt={}
        while time.monotonic()<deadline:
            self.registry.once()
            if self.receipt.exists():receipt=json.loads(self.receipt.read_bytes())
            if receipt.get('ok') and selected.poll() is not None:break
            time.sleep(.02)
        self.assertTrue(receipt.get('ok'));self.assertIsNotNone(selected.poll())
        self.assertIsNone(sibling.poll());self.assertEqual(self.manager.manifest(self.run)['status'],'paused')
