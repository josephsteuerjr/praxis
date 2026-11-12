"""Real durable pulse scheduling and batched intentions without model calls."""
import copy, json, os, sys, tempfile, threading, types, unittest
from pathlib import Path
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[2]
os.environ['PRAXIS_TEST']='1'
sys.path[:0]=[str(ROOT/'desk'),str(ROOT/'desk/localharness'),str(ROOT/'helene/core'),str(ROOT/'praxis')]
import _sandbox
_sandbox.activate_if_testing()
import tasks, social_pulse, heartbeat, appetite
from localharness.reflection import Reflection

class ReflectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tree=Path(self.tmp.name);self.reflection=Reflection(self.tree)
        p=patch.object(tasks,'TASKS',self.tree/'tasks.json');p.start();self.addCleanup(p.stop)
        schema={'name':'remind_self','input_schema':{'type':'object','properties':{k:{'type':'string'} for k in ('kind','goal','when','target','after_run')},'required':['kind','goal']}}
        def single(**kw):
            if kw['goal']=='reject':return 'Rejected before creation'
            task=tasks.add(kw['kind'],kw['goal'],kw.get('when'),kw.get('target'),after_run=kw.get('after_run'))
            return task['id']
        self.agent=types.SimpleNamespace(TOOL_IMPL={'remind_self':single,'my_agenda':lambda:'Agenda'},BASE_TOOLS=[schema],REMIND_SELF_TOOL=schema)
        self.reflection.install(self.agent);self.call=self.agent.TOOL_IMPL['remind_self']

    def test_agent_switch_survives_restart_and_excludes_mixed_actions(self):
        self.assertFalse(self.reflection.state()['enabled'])
        self.assertTrue(json.loads(self.call(reflection='on'))['ok'])
        self.assertTrue(Reflection(self.tree).state()['enabled'])
        self.assertFalse(json.loads(self.call(reflection='off',kind='wake',goal='x'))['ok'])
        self.assertTrue(self.reflection.state()['enabled'])
        self.call(reflection='off');self.assertFalse(self.reflection.state()['enabled'])

    def test_batch_has_individual_receipts_and_keeps_successes(self):
        result=json.loads(self.call(alarms=[{'kind':'wake','goal':'first','when':'in 30m'},
            {'kind':'wake','goal':'reject','when':'in 1h'}, {'kind':'note','goal':'last','when':'tomorrow 10:00'}]))
        self.assertEqual([x['ok'] for x in result['results']],[True,False,True])
        self.assertEqual([x['goal'] for x in tasks.list_open()],['first','last'])
        self.assertEqual(len({i for x in result['results'] for i in x['ids']}),2)
        self.assertFalse(json.loads(self.call(alarms=[{'kind':'wake','goal':'valid'},{}]))['ok'])
        self.assertEqual(len(tasks.list_open()),2)
        self.assertFalse(json.loads(self.call(alarms=[{'kind':'typo','goal':'must not become a note'}]))['ok'])
        self.assertEqual(len(tasks.list_open()),2)

    def test_preserved_old_core_refuses_batch_without_creating_untracked_alarms(self):
        with patch.object(tasks,'capture_additions',None):
            result=json.loads(self.call(alarms=[{'kind':'note','goal':'not yet'}]))
            self.assertFalse(result['ok']);self.assertEqual(tasks.list_open(),[])
            self.call(kind='note',goal='single still works')
        self.assertEqual([row['goal'] for row in tasks.list_open()],['single still works'])

    def test_schema_batch_items_are_single_alarm_schema(self):
        schema=self.agent.BASE_TOOLS[0]['input_schema']
        self.assertEqual(schema['properties']['alarms']['items']['required'],['kind','goal'])
        self.assertNotIn('alarms',schema['properties']['alarms']['items']['properties'])
        self.assertIn('Reflection:',self.agent.TOOL_IMPL['my_agenda']())

    def test_repeat_install_does_not_nest_schema_or_agenda_wrappers(self):
        Reflection(self.tree).install(self.agent)
        schema=self.agent.BASE_TOOLS[0]['input_schema']
        self.assertNotIn('alarms',schema['properties']['alarms']['items']['properties'])
        self.assertNotIn('reflection',schema['properties']['alarms']['items']['properties'])
        self.assertEqual(self.agent.TOOL_IMPL['my_agenda']().count('Reflection:'),1)
        result=json.loads(self.agent.TOOL_IMPL['remind_self'](alarms=[{'kind':'note','goal':'one'}]))
        self.assertTrue(result['ok']);self.assertEqual(len(tasks.list_open()),1)

    def test_unreadable_state_is_reported_and_never_enables_itself(self):
        self.reflection.path.parent.mkdir(parents=True)
        for value in ('{broken','[]','{"enabled":"yes"}'):
            self.reflection.path.write_text(value)
            result=json.loads(self.call(reflection='status'))
            self.assertFalse(result['ok']);self.assertFalse(result['reflection']['enabled'])
            invoke=Mock();self.reflection.tick(invoke);invoke.assert_not_called()
            self.assertEqual(self.reflection.path.read_text(),value)
        self.call(reflection='on');self.assertTrue(self.reflection.state()['enabled'])

    def test_batch_receipt_excludes_another_threads_addition(self):
        original,agenda=self.agent._helene_reflection_original
        def interleaved(**kw):
            other=threading.Thread(target=lambda:tasks.add('note','foreign'))
            other.start();other.join()
            return original(**kw)
        self.agent._helene_reflection_original=(interleaved,agenda)
        self.reflection.install(self.agent)
        result=json.loads(self.agent.TOOL_IMPL['remind_self'](alarms=[{'kind':'note','goal':'own'}]))
        rows={task['goal']:task['id'] for task in tasks.list_open()}
        self.assertEqual(result['results'][0]['ids'],[rows['own']])
        self.assertEqual(result['results'][0]['outcome'],'created')

    def test_lost_save_acknowledgement_is_unknown_not_rejected(self):
        save=tasks._save
        def committed_then_disconnected(items):
            save(items)
            raise OSError('fixture lost acknowledgement')
        with patch.object(tasks,'_save',side_effect=committed_then_disconnected):
            result=json.loads(self.call(alarms=[{'kind':'note','goal':'persisted'}]))
        self.assertEqual(result['results'][0]['outcome'],'unknown')
        self.assertEqual(result['results'][0]['ids'],[])
        self.assertEqual([task['goal'] for task in tasks.list_open()],['persisted'])

    def test_real_core_tool_supplies_receipts_without_parsing_its_prose(self):
        import agent
        # Install on a separate surface: leave the imported module's tools unchanged.
        surface=types.SimpleNamespace(TOOL_IMPL={'remind_self':agent.tool_remind_self,'my_agenda':agent.tool_my_agenda})
        self.reflection.install(surface)
        result=json.loads(surface.TOOL_IMPL['remind_self'](alarms=[{'kind':'note','goal':'actual','when':'in 30m'}]))
        self.assertTrue(result['ok']);self.assertEqual(result['results'][0]['ids'],[tasks.list_open()[0]['id']])

    def test_actual_audience_schemas_and_english_overlay_keep_new_forms(self):
        import agent, edition_tools, tool_text_en
        surface=types.SimpleNamespace(TOOL_IMPL=dict(agent.TOOL_IMPL),HAND_PURPOSE=dict(agent.HAND_PURPOSE),tool_text_en=tool_text_en)
        for name in (*edition_tools.TOOL_LISTS,'REMIND_SELF_TOOL'):
            setattr(surface,name,copy.deepcopy(getattr(agent,name,[])))
        count=0
        with patch.object(tool_text_en,'EN',copy.deepcopy(tool_text_en.EN)), \
             patch.object(tool_text_en,'POINTER_PURPOSE',dict(tool_text_en.POINTER_PURPOSE)), \
             patch.object(tool_text_en,'enabled',return_value=True):
            self.reflection.install(surface)
            for name in edition_tools.TOOL_LISTS:
                for tool in tool_text_en.apply(getattr(surface,name,[])):
                    if tool.get('name')=='remind_self':
                        count+=1
                        self.assertIn('reflection',tool['input_schema']['properties'])
                        self.assertIn('alarms',tool['input_schema']['properties'])
                        self.assertIn('hourly',tool['description'])
                        self.assertIn('batch',tool_text_en.pointer_purpose('remind_self',''))
            self.assertGreater(count,0)

    def test_sleep_and_background_hold_do_not_claim_a_pulse(self):
        self.call(reflection='on')
        for sleeping,hold in ((True,None),(False,'hold')):
            with patch.object(heartbeat,'hour_in',return_value=sleeping), \
                 patch.object(appetite,'background_hold',return_value=hold),patch.object(social_pulse,'begin') as begin:
                invoke=Mock();self.reflection.tick(invoke)
                begin.assert_not_called();invoke.assert_not_called()

    def test_praxis_pulse_claim_is_durable_and_no_catchup_storm(self):
        path=self.tree/'pulse.json';now=[10000.0];calls=[]
        begin=social_pulse.begin;finish=social_pulse.finish;observe=social_pulse.observability
        with patch.object(social_pulse,'begin',side_effect=lambda:begin(path=path,now=now[0])), \
             patch.object(social_pulse,'finish',side_effect=lambda pulse,**kw:finish(pulse,path=path,now=now[0],**kw)), \
             patch.object(social_pulse,'observability',side_effect=lambda pulse:observe(pulse,path=path,now=now[0])), \
             patch.object(heartbeat,'window_context',return_value='Shared context'), \
             patch.object(heartbeat,'hour_in',return_value=False), \
             patch.object(appetite,'background_hold',return_value=None), \
             patch.dict(sys.modules,{'telegram_followups':types.SimpleNamespace(LEDGER=types.SimpleNamespace(context=lambda:'Open followups')),
                                     'mailroom':types.SimpleNamespace(list_open=lambda:[],index_block=lambda *a,**kw:'Fresh mailbox')}), \
             patch.dict(os.environ,{'PRAXIS_SOCIAL_PULSE_HOURS':'1'}):
            invoke=lambda pid,goal,**kw:calls.append((pid,goal,kw)) or 'silent'
            self.reflection.tick(invoke);self.assertFalse(calls)
            self.call(reflection='on');self.reflection.tick(invoke)
            self.assertIn('Твой час.',calls[0][1]);self.assertIn('Shared context',calls[0][1])
            self.assertIn('Open followups',calls[0][1]);self.assertEqual(calls[0][2]['mailbox_index'],'Fresh mailbox')
            Reflection(self.tree).tick(invoke);self.assertEqual(len(calls),1)
            now[0]+=24*3600;self.reflection.tick(invoke);self.reflection.tick(invoke)
            self.assertEqual(len(calls),2)
            self.assertTrue(all(row['status']=='done' for row in social_pulse.runs(path=path)))

