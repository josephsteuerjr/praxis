"""Desktop edition regressions through its installed owner and self-review APIs."""
import json
import ast
import logging
from types import SimpleNamespace
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock
import forge
import forge_worker
import forge_reader
import llm
from deskd import forge_control as desktop_control, forge_reader as desktop_reader
from localharness import self_review, self_review_worker
from localharness.forge_events import ForgeEvents
import test_forge_wave2_server as fixtures
import test_llm


class ReviewCorrections(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ServerWave2();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.tid=self.fixture.tid;self.base=self.fixture.base

    def test_selected_stop_accepts_completed_skip_and_stale_verifications_twice(self):
        for status in ('passed_with_skips','stale'):
            uid='agent-'+('11223344' if status=='stale' else '55667788')
            directory,request=self.fixture.unit(uid=uid,status='queued')
            check=forge._unit_dir(self.tid,'verifications','verify-'+status)
            forge._atomic_json(check/'request.json',{'id':check.name,'task_id':self.tid,'parent_unit_id':uid})
            forge._atomic_json(check/'result.json',{'status':status,'checks':[{'status':'skipped'}]})
            raw=(check/'result.json').read_bytes()
            for _ in range(2):
                forge.agent(self.tid,'stop',agent_id=uid)
                self.assertEqual(forge._read_json(directory/'stop.json')['state'],'settled')
                self.assertEqual((check/'result.json').read_bytes(),raw)

    def test_finish_respects_owner_wide_pause_before_survey(self):
        directory=self.base/'memory/.control';directory.mkdir(parents=True)
        (directory/'autonomy-paused.json').write_text('{}')
        with mock.patch.object(forge,'_finish_survey') as survey:
            self.assertIn('owner Pause',forge.finish(self.tid,submit=False))
            self.assertIn('owner Pause',forge._finish_unlocked(self.tid,submit=False,survey={}))
        survey.assert_not_called()

    def test_owner_event_second_page_preserves_both_cursor_coordinates(self):
        directory,request=self.fixture.unit(role='scout')
        forge._atomic_json(directory/'progress.json',{'generation':'one'})
        (directory/'events.jsonl').write_text(''.join(json.dumps({'seq':i,'kind':'observed'})+'\n' for i in range(1,71)))
        first=desktop_reader.events(self.base,self.tid,request['id'],generation='one',limit=50)
        self.assertTrue(first['has_more'])
        second=desktop_reader.events(self.base,self.tid,request['id'],generation='one',limit=50,
            offset=first['next_offset'],after_seq=first['next_after_seq'])
        self.assertEqual([r['seq'] for r in second['events']],list(range(51,71)))
        self.assertFalse(second['has_more'])

    def test_direct_stop_cascades_owned_jobs_and_preserves_sibling(self):
        _,request=self.fixture.unit(status='queued')
        _,sibling=self.fixture.unit(uid='agent-11223344',status='queued')
        for kind,ident,parent in [('processes','proc-aa',request['id']),('verifications','verify-aa',request['id']),('processes','proc-bb',sibling['id'])]:
            forge._atomic_json(forge._unit_dir(self.tid,kind,ident)/'request.json',{'id':ident,'task_id':self.tid,'parent_unit_id':parent,'status':'running'})
        called=[]
        def stopped(kind,key,task,action,**kw):
            ident=kw[key];called.append(ident)
            forge._atomic_json(forge._unit_dir(task,kind,ident)/'result.json',{'status':'stopped'})
            return 'settled'
        with mock.patch.object(forge,'process',side_effect=lambda *a,**k:stopped('processes','process_id',*a,**k)), \
             mock.patch.object(forge,'verify',side_effect=lambda *a,**k:stopped('verifications','verification_id',*a,**k)):
            forge.agent(self.tid,'stop',agent_id=request['id'])
        self.assertEqual(set(called),{'proc-aa','verify-aa'})
        self.assertFalse((forge._unit_dir(self.tid,'processes','proc-bb')/'result.json').exists())

    def test_finish_cannot_integrate_while_unreleased_writer_or_lost_job_exists(self):
        unit,_=self.fixture.unit(status='running')
        forge._atomic_json(unit/'result.json',{'status':'done'})
        with mock.patch.object(forge,'_checkpoint_unlocked') as integrate:
            self.assertIn('Finish refused',forge.finish(self.tid,submit=True))
            self.assertIn('Finish refused',forge._finish_unlocked(self.tid,submit=True,survey={}))
        integrate.assert_not_called();self.assertEqual(forge.get(self.tid)['status'],'active')
        forge.release_worker_root(unit/'request.json')
        job=forge._unit_dir(self.tid,'processes','proc-lost')
        forge._atomic_json(job/'request.json',{'id':'proc-lost','task_id':self.tid,'status':'starting'})
        self.assertIn('proc-lost',forge.finish(self.tid,submit=False))

    def test_foreground_intent_exists_before_any_process_can_start(self):
        observed=[]
        def refused(*args,**kwargs):
            paths=list(forge._task_dir(self.tid).glob('processes/*/request.json'))
            self.assertEqual(len(paths),1)
            intent=json.loads(paths[0].read_text());observed.append(intent)
            self.assertEqual(intent['status'],'starting');self.assertTrue(kwargs['defer_start'])
            raise OSError('fixture launch refused')
        with mock.patch('command_process.CommandProcess',side_effect=refused):out=forge.run(self.tid,'fixture-no-execution')
        self.assertEqual(len(observed),1);self.assertIn('error',out)

    def test_finish_respects_pause_and_owner_fences_before_any_survey_or_integration(self):
        forge.control(self.tid,'pause')
        with mock.patch.object(forge,'_finish_survey') as survey:
            self.assertIn('pause-fenced',forge.finish(self.tid,submit=False))
            self.assertIn('pause-fenced',forge._finish_unlocked(self.tid,submit=False,survey={}))
        survey.assert_not_called();self.assertEqual(forge.get(self.tid)['status'],'active')
        forge.control(self.tid,'resume')
        (self.base/'memory/.panic').write_text('fixture panic')
        with mock.patch.object(forge,'_finish_survey') as survey:
            self.assertIn('panic',forge.finish(self.tid,submit=False))
        survey.assert_not_called()

    def test_legacy_terminal_writer_without_generation_or_release_stays_visible(self):
        directory,request=self.fixture.unit(status='running')
        forge._atomic_json(directory/'result.json',{'status':'done'})
        detail=forge_reader.task_detail(self.base,self.tid)
        self.assertTrue(detail['agents'][0]['root_release_pending'])
        self.assertEqual(forge_reader.activity(self.base)['active'][0]['id'],self.tid)
        forge.release_worker_root(directory/'request.json')
        self.assertFalse(forge_reader.task_detail(self.base,self.tid)['agents'][0]['root_release_pending'])

    def test_owner_projection_keeps_surviving_jobs_and_their_parent_visible(self):
        unit,request=self.fixture.unit(status='running')
        forge._atomic_json(unit/'result.json',{'status':'done'})
        for family,ident in [('processes','proc-aabbccdd'),('verifications','verify-aabbccdd')]:
            forge._atomic_json(forge._unit_dir(self.tid,family,ident)/'request.json',
                {'id':ident,'task_id':self.tid,'parent_unit_id':request['id'],'status':'running','command':'synthetic job'})
        before={str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        detail=forge_reader.task_detail(self.base,self.tid);activity=forge_reader.activity(self.base)
        self.assertEqual((len(detail['processes']),len(detail['verifications'])),(1,1))
        self.assertGreaterEqual(detail['counts']['active'],2)
        self.assertEqual(activity['active'][0]['id'],self.tid)
        self.assertEqual(detail['processes'][0]['parent_unit_id'],request['id'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()})
        ref=detail['processes'][0]['request_ref']
        self.assertIn('synthetic job',forge_reader.payload(self.base,self.tid,'proc-aabbccdd',ref['sha256'],'request')['text'])

    def test_damaged_control_retains_bytes_and_does_not_block_existing_event_query(self):
        from core import events
        commands=self.base/'memory/.control/forge/commands';commands.mkdir(parents=True)
        key='d'*32;raw=b'{incomplete-intent'
        (commands/(key+'.json')).write_bytes(raw)
        journal=self.base/'event-journal';journal.write_text('fixture journal stat',encoding='utf-8')
        # Execute the installed runner's real maintenance body without importing
        # the authored agent or starting its host, transport and background loop.
        path=Path(self_review.__file__).with_name('runner.py')
        tree=ast.parse(path.read_text(encoding='utf-8'))
        body=next(row for row in tree.body if isinstance(row,ast.FunctionDef)
            and row.name=='_forge_maintenance_tick')
        owner=SimpleNamespace(stopped=lambda:False,paused=lambda:False)
        namespace={'log':logging.getLogger('desktop-review-fixture'),'owner_stop':owner}
        exec(compile(ast.Module(body=[body],type_ignores=[]),str(path),'exec'),namespace)
        # An already reported completion is still queried and acknowledged by
        # the event consumer. No ordinary model turn belongs to this fixture.
        event={'kind':'subagent_result','dedup_key':'existing-completion',
            'payload':{'reported_inline':True}}
        agent=SimpleNamespace(llm=SimpleNamespace(configured=lambda:True),forge_event_turn=mock.Mock())
        continuity=SimpleNamespace(desks=SimpleNamespace(tree=self.base),agent=agent)
        consumer=ForgeEvents(continuity,events,forge,SimpleNamespace(value=lambda name:0))
        with mock.patch.object(events,'JOURNAL',journal), \
             mock.patch.object(events,'DELIVERED',self.base/'delivered.json'), \
             mock.patch.object(events,'enabled',return_value=True), \
             mock.patch.object(events,'undelivered',return_value=[event]) as query, \
             mock.patch.object(forge,'tick_queued',return_value=[]), \
             mock.patch.object(forge,'observe_liveness'), \
             mock.patch.object(forge,'reconcile_subagent_events'), \
             mock.patch.object(forge,'mark_seen'),mock.patch.object(forge,'_wake_load_seen',return_value=set()):
            namespace['_forge_maintenance_tick'](self.base,forge,None)
            consumer.tick()
        query.assert_called_once_with({'subagent_result'})
        agent.forge_event_turn.assert_not_called()
        self.assertEqual(desktop_control.receipt(self.base,key)['state'],'unknown')
        self.assertIn(raw,[p.read_bytes() for p in (commands.parent/'damaged').glob('*.json')])
        # A second maintenance tick retains that uncertainty without replay.
        callback=mock.Mock()
        desktop_control.consume_pending(self.base,callback)
        callback.assert_not_called()
        self.assertEqual(desktop_control.receipt(self.base,key)['state'],'unknown')

    def test_damaged_intent_survives_interrupted_quarantine_without_replay(self):
        for folder,key in [('processing','e'*32),('commands','f'*32)]:
            with self.subTest(folder=folder):
                raw=b'{incomplete-intent-before-quarantine'
                path=self.base/'memory/.control/forge'/folder/(key+'.json')
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                callback=mock.Mock()
                replace=desktop_control.os.replace
                def interrupted(source,target):
                    if Path(source)==path:
                        raise OSError('fixture quarantine interrupted after unknown receipt')
                    return replace(source,target)
                with mock.patch.object(desktop_control.os,'replace',side_effect=interrupted):
                    with self.assertRaises(OSError):desktop_control.consume_pending(self.base,callback)
                self.assertEqual(path.read_bytes(),raw)
                self.assertEqual(desktop_control.receipt(self.base,key)['state'],'unknown')
                desktop_control.consume_pending(self.base,callback)
                callback.assert_not_called()
                self.assertFalse(path.exists())
                self.assertIn(raw,[p.read_bytes() for p in (path.parent.parent/'damaged').glob('*.json')])
                self.assertEqual(desktop_control.receipt(self.base,key)['state'],'unknown')
                desktop_control.recent(self.base,self.tid)

    def test_damaged_intent_does_not_block_a_valid_queued_owner_control(self):
        commands=self.base/'memory/.control/forge/commands';commands.mkdir(parents=True)
        raw=b'{damaged-before-valid';(commands/('a'*32+'.json')).write_bytes(raw)
        key='b'*32
        desktop_control.enqueue(self.base,self.tid,'pause',command_id=key)
        callback=mock.Mock(return_value={'ok':True,'status':'paused'})
        desktop_control.consume_pending(self.base,callback)
        callback.assert_called_once_with(self.tid,'pause',agent_id='',expected_generation='')
        self.assertEqual(desktop_control.receipt(self.base,key)['state'],'observed')
        self.assertEqual(desktop_control.receipt(self.base,'a'*32)['state'],'unknown')
        self.assertIn(raw,[p.read_bytes() for p in (commands.parent/'damaged').glob('*.json')])

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux separate coordinator processes')
    def test_real_custodian_death_before_pid_publication_never_admits_command(self):
        marker=self.base/'forbidden-effect'
        process=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--crash-start',str(self.base),self.tid,str(marker)],
            cwd=Path(__file__).resolve().parent,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=10)
        self.assertEqual(process.returncode,91,process.stdout)
        units=list(forge._task_dir(self.tid).glob('processes/*'))
        self.assertEqual(len(units),1)
        end=time.monotonic()+5;settled=units[0]/'command-custody/settled.json'
        while not settled.exists() and time.monotonic()<end:time.sleep(.02)
        self.assertTrue(settled.exists(),'private helper must settle without an admitted executable')
        self.assertTrue(json.loads(settled.read_text())['stopped'])
        self.assertFalse(marker.exists())
        self.assertEqual(json.loads((units[0]/'request.json').read_text())['status'],'starting')
        self.assertIn(units[0].name,forge._admission_reason(self.tid,general=True))

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux separate coordinator processes')
    def test_two_actual_start_callers_share_one_review_reservation(self):
        package=self.base/'package';(package/'app').mkdir(parents=True)
        (package/'app/runner.py').write_text('APP = 1\n',encoding='utf-8')
        service=self_review.Service(self.base,self.fixture.root,package,forge)
        value=service.request(critics=1);rid=value['id']
        source=Path(__file__).resolve().parent
        children=[]
        try:
            for index in range(2):
                children.append(subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--race-start',str(self.base),self.tid,rid,str(index)],cwd=source))
            end=time.monotonic()+8
            while len(list(self.base.glob('ready-*')))<2 and time.monotonic()<end:time.sleep(.02)
            self.assertEqual(len(list(self.base.glob('ready-*'))),2)
            (self.base/'go').write_text('go')
            for process in children:self.assertEqual(process.wait(timeout=10),0)
            record=service.status(rid)
            self.assertEqual(len(record['critics']),1)
            units=list(forge._task_dir(record['task_id']).glob('agents/*/request.json'))
            self.assertEqual(len(units),1)
            self.assertEqual(json.loads(units[0].read_text(encoding='utf-8'))['id'],record['critics'][0]['unit_id'])
            self.assertEqual(Path(forge.get(record['task_id'])['self_review']['worker_script']).resolve(),
                Path(self_review_worker.__file__).resolve())
            self.assertEqual(len((self.base/'admissions').read_text().splitlines()),1)
            linked=[json.loads(p.read_text()) for p in forge.TASKS_DIR.glob('*/task.json') if p.parent.name!=self.tid]
            self.assertEqual(len(linked),1)
            self.assertEqual(linked[0]['id'],record['task_id'])
        finally:
            for process in children:
                if process.poll() is None:process.kill();process.wait()

    def test_unknown_provider_response_is_not_replayed_or_resumable(self):
        directory,request=self.fixture.unit(status='starting',max_iters=2)
        with mock.patch.object(forge_worker.llm,'chat',side_effect=llm.BrokenChannelError('acceptance unknown')) as chat, \
             mock.patch.object(forge_worker,'_system',return_value='frozen'):
            self.assertEqual(forge_worker.run(directory/'request.json'),2)
        self.assertEqual(chat.call_count,1)
        checkpoint=json.loads((directory/'checkpoint.json').read_text())
        self.assertEqual(checkpoint['next_boundary'],'model_in_flight')
        self.assertFalse((directory/'continuation.json').exists())


class ProviderAttempt(test_llm.Base):
    def test_real_adapter_exposes_torn_primary_to_worker_journal_without_fallback(self):
        self._write_cfg(voice={'framework':'openai','model':'primary','fallback_framework':'anthropic','fallback_model':'backup'})
        fixture=fixtures.ServerWave2();fixture.setUp()
        try:
            directory,request=fixture.unit(status='starting',max_iters=2)
            partial=llm.LLMResponse(text='received opaque evidence',blocks=[{'type':'text','text':'received opaque evidence'}],
                usage={'in':7,'out':3},model='primary',stop_reason='error')
            with mock.patch.object(llm,'_resolve_model',side_effect=lambda fw,m:m),mock.patch.object(llm,'_client_for',return_value=object()), \
                 mock.patch.object(llm,'_call',side_effect=llm.TornStreamError('injected primary loss',partial=partial)) as call, \
                 mock.patch.object(forge_worker,'_system',return_value='frozen'):
                self.assertEqual(forge_worker.run(directory/'request.json'),2)
            self.assertEqual(call.call_count,1)
            import forge_runtime
            event=next(row for row in forge_runtime._events(directory) if row['kind']=='model_transport_error')
            raw=json.loads(forge_runtime.read_payload_full(directory,event['output_ref']))
            self.assertEqual(raw['text'],partial.text);self.assertEqual(raw['usage'],partial.usage)
            self.assertFalse(event['retry'])
        finally:fixture.doCleanups()

    def test_strict_context_has_one_empty_attempt_and_no_fallback(self):
        self._write_cfg(voice={'framework':'openai','model':'primary','fallback_framework':'anthropic','fallback_model':'backup'})
        with mock.patch.object(llm,'_resolve_model',side_effect=lambda fw,m:m),mock.patch.object(llm,'_client_for',return_value=object()), \
             mock.patch.object(llm,'_call',side_effect=llm.EmptyResponseError('known empty')) as call:
            with llm.single_provider_attempt(),self.assertRaises(llm.EmptyResponseError):llm.chat('voice',system='fixture',messages=[])
        self.assertEqual(call.call_count,1)
        self.assertFalse(llm._SINGLE_PROVIDER_ATTEMPT.get())

    def test_strict_context_disables_sdk_internal_retries(self):
        client=mock.Mock();client.with_options.return_value='single-attempt-client'
        self.assertIs(llm._custody_client(client),client)
        client.with_options.assert_not_called()
        with llm.single_provider_attempt():self.assertEqual(llm._custody_client(client),'single-attempt-client')
        client.with_options.assert_called_once_with(max_retries=0)


def race_start(base,tid,rid,index):
    base=Path(base);forge.BASE=base;forge.STATE_DIR=base/'memory/.forge';forge.TASKS_DIR=forge.STATE_DIR/'tasks'
    service=self_review.Service(base,base/'project',base/'package',forge)
    original=service.status;first=True
    def stale(*args):
        nonlocal first
        value=original(*args)
        if first:
            first=False;(base/('ready-'+index)).write_text('snapshot',encoding='utf-8')
            end=time.monotonic()+10
            while not (base/'go').exists() and time.monotonic()<end:time.sleep(.01)
        return value
    def spawn(script,request,log):
        # Keep the actual cross-process Forge reservation, identity/installed
        # worker checks and request publication. Replace only OS worker launch.
        with (base/'admissions').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'script':str(script),'request':str(request)})+'\n')
        return os.getppid()
    with mock.patch.object(service,'status',side_effect=stale),mock.patch.object(forge,'_spawn_runner',side_effect=spawn):
        service.tick(review_ids={rid})
    return 0


def crash_start(base,tid,marker):
    base=Path(base);forge.BASE=base;forge.STATE_DIR=base/'memory/.forge';forge.TASKS_DIR=forge.STATE_DIR/'tasks'
    original=forge._atomic_json
    def crash(path,data):
        if Path(path).name=='request.json' and 'supervisor_pid' in data:os._exit(91)
        return original(path,data)
    command=sys.executable+' -c '+__import__('shlex').quote(f'from pathlib import Path;Path({marker!r}).write_text("unsafe")')
    with mock.patch.object(forge,'_atomic_json',side_effect=crash):forge.run(tid,command)
    return 92


if __name__=='__main__':
    if len(sys.argv)==5 and sys.argv[1]=='--crash-start':raise SystemExit(crash_start(*sys.argv[2:]))
    if len(sys.argv)==6 and sys.argv[1]=='--race-start':raise SystemExit(race_start(*sys.argv[2:]))
    unittest.main()
