"""Responses SDK selection, streamed tool calls and scoped replay without network."""
import types
import unittest
from unittest import mock
import llm


class ResponsesProtocol(unittest.TestCase):
    def client(self, events):
        create = mock.Mock(return_value=iter(events))
        return types.SimpleNamespace(base_url='https://api.openai.com/v1', api_key='fixture',
                                     responses=types.SimpleNamespace(create=create))

    def call(self, client, messages=None, tools=None):
        with mock.patch.object(llm, 'cache_address', return_value=None):
            return llm._call_responses(client, 'fixture-model', system='System',
                messages=messages or [{'role':'user','content':'Go'}], tools=tools,
                max_tokens=100, thinking=False)

    def test_auto_sdk_fallback_and_explicit_unsupported_mode(self):
        old = types.SimpleNamespace(base_url='https://api.openai.com/v1')
        modern = self.client([])
        config = {'frameworks':{'openai':{}}}
        with mock.patch.object(llm, '_client_for', return_value=old) as client, \
             mock.patch.object(llm, '_config', return_value=config), \
             mock.patch.object(llm, '_protocol', return_value='openai'), \
             mock.patch.object(llm, '_call_openai', return_value='chat') as chat, \
             mock.patch.object(llm, '_call_responses', return_value='responses') as responses:
            self.assertEqual(llm._call('openai','fixture',_resolved=True),'chat')
            responses.assert_not_called()
            client.return_value = modern
            self.assertEqual(llm._call('openai','fixture',_resolved=True),'responses')
            client.return_value = old;config['frameworks']['openai']['api_mode']='responses'
            with self.assertRaisesRegex(RuntimeError,'SDK.*Responses'):
                llm._call('openai','fixture',_resolved=True)
            self.assertEqual(chat.call_count,1)

    def test_streamed_tool_call_replays_opaque_items_and_actual_tool_result(self):
        output = [{'type':'reasoning','id':'rs_fixture','encrypted_content':'opaque-fixture'},
                  {'type':'function_call','id':'fc_fixture','call_id':'call_fixture',
                   'name':'shell','arguments':'{"command":"echo ok"}'}]
        final = {'status':'completed','output':output,'usage':{'input_tokens':7,
                 'output_tokens':3,'input_tokens_details':{'cached_tokens':2}}}
        client = self.client([types.SimpleNamespace(type='response.completed',response=final)])
        tools=[{'name':'shell','description':'Run command','input_schema':{
            'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}]
        answer=self.call(client,tools=tools)
        self.assertEqual(answer.stop_reason,'tool_use')
        self.assertEqual(answer.blocks[0]['input'],{'command':'echo ok'})
        self.assertEqual(answer.usage['in'],5);self.assertEqual(answer.usage['cache_read'],2)
        sent=client.responses.create.call_args.kwargs
        self.assertTrue(sent['stream']);self.assertFalse(sent['store'])
        self.assertEqual(sent['tools'][0]['name'],'shell')
        self.assertEqual(sent['include'],['reasoning.encrypted_content'])
        messages=[llm.assistant_message(answer),{'role':'user','content':[
            {'type':'tool_result','tool_use_id':'call_fixture','content':'ok'}]}]
        client.responses.create.return_value=iter([types.SimpleNamespace(type='response.completed',
            response={'status':'completed','output':[{'type':'message','role':'assistant',
                      'content':[{'type':'output_text','text':'Done'}]}]})])
        self.assertEqual(self.call(client,messages).text,'Done')
        self.assertEqual(client.responses.create.call_args.kwargs['input'],output+[
            {'type':'function_call_output','call_id':'call_fixture','output':'ok'}])
        other=self.client([]);other.api_key='other-fixture'
        self.assertIsNone(llm._replay(messages[0],llm._continuation_scope(other,'responses','fixture-model'),'responses'))

    def test_stream_without_terminal_event_retains_partial_and_never_replays_call(self):
        item={'type':'function_call','call_id':'call_fixture','name':'shell','arguments':'{}'}
        client=self.client([types.SimpleNamespace(type='response.output_item.done',output_index=0,item=item)])
        with self.assertRaises(llm.TornStreamError) as caught:self.call(client)
        self.assertEqual(caught.exception.partial.continuation['payload'],[item])
        client.responses.create.assert_called_once()

    def test_output_budget_stop_is_not_an_empty_transport_retry(self):
        client=self.client([types.SimpleNamespace(type='response.incomplete',response={
            'status':'incomplete','output':[], 'incomplete_details':{'reason':'max_output_tokens'}})])
        self.assertEqual(self.call(client).stop_reason,'max_tokens')
        client.responses.create.assert_called_once()

    def test_transport_failure_before_output_keeps_empty_retry_classification(self):
        def broken():
            yield from ()
            raise ConnectionError('fixture transport interrupted')
        client = self.client([])
        client.responses.create.return_value = broken()
        with mock.patch.object(llm, '_stream_transport_error', return_value=True):
            with self.assertRaises(llm.EmptyResponseError):
                self.call(client)

    def test_transport_failure_after_opaque_output_is_never_empty(self):
        item = {'type': 'reasoning', 'id': 'opaque', 'encrypted_content': 'fixture'}
        def broken():
            yield types.SimpleNamespace(type='response.output_item.done', output_index=0, item=item)
            raise ConnectionError('fixture transport interrupted')
        client = self.client([])
        client.responses.create.return_value = broken()
        with mock.patch.object(llm, '_stream_transport_error', return_value=True):
            with self.assertRaises(llm.TornStreamError) as caught:
                self.call(client)
        self.assertEqual(caught.exception.partial.continuation['payload'], [item])

    def test_nonstream_empty_envelope_is_retryable_but_reasoning_is_not(self):
        for content, reasoning, error in ((None, None, llm.EmptyResponseError),
                                          ('   ', None, llm.EmptyResponseError),
                                          (None, 'opaque reasoning', llm.TornStreamError)):
            with self.subTest(content=content, reasoning=reasoning):
                message = types.SimpleNamespace(content=content, tool_calls=[], reasoning_content=reasoning)
                completion = types.SimpleNamespace(choices=[types.SimpleNamespace(
                    message=message, finish_reason=None)], usage=None)
                create = mock.Mock(return_value=completion)
                client = types.SimpleNamespace(base_url='https://api.openai.com/v1', api_key='fixture',
                    chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
                with mock.patch.object(llm, 'cache_address', return_value=None):
                    with self.assertRaises(error):
                        llm._call_openai(client, 'fixture', system='',
                            messages=[{'role':'user','content':'Go'}], tools=None, max_tokens=100, thinking=False)
                create.assert_called_once()


    def test_completed_opaque_responses_output_never_retries_as_empty(self):
        item = {'type': 'reasoning', 'id': 'opaque', 'encrypted_content': 'fixture'}
        client = self.client([types.SimpleNamespace(type='response.completed',
            response={'status': 'completed', 'output': [item]})])
        with mock.patch.object(llm, '_call', side_effect=lambda *a, **kw: self.call(client)) as call:
            with self.assertRaises(llm.TornStreamError) as caught:
                llm._call_retrying_empty('openai', 'fixture', retries=2)
        call.assert_called_once()
        self.assertEqual(caught.exception.partial.continuation['payload'], [item])

    def test_completed_opaque_chat_output_never_retries_as_empty(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                message = types.SimpleNamespace(content=None, tool_calls=[], reasoning_content='opaque')
                choice = types.SimpleNamespace(message=message, delta=message, finish_reason='stop')
                completion = types.SimpleNamespace(choices=[choice], usage=None)
                create = mock.Mock(return_value=iter([completion]) if streaming else completion)
                client = types.SimpleNamespace(base_url='https://api.openai.com/v1', api_key='fixture',
                    chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
                def call(*args, **kw):
                    return llm._call_openai(client, 'fixture', system='',
                        messages=[{'role':'user','content':'Go'}], tools=None, max_tokens=100, thinking=False)
                with mock.patch.object(llm, 'cache_address', return_value=None), \
                     mock.patch.object(llm, '_call', side_effect=call) as invoke:
                    with self.assertRaises(llm.TornStreamError) as caught:
                        llm._call_retrying_empty('openai', 'fixture', retries=2)
                invoke.assert_called_once()
                create.assert_called_once()
                self.assertTrue(caught.exception.partial.continuation['payload'])


class ModelAccountSelection(unittest.TestCase):
    def resolve(self, current, specs):
        cfg = {'roles': {'voice': {'framework': current, 'model': 'other'}},
               'frameworks': specs}
        with mock.patch.object(llm, '_config', return_value=cfg), \
             mock.patch.object(llm, '_available_models', side_effect=lambda fw: [] if fw == 'unrelated' else ['shared']), \
             mock.patch.object(llm, '_protocol', return_value='openai'), \
             mock.patch.object(llm, '_resolve_model', side_effect=lambda fw, model: model):
            return llm._resolve_requested_model('voice', 'shared')

    def test_distinct_accounts_require_explicit_endpoint(self):
        for differing in ({'api_key': 'second'}, {'base_url': 'https://second.invalid/v1'},
                          {'api_mode': 'responses'}):
            with self.subTest(differing=differing):
                first = {'api_key': 'first', 'base_url': 'https://first.invalid/v1'}
                with self.assertRaisesRegex(ValueError, 'several configured accounts'):
                    self.resolve('unrelated', {'unrelated': {}, 'a': first, 'b': {**first, **differing}})

    def test_current_matching_account_wins(self):
        self.assertEqual(self.resolve('b', {'a': {'api_key': 'first'}, 'b': {'api_key': 'second'}}),
                         ('b', 'shared'))

    def test_equivalent_aliases_select_configured_endpoint(self):
        self.assertEqual(self.resolve('unrelated', {'openai': {'base_url': 'https://same.invalid/v1/'},
            'account': {'base_url': 'https://same.invalid/v1'}, 'unrelated': {}}), ('account', 'shared'))


if __name__ == '__main__':unittest.main()
