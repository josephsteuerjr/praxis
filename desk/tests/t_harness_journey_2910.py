"""Real tool dispatch/result journal -> desktop reader, with an offline model.

Only the platform shell adapter is replaced: its returned process is a real
native Python child. No Bash, model endpoint, accounts or installed state run.
"""
from pathlib import Path
import json,os,subprocess,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[2]


def consumer(profile,output):
    profile=Path(profile).resolve()
    os.environ.update(PRAXIS_BASE=str(profile),PRAXIS_TEST='1',PRAXIS_KEAT='off',PRAXIS_KEAT_CAPTURE='off',
                      PRAXIS_FRAME_SHADOW='off',PRAXIS_FRAME_MEASURE='off',PRAXIS_FRAME_SERVE='off')
    sys.path[:0]=[str(ROOT/'helene/core'),str(ROOT/'praxis'),str(ROOT/'desk'),str(ROOT/'desk/localharness')]
    import copy,contextlib,types
    from unittest.mock import patch
    import agent,run_context,run_manager
    from deskd import readers
    manager=run_manager.RunManager(profile);agent._RUN_MANAGER=manager
    context=run_context.RunContext.create(kind='test',goal='actual harness projection',principal_id='owner',scope='owner',origin_chat_id='1',delivery_chat_id='1')
    context=manager.create(context,'Synthetic offline context');manager.transition(context.run_id,'running')
    provider=[];processes=[];native_run=subprocess.run
    def chat(*args,**kwargs):
        provider.append(copy.deepcopy(kwargs['messages']))
        if len(provider)==1:
            return types.SimpleNamespace(stop_reason='tool_use',text='',blocks=[{'type':'tool_use','id':'real-call',
                'name':'shell','input':{'command':'harness <requested>'}}])
        return types.SimpleNamespace(stop_reason='end_turn',text='observed',blocks=[])
    def shell_adapter(argv,**kwargs):
        assert argv[:2]==['bash','-lc'] and 'harness <requested>' in argv[-1]
        actual=[sys.executable,'-I','-c',"print('REAL <observed> ' + 'x'*24000)"]
        result=native_run(actual,cwd=profile,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
        processes.append({'argv':result.args,'stdout':result.stdout.decode('utf-8').replace('\r\n','\n'),'exit':result.returncode})
        return result
    with contextlib.ExitStack() as stack:
        stack.enter_context(run_context.bind_run(context))
        stack.enter_context(patch.object(agent.llm,'chat',side_effect=chat))
        for target,name,value in ((agent.stewardship,'check',None),(agent.stewardship,'touches_path_mentioned',False),
            (agent,'_core_edit_check_on',False),(agent.selfgit,'safety_point',None),(agent,'_pre_shell_dirt',set()),
            (agent,'_autocommit_self_edit',None),(agent,'_shell_workdir',profile),(agent,'TOOL_CEILING_SEC',0)):
            stack.enter_context(patch.object(target,name,return_value=value) if callable(getattr(target,name,None)) else patch.object(target,name,value))
        stack.enter_context(patch('socket.create_connection',side_effect=AssertionError('network forbidden')))
        stack.enter_context(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        stack.enter_context(patch.object(agent.subprocess,'run',side_effect=shell_adapter))
        answer=agent._terminal_tool_loop(system='synthetic',messages=[],tools=[{'name':'shell','description':'offered shell',
            'input_schema':{'type':'object','properties':{'command':{'type':'string'}}}}])
    assert answer=='observed' and len(processes)==1 and processes[0]['exit']==0
    with patch.object(readers,'run_dir',return_value=manager.path(context.run_id)):
        detail=readers.run_detail(context.run_id)
    tool=next(t for row in detail['iterations'] for t in row.get('tools',[]) if t.get('call_id')=='real-call')
    assert tool['args']=={'command':'harness <requested>'}
    assert tool['execution']['handler'].endswith('agent.tool_shell')
    assert tool['operations'][0]['argv']==processes[0]['argv']
    assert tool['operations'][0]['status']=='returned'
    assert tool['model_observation']==provider[-1][-1]['content'][0]['content']
    assert tool['model_observation']!=processes[0]['stdout'] and 'ResultRef' in tool['model_observation']
    assert tool['result']['truncated'] and tool['status']=='received'
    Path(output).write_text(json.dumps({'detail':detail,'actual_process':processes[0],
        'events':[row['kind'] for row in manager.events(context.run_id)]},ensure_ascii=False),encoding='utf-8')


class HarnessJourney(unittest.TestCase):
    def test_actual_tool_journal_is_projected_without_replacing_it_with_model_intent(self):
        with tempfile.TemporaryDirectory(prefix='helene-harness-journey-') as temporary:
            root=Path(temporary);output=root/'observed.json'
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--consumer',str(root),str(output)],
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            value=json.loads(output.read_text(encoding='utf-8'))
            self.assertIn('tool_dispatch',value['events']);self.assertIn('tool_execution',value['events'])
            self.assertIn('tool_model_response',value['events'])


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--consumer':consumer(sys.argv[2],sys.argv[3])
    else:unittest.main()
