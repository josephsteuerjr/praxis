"""Real child -> live parent IPC, durable effects, context and custody."""
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import agent
import forge
import run_context
import run_manager


class EndpointAuthority(unittest.TestCase):
    def test_only_current_live_exact_endpoint_can_enter_a_fence(self):
        import hand_bridge as bridge
        active = SimpleNamespace(path=str(Path(tempfile.gettempdir()).resolve()/'endpoint-test'),
                                 token='fixture-token',closed=False)
        env = {bridge.ENV:json.dumps(dict(socket=active.path,token=active.token,transport='unix'))}
        self.assertIsNone(bridge.unix_endpoint(env))
        token = bridge._ACTIVE.set(active)
        try:
            self.assertEqual(bridge.unix_endpoint(env),active.path)
            for key,value in [('socket',active.path+'-foreign'),('token','wrong'),('transport','pipe')]:
                row=json.loads(env[bridge.ENV]);row[key]=value
                self.assertIsNone(bridge.unix_endpoint({bridge.ENV:json.dumps(row)}))
            active.closed=True
            self.assertIsNone(bridge.unix_endpoint(env))
        finally:bridge._ACTIVE.reset(token)


class LiveHands(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.manager=run_manager.RunManager(self.root)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        for target,name,value in [(agent,'BASE',self.root),(agent,'_RUN_MANAGER',self.manager),
                                  (agent,'TOOL_CEILING_SEC',0)]:
            self.stack.enter_context(patch.object(target,name,value))
        self.stack.enter_context(patch.object(agent,'_pre_shell_dirt',return_value={}))
        self.stack.enter_context(patch.object(agent,'_autocommit_self_edit'))
        self.stack.enter_context(patch.object(agent.selfgit,'safety_point'))
        self.channel=agent.ChannelContext(principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                                         owner=False,known=False,is_dm=True,_scope_override='owner')
        current=run_context.RunContext.create(kind='chat_turn',goal='IPC fixture',
                    principal_id=agent.PRAXIS_SELF_PRINCIPAL,scope='owner')
        self.manager.create(current,'fixture')
        self.manager.transition(current.run_id,'running',expected='pending')
        self.current=self.manager.context(current.run_id)
        self.silence={};self.outbound=[]
        self.catalog={}
        self.calls=[]
        self.stack.enter_context(patch.dict(agent.TOOL_IMPL,{'fixture':self.echo}))
        self.catalog['fixture']={'name':'fixture','input_schema':{'type':'object'}}
        self.stack.enter_context(run_context.bind_run(self.current))
        for variable,value in [(agent._TURN_CHANNEL,self.channel),(agent._TOOL_CATALOG,self.catalog),
                               (agent._TURN_SILENCE,self.silence),(agent._TURN_OUTBOUND,self.outbound),
                               (agent._TURN_MEDIA_GUARD,{})]:
            token=variable.set(value);self.stack.callback(variable.reset,token)

    def echo(self,value=''):
        self.calls.append(value)
        return json.dumps({'value':value,'principal':run_context.current_run().principal_id,
                           'channel':agent._TURN_CHANNEL.get().principal_id})

    def call(self,code,**kwargs):
        outer='outer-'+uuid.uuid4().hex
        args=dict(code=code,cwd=str(self.root),timeout=8,**kwargs)
        if kwargs.get('task_id'):args['cwd']='.'
        self.manager.start_tool(self.current.run_id,outer,'python',args,side_effect=True)
        self.last_execution={'run_id':self.current.run_id,'call_id':outer,'tool':'python','args':args}
        token=agent._TOOL_EXECUTION.set(self.last_execution)
        try:return agent._call_tool_with_ceiling('python',agent.tool_python,args)
        finally:agent._TOOL_EXECUTION.reset(token)

    def nested(self):
        return [x for x in self.manager.events(self.current.run_id) if x.get('kind')=='tool_started' and x.get('call_id','').startswith('hand-')]

    def test_real_child_uses_parent_context_and_records_one_effect_for_exact_retry(self):
        out=self.call("a=h.hand('fixture',value='hello',_request_id='same');b=h.hand('fixture',value='hello',_request_id='same');assert a==b;print(a['content'])")
        self.assertIn(agent.PRAXIS_SELF_PRINCIPAL,str(out));self.assertEqual(self.calls,['hello'])
        rows=self.nested();self.assertEqual(len(rows),1);self.assertTrue(rows[0]['side_effect'])
        self.assertEqual(rows[0]['args'],{'value':'hello'})
        self.assertTrue(any(x.get('kind')=='tool_result' and x.get('call_id')==rows[0]['call_id'] for x in self.manager.events(self.current.run_id)))

    def test_same_request_id_different_intent_refuses_before_second_effect(self):
        out=self.call("h.hand('fixture',value='first',_request_id='same')\ntry:h.hand('fixture',value='second',_request_id='same')\nexcept h.Refused:print('CONFLICT')")
        self.assertIn('CONFLICT',str(out));self.assertEqual(self.calls,['first'])

    def test_unoffered_hand_and_bad_arguments_never_execute(self):
        self.catalog.clear()
        out=self.call("try:h.hand('fixture',value='no')\nexcept h.Refused:print('NOT-OFFERED')")
        self.assertIn('NOT-OFFERED',str(out));self.assertEqual(self.calls,[]);self.assertEqual(self.nested(),[])
        self.catalog['fixture']={'name':'fixture'}
        out=self.call("try:h.hand('fixture',unknown='no')\nexcept h.Refused:print('BAD-ARGS')")
        self.assertIn('BAD-ARGS',str(out));self.assertEqual(self.nested(),[])

    def test_describe_reuses_the_exact_bound_catalogue(self):
        out=self.call("r=h.describe('fixture');print(r['content'])")
        self.assertIn('fixture',str(out));self.assertIn('input_schema',str(out))
        self.assertEqual(self.nested()[0]['tool'],'describe')

    def test_bad_socket_token_does_not_start_a_call(self):
        out=self.call("import os,json\nx=json.loads(os.environ['PRAXIS_HAND_BRIDGE']);x['token']='wrong';os.environ['PRAXIS_HAND_BRIDGE']=json.dumps(x)\ntry:h.hand('fixture')\nexcept h.Refused:print('AUTH-REFUSED')")
        self.assertIn('AUTH-REFUSED',str(out));self.assertEqual(self.nested(),[])

    def test_lost_response_reuses_receipt_without_reexecuting_effect(self):
        code="""import socket,struct,json,os
endpoint=json.loads(os.environ['PRAXIS_HAND_BRIDGE'])
payload=json.dumps(dict(token=endpoint['token'],request_id='lost',name='fixture',args={'value':'once'})).encode()
if endpoint.get('transport') == 'pipe':
    from multiprocessing.connection import Client
    with Client(endpoint['socket'], family='AF_PIPE', authkey=None) as s:s.send_bytes(payload)
else:
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.connect(endpoint['socket']);s.sendall(struct.pack('!I',len(payload))+payload)
r=h.hand('fixture',value='once',_request_id='lost');print(r['content'])
"""
        out=self.call(code);self.assertIn('once',str(out));self.assertEqual(self.calls,['once']);self.assertEqual(len(self.nested()),1)

    def test_uncertain_side_effect_is_never_replayed_and_run_is_in_doubt(self):
        def fail(value=''):
            self.calls.append(value);raise OSError('lost after effect')
        with patch.dict(agent.TOOL_IMPL,{'fixture':fail}):
            with self.assertRaises(agent.RunStopped):
                self.call("for _ in range(2):\n try:h.hand('fixture',value='once',_request_id='same')\n except h.InDoubt:pass")
        self.assertEqual(self.calls,['once']);self.assertEqual(self.manager.manifest(self.current.run_id)['status'],'in_doubt')
        self.assertIn(self.nested()[0]['call_id'],self.manager.outstanding_tools(self.current.run_id))

    def test_live_silence_holder_survives_ceiling_thread_and_nested_dispatch(self):
        self.catalog['stay_silent']={'name':'stay_silent'}
        with patch.object(agent,'TOOL_CEILING_SEC',6),patch.object(agent,'tool_journal'):
            out=self.call("print(h.hand('stay_silent',reason='fixture')['content'])")
        self.assertIn('[exit 0]',str(out));self.assertEqual(self.silence.get('chosen'),'1')

    def test_nested_images_reach_outer_observation(self):
        path=self.root/'fixture.png';path.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        pixels=({'type':'image','path':str(path),'mime':'image/png'},)
        with patch.dict(agent.TOOL_IMPL,{'fixture':lambda value='':agent.ToolObservation('pixels',pixels)}):
            out=self.call("r=h.hand('fixture');assert r['image_count']==1;print(r['content'])")
        self.assertIsInstance(out,agent.ToolObservation)
        self.assertEqual(Path(out.images[0]['path']).read_bytes(),path.read_bytes())
        meta=agent._tool_result_metadata((),self.last_execution)
        ref=self.manager.store_result(self.current.run_id,str(out),name='python',call_id=self.last_execution['call_id'],metadata=meta)
        path.unlink()
        restored=agent._resume_result_image(self.current.run_id,'python',{},ref)
        self.assertEqual(restored,out.images)
        Path(restored[0]['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(agent.DurableExecutionError,'integrity'):
            agent._resume_result_image(self.current.run_id,'python',{},ref)

    LEAVE_EARLY=("import threading,time,pathlib\nthreading.Thread(target=lambda:h.hand('fixture',value='late'),daemon=True).start()\n"
                 "p=pathlib.Path('started')\nwhile not p.exists():time.sleep(.01)")

    def _slow_until_drain(self,release_on_drain=True):
        """A nested hand that is still running when the script has already exited."""
        import hand_bridge
        marker=self.root/'started';release=threading.Event();finished=threading.Event();drained=[]
        def slow(value=''):
            self.calls.append(value);marker.write_text('started');release.wait(10);finished.set();return 'finished late'
        original=hand_bridge.Bridge.drain
        def drain(bridge,*args,**kwargs):
            drained.append(bridge.inflight)
            if release_on_drain:release.set()
            return original(bridge,*args,**kwargs)
        self.stack.enter_context(patch.dict(agent.TOOL_IMPL,{'fixture':slow}))
        self.stack.enter_context(patch.object(hand_bridge.Bridge,'drain',drain))
        self.addCleanup(lambda:(release.set(),finished.wait(3),time.sleep(.1)))
        return drained,finished

    def test_nested_call_outliving_the_script_finishes_with_its_receipt(self):
        # The script's own timeout (30 s by default) must not turn a slow nested hand into an
        # uncertain run: the hand already has a durable intent, so the outer call waits for it.
        drained,finished=self._slow_until_drain()
        out=self.call(self.LEAVE_EARLY)
        self.assertEqual(drained,[1]);self.assertTrue(finished.is_set())
        self.assertIn('[late hand fixture: completed after the script stopped waiting; outcome=unknown',str(out))
        self.assertRegex(str(out),r"read_run_result\(result='[^']+'\)")
        self.assertEqual(self.calls,['late']);self.assertEqual(len(self.nested()),1)
        self.assertEqual(self.manager.manifest(self.current.run_id)['status'],'running')
        self.assertTrue(any(x.get('kind')=='tool_result' and x.get('call_id')==self.nested()[0]['call_id']
                            for x in self.manager.events(self.current.run_id)))

    def test_nested_result_carries_the_hands_own_outcome(self):
        import tool_report
        def refuse(value=''):
            tool_report.note(outcome='refused');return 'not done'
        with patch.dict(agent.TOOL_IMPL,{'fixture':refuse}):
            out=self.call("r=h.hand('fixture');print('OUTCOME',r['outcome'],r['content'].splitlines()[0])")
        self.assertIn('OUTCOME refused not done',str(out))
        out=self.call("print('OUTCOME',h.hand('fixture',value='x')['outcome'])")
        self.assertIn('OUTCOME unknown',str(out))

    def test_late_nested_call_without_permission_to_wait_stays_explicitly_uncertain(self):
        self._slow_until_drain(release_on_drain=False)
        with patch.object(agent,'_nested_hand_may_finish',return_value=False):
            with self.assertRaises(agent.RunStopped):self.call(self.LEAVE_EARLY)
        self.assertEqual(self.manager.manifest(self.current.run_id)['status'],'in_doubt')

    def test_pause_request_stops_waiting_for_a_late_nested_hand(self):
        runtime=agent._AgentHandRuntime({'call_id':'outer-fixture'})
        self.assertTrue(agent._nested_hand_may_finish(runtime))
        self.manager.request_pause(self.current.run_id,actor='runtime:test',reason='fixture pause')
        self.assertFalse(agent._nested_hand_may_finish(runtime))

    def test_new_requests_are_refused_once_the_command_stopped_waiting(self):
        import hand_bridge
        bridge=hand_bridge.Bridge(lambda request_id,name,args:{'status':'completed','content':'never'})
        self.addCleanup(bridge.close)
        self.assertEqual(bridge.drain(lambda:True),[])
        response=bridge.invoke({'request_id':'after','name':'fixture','args':{}},os.getpid())
        self.assertEqual(response['status'],'refused')

    def test_without_live_dispatcher_the_child_has_no_agent_and_no_endpoint(self):
        out=agent.tool_python("import sys\nassert 'agent' not in sys.modules\ntry:h.hand('fixture')\nexcept h.Refused:print('NO-ENDPOINT')",cwd=str(self.root))
        self.assertIn('NO-ENDPOINT',str(out));self.assertEqual(self.calls,[])

    def test_outer_ceiling_revokes_endpoint_before_the_command_finishes(self):
        ended=self.root/'ended'
        code="import time,pathlib\ntime.sleep(.5)\ntry:h.hand('fixture',value='too late')\nexcept h.Refused:pathlib.Path('ended').write_text('refused')"
        with patch.object(agent,'TOOL_CEILING_SEC',.2),patch.object(agent,'tool_journal'):
            out=self.call(code)
            self.assertIsInstance(out,agent.ToolCeilingExpired)
            # Desktop Stop owns the command too: it cannot continue after the
            # ceiling merely to write a marker. The bridge and child both end.
            time.sleep(.7)
            self.assertFalse(ended.exists())
        self.assertEqual(self.calls,[]);self.assertEqual(self.nested(),[])

    def test_expired_outer_retains_already_returned_nested_images(self):
        path=self.root/'pixel.png';path.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        image={'type':'image','path':str(path),'mime':'image/png'}
        done=self.root/'command-ended'
        ready=self.root/'image-returned'
        real_pool=agent._futures.ThreadPoolExecutor
        class StartedPool(real_pool):
            def __init__(pool,*args,**kwargs):
                pool.outer=kwargs.get('thread_name_prefix')=='tool-python'
                super().__init__(*args,**kwargs)
            def submit(pool,*args,**kwargs):
                future=super().submit(*args,**kwargs)
                if pool.outer:
                    result=future.result
                    def after_response(timeout=None):
                        # Qualify an already-returned image, not cold interpreter
                        # startup speed under another suite's CPU/disk load.
                        deadline=time.monotonic()+8
                        while not ready.exists() and not future.done() and time.monotonic()<deadline:time.sleep(.01)
                        self.assertTrue(ready.exists(),'child never received its nested image')
                        return result(timeout)
                    future.result=after_response
                return future
        with patch.dict(agent.TOOL_IMPL,{'fixture':lambda value='':agent.ToolObservation('pixels',(image,))}), \
             patch.object(agent,'TOOL_CEILING_SEC',.7),patch.object(agent,'tool_journal'), \
             patch.object(agent._futures,'ThreadPoolExecutor',StartedPool):
            out=self.call("import pathlib,time\nh.hand('fixture')\npathlib.Path('image-returned').write_text('yes')\ntime.sleep(2)\npathlib.Path('command-ended').write_text('yes')")
            self.assertIsInstance(out,agent.ToolCeilingExpired);self.assertEqual(len(out.images),1)
            self.assertEqual(Path(out.images[0]['path']).read_bytes(),path.read_bytes())
            time.sleep(1.1)
            self.assertFalse(done.exists())

    def test_task_file_hands_inherit_verified_custody_and_refuse_other_task(self):
        (self.root/'a.txt').write_text('one\n')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'add','a.txt'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=T','-c','user.email=t@local','commit','-qm','fixture'],check=True)
        task=re.search(r'code-[0-9a-f]+',forge.start('hand fixture',target=str(self.root),isolation='direct')).group()
        for name in ['fs_read','edit']:self.catalog[name]={'name':name}
        code="""r=h.hand('fs_read',path='a.txt');assert 'one' in r['content']
text,version=h.read('a.txt')
r=h.hand('edit',action='replace',path='a.txt',old='one',new='two',expected_sha256=version)
assert 'confirmed' in r['content'],r
try:h.hand('fs_read',path='a.txt',task_id='code-foreign')
except h.Refused:print('TASK-OK')
"""
        out=self.call(code,task_id=task)
        self.assertIn('TASK-OK',str(out));self.assertEqual((self.root/'a.txt').read_text(),'two\n')
        self.assertEqual([x['args']['task_id'] for x in self.nested()],[task,task])

    def test_pause_prevents_the_next_nested_effect(self):
        def pause(value=''):
            self.calls.append(value)
            self.manager.request_pause(self.current.run_id,actor='runtime:test',reason='fixture stop')
            return 'paused'
        with patch.dict(agent.TOOL_IMPL,{'fixture':pause}):
            with self.assertRaises(agent.RunStopped):
                self.call("h.hand('fixture',value='first')\ntry:h.hand('fixture',value='forbidden')\nexcept h.InDoubt:pass")
        self.assertEqual(self.calls,['first']);self.assertEqual(len(self.nested()),1)

    def test_current_owner_revocation_is_checked_again_before_nested_effect(self):
        channel=agent.ChannelContext(principal_id='100',owner=True,known=True,is_dm=True)
        current=run_context.RunContext.create(kind='chat_turn',goal='revoked owner fixture',principal_id='100',scope='owner')
        self.manager.create(current,'fixture');self.manager.transition(current.run_id,'running',expected='pending')
        self.current=self.manager.context(current.run_id)
        token=agent._TURN_CHANNEL.set(channel)
        try:
            with run_context.bind_run(self.current),patch.object(agent.social,'owner_id',return_value='999'):
                out=self.call("try:h.hand('fixture',value='forbidden')\nexcept h.InDoubt:print('REVOKED')")
        finally:agent._TURN_CHANNEL.reset(token)
        self.assertIn('REVOKED',str(out));self.assertEqual(self.calls,[]);self.assertEqual(self.nested(),[])

    def test_critical_nested_inputs_and_outputs_use_existing_redaction(self):
        secret='unique-private-fixture-secret'
        self.catalog['telegram_account']={'name':'telegram_account'}
        def critical(action='',request='',params=None):
            self.calls.append(params['password']);return 'received '+params['password']
        with patch.dict(agent.TOOL_IMPL,{'telegram_account':critical}), \
             patch.object(agent,'_critical_telegram_tool_input',return_value=True), \
             patch.dict(os.environ,{'HAND_TEST_SECRET':secret}):
            out=self.call("import os\nr=h.hand('telegram_account',action='call',request='fixture',params={'password':os.environ['HAND_TEST_SECRET']});print(r['content'])")
        self.assertEqual(self.calls,[secret]);self.assertNotIn(secret,str(out))
        for path in self.manager.path(self.current.run_id).rglob('*'):
            if path.is_file():self.assertNotIn(secret.encode(),path.read_bytes(),str(path))
        self.assertIn('_praxis_redacted',json.dumps(self.nested()[0]['args']))

    def test_nested_receipt_records_its_outer_parent(self):
        self.call("h.hand('fixture',value='linked')")
        rows=[row for row in self.manager.events(self.current.run_id) if row.get('kind')=='hand_requested']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['parent_call_id'],self.last_execution['call_id'])
        self.assertEqual(rows[0]['call_id'],self.nested()[0]['call_id'])

    def test_nested_python_owns_its_own_endpoint_and_durable_parent_link(self):
        self.catalog['python']={'name':'python'}
        inner="print(h.hand('fixture',value='grandchild')['content'])"
        out=self.call("print(h.hand('python',code="+repr(inner)+",cwd="+repr(str(self.root))+")[\"content\"])")
        self.assertIn('grandchild',str(out));self.assertEqual(self.calls,['grandchild'])
        rows=[x for x in self.manager.events(self.current.run_id) if x.get('kind')=='hand_requested']
        self.assertEqual(len(rows),2);self.assertEqual(rows[1]['parent_call_id'],rows[0]['call_id'])

    def test_telegram_nested_call_gets_its_own_stable_delivery_key(self):
        self.catalog['reply']={'name':'reply'}
        keys=[]
        def reply(text):
            keys.append(agent._TOOL_EXECUTION.get()['idempotency_key']);return 'delivered fixture'
        with patch.dict(agent.TOOL_IMPL,{'reply':reply}):
            out=self.call("a=h.hand('reply',text='fixture',_request_id='delivery');b=h.hand('reply',text='fixture',_request_id='delivery');assert a==b;print(a['content'])")
        self.assertIn('delivered fixture',str(out));self.assertEqual(len(keys),1)
        call=self.nested()[0]
        self.assertEqual(keys[0],f"telegram-outbox:{self.current.run_id}:tool:{call['call_id']}")
        self.assertEqual(call['idempotency_key'],keys[0])

    def test_empty_explicit_request_id_is_refused_without_an_effect(self):
        out=self.call("try:h.hand('fixture',_request_id='')\nexcept h.Refused:print('EMPTY-ID')")
        self.assertIn('EMPTY-ID',str(out));self.assertEqual(self.nested(),[])

    def test_end_turn_reaches_actual_parent_work_loop_state(self):
        self.catalog['end_turn']={'name':'end_turn'}
        self.addCleanup(agent.work_loop.release,self.current.run_id)
        out=self.call("h.hand('fixture',value='work');print(h.hand('end_turn',outcome='done',note='fixture')['content'])")
        self.assertIn('[exit 0]',str(out));self.assertTrue(agent.work_loop.finished(self.current.run_id))
        self.assertEqual(agent.work_loop.hands_called(self.current.run_id),('fixture','end_turn'))

    def test_pending_delivery_pauses_run_without_closing_its_nested_intent(self):
        self.catalog['reply']={'name':'reply'}
        def pending(text):
            self.calls.append(text)
            raise agent.DurableSideEffectPending(agent._TOOL_EXECUTION.get()['idempotency_key'],'fixture awaits receipt')
        with patch.dict(agent.TOOL_IMPL,{'reply':pending}):
            with self.assertRaises(agent.RunStopped):self.call("h.hand('reply',text='once',_request_id='pending')")
        self.assertEqual(self.calls,['once']);self.assertEqual(self.manager.manifest(self.current.run_id)['status'],'paused')
        self.assertIn(self.nested()[0]['call_id'],self.manager.outstanding_tools(self.current.run_id))


if __name__=='__main__':unittest.main()
