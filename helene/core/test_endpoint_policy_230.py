"""Real SDK JSON send/refusal with a test-only HTTP transport, zero sockets.

This qualifies Python's declared integration policy, NOT the retained relay
binary. A compatible endpoint is not guessed from protocol, model or URL.
"""
import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
import httpx
import openai
import anthropic
import brain
import capabilities
import llm


class EndpointPolicy(unittest.TestCase):
    def setUp(self):
        self.cfg = llm._normalize({'frameworks': {
            'embedded': {'protocol': 'openai', 'provider': 'relay', 'api_mode': 'chat_completions',
                         'base_url': 'http://127.0.0.1:5012/v1', 'api_key': 'synthetic-test-only'},
            'compatible': {'protocol': 'openai', 'api_mode': 'chat_completions',
                           'base_url': 'http://127.0.0.1:5012/v1', 'api_key': 'synthetic-test-only'},
            'direct': {'protocol': 'openai', 'api_mode': 'responses',
                       'base_url': 'https://api.openai.com/v1', 'api_key': 'synthetic-test-only'},
            'glm': {'protocol': 'anthropic', 'base_url': 'https://api.z.ai/api/anthropic',
                    'api_key': 'synthetic-test-only'}},
            'roles': {'voice': {'framework': 'embedded', 'model': 'synthetic-model'}}})
        self.enterContext(mock.patch.object(llm, '_config', return_value=self.cfg))
        self.seen = []

    def capture(self, request):
        self.seen.append(json.loads(request.content))
        if request.url.path.endswith('/responses'):
            result = {'id': 'resp_offline', 'object': 'response',
                'created_at': 1, 'model': 'synthetic-model', 'status': 'completed',
                'output': [{'id':'msg_offline', 'type':'message', 'role':'assistant', 'status':'completed',
                            'content':[{'type':'output_text','text':'ok','annotations':[]}]}],
                'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}
            events = 'event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':result})+'\n\n'
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, content=events.encode())
        if request.url.path.endswith('/messages'):
            message={'id':'msg_offline','type':'message','role':'assistant','model':'glm-5.3',
                     'stop_reason':None,'stop_sequence':None,'content':[],
                     'usage':{'input_tokens':1,'output_tokens':1}}
            events=[('message_start',{'type':'message_start','message':message}),
                    ('content_block_start',{'type':'content_block_start','index':0,'content_block':{'type':'text','text':'ok'}}),
                    ('content_block_stop',{'type':'content_block_stop','index':0}),
                    ('message_delta',{'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':1}}),
                    ('message_stop',{'type':'message_stop'})]
            data=''.join('event: '+name+'\ndata: '+json.dumps(value)+'\n\n' for name,value in events)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=data.encode())
        # Real SDK streaming parser, not an object stub.
        payload = {'id':'chat_offline','object':'chat.completion.chunk','created':1,
                   'model':'synthetic-model','choices':[{'index':0,'delta':{'content':'ok'},'finish_reason':None}]}
        done = dict(payload, choices=[{'index':0,'delta':{},'finish_reason':'stop'}])
        data = 'data: '+json.dumps(payload)+'\n\ndata: '+json.dumps(done)+'\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, content=data.encode())

    def call(self, account, effort, thinking=None):
        spec = self.cfg['frameworks'][account]
        transport = httpx.MockTransport(self.capture)
        with httpx.Client(transport=transport, trust_env=False) as http:
            factory = anthropic.Anthropic if spec['protocol'] == 'anthropic' else openai.OpenAI
            with factory(api_key='synthetic-test-only', base_url=spec['base_url'], http_client=http) as client, \
                    mock.patch.object(llm, '_client_for', return_value=client):
                return llm._call(account, 'glm-5.3' if account == 'glm' else 'synthetic-model',
                    _resolved=True, system='synthetic', messages=[{'role':'user','content':'test'}],
                    tools=None, max_tokens=128, thinking=thinking, reasoning_effort=effort)

    def test_all_six_relay_tiers_reach_the_actual_sdk_transport(self):
        for effort in llm.EMBEDDED_RELAY_EFFORTS:
            with self.subTest(effort=effort):
                self.assertEqual(self.call('embedded', effort).text, 'ok')
                self.assertEqual(self.seen[-1]['reasoning_effort'], effort)
        self.assertEqual(len(self.seen), 6)

    def test_relay_max_refuses_before_sdk_client_or_send_without_downshift(self):
        with mock.patch.object(llm, '_client_for', side_effect=AssertionError('SDK must not prepare')):
            with self.assertRaises(llm.UnsupportedReasoningEffort) as raised:
                llm._call('embedded', 'synthetic-model', thinking=None, reasoning_effort='max')
        self.assertEqual(raised.exception.requested, 'max')
        self.assertEqual(raised.exception.account, 'embedded')
        self.assertEqual(raised.exception.supported, llm.EMBEDDED_RELAY_EFFORTS)
        self.assertIn('donor binary support unknown', str(raised.exception))
        self.assertEqual(self.seen, [])

    def test_held_relay_preserves_configured_direct_fallback_canonical_max_and_route(self):
        role=self.cfg['roles']['voice']
        role.update(framework='embedded',model='synthetic-model',reasoning_effort='max',
                    fallback_framework='direct',fallback_model='synthetic-fallback')
        hold={'since':1,'until':9999999999,'code':'subscription_limit','words':'synthetic hold'}
        spec=self.cfg['frameworks']['direct']
        with httpx.Client(transport=httpx.MockTransport(self.capture),trust_env=False) as http, \
                openai.OpenAI(api_key='synthetic-test-only',base_url=spec['base_url'],http_client=http) as sdk:
            def client(account):
                self.assertEqual(account,'direct','held primary must never be attempted')
                return sdk
            with mock.patch.object(llm,'_resolve_model',side_effect=lambda fw,model:model), \
                    mock.patch.object(llm,'_endpoint_hold',return_value=hold), \
                    mock.patch.object(llm,'_client_for',side_effect=client), \
                    mock.patch.object(llm,'_STATE',copy.deepcopy(llm._STATE)), \
                    mock.patch.object(llm,'_brain_note') as observed, \
                    mock.patch.object(llm,'_usage_add'), mock.patch.object(llm,'_journal'):
                result=llm.chat('voice',messages=[{'role':'user','content':'synthetic'}])
        self.assertEqual(result.text,'ok')
        self.assertEqual(result.framework,'openai','protocol ABI must remain unchanged')
        self.assertEqual(result.account_route,'direct')
        self.assertEqual(len(self.seen),1)
        self.assertEqual(self.seen[0]['reasoning']['effort'],'max')
        self.assertEqual(self.seen[0]['model'],'synthetic-fallback')
        self.assertTrue(any(call.args[:3]==('voice','direct','synthetic-model')
                            and call.kwargs.get('fallback') for call in observed.call_args_list))

    def test_identical_loopback_url_unknown_compatible_keeps_canonical_max(self):
        self.assertEqual(self.call('compatible', 'max').text, 'ok')
        self.assertEqual(self.seen[-1]['reasoning_effort'], 'max')

    def test_direct_responses_max_and_explicit_thinking_precedence(self):
        self.assertEqual(self.call('direct', 'max').text, 'ok')
        self.assertEqual(self.seen[-1]['reasoning']['effort'], 'max')
        self.assertEqual(self.call('embedded', 'max', thinking=1000).text, 'ok')
        self.assertEqual(self.seen[-1]['reasoning_effort'], 'low')

    def test_glm_messages_keeps_canonical_max(self):
        self.assertEqual(self.call('glm', 'max').text, 'ok')
        self.assertEqual(self.seen[-1]['output_config']['effort'], 'max')

    def test_account_route_is_attempt_bound_not_current_config_after_response(self):
        self.cfg['roles']['voice'].update(framework='compatible',model='synthetic-model')
        spec=self.cfg['frameworks']['compatible']
        def reply(request):
            # Simulate a concurrent role switch while the real SDK is in flight.
            self.cfg['roles']['voice']['framework']='direct'
            return self.capture(request)
        with httpx.Client(transport=httpx.MockTransport(reply),trust_env=False) as http, \
                openai.OpenAI(api_key='synthetic-test-only',base_url=spec['base_url'],http_client=http) as sdk, \
                mock.patch.object(llm,'_client_for',return_value=sdk), \
                mock.patch.object(llm,'_resolve_model',side_effect=lambda fw,model:model), \
                mock.patch.object(llm,'_STATE',copy.deepcopy(llm._STATE)), \
                mock.patch.object(llm,'_usage_add'), mock.patch.object(llm,'_journal'):
            result=llm.chat('voice',messages=[{'role':'user','content':'synthetic'}])
        self.assertEqual(self.cfg['roles']['voice']['framework'],'direct')
        self.assertEqual(result.framework,'openai')
        self.assertEqual(result.account_route,'compatible')

    def test_named_alias_actual_sdk_voice_preserves_first_request_across_turns(self):
        import agent
        import frame_continuation as fc
        import os
        self.cfg['roles']['voice'].update(framework='compatible',model='synthetic-model',reasoning_effort='low')
        spec=self.cfg['frameworks']['compatible']
        with tempfile.TemporaryDirectory() as directory, \
                httpx.Client(transport=httpx.MockTransport(self.capture),trust_env=False) as http, \
                openai.OpenAI(api_key='synthetic-test-only',base_url=spec['base_url'],http_client=http) as sdk:
            source={'chat':'named-alias','source':'synthetic-installation','generation':{},
                    'admission':{'owner':'fixture'},'cache_root':directory,
                    'rows':[{'id':'1','source_id':'1','actor':'person','direction':'in','line':'first input'}]}
            receipts=[]; original_loop=agent._terminal_tool_loop
            def loop(**kwargs):
                answer=original_loop(**kwargs); receipts.append(fc.receipt()); return answer
            with mock.patch.dict(os.environ,{'PRAXIS_FRAME_CONTINUATION':'on','PRAXIS_KEAT':'off',
                    'PRAXIS_KEAT_CAPTURE':'off','PRAXIS_FRAME_SHADOW':'off','PRAXIS_FRAME_SERVE':'off'}), \
                    mock.patch.object(llm,'_client_for',return_value=sdk), \
                    mock.patch.object(llm,'_resolve_model',side_effect=lambda fw,model:model), \
                    mock.patch.object(llm,'_config_stamp',return_value='stable-alias-config'), \
                    mock.patch.object(llm,'_STATE',copy.deepcopy(llm._STATE)), \
                    mock.patch.object(llm,'_usage_add'), mock.patch.object(llm,'_journal'), \
                    mock.patch.object(agent,'_build_prompt_parts',return_value=('synthetic persona','','evidence')), \
                    mock.patch.object(agent,'_arm_private_floor',return_value=0), \
                    mock.patch.object(agent,'_work_loop_continue',return_value=False), \
                    mock.patch.object(agent,'_terminal_tool_loop',side_effect=loop):
                ctx=agent.ChannelContext(chat_id='named-alias',is_dm=True,owner=True,principal_id='1')
                for index in range(2):
                    if index: source['rows'] += [
                        {'id':'2','source_id':'2','actor':'agent','direction':'out','line':'accepted output'},
                        {'id':'3','source_id':'3','actor':'person','direction':'in','line':'next input'}]
                    history,current=fc.dialogue(source['rows'])
                    with fc.bind_source(source,lambda:copy.deepcopy(source)):
                        self.assertEqual(agent._voice_impl(current,history,'Synthetic person',ctx=ctx,
                                                          tools_override=[]),'ok')
            self.assertEqual(len(self.seen),2)
            self.assertEqual(receipts[1]['status'],'continued')
            self.assertGreater(receipts[1]['reused_messages'],0)
            self.assertEqual(self.seen[1]['messages'][:len(self.seen[0]['messages'])],self.seen[0]['messages'])


class ExactAccountFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        # Every fake-routing observation remains local to this test; a later
        # snapshot/owner capability test must not inherit its synthetic failure.
        self.enterContext(mock.patch.object(llm, '_STATE', copy.deepcopy(llm._STATE)))
        self.enterContext(mock.patch.object(capabilities, '_CACHE', {'fp':None,'snap':None}))
        for module, name, value in (
            (llm,'CONFIG_PATH',self.base/'llm.json'), (llm,'USAGE_PATH',self.base/'usage.json'),
            (brain,'BASE',self.base), (brain,'STATS_PATH',self.base/'stats.json'),
            (brain,'JOURNAL_DIR',self.base/'journal')):
            self.enterContext(mock.patch.object(module,name,value))
        self.enterContext(mock.patch.object(brain,'_spine'))
        self.enterContext(mock.patch.object(llm,'ping',return_value=(True,'')))
        self.enterContext(mock.patch.object(llm,'_available_models',side_effect=lambda fw:['model-'+fw]))
        self.addCleanup(lambda: llm._CACHE.update(mtime=None,cfg=None))
        cfg = {'frameworks': {name: {'protocol':'openai','api_key':'synthetic-secret-'+name,
                                      'base_url':'https://'+name+'.invalid/v1'} for name in ('a','b')},
               'roles': {'voice':{'framework':'a','model':'model-a','max_tokens':128}}}
        llm.save_config(llm._normalize(cfg)); llm._CACHE.update(mtime=None,cfg=None)

    def test_same_protocol_switch_pins_and_fallback_dispatches_exact_old_account(self):
        self.assertTrue(brain.switch('voice','model-b',endpoint='b',why='synthetic account test')['ok'])
        rc = llm._config()['roles']['voice']
        self.assertEqual((rc['framework'], rc['fallback_framework'], rc['fallback_model']), ('b','a','model-a'))
        seen=[]
        def call(account, model, **kwargs):
            seen.append((account, model))
            if account == 'b': raise TimeoutError('synthetic primary unavailable')
            return llm.LLMResponse(text='fallback ok',framework=account,model=model)
        with mock.patch.object(llm,'_call',side_effect=call), \
                mock.patch.object(llm,'_client_for',return_value=object()), \
                mock.patch.object(llm,'NET_RETRIES',0), \
                mock.patch.object(llm,'_usage_add'), mock.patch.object(llm,'_journal'):
            result=llm.chat('voice',messages=[{'role':'user','content':'synthetic'}])
        self.assertEqual(result.framework,'a')
        self.assertEqual(seen,[('b','model-b'),('a','model-a')])

    def test_owner_capabilities_name_effective_account_protocol_model_not_key(self):
        self.assertTrue(brain.switch('voice','model-b',endpoint='b',why='synthetic account test')['ok'])
        snapshot=capabilities.snapshot()
        snapshot['brain']=llm.snapshot()
        with mock.patch.object(capabilities,'snapshot',return_value=snapshot):
            text=capabilities.describe('owner')
            public=capabilities.describe('group')
        self.assertIn('a (protocol=openai)/model-a',text)
        self.assertIn('b, protocol=openai',text)
        self.assertNotIn('synthetic-secret',text+public)
        self.assertNotIn('a (protocol=openai)/model-a',public)


if __name__ == '__main__': unittest.main()
