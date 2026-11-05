"""Loopback wire evidence for role tiers, computer images and preserved cores."""
from pathlib import Path
from contextlib import ExitStack
import copy
import importlib.util
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from importlib.machinery import SourceFileLoader
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
os.environ['PRAXIS_TEST'] = '1'
sys.path[:0] = [str(ROOT/'desk'), str(ROOT/'helene/core'), str(ROOT/'praxis')]
import _sandbox
assert _sandbox.activate_if_testing()
import llm
from localharness import boot, fast_mode, python_relay


class Wire(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps({'data':[{'id':'gpt-6.1-sol','reasoning_efforts':['low','high','ultra'],
            'default_reasoning_effort':'high','service_tiers':[{'id':'priority','name':'Fast'}],
            'additional_speed_tiers':['fast']}]}).encode())
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.received.append((self.path, body))
        if self.server.fail_model == body['model']:
            self.send_response(getattr(self.server,'fail_status',503)); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(b'{"error":{"message":"controlled refusal","type":"server_error"}}'); return
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        actual = self.server.actual
        if self.path.endswith('/responses'):
            output = [{'id':'msg_one','type':'message','role':'assistant','content':[{'type':'output_text','text':'accepted','annotations':[]}]}]
            if body.get('tools'):
                output = [{'type':'function_call','call_id':'cu_one','name':'computer','arguments':'{"action":"read"}'}]
            response = {'id':'resp_one','object':'response','status':'completed','model':body['model'],
                'service_tier':actual,'output':output,'usage':{'input_tokens':9,'output_tokens':2,'input_tokens_details':{'cached_tokens':0}}}
            if hasattr(self.server,'begin_tier'):
                early=dict(response,status='in_progress',output=[],service_tier=self.server.begin_tier)
                self.wfile.write(('event: response.created\ndata: '+json.dumps({'type':'response.created','response':early})+'\n\n').encode())
            if hasattr(self.server,'terminal_tier'):
                if self.server.terminal_tier=='__absent__':response.pop('service_tier')
                else:response['service_tier']=self.server.terminal_tier
            if getattr(self.server,'torn',False):
                early=dict(response,status='in_progress',output=[])
                events=[{'type':'response.created','response':early},
                    {'type':'response.output_text.delta','item_id':'msg_one','output_index':0,'content_index':0,'delta':'started'},
                    {'type':'error','code':'controlled','param':None,'message':'controlled torn stream'}]
                for event in events:
                    self.wfile.write(('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n').encode())
                return
            event = {'type':'response.completed','response':response}
            self.wfile.write(('event: response.completed\ndata: '+json.dumps(event)+'\n\n').encode())
        else:
            for delta, finish in (({'content':'accepted'}, None), ({}, 'stop')):
                packet={'id':'chat_one','object':'chat.completion.chunk','created':1,'model':body['model'],
                    'service_tier':actual,'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                if finish is not None and hasattr(self.server, 'terminal_tier'):
                    packet['service_tier'] = self.server.terminal_tier
                if finish is not None and getattr(self.server,'torn',False):packet['choices'][0]['finish_reason']='error'
                self.wfile.write(('data: '+json.dumps(packet)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')


class FastMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        from openai import OpenAI
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Wire)
        cls.server.received=[];cls.server.actual='priority';cls.server.fail_model=None
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.client=OpenAI(api_key='loopback-fixture',base_url=f'http://127.0.0.1:{cls.server.server_port}/v1',
            max_retries=0,http_client=httpx.Client(trust_env=False))
    @classmethod
    def tearDownClass(cls):
        cls.client.close();cls.server.shutdown();cls.server.server_close();cls.thread.join(2)
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.server.received.clear();self.server.actual='priority';self.server.fail_model=None
        if hasattr(self.server,'terminal_tier'):del self.server.terminal_tier
        for field in ('fail_status','begin_tier','torn'):
            if hasattr(self.server,field):delattr(self.server,field)
        self.cfg={'frameworks':{'relay':{'protocol':'openai','provider':'relay','base_url':str(self.client.base_url),'api_key':'fixture'},
             'api':{'protocol':'openai','provider':'openai','base_url':str(self.client.base_url),'api_key':'fixture'},
             'anthropic':{'protocol':'anthropic','provider':'anthropic','api_key':'fixture'}},
            'roles':{'voice':{'framework':'relay','model':'gpt-6.1-sol','max_tokens':1024,'reasoning_effort':'high','service_tier':'priority','vision_prepass':False},
                     'evaluator':{'framework':'relay','model':'gpt-6.1-sol','max_tokens':1024}},'limits':{}}
        self.stack.enter_context(patch.object(llm,'_config',side_effect=lambda:self.cfg))
        self.stack.enter_context(patch.object(llm,'_client_for',return_value=self.client))
        self.stack.enter_context(patch.object(llm,'_available_models',return_value=[]))
        self.stack.enter_context(patch.object(llm,'NET_RETRIES',0))
        self.stack.enter_context(patch.object(llm,'_endpoint_hold',return_value=None))
        self.stack.enter_context(patch.object(llm,'_usage_add'))
        self.stack.enter_context(patch.object(llm,'_brain_note'))
        self.stack.enter_context(patch.object(llm,'_journal'))
        self.stack.enter_context(patch.object(llm,'_call_trace'))
        llm._MODEL_DETAILS.clear()
    def call(self, role='voice', mode='chat_completions', **kwargs):
        self.cfg['frameworks']['relay']['api_mode']=mode
        return llm.chat(role,messages=[{'role':'user','content':'controlled request'}],**kwargs)
    def test_chat_and_responses_actual_wire_tier_does_not_change_effort(self):
        for mode in ('chat_completions','responses'):
            with self.subTest(mode=mode):
                result=self.call(mode=mode);wire=self.server.received[-1][1]
                self.assertEqual(wire['service_tier'],'priority')
                self.assertEqual(wire.get('reasoning_effort') or wire.get('reasoning',{}).get('effort'),'high')
                self.assertEqual(result.service_tier_requested,'priority');self.assertEqual(result.service_tier,'priority')
    def test_failed_actual_call_clears_previous_actual_and_keeps_requested(self):
        from openai import BadRequestError
        self.call();self.server.fail_model='gpt-6.1-sol';self.server.fail_status=400
        with self.assertRaises(BadRequestError):self.call()
        self.assertEqual(llm.snapshot()['voice']['service_tier_requested'],'priority')
        self.assertIsNone(llm.snapshot()['voice']['service_tier_actual'])
        trace=llm._call_trace.call_args.kwargs
        self.assertEqual(trace['service_tier_requested'],'priority');self.assertIsNone(trace['service_tier_actual'])
        before=copy.deepcopy(llm._STATE['voice'])
        with self.assertRaises(ValueError):self.call(reasoning_effort='none')
        self.assertEqual(llm._STATE['voice'].get('service_tier_actual'),before.get('service_tier_actual'))
    def test_torn_stream_retains_observed_actual_for_failed_attempt(self):
        self.server.torn=True
        for mode in ('chat_completions','responses'):
            with self.subTest(mode=mode),self.assertRaises(llm.TornStreamError):self.call(mode=mode,allow_fallback=False)
            state=llm.snapshot()['voice']
            self.assertEqual(state['service_tier_requested'],'priority');self.assertEqual(state['service_tier_actual'],'priority')
    def test_abandoned_failure_does_not_overwrite_newer_tier_state(self):
        from openai import BadRequestError
        self.server.actual='default';self.call();self.server.fail_model='gpt-6.1-sol';self.server.fail_status=400
        with patch.object(llm,'call_abandoned',return_value=True),self.assertRaises(BadRequestError):self.call()
        self.assertEqual(llm.snapshot()['voice']['service_tier_actual'],'default')
    def test_provider_downgrade_is_actual_default(self):
        self.server.actual='default';result=self.call()
        self.assertEqual(result.service_tier_requested,'priority');self.assertEqual(result.service_tier,'default')
        snapshot=llm.snapshot()['voice'];self.assertEqual(snapshot['service_tier_actual'],'default')
    def test_terminal_explicit_null_clears_early_priority_with_actual_sdk(self):
        self.server.terminal_tier=None;result=self.call()
        self.assertEqual(result.service_tier_requested,'priority');self.assertIsNone(result.service_tier)
    def test_future_opaque_actual_tier_is_preserved(self):
        self.server.actual='future-processing-lane'
        for mode in ('chat_completions','responses'):
            self.assertEqual(self.call(mode=mode).service_tier,'future-processing-lane')
    def test_responses_carries_early_tier_only_when_terminal_field_is_absent(self):
        self.server.begin_tier='priority'
        for terminal,expected in (('__absent__','priority'),(None,None),('default','default')):
            self.server.terminal_tier=terminal
            self.assertEqual(self.call(mode='responses').service_tier,expected)
    def test_unspecified_evaluator_does_not_inherit_voice_fast(self):
        self.call('evaluator');self.assertNotIn('service_tier',self.server.received[-1][1])
    def test_saved_legacy_none_uses_model_default_without_rewriting_profile(self):
        self.cfg['roles']['voice']['reasoning_effort']='none'
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'profile.json';path.write_text(json.dumps(self.cfg),encoding='utf8');before=path.read_bytes()
            self.call();wire=self.server.received[-1][1]
            self.assertNotIn('reasoning_effort',wire);self.assertNotIn('reasoning',wire)
            self.assertNotIn('reasoning_effort',llm._normalize(self.cfg)['roles']['voice'])
            with self.assertRaises(ValueError):self.call(reasoning_effort='none')
            self.assertEqual(len(self.server.received),1);self.assertEqual(path.read_bytes(),before)
    def test_default_auto_and_alias_have_distinct_wire_controls(self):
        for value,expected in (('fast','priority'),('default','default'),('auto','auto')):
            self.cfg['roles']['voice']['service_tier']=value;self.call()
            self.assertEqual(self.server.received[-1][1]['service_tier'],expected)
    def test_computer_image_and_tool_path_receives_the_same_tier(self):
        self.cfg['frameworks']['relay']['api_mode']='responses'
        message={'role':'user','content':[{'type':'text','text':'read the screen'},
            {'type':'image','source':{'type':'base64','media_type':'image/png',
                'data':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5eEAAAAASUVORK5CYII='}}]}
        tool={'name':'computer','description':'read screen','input_schema':{'type':'object','properties':{'action':{'type':'string'}}}}
        result=llm.chat('voice',messages=[message],tools=[tool]);wire=self.server.received[-1][1]
        self.assertEqual(wire['service_tier'],'priority');self.assertEqual(result.stop_reason,'tool_use')
        self.assertIn('input_image',json.dumps(wire));self.assertEqual(message['content'][0]['text'],'read the screen')
    def test_unsupported_fast_route_and_codex_none_make_no_post(self):
        for framework,effort in (('api','high'),('anthropic','high'),('relay','none')):
            self.cfg['roles']['voice'].update(framework=framework,reasoning_effort=effort)
            with self.subTest(framework=framework),self.assertRaises(ValueError):
                self.call(**({'reasoning_effort':'none'} if framework=='relay' else {}))
        self.assertEqual(self.server.received,[])
    def test_model_catalog_efforts_validate_without_downshift(self):
        llm._MODEL_DETAILS['relay']={'expires':llm._time.time()+60,'endpoint':llm._endpoint_key('relay'),
            'models':{'gpt-6.1-sol':{'reasoning_efforts':['medium','high']}}}
        self.cfg['roles']['voice']['reasoning_effort']='low'
        with self.assertRaises(llm.UnsupportedReasoningEffort):self.call()
        self.assertFalse(self.server.received)
    def test_advertised_ultra_is_emitted_and_unknown_future_effort_is_refused(self):
        llm._MODEL_DETAILS['relay']={'expires':llm._time.time()+60,'endpoint':llm._endpoint_key('relay'),
            'models':{'gpt-6.1-sol':{'reasoning_efforts':['high','ultra','mega']}}}
        self.cfg['roles']['voice']['reasoning_effort']='ultra';self.call()
        self.assertEqual(self.server.received[-1][1]['reasoning_effort'],'ultra')
        self.cfg['roles']['voice']['reasoning_effort']='mega'
        with self.assertRaises(llm.UnsupportedReasoningEffort):self.call()
        self.assertEqual(len(self.server.received),1)
    def test_known_model_without_fast_tier_refuses_before_wire(self):
        llm._MODEL_DETAILS['relay']={'expires':llm._time.time()+60,'endpoint':llm._endpoint_key('relay'),
            'models':{'gpt-6.1-sol':{'service_tiers':[],'additional_speed_tiers':[]}}}
        with self.assertRaisesRegex(ValueError,'unavailable'):self.call()
        self.assertFalse(self.server.received)
    def test_explicit_empty_tier_capability_overrides_additional_alias(self):
        card={'service_tiers':[],'additional_speed_tiers':['fast']}
        llm._MODEL_DETAILS['relay']={'expires':llm._time.time()+60,'endpoint':llm._endpoint_key('relay'),
            'models':{'gpt-6.1-sol':card}}
        with self.assertRaisesRegex(ValueError,'unavailable'):self.call()
        self.assertFalse(self.server.received)
        name='_fast_capability_presence_fixture';path=ROOT/'desk/server/deskctl.py'
        spec=importlib.util.spec_from_file_location(name,path);api=importlib.util.module_from_spec(spec)
        sys.modules[name]=api;spec.loader.exec_module(api);self.addCleanup(sys.modules.pop,name,None)
        account=self.cfg['frameworks']['relay']
        api._MODEL_CAPABILITIES['relay']={'expires':api.time.monotonic()+60,'base_url':account['base_url'],
            'api_key':account['api_key'],'models':{'gpt-6.1-sol':card}}
        self.assertFalse(api._fast_available(self.cfg,'voice'))
        card.pop('service_tiers')
        self.assertTrue(api._fast_available(self.cfg,'voice'))
        self.call();self.assertEqual(self.server.received[-1][1]['service_tier'],'priority')
    def test_fallback_keeps_requested_tier(self):
        self.cfg['frameworks']['spare']=dict(self.cfg['frameworks']['relay'])
        self.cfg['roles']['voice'].update(fallback_model='gpt-6-sol',fallback_framework='spare')
        self.server.fail_model='gpt-6.1-sol';result=self.call()
        self.assertEqual([body['service_tier'] for _,body in self.server.received],['priority','priority'])
        self.assertEqual(result.service_tier_requested,'priority')
    def test_tier_rejection_has_one_post_and_keeps_the_requested_field(self):
        from openai import BadRequestError
        self.cfg['frameworks']['spare']=dict(self.cfg['frameworks']['relay'])
        self.cfg['roles']['voice'].update(fallback_model='gpt-6-sol',fallback_framework='spare')
        self.server.fail_model='gpt-6.1-sol';self.server.fail_status=400
        with self.assertRaises(BadRequestError):self.call()
        self.assertEqual(len(self.server.received),1)
        self.assertEqual(self.server.received[0][1]['service_tier'],'priority')
    def test_actual_preserved_consumer_sdk_edge_and_private_status(self):
        source=ROOT/'desk/tests/fixtures/pre-alias-praxis-llm.py.txt';before=source.read_bytes()
        name='_preserved_fast_mode_fixture'
        spec=importlib.util.spec_from_file_location(name,source,loader=SourceFileLoader(name,str(source)))
        old=importlib.util.module_from_spec(spec);sys.modules[name]=old;spec.loader.exec_module(old)
        self.addCleanup(sys.modules.pop,name,None)
        config={'frameworks':{'openai':{'protocol':'openai','provider':'relay','api_key':'fixture','base_url':str(self.client.base_url)}},
            'roles':{'voice':{'framework':'openai','model':'gpt-6.1-sol','reasoning_effort':'ultra','service_tier':'fast'}}}
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(old,'_config',side_effect=lambda:old._normalize(config)))
            stack.enter_context(patch.object(old,'_available_models',return_value=[]))
            for field in ('_usage_add','_brain_note','_journal','_call_trace'):stack.enter_context(patch.object(old,field))
            old.use_test_client(self.client,'openai');self.assertTrue(fast_mode.install(old,temporary))
            self.server.terminal_tier=None
            response=old.chat('voice',messages=[{'role':'user','content':'preserved call'}])
            wire=self.server.received[-1][1]
            self.assertEqual(wire['service_tier'],'priority');self.assertEqual(wire['reasoning_effort'],'ultra')
            self.assertEqual(response.service_tier_requested,'priority');self.assertIsNone(response.service_tier)
            status=json.loads((Path(temporary)/'memory/.state/service-tiers.json').read_text(encoding='utf8'))
            self.assertIsNone(status['voice']['actual']);self.assertEqual(status['voice']['requested'],'priority')
            config['roles']['voice']['reasoning_effort']='none';saved=copy.deepcopy(config)
            old.chat('voice',messages=[{'role':'user','content':'legacy none'}])
            self.assertNotIn('reasoning_effort',self.server.received[-1][1]);self.assertEqual(config,saved)
        self.assertEqual(source.read_bytes(),before)
    def test_preserved_failed_call_records_unknown_and_foreign_leg_clears_tier(self):
        from openai import BadRequestError
        source=ROOT/'desk/tests/fixtures/pre-alias-praxis-llm.py.txt'
        name='_preserved_fast_failure_fixture'
        spec=importlib.util.spec_from_file_location(name,source,loader=SourceFileLoader(name,str(source)))
        old=importlib.util.module_from_spec(spec);sys.modules[name]=old;spec.loader.exec_module(old)
        self.addCleanup(sys.modules.pop,name,None)
        config={'frameworks':{'openai':{'protocol':'openai','provider':'relay','api_key':'fixture','base_url':str(self.client.base_url)},
            'anthropic':{'protocol':'anthropic','provider':'anthropic','api_key':'fixture','base_url':'https://api.anthropic.invalid'}},
            'roles':{'voice':{'framework':'openai','model':'gpt-6.1-sol','reasoning_effort':'high','service_tier':'priority'}}}
        foreign=types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kwargs:types.SimpleNamespace(
            model='claude-test',content=[types.SimpleNamespace(type='text',text='foreign response')],
            usage=types.SimpleNamespace(input_tokens=1,output_tokens=2),stop_reason='end_turn')))
        with tempfile.TemporaryDirectory() as temporary,ExitStack() as stack:
            stack.enter_context(patch.object(old,'_config',side_effect=lambda:old._normalize(config)))
            stack.enter_context(patch.object(old,'_available_models',return_value=[]))
            for field in ('_usage_add','_brain_note','_journal','_call_trace'):stack.enter_context(patch.object(old,field))
            old.use_test_client(self.client,'openai');old.use_test_client(foreign,'anthropic');fast_mode.install(old,temporary)
            status=Path(temporary)/'memory/.state/service-tiers.json'
            status.parent.mkdir(parents=True);status.write_text('[]',encoding='utf8')
            old.chat('voice',messages=[{'role':'user','content':'success'}])
            self.assertEqual(json.loads(status.read_text(encoding='utf8'))['voice']['actual'],'priority')
            self.server.fail_model='gpt-6.1-sol';self.server.fail_status=400
            with self.assertRaises(BadRequestError):old.chat('voice',messages=[{'role':'user','content':'failure'}])
            status=Path(temporary)/'memory/.state/service-tiers.json'
            receipt=json.loads(status.read_text(encoding='utf8'))['voice']
            self.assertEqual(receipt['requested'],'priority');self.assertIsNone(receipt['actual'])
            self.server.fail_model=None;self.server.torn=True
            config['roles']['voice'].update(service_tier='default',fallback_model='claude-test',fallback_framework='anthropic')
            result=old.chat('voice',messages=[{'role':'user','content':'foreign fallback'}])
            self.assertEqual(result.text,'foreign response');self.assertIsNone(result.service_tier_requested);self.assertIsNone(result.service_tier)
            receipt=json.loads(status.read_text(encoding='utf8'))['voice']
            self.assertIsNone(receipt['requested']);self.assertIsNone(receipt['actual'])
    def test_brain_http_surface_canonicalizes_and_withdraws_tier_without_credentials(self):
        path=ROOT/'desk/server/deskctl.py';name='_fast_brain_api_fixture'
        spec=importlib.util.spec_from_file_location(name,path);api=importlib.util.module_from_spec(spec)
        sys.modules[name]=api;spec.loader.exec_module(api);self.addCleanup(sys.modules.pop,name,None)
        with tempfile.TemporaryDirectory() as temporary:
            tree=Path(temporary);file=tree/'memory/llm.json';file.parent.mkdir();file.write_text(json.dumps(self.cfg),encoding='utf8')
            with patch.object(api,'TREE',tree):
                self.assertTrue(api.brain_set('voice',{'service_tier':'fast'})['ok'])
                self.assertEqual(json.loads(file.read_text(encoding='utf8'))['roles']['voice']['service_tier'],'priority')
                models=api.brain_models();card=models['by_framework']['relay']['model_details'][0]
                self.assertEqual(card['reasoning_efforts'],['low','high','ultra']);self.assertEqual(card['service_tiers'][0]['id'],'priority')
                self.assertTrue(api.brain_state()['service_tiers']['voice']['available'])
                self.assertFalse(api.brain_set('voice',{'reasoning_effort':'none'})['ok'])
                self.assertFalse(api.brain_set('voice',{'framework':'api','service_tier':'priority'})['ok'])
                self.assertTrue(api.brain_set('voice',{'service_tier':''})['ok'])
                self.assertNotIn('service_tier',json.loads(file.read_text(encoding='utf8'))['roles']['voice'])
                serialized=json.dumps(api.brain_state());self.assertNotIn('"api_key"',serialized)
                api.brain_set('voice',{'service_tier':'priority'})
                self.assertTrue(api.brain_set('voice',{'model':'no-fast-catalog'})['ok'])
                saved=json.loads(file.read_text(encoding='utf8'))['roles']['voice']
                self.assertNotIn('service_tier',saved);self.assertEqual(saved['reasoning_effort'],'high')


class Settings(unittest.TestCase):
    def test_boot_tracks_owned_tier_with_clear_and_independent_role_edits(self):
        cfg={'model':{'provider':'chatgpt','framework':'openai','model':'gpt-6.1-sol','service_tier':'fast'}}
        built=boot._brain_config(cfg);self.assertEqual(built['roles']['voice']['service_tier'],'priority')
        self.assertNotIn('service_tier',built['roles']['evaluator'])
        receipt=boot._projected_fields(built);del cfg['model']['service_tier'];plain=boot._brain_config(cfg)
        self.assertNotIn('service_tier',boot._merge_brain(built,plain,receipt)['roles']['voice'])
        independent=copy.deepcopy(built);independent['roles']['voice']['service_tier']='default'
        self.assertEqual(boot._merge_brain(independent,plain,receipt)['roles']['voice']['service_tier'],'default')
        cfg['model'].update(provider='api',service_tier='priority')
        self.assertNotIn('service_tier',boot._brain_config(cfg)['roles']['voice'])
    def test_missing_relay_effort_uses_model_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'helene.json';path.write_text('{"model":{}}',encoding='utf8')
            with patch.object(python_relay,'control',return_value={}) as control:
                python_relay.hot_settings(types.SimpleNamespace(config=path))
            self.assertEqual(control.call_args.kwargs['settings']['defaults']['reasoning'],'default')
    def test_saved_legacy_none_stays_raw_while_boot_and_relay_use_default(self):
        cfg={'model':{'provider':'chatgpt','framework':'openai','model':'gpt-6.1-sol','reasoning_effort':'none'}}
        before=copy.deepcopy(cfg);self.assertNotIn('reasoning_effort',boot._brain_config(cfg)['roles']['voice'])
        self.assertEqual(cfg,before)
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'helene.json';path.write_text(json.dumps(cfg),encoding='utf8');raw=path.read_bytes()
            with patch.object(python_relay,'control',return_value={}) as control:
                python_relay.hot_settings(types.SimpleNamespace(config=path))
            self.assertEqual(control.call_args.kwargs['settings']['defaults']['reasoning'],'default')
            self.assertEqual(path.read_bytes(),raw)
    def test_preserved_receipt_is_newer_than_legacy_trace_and_new_null_clears(self):
        name='_fast_service_receipt_fixture';path=ROOT/'desk/server/deskctl.py'
        spec=importlib.util.spec_from_file_location(name,path);api=importlib.util.module_from_spec(spec)
        sys.modules[name]=api;spec.loader.exec_module(api);self.addCleanup(sys.modules.pop,name,None)
        with tempfile.TemporaryDirectory() as temporary:
            config=Path(temporary)/'memory/llm.json';state=config.parent/'.state';state.mkdir(parents=True)
            (state/'service-tiers.json').write_text(json.dumps({'voice':{'requested':'priority','actual':'priority','at':20}}),encoding='utf8')
            log=state/'llm_calls.jsonl'
            rows=[{'role':'voice','ts':30}, {'role':'voice','ts':10,'service_tier_actual':'default'}]
            log.write_text('\n'.join(map(json.dumps,rows)),encoding='utf8')
            self.assertEqual(api._recent_service_tiers(config)['voice']['actual'],'priority')
            rows.append({'role':'voice','ts':40,'service_tier_requested':'priority','service_tier_actual':None})
            log.write_text('\n'.join(map(json.dumps,rows)),encoding='utf8')
            self.assertIsNone(api._recent_service_tiers(config)['voice']['actual'])
    def test_normalization_rejects_invalid_and_keeps_optional_field_absent(self):
        self.assertEqual(fast_mode.normalize('fast'),'priority');self.assertIsNone(fast_mode.normalize(''))
        for value in (True,{},'turbo'):
            with self.assertRaises(ValueError):fast_mode.normalize(value)
        cfg=llm._normalize({'roles':{'voice':{'service_tier':'fast'}}})
        self.assertEqual(cfg['roles']['voice']['service_tier'],'priority')
        self.assertNotIn('service_tier',llm._normalize({})['roles']['voice'])


if __name__=='__main__': unittest.main()
