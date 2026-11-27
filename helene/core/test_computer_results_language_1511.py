"""Actual computer producer leaves with fake native calls, never desktop effects."""
from contextlib import ExitStack
import json
import unittest
from unittest.mock import Mock,patch
import agent
import frame_language

RAW='Windows body UI Automation tree PowerShell Алекс 中文 {literal} $&'
PATH='/tmp/Windows body Алекс {literal} $&.txt'


class ComputerResultLanguages(unittest.TestCase):
    def guards(self,stack,*,allowed=True,sovereign=True,actor='fixture'):
        stack.enter_context(patch.object(agent,'_computer_allowed',return_value=allowed))
        stack.enter_context(patch.object(agent,'_is_sovereign_actor',return_value=sovereign))
        stack.enter_context(patch.object(agent,'_computer_actor',return_value=actor))

    def test_actual_preflight_captions_follow_all16_axes_without_native_dispatch(self):
        cases=[({'action':'hash'},'path',{'action':'hash'}),({'action':'read'},'path',{'action':'read'}),
          ({'action':'write'},'path',{'action':'write'}),({'action':'read','path':PATH,'start':'not-integer'},'lines_integer',{}),
          ({'action':'run','timeout_ms':-1},'run_timeout',{}),({'action':'run','timeout_ms':True},'run_timeout',{}),
          ({'action':'act_element'},'element_action',{}),({'action':'act_element','element_action':'focus'},'element_key',{}),
          ({'action':'activate'},'activate',{}),({'action':'input'},'events',{}),({'action':'type_text'},'text',{}),
          ({'action':'hotkey'},'keys',{}),({'action':'key'},'key',{}),({'action':'move','x':1},'move',{}),
          ({'action':'click','x':1},'click_coords',{}),({'action':'scroll','steps':101},'steps',{}),
          ({'action':'scroll','delta':1},'delta',{}),({'action':'scroll'},'scroll_amount',{}),
          ({'action':'scroll','steps':1,'x':1},'scroll_coords',{}),({'action':'scroll','steps':1},'scroll_target',{}),
          ({'action':'windows','execution':'system'},'interactive',{'action':'windows'})]
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                with self.subTest(ui=ui,frame=language),ExitStack() as stack:
                    stack.enter_context(patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}))
                    self.guards(stack)
                    native=stack.enter_context(patch.object(agent.body_client,'call',side_effect=AssertionError('preflight dispatched')))
                    process=stack.enter_context(patch.object(agent.body_client,'process_start',side_effect=AssertionError('process launched')))
                    input_call=stack.enter_context(patch.object(agent.body_client,'desktop_input_perform',side_effect=AssertionError('desktop input')))
                    for ask,key,values in cases:
                        with self.subTest(ask=ask):self.assertEqual(agent.tool_computer(**ask),frame_language.text('computer.result.'+key,**values))
                    native.assert_not_called();process.assert_not_called();input_call.assert_not_called()

    def test_actual_permissions_actor_and_sovereign_refusals_never_cross_transport(self):
        cases=[({'action':'read','path':PATH},{'allowed':False},'permission',{'required':'computer.files'}),
         ({'action':'status','execution':'system'},{'sovereign':False},'sovereign_system',{}),
         ({'action':'write','path':PATH},{'sovereign':False},'sovereign_write',{}),
         ({'action':'act_element','element_action':'focus','idempotency_key':'literal'},{'actor':''},'principal',{})]
        for language in frame_language.LANGUAGES:
            for ask,guards,key,values in cases:
                with frame_language.use(language),ExitStack() as stack:
                    self.guards(stack,**guards)
                    native=stack.enter_context(patch.object(agent.body_client,'call',side_effect=AssertionError('refusal dispatched')))
                    self.assertEqual(agent.tool_computer(**ask),frame_language.text('computer.result.'+key,**values));native.assert_not_called()

    def test_file_data_paths_hashes_and_native_diagnostics_remain_literal_across16_axes(self):
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                calls=[]
                def native(capability,args,**kwargs):
                    calls.append((capability,args,kwargs))
                    if capability=='fs.read':return {'ok':True,'text':RAW+'\n二行','size':123,'eof':False,'next_offset':321,'lossy':True}
                    if capability=='fs.hash':return {'ok':True,'sha256':'f'*64}
                    return {'ok':False,'error':RAW,'note':RAW}
                with patch.object(frame_language,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),ExitStack() as stack:
                    self.guards(stack);stack.enter_context(patch.object(agent.body_client,'call',side_effect=native))
                    result=agent.tool_computer(action='read',path=PATH)
                    expected='1: '+RAW+'\n2: 二行\n'+frame_language.text('computer.result.footer',path=PATH,first=1,last=2,total=2,size=123,sha='f'*64)
                    expected+=frame_language.text('computer.result.partial_read',next_offset=321)+frame_language.text('computer.result.lossy')
                    self.assertEqual(result,expected)
                    self.assertEqual([row[0] for row in calls],['fs.read','fs.hash'])
                    self.assertTrue(all(row[1]['path']==PATH for row in calls))
                    failure=json.loads(agent.tool_computer(action='list',path=PATH))
                    self.assertEqual(failure,{'ok':False,'error':RAW,'note':RAW})

    def test_read_footer_is_pinned_while_settings_change_and_missing_hash_stays_unknown(self):
        config={'lang':'en','agent':{'frame_language':'fr'}}
        def native(capability,args,**kwargs):
            config['agent']['frame_language']='zh'
            return {'ok':True,'text':RAW,'size':12} if capability=='fs.read' else {'ok':False,'error':RAW}
        with patch.object(frame_language,'settings',side_effect=lambda:config),ExitStack() as stack:
            self.guards(stack);stack.enter_context(patch.object(agent.body_client,'call',side_effect=native))
            result=agent.tool_computer(action='read',path=PATH)
        with frame_language.use('fr'):
            self.assertEqual(result,'1: '+RAW+'\n'+frame_language.text('computer.result.footer',path=PATH,first=1,last=1,total=1,size=12,sha='—'))

    def test_limited_read_marker_keeps_exact_prefix_and_reports_omitted_character_count(self):
        content=RAW*500;numbered='1: '+content
        for language in frame_language.LANGUAGES:
            def native(capability,args,**kwargs):
                return {'ok':True,'text':content,'size':len(content.encode())} if capability=='fs.read' else {'ok':True,'sha256':'a'*64}
            with frame_language.use(language),ExitStack() as stack:
                self.guards(stack);stack.enter_context(patch.object(agent.body_client,'call',side_effect=native))
                result=agent.tool_computer(action='read',path=PATH)
                self.assertTrue(result.startswith(numbered[:24000]))
                self.assertIn(frame_language.text('computer.result.truncated',omitted=len(numbered)-24000),result)

    def test_write_cap_and_unconfirmed_receipt_do_not_invent_effect_or_rewrite_content(self):
        for language in frame_language.LANGUAGES:
            with frame_language.use(language),ExitStack() as stack:
                self.guards(stack)
                native=stack.enter_context(patch.object(agent.body_client,'call',return_value={'ok':True,'type':'accepted','operation_id':RAW}))
                with patch.object(agent,'_COMPUTER_WRITE_CAP',16):
                    self.assertEqual(agent.tool_computer(action='write',path=PATH,content=RAW),frame_language.text('computer.result.write_cap',cap=16))
                    native.assert_not_called()
                result=json.loads(agent.tool_computer(action='write',path=PATH,content=RAW))
                self.assertFalse(result['confirmed']);self.assertEqual(result['operation_id'],RAW)
                self.assertEqual(result['note'],frame_language.text('computer.result.write_unconfirmed'))
                native.assert_called_once();self.assertEqual(native.call_args.args[1],{'path':PATH,'content':RAW})

    def test_observation_failure_and_image_bearing_value_remain_read_only_and_literal(self):
        class Images(str):images=('unchanged-image',)
        image=Images(RAW)
        for language in frame_language.LANGUAGES:
            with frame_language.use(language),ExitStack() as stack:
                self.guards(stack)
                native=stack.enter_context(patch.object(agent.body_client,'call',return_value={'ok':False,'error':RAW}))
                self.assertEqual(agent.tool_computer(action='observe',path=PATH),frame_language.text('computer.result.export',detail=RAW))
                artifact={'size':1,'sha256':'a'*64,'mime':'image/png'};native.return_value={'ok':True,'artifact':artifact}
                with patch.object(agent,'_observe_computer_artifact',return_value=image) as observe:
                    self.assertIs(agent.tool_computer(action='observe',path=PATH),image);observe.assert_called_once_with(artifact)
                native.return_value={'ok':True,'artifact':dict(artifact,size=agent._OBSERVE_FILE_CAP+1)}
                with patch.object(agent,'_observe_computer_artifact') as observe:
                    self.assertEqual(agent.tool_computer(action='observe',path=PATH),frame_language.text('computer.result.observe_cap',size=agent._OBSERVE_FILE_CAP+1,cap=agent._OBSERVE_FILE_CAP))
                    observe.assert_not_called()
                self.assertTrue(all(call.args[0]=='fs.export' and call.args[1]=={'path':PATH} for call in native.call_args_list))
                self.assertEqual(image.images,('unchanged-image',))

    def test_protocol_identifier_lists_remain_canonical(self):
        reference=None
        for language in frame_language.LANGUAGES:
            with frame_language.use(language),ExitStack() as stack:
                self.guards(stack)
                actions=agent.tool_computer(action='not-an-action')
                if reference is None:reference=actions
                self.assertEqual(actions,reference);self.assertTrue(actions.startswith('action: status | inventory'))
                self.assertEqual(agent.tool_computer(action='scroll',direction='not-a-direction'),'scroll: direction = up | down | left | right')


if __name__=='__main__':unittest.main()
