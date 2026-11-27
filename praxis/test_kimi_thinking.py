from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import agent
import llm
import run_resume
from run_manager import RunManager


THINK = {'type': 'thinking', 'thinking': 'private reasoning\nю', 'signature': 'sig+/='}
REDACTED = {'type': 'redacted_thinking', 'data': 'opaque+/='}
USE = {'type': 'tool_use', 'id': 'tool-1', 'name': 'fs_ls', 'input': {}}
BLOCKS = [{'type': 'text', 'text': 'Before '}, THINK, REDACTED,
          {'type': 'text', 'text': 'after'}, USE]


def wire(*blocks, model='k3.1-pro', stop='tool_use'):
    return NS(content=[NS(**b) for b in blocks], model=model, stop_reason=stop)


def call(client, messages, model='k3.1-pro', tools=None):
    return llm._call_anthropic(client, model, system='test', messages=messages,
                               tools=tools, max_tokens=1024, thinking=None)


class KimiThinkingTests(unittest.TestCase):
    def test_order_text_of_and_opaque_server_tool_filter(self):
        raw = wire(*BLOCKS)
        self.assertEqual(llm._blocks_from_anthropic(raw), BLOCKS)
        self.assertEqual(llm.text_of(raw), 'Before after')
        hosted = wire({'type': 'text', 'text': 'narration'}, {'type': 'server_tool_use'},
                      {'type': 'text', 'text': 'raw JSON'}, {'type': 'tool_result'}, *BLOCKS)
        self.assertEqual(llm._blocks_from_anthropic(hosted), BLOCKS)
        self.assertEqual(llm.text_of(hosted), 'Before after')
        self.assertEqual(llm.text_of(wire(*BLOCKS, model='glm-5')), 'after')
        self.assertEqual(llm._blocks_from_anthropic(wire({'type': 'thinking', 'thinking': 'unsigned'}, USE)), [USE])

    def test_both_loops_replay_signed_blocks_on_the_wire(self):
        for scout in (False, True):
            with self.subTest(scout=scout):
                requests = []
                replies = iter([wire(*BLOCKS, model='k3.1-pro-observed'),
                                wire({'type': 'text', 'text': 'done'}, stop='end_turn')])

                def create(**kwargs):
                    requests.append(kwargs['messages'])
                    return next(replies)

                client = NS(messages=NS(create=create))
                with patch.object(llm, 'chat', side_effect=lambda role, **kw: call(
                        client, kw['messages'], tools=kw.get('tools'))), \
                        patch.dict(agent.TOOL_IMPL, {'fs_ls': lambda: 'tool output'}), \
                        patch.object(agent, 'tool_journal'):
                    if scout:
                        reply = agent.second_look('test')
                    else:
                        reply = agent._terminal_tool_loop(system='test', messages=[],
                                                          tools=[{'name': 'fs_ls', 'input_schema': {}}],
                                                          max_iters=2)
                self.assertEqual(reply, 'done')
                self.assertEqual(requests[1][-2:], [
                    {'role': 'assistant', 'content': BLOCKS},
                    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'tool-1',
                                                 'content': 'tool output'}]},
                ])

    def test_non_kimi_fallback_does_not_send_kimi_signature(self):
        requests = []

        def create(**kwargs):
            requests.extend(kwargs['messages'])
            return wire({'type': 'text', 'text': 'done'}, model='glm-5', stop='end_turn')

        history = [{'role': 'assistant', 'content': BLOCKS}]
        call(NS(messages=NS(create=create)), history, model='glm-5')
        self.assertEqual(requests[0]['content'], [BLOCKS[0], BLOCKS[3], USE])
        self.assertEqual(history[0]['content'], BLOCKS)

    def test_durable_output_resume_and_checkpoint_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = RunManager(Path(tmp))
            ctx = agent.run_context.RunContext.create(kind='chat_turn', scope='owner', goal='test',
                                                      principal_id='101', origin_chat_id=101)
            ctx = runs.create(ctx, '# Signed thinking test\n')
            response = call(NS(messages=NS(create=lambda **kw: wire(*BLOCKS))), [])
            tape = [{'role': 'user', 'content': 'hello'}]
            tools = [{'name': 'fs_ls', 'input_schema': {}}]
            result = {'type': 'tool_result', 'tool_use_id': 'tool-1', 'content': 'done'}
            runtime = object.__new__(agent._AgentResumeRuntime)
            runtime.tool_trace = []

            def load(name):
                event = next(r for r in reversed(runs.events(ctx.run_id)) if r.get('name') == name)
                ref = event['result']
                if name != 'model-input':
                    self.assertEqual((ref['inline']['head'], ref['inline']['tail']), ('', ''))
                return run_resume.read_full_json_result(runs, ctx.run_id, ref)

            with agent.run_context.bind_run(ctx), patch.object(agent, '_runs', return_value=runs), \
                    patch.object(agent, '_run_status_gate'), patch.object(agent.llm, 'chat', return_value=response):
                agent._model_call('test', tape, tools)
                output = load('model-output')
                self.assertEqual(output['blocks'], BLOCKS)
                self.assertEqual(output['text'], 'Before after')
                request = NS(model_input=load('model-input'), model_output=output,
                             resolutions=[object()], checkpoint=None)
                with patch.object(runtime, 'bind', return_value=nullcontext()), \
                        patch.object(runtime, '_resolution_blocks', return_value=[result]), \
                        patch.object(runtime, '_restore_hourly_response_words', return_value=False), \
                        patch.object(runtime, '_land_addressee_free_work_control', return_value=None), \
                        patch.object(runtime, '_prepare_authored_delivery', return_value='delivered'), \
                        patch.object(agent, '_terminal_tool_loop', return_value='reply') as loop:
                    self.assertEqual(runtime.continue_tool_response(request), 'delivered')
                expected = tape + [{'role': 'assistant', 'content': BLOCKS},
                                   {'role': 'user', 'content': [result]}]
                self.assertEqual(loop.call_args.kwargs['messages'], expected)
                self.assertEqual(load('tool-loop-checkpoint')['messages'], expected)

    def test_critical_signed_thinking_is_private_and_resume_fails_closed(self):
        secret = 'fixture-critical-secret-not-a-real-credential'
        critical_use = {'type': 'tool_use', 'id': 'critical-1', 'name': 'telegram_account',
                        'input': {'action': 'call', 'request': 'fixture',
                                  'params': {'password': secret}}}
        secret_think = dict(THINK, thinking='reasoning mentions ' + secret)
        for redacted in (None, REDACTED):
            with self.subTest(redacted=bool(redacted)), tempfile.TemporaryDirectory() as tmp:
                runs = RunManager(Path(tmp))
                ctx = agent.run_context.RunContext.create(kind='chat_turn', scope='owner', goal='test',
                                                          principal_id='101', origin_chat_id=101)
                ctx = runs.create(ctx, '# Private signed thinking test\n')
                raw_blocks = [secret_think, *([redacted] if redacted else []), critical_use]
                response = llm.LLMResponse(stop_reason='tool_use', blocks=raw_blocks,
                                           model='k3.1-pro', framework='anthropic')
                tape = [{'role': 'user', 'content': 'hello'}]
                tools = [{'name': 'telegram_account', 'input_schema': {}}]
                result = {'type': 'tool_result', 'tool_use_id': 'critical-1', 'content': 'done'}
                runtime = object.__new__(agent._AgentResumeRuntime)
                runtime.tool_trace = []

                def load(name):
                    event = next(r for r in reversed(runs.events(ctx.run_id)) if r.get('name') == name)
                    ref = event['result']
                    if name != 'model-input':
                        self.assertEqual((ref['inline']['head'], ref['inline']['tail']), ('', ''))
                    return run_resume.read_full_json_result(runs, ctx.run_id, ref)

                with agent.run_context.bind_run(ctx), patch.object(agent, '_runs', return_value=runs), \
                        patch.object(agent, '_run_status_gate'), \
                        patch.object(agent.llm, 'chat', return_value=response):
                    agent._model_call('test', tape, tools)
                    output = load('model-output')
                    self.assertNotIn(secret, repr(output))
                    self.assertNotIn('thinking', [b['type'] for b in output['blocks']])
                    self.assertTrue(output['signed_thinking_omitted'])
                    if redacted:
                        self.assertIn('redacted_thinking', [b['type'] for b in output['blocks']])
                    request = NS(context=ctx, model_input=load('model-input'), model_output=output,
                                 resolutions=[object()], checkpoint=None)
                    with patch.object(runtime, 'bind', return_value=nullcontext()), \
                            patch.object(runtime, '_resolution_blocks', return_value=[result]), \
                            patch.object(agent, '_terminal_tool_loop') as loop:
                        with self.assertRaisesRegex(agent.DurableExecutionError,
                                                    'signed thinking.*omitted'):
                            runtime.continue_tool_response(request)
                    loop.assert_not_called()
                    expected = tape + [{'role': 'assistant', 'content': raw_blocks},
                                       {'role': 'user', 'content': [result]}]
                    agent._persist_tool_loop_checkpoint(current=ctx, iteration=1, system='test',
                                                       messages=expected, tools=tools)
                    checkpoint = load('tool-loop-checkpoint')
                    self.assertNotIn(secret, repr(checkpoint))
                    self.assertNotIn(secret_think, checkpoint['messages'][-2]['content'])
                    self.assertTrue(checkpoint['signed_thinking_omitted'])
                    checkpoint_request = NS(checkpoint=checkpoint, messages=checkpoint['messages'],
                        system='test', tools=tools, iteration=checkpoint['iteration'])
                    with patch.object(runtime, 'bind', return_value=nullcontext()), \
                            patch.object(agent, '_terminal_tool_loop') as loop:
                        with self.assertRaisesRegex(agent.DurableExecutionError,
                                                    'signed thinking.*omitted'):
                            runtime.continue_checkpoint(checkpoint_request)
                    loop.assert_not_called()

    def test_scrubbing_does_not_persist_an_invalid_signature(self):
        self.assertEqual(agent._durable_model_blocks([THINK, USE],
                                                    secret_values=('private reasoning',)), [USE])

    def test_later_clean_output_cannot_replay_previously_scrubbed_history(self):
        secret = 'fixture-critical-secret-not-a-real-credential'
        critical_use = {'type': 'tool_use', 'id': 'critical-1', 'name': 'telegram_account',
                        'input': {'action': 'call', 'request': 'fixture',
                                  'params': {'password': secret}}}
        secret_think = dict(THINK, thinking='reasoning mentions ' + secret)
        clean_use = dict(USE, id='clean-1')
        with tempfile.TemporaryDirectory() as tmp:
            runs = RunManager(Path(tmp))
            ctx = agent.run_context.RunContext.create(kind='chat_turn', scope='owner', goal='test',
                                                      principal_id='101', origin_chat_id=101)
            ctx = runs.create(ctx, '# Previously scrubbed history test\n')
            tape = [{'role': 'user', 'content': 'hello'}]
            tools = [{'name': 'telegram_account', 'input_schema': {}}]
            result = {'type': 'tool_result', 'tool_use_id': 'critical-1', 'content': 'done'}
            runtime = object.__new__(agent._AgentResumeRuntime)
            runtime.tool_trace = []

            def load(name):
                event = next(r for r in reversed(runs.events(ctx.run_id)) if r.get('name') == name)
                return run_resume.read_full_json_result(runs, ctx.run_id, event['result'])

            with agent.run_context.bind_run(ctx), patch.object(agent, '_runs', return_value=runs), \
                    patch.object(agent, '_run_status_gate'):
                with patch.object(agent.llm, 'chat', return_value=llm.LLMResponse(
                        stop_reason='tool_use', blocks=[secret_think, critical_use],
                        model='k3.1-pro', framework='anthropic')):
                    agent._model_call('test', tape, tools)
                tape.extend([{'role': 'assistant', 'content': [secret_think, critical_use]},
                             {'role': 'user', 'content': [result]}])
                with patch.object(agent.llm, 'chat', return_value=llm.LLMResponse(
                        stop_reason='tool_use', blocks=[clean_use],
                        model='k3.1-pro', framework='anthropic')):
                    agent._model_call('test', tape, tools)
                model_input = load('model-input')
                model_output = load('model-output')
                self.assertTrue(model_input['signed_thinking_omitted'])
                self.assertFalse(model_output['signed_thinking_omitted'])
                self.assertNotIn(secret, repr((model_input, model_output)))
                request = NS(context=ctx, model_input=model_input, model_output=model_output,
                             resolutions=[object()], checkpoint=None)
                with patch.object(runtime, 'bind', return_value=nullcontext()), \
                        patch.object(agent, '_terminal_tool_loop') as loop:
                    with self.assertRaisesRegex(agent.DurableExecutionError,
                                                'signed thinking.*omitted'):
                        runtime.continue_tool_response(request)
                loop.assert_not_called()