class RunnerReflection(unittest.TestCase):
    def test_own_window_keeps_transport_and_records_only_confirmed_done(self):
        import runner
        for status in ('done','failed','cancelled','running',''):
            with self.subTest(status=status):
                outcomes=[]
                window=Mock(side_effect=lambda goal,**kw:kw['on_run']('own-run'))
                agent=types.SimpleNamespace(task_window=window,_runs=lambda:types.SimpleNamespace(manifest=lambda _: {'status':status}))
                reflection=types.SimpleNamespace(tick=lambda invoke:outcomes.append(invoke('pulse','own goal',mailbox_index='mail')))
                with patch.object(runner,'_reflection',reflection),patch.object(runner,'_agent',agent), \
                     patch.object(runner,'_desk',object()),patch.object(runner,'_brain_ready',return_value=True), \
                     patch.object(runner,'_busy',{'busy':False}),patch.object(runner.owner_stop,'paused',return_value=False), \
                     patch.object(runner.owner_stop,'stopped',return_value=False),patch.object(runner,'_set_busy') as busy, \
                     patch.object(runner,'_bot',types.SimpleNamespace(connected=lambda:True)), \
                     patch.object(runner,'_turn_in_window',side_effect=AssertionError('no synthetic chat turn')) as chat:
                    runner._reflection_tick()
                    chat.assert_not_called()
                    self.assertEqual(window.call_args.args,('own goal',))
                    self.assertEqual(window.call_args.kwargs['transport'],'connected')
                    self.assertEqual(window.call_args.kwargs['mailbox_index'],'mail')
                    busy.assert_any_call(True,'own-run');busy.assert_called_with(False,'own-run')
                self.assertEqual(outcomes,['silent' if status=='done' else 'failed'])

    def test_pause_stop_and_busy_prevent_claiming_an_hour(self):
        import runner
        for paused,stopped,busy in ((True,False,False),(False,True,False),(False,False,True)):
            reflection=Mock()
            with patch.object(runner,'_reflection',reflection),patch.object(runner,'_agent',object()), \
                 patch.object(runner,'_desk',object()),patch.object(runner,'_brain_ready',return_value=True), \
                 patch.object(runner,'_busy',{'busy':busy}),patch.object(runner.owner_stop,'paused',return_value=paused), \
                 patch.object(runner.owner_stop,'stopped',return_value=stopped):runner._reflection_tick()
            reflection.tick.assert_not_called()

if __name__=='__main__':unittest.main()
