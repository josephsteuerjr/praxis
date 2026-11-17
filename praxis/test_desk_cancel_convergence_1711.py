"""Actual phone consumer, resource owner and durable pending-tool ledger."""
from dataclasses import replace
import datetime as dt
import json,os,secrets,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import mtproto_runner as runner
import run_context,run_manager,step_control


class Handle:
    pid=0;kind='test-resource'
    def __init__(self):self.ended=threading.Event()
    def stop_tree(self):self.ended.set();return True,'resource ended'
    def poll(self):return 0 if self.ended.is_set() else None


class Convergence(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.base=Path(tmp.name)
        self.manager=run_manager.RunManager(self.base)
        self.ctx=replace(run_context.RunContext.create(kind='computer',goal='fixture',principal_id='owner',scope='owner'),
            created_at=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=5)).isoformat())
        self.rid=self.ctx.run_id;self.manager.create(self.ctx,'fixture');self.manager.transition(self.rid,'running')
        self.manager.start_tool(self.rid,'pending-call','fixture.wait',{},side_effect=True)
        with patch.object(step_control.Registry,'_watch',return_value=None):self.registry=step_control.Registry(self.base)
        self.registry.thread.join(1);self.addCleanup(self.registry.closed.set)
        self.path=self.base/'memory/.control/run-cancel'/(self.rid+'.json');self.receipt=self.path.with_suffix('.receipt')
        # The signal lookup must use this actual test resource owner.
        self.enterContext(patch.object(step_control,'start',return_value=self.registry))
        self.enterContext(patch.object(runner.agent,'_runs',return_value=self.manager))

    def request(self,reason='phone Stop'):
        return {'id':secrets.token_hex(12),'scope':self.rid,'by':'phone','reason':reason,
                'at':dt.datetime.now(dt.timezone.utc).isoformat()}

    def owner_wire(self,reason):
        # Exact existing /api/run/{id}/cancel wire format, with no mock manager.
        value={'id':secrets.token_hex(12),'run_id':self.rid,'action':'cancel','by':'desk:owner','reason':reason,'at':time.time()}
        self.path.parent.mkdir(parents=True,exist_ok=True);self.path.write_text(json.dumps(value))
        return value

    def tick(self):
        self.registry.once()
        for handle in self.registry.handles.get(self.rid,[]):
            if isinstance(handle,Handle):handle.ended.wait(1)
        self.registry.once()
        return json.loads(self.receipt.read_bytes()) if self.receipt.exists() else {}

    def scenario(self,first):
        handle=self.registry.track(self.rid,Handle());canonical=self.owner_wire('phone Stop')
        if first=='registry':self.assertTrue(self.tick()['ok'])
        observed=runner._apply_desk_interrupt(self.request());self.assertEqual(observed['skipped'],[])
        receipt=self.tick();self.assertTrue(receipt['ok']);self.assertTrue(handle.ended.is_set())
        self.assertEqual(receipt['request_id'],canonical['id'])
        self.assertEqual(self.manager.manifest(self.rid)['control']['requested_by'],'desk:owner')
        again=runner._apply_desk_interrupt(self.request('different gesture label'))
        self.assertEqual(again['skipped'],[]);self.assertTrue(self.tick()['ok'])
        self.assertEqual(json.loads(self.path.read_bytes())['id'],canonical['id'])
        self.assertEqual(self.manager.manifest(self.rid)['status'],'paused')
        events=list(self.manager.events(self.rid))
        audit=[r for r in events if r['kind']=='desk_cancel_observed']
        self.assertEqual(audit[-1]['source']['by'],'phone');self.assertEqual(audit[-1]['source']['reason'],'different gesture label')
        self.manager.store_result(self.rid,'actual completion',call_id='pending-call');self.tick()
        self.assertEqual(self.manager.manifest(self.rid)['status'],'cancelled')

    def test_consumer_first_with_existing_owner_wire(self):self.scenario('consumer')
    def test_registry_first_with_existing_owner_wire(self):self.scenario('registry')

    def test_first_reset_reason_is_immutable(self):
        self.owner_wire('first owner reason');self.registry.track(self.rid,Handle());self.tick()
        result=runner._apply_desk_interrupt(self.request('later phone reason'))
        self.assertEqual(result['skipped'],[]);self.assertTrue(self.tick()['ok'])
        self.assertEqual(self.manager.manifest(self.rid)['control']['reason'],'first owner reason')

    def test_empty_observer_cannot_confirm_and_late_handle_stops(self):
        self.assertEqual(runner._apply_desk_interrupt(self.request())['skipped'],[])
        self.assertFalse(self.tick());handle=self.registry.track(self.rid,Handle())
        self.assertTrue(self.tick()['ok']);self.assertTrue(handle.ended.is_set())

    def test_stale_or_unbound_request_never_cancels_new_run(self):
        for stamp in ('2000-01-01T00:00:00Z','invalid',''):
            request=self.request();request['at']=stamp;runner._apply_desk_interrupt(request)
            self.assertFalse(self.path.exists());self.assertEqual(self.manager.manifest(self.rid)['status'],'running')

    def test_unrelated_pending_control_still_conflicts_but_signals_fan_out(self):
        handle=self.registry.track(self.rid,Handle())
        self.manager.request_cancel(self.rid,actor='other-authority',reason='another decision')
        result=runner._apply_desk_interrupt(self.request())
        self.assertTrue(handle.ended.wait(1));self.assertIn('RunConflict',str(result['skipped']))
        self.assertEqual(self.manager.manifest(self.rid)['control']['requested_by'],'other-authority')

    @unittest.skipUnless(sys.platform.startswith('linux'),'Real Linux process custody integration')
    def test_selected_process_stops_and_sibling_survives(self):
        from command_process import CommandProcess
        with open(os.devnull,'wb') as output:
            selected=CommandProcess([sys.executable,'-c','import time;time.sleep(90)'],stdout=output,stderr=output)
            self.addCleanup(selected.stop_tree)
            sibling=CommandProcess([sys.executable,'-c','import time;time.sleep(90)'],stdout=output,stderr=output)
            self.addCleanup(sibling.stop_tree)
        self.registry.track(self.rid,selected)
        self.assertEqual(runner._apply_desk_interrupt(self.request())['skipped'],[])
        deadline=time.monotonic()+5;receipt={}
        while time.monotonic()<deadline:
            receipt=self.tick()
            if receipt.get('ok') and selected.poll() is not None:break
            time.sleep(.02)
        self.assertTrue(receipt.get('ok'));self.assertIsNotNone(selected.poll());self.assertIsNone(sibling.poll())
        self.assertEqual(self.manager.manifest(self.rid)['status'],'paused')
