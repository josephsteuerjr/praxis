"""Provider request/replay conformance: real edition adapters, fake SDK, no network."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import subprocess
import textwrap
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
os.environ["PRAXIS_TEST"] = "1"
sys.path[:0] = [str(ROOT / "helene/core"), str(ROOT / "praxis")]
import _sandbox
assert _sandbox.activate_if_testing()
import llm


def message(text="ok", tools=None, reasoning=None):
    return NS(content=text, tool_calls=tools or [], reasoning_content=reasoning)


def chunk(text=None, tool=None, finish=None, reasoning=None, usage=None):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=[tool] if tool else [],
                                  reasoning_content=reasoning), finish_reason=finish)], usage=usage)


def tool_delta(arguments='{}', name="probe", index=0):
    return NS(index=index, id="call_a", function=NS(name=name, arguments=arguments))


def completion(text="ok", tools=None, reasoning=None):
    return NS(choices=[NS(message=message(text, tools, reasoning), finish_reason="tool_calls" if tools else "stop")],
              usage=NS(prompt_tokens=10, completion_tokens=2))


class SDK:
    def __init__(self, replies, url="https://vendor.invalid/v1", key="fixture-key"):
        self.replies = list(replies)
        self.calls = []
        self.base_url = url
        self.api_key = key
        self.messages = self
        self.chat = NS(completions=self)
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.replies.pop(0)


class TransportTests(unittest.TestCase):
    def call(self, client, framework="openai", model="fixture-model", messages=None, **kwargs):
        method = llm._call_anthropic if framework == "anthropic" else llm._call_openai
        return method(client, model, system="", messages=messages or [{"role": "user", "content": "hi"}],
                      tools=[], max_tokens=4096, thinking=kwargs.pop("thinking", None), **kwargs)

    def test_eof_never_authorizes_text_or_tools(self):
        streams = ([chunk(text="partial")], [chunk(tool=tool_delta())],
                   [chunk(tool=tool_delta(name=None, arguments='{'))])
        for chunks in streams:
            with self.subTest(chunks=chunks), self.assertRaises(llm.TornStreamError) as caught:
                self.call(SDK([iter(chunks)]))
            self.assertEqual(caught.exception.partial.stop_reason, "error")
        with self.assertRaises(llm.EmptyResponseError):
            self.call(SDK([iter([])]))

    def test_finish_and_usage_tail_and_split_tool_arguments(self):
        chunks = [chunk(tool=tool_delta('{"value":')), chunk(tool=tool_delta('7}', name=None)),
                  chunk(finish="tool_calls"), NS(choices=[], usage=NS(prompt_tokens=20, completion_tokens=5))]
        response = self.call(SDK([iter(chunks)]))
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertEqual(response.blocks[0]["input"], {"value": 7})
        self.assertEqual(response.usage["in"], 20)

    def test_length_dominates_complete_tool(self):
        response = self.call(SDK([iter([chunk(tool=tool_delta()), chunk(finish="length")])]))
        self.assertEqual(response.stop_reason, "max_tokens")

    def test_missing_terminal_in_completion_and_anthropic_is_not_success(self):
        response = completion("unfinished")
        response.choices[0].finish_reason = None
        with self.assertRaises(llm.TornStreamError):
            self.call(SDK([response]))
        response = NS(content=[NS(type="tool_use", id="call_a", name="probe", input={})],
                      stop_reason=None, usage=None)
        with self.assertRaises(llm.TornStreamError):
            self.call(SDK([response]), "anthropic")

    def test_anthropic_signed_raw_replay_and_display_separation(self):
        blocks = [NS(type="text", text="before thinking"),
                  NS(type="thinking", thinking="opaque", signature="signature-as-returned"),
                  NS(type="redacted_thinking", data="encrypted"),
                  NS(type="tool_use", id="call_a", name="probe", input={})]
        first = NS(content=blocks, model="fixture-model", stop_reason="tool_use", usage=None)
        client = SDK([first, NS(content=[NS(type="text", text="done")], stop_reason="end_turn", usage=None)])
        response = self.call(client, "anthropic")
        self.assertEqual(response.text, "before thinking")
        self.assertNotIn("opaque", json.dumps(response.blocks))
        history = json.loads(json.dumps([llm.assistant_message(response),
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_a", "content": "ok"}]}]))
        self.call(client, "anthropic", messages=history)
        self.assertEqual(client.calls[1]["messages"][0]["content"], llm._wire_value(blocks))
        self.assertNotIn("provider_continuation", client.calls[1]["messages"][0])
        for changed in (SDK([], key="other-key"), SDK([], url="https://other.invalid/v1")):
            wire = llm.messages_to_anthropic(history, scope=llm._continuation_scope(changed, "anthropic", "fixture-model"))
            self.assertNotIn("signature-as-returned", json.dumps(wire))
        wire = llm.messages_to_openai(history, scope=llm._continuation_scope(client, "chat_completions", "fixture-model"))
        self.assertNotIn("signature-as-returned", json.dumps(wire))

    def test_chat_reasoning_roundtrip_complete_and_stream(self):
        tool = NS(id="call_a", type="function", function=NS(name="probe", arguments="{}"))
        replies = [completion(None, [tool], "private reasoning"),
                   iter([chunk(tool=tool_delta(), reasoning="private "), chunk(reasoning="reasoning", finish="tool_calls")])]
        for reply in replies:
            client = SDK([reply, completion()])
            response = self.call(client)
            self.assertNotIn("private reasoning", response.text)
            self.call(client, messages=[llm.assistant_message(response)])
            self.assertEqual(client.calls[1]["messages"][0]["reasoning_content"], "private reasoning")
            self.assertEqual(client.calls[1]["messages"][0]["tool_calls"][0]["function"]["arguments"], "{}")

    def test_streamed_structured_reasoning_sequence_is_replayed_unmodified(self):
        first = chunk(tool=tool_delta(arguments='{ "value" : 1 }'))
        first.choices[0].delta.reasoning_details = [
            {"type": "reasoning.text", "text": "summary fragment", "signature": None, "id": "r1", "index": 0}]
        second = chunk(finish="tool_calls")
        second.choices[0].delta.reasoning_details = [
            {"type": "reasoning.encrypted", "data": "opaque", "id": "r2", "index": 1}]
        client = SDK([iter([first, second]), completion()])
        response = self.call(client)
        self.call(client, messages=[llm.assistant_message(response)])
        expected = first.choices[0].delta.reasoning_details + second.choices[0].delta.reasoning_details
        self.assertEqual(client.calls[1]["messages"][0]["reasoning_details"], expected)
        self.assertEqual(client.calls[1]["messages"][0]["tool_calls"][0]["function"]["arguments"], '{ "value" : 1 }')

    def test_vendor_controls(self):
        cases = [
            ("https://api.anthropic.com", "claude-opus-4-7", "xhigh", "adaptive", "xhigh"),
            ("https://api.anthropic.com", "claude-sonnet-4-6", "xhigh", "adaptive", "max"),
            ("https://api.minimax.io/anthropic", "MiniMax-M3.1-Flash", "max", "adaptive", "max"),
            ("https://api.minimax.io/anthropic", "MiniMax-M3", "low", "adaptive", None),
            ("https://api.moonshot.ai/anthropic", "kimi-k3", "medium", None, "high"),
            ("https://api.deepseek.com/anthropic", "deepseek-flash", "xhigh", "enabled", "max"),
            ("https://workspace.cn-beijing.maas.aliyuncs.com/apps/anthropic", "qwen3.8-max", "high", "enabled", "xhigh"),
            ("https://api.z.ai/api/anthropic", "glm-5.3", "max", "enabled", "max"),
        ]
        for url, model, effort, thinking_type, expected in cases:
            with self.subTest(model=model):
                client = SDK([NS(content=[NS(type="text", text="ok")], stop_reason="end_turn", usage=None)], url=url)
                self.call(client, "anthropic", model=model, reasoning_effort=effort)
                self.assertEqual((client.calls[0].get("thinking") or {}).get("type"), thinking_type)
                self.assertEqual((client.calls[0].get("output_config") or {}).get("effort"), expected)
                self.assertNotIn("budget_tokens", client.calls[0].get("thinking") or {})
        client = SDK([NS(content=[NS(type="text", text="ok")], stop_reason="end_turn", usage=None)], url="https://api.anthropic.com")
        self.call(client, "anthropic", model="claude-opus-4-7", thinking=16384)
        self.assertEqual(client.calls[0]["thinking"], {"type": "adaptive"})

    def test_alias_protocol_normalization_and_model_override(self):
        config = llm._normalize({"frameworks": {
            "voice_primary": {"protocol": "openai", "api_key": "v", "base_url": "https://one.invalid/v2"},
            "evaluator_primary": {"protocol": "openai", "api_key": "e", "base_url": "https://two.invalid/v1"}},
            "roles": {"voice": {"framework": "voice_primary", "model": "voice"},
                      "evaluator": {"framework": "evaluator_primary", "model": "judge"}}})
        with patch.object(llm, "_config", return_value=config), patch.object(llm, "_available_models", return_value=[]):
            self.assertEqual(llm._protocol("voice_primary"), "openai")
            self.assertEqual(llm._resolve_requested_model("voice", "judge"), ("evaluator_primary", "judge"))
            self.assertEqual(llm._models_url("https://one.invalid/v2/", "openai"), "https://one.invalid/v2/models")
            self.assertEqual(llm._models_url("http://127.0.0.1:5011", "openai"), "http://127.0.0.1:5011/v1/models")

    def test_duplicate_alias_is_not_distinct_fallback_channel(self):
        spec = {"protocol": "openai", "base_url": "https://same.invalid/v1", "api_key": "same"}
        config = {"frameworks": {"voice_primary": dict(spec), "voice_fallback": dict(spec)}}
        with patch.object(llm, "_config", return_value=config), patch.object(llm, "_available_models", return_value=[]):
            self.assertEqual(llm._resolve_fallback_model("voice_primary", "same-model", "voice_fallback", "same-model"), "")
            config["frameworks"]["voice_fallback"]["api_key"] = "another-account"
            self.assertEqual(llm._resolve_fallback_model("voice_primary", "same-model", "voice_fallback", "same-model"), "same-model")

    def test_anthropic_hosted_tool_display_cleanup_does_not_change_replay(self):
        raw = [NS(type="text", text="hosted scaffold"), NS(type="server_tool_use", name="search", id="server_a"),
               NS(type="text", text="raw results"), NS(type="web_search_tool_result", content=[]),
               NS(type="text", text="clean final")]
        client = SDK([NS(content=raw, stop_reason="end_turn", usage=None)])
        response = self.call(client, "anthropic")
        self.assertEqual(response.text, "clean final")
        self.assertEqual(response.continuation["payload"], llm._wire_value(raw))

    def test_real_toolloop_and_checkpoint_keep_protocol_continuation(self):
        import agent
        import run_context
        from contextlib import ExitStack
        response = llm.LLMResponse(blocks=[{"type": "tool_use", "id": "call_a", "name": "probe", "input": {}}],
                                   stop_reason="tool_use", continuation={"scope": "scope", "format": "anthropic", "payload": [
                                       {"type": "thinking", "thinking": "opaque", "signature": "sig"},
                                       {"type": "tool_use", "id": "call_a", "name": "probe", "input": {}}]})
        final = llm.LLMResponse(text="done", blocks=[{"type": "text", "text": "done"}])
        history = [{"role": "user", "content": "hi"}]
        captured = []
        with ExitStack() as stack:
            for name, value in (("_run_status_gate", lambda **kwargs: None),
                                ("_drain_owner_input", lambda *args, **kwargs: False),
                                ("_arm_private_floor", lambda *args: 0),
                                ("_notices_block", lambda **kwargs: ""),
                                ("_work_loop_continue", lambda *args, **kwargs: False),
                                ("_tool_has_side_effect", lambda *args: False),
                                ("_call_tool_with_ceiling", lambda *args: "ok"),
                                ("_persist_tool_loop_checkpoint", lambda **kwargs: captured.append(copy.deepcopy(kwargs["messages"]))),
                                ("_prune_stale_screenshots", lambda *args: None)):
                stack.enter_context(patch.object(agent, name, value))
            stack.enter_context(patch.object(agent, "_model_call", side_effect=[response, final]))
            stack.enter_context(patch.dict(agent.TOOL_IMPL, {"probe": lambda: "ok"}))
            stack.enter_context(patch.object(run_context, "current_run", return_value=None))
            stack.enter_context(patch.object(agent.work_loop, "taken", return_value=None))
            stack.enter_context(patch.object(agent.work_loop, "finished", return_value=False))
            reply = agent._terminal_tool_loop(system="", messages=history,
                       tools=[{"name": "probe", "input_schema": {"type": "object", "properties": {}}}])
        self.assertEqual(reply, "done")
        self.assertEqual(captured[0][1]["provider_continuation"], response.continuation)
        stored = []
        runs = NS(store_result=lambda *args, **kwargs: stored.append(json.loads(args[1])))
        with patch.object(agent, "_runs", return_value=runs):
            agent._persist_tool_loop_checkpoint(current=NS(run_id="fixture"), iteration=1,
                                                system="", messages=captured[0], tools=[])
        self.assertEqual(stored[0]["messages"][1]["provider_continuation"], response.continuation)

    def test_checkpoint_redaction_drops_instead_of_mutating_signature(self):
        import agent
        continuation = {"scope": "scope", "format": "anthropic", "payload": [{"type": "thinking", "signature": "secret"}]}
        history = [{"role": "assistant", "content": [], "provider_continuation": continuation}]
        with patch.object(agent, "_critical_secret_values_from_messages", return_value=("secret",)):
            safe = agent._durable_model_messages(history)
        self.assertNotIn("provider_continuation", safe[0])
        self.assertEqual(safe[0]["continuation_unavailable"], "redacted")
        self.assertEqual(history[0]["provider_continuation"], continuation)
        self.assertEqual(llm.messages_to_anthropic(safe), [{"role": "assistant", "content": []}])

    def test_crash_before_checkpoint_recovers_original_protocol_output(self):
        import agent
        import run_context
        import tempfile
        from contextlib import ExitStack, nullcontext
        from run_manager import RunManager
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            current = manager.create(run_context.RunContext.create(kind="test", goal="fixture", principal_id="owner", scope="owner"), "context")
            manager.transition(current.run_id, "running")
            client = SDK([], url="https://api.minimax.io/anthropic")
            raw = [{"type": "thinking", "thinking": "opaque", "signature": "exact-signature"},
                   {"type": "tool_use", "id": "call_a", "name": "probe", "input": {}}]
            response = llm.LLMResponse(blocks=[raw[1]], stop_reason="tool_use", framework="anthropic", model="MiniMax-M3.1-Flash",
                                      continuation={"scope": llm._continuation_scope(client, "anthropic", "MiniMax-M3.1-Flash"),
                                                    "format": "anthropic", "payload": raw})
            with patch.object(agent, "_runs", return_value=manager), run_context.bind_run(current), \
                 patch.object(agent, "_chat_cancellable", return_value=response):
                agent._model_call("system", [{"role": "user", "content": "hi"}], [])
            outputs = [event for event in manager.events(current.run_id) if event.get("kind") == "model_output"]
            self.assertEqual(len(outputs), 1)
            results = manager.path(current.run_id) / "results"
            output = json.loads(next(results.glob("*model-output*")).read_text(encoding="utf-8"))
            model_input = json.loads(next(results.glob("*model-input*")).read_text(encoding="utf-8"))
            self.assertEqual(output["provider_continuation"], response.continuation)
            self.assertFalse(list(results.glob("*tool-loop-checkpoint*")))
            runtime = object.__new__(agent._AgentResumeRuntime)
            runtime.plan = NS(run_id=current.run_id)
            runtime.tool_trace = []
            request = NS(model_input=model_input, model_output=output, checkpoint={}, resolutions=[])
            captured = []
            with ExitStack() as stack:
                stack.enter_context(patch.object(runtime, "bind", return_value=nullcontext()))
                stack.enter_context(patch.object(runtime, "_land_addressee_free_work_control", return_value={"done": True}))
                stack.enter_context(patch.object(agent, "_persist_tool_loop_checkpoint"))
                stack.enter_context(patch.object(agent, "_terminal_tool_loop", side_effect=lambda **kwargs: captured.append(kwargs["messages"]) or "done"))
                runtime.continue_tool_response(request)
            wire = llm.messages_to_anthropic(captured[0], scope=response.continuation["scope"])
            self.assertEqual(wire[1]["content"], raw)

    def test_explicit_route_never_searches_catalog_or_spends_fallback(self):
        config = llm._normalize({"frameworks": {
            "voice_primary": {"protocol": "openai", "api_key": "fixture", "base_url": "http://127.0.0.1:5011"},
            "voice_fallback": {"protocol": "anthropic", "api_key": "other", "base_url": "https://other.invalid"}},
            "roles": {"voice": {"framework": "voice_primary", "model": "configured", "fallback_framework": "voice_fallback", "fallback_model": "other"}}})
        client = SDK([completion()])
        with patch.object(llm, "_config", return_value=config), patch.object(llm, "_client_for", return_value=client), \
             patch.object(llm, "_available_models", side_effect=AssertionError("catalog forbidden")), \
             patch.object(llm, "_usage_add"), patch.object(llm, "_brain_note"), patch.object(llm, "_call_trace"), patch.object(llm, "_journal"):
            response = llm.chat("voice", model="explicit-unknown-slug", framework="voice_primary", allow_fallback=False,
                                messages=[{"role": "user", "content": "hi"}])
            self.assertEqual(client.calls[0]["model"], "explicit-unknown-slug")
            self.assertEqual(response.model, "explicit-unknown-slug")
            with patch.object(llm, "_call_retrying_net", side_effect=RuntimeError("provider failed")), \
                 patch.object(llm, "_call", side_effect=AssertionError("fallback forbidden")):
                with self.assertRaisesRegex(RuntimeError, "provider failed"):
                    llm.chat("voice", model="explicit-unknown-slug", framework="voice_primary", allow_fallback=False,
                             messages=[{"role": "user", "content": "hi"}])


class ResponsesTests(unittest.TestCase):
    def result(self, status="completed", output=None, **kwargs):
        return NS(status=status, output=output or [], model="gpt-6.1-sol", usage=None, **kwargs)

    def call(self, client, messages=None, **kwargs):
        return llm._call_responses(client, "gpt-6.1-sol", system="instructions",
                                   messages=messages or [{"role": "user", "content": "hi"}],
                                   tools=[{"name": "probe", "input_schema": {"type": "object", "properties": {}}}],
                                   max_tokens=4096, thinking=None, **kwargs)

    def test_full_output_encrypted_reasoning_phase_and_function_replayed(self):
        output = [{"type": "reasoning", "id": "rs_a", "encrypted_content": "opaque", "summary": []},
                  {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "checking"}]},
                  {"type": "function_call", "id": "fc_a", "call_id": "call_a", "name": "probe", "arguments": "{}"}]
        client = SDK([self.result(output=output), self.result(output=[{"type": "message", "content": [{"type": "output_text", "text": "done"}]}])], url="https://api.openai.com/v1")
        response = self.call(client, reasoning_effort="medium")
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertEqual(response.text, "checking")
        self.assertEqual(client.calls[0]["reasoning"], {"effort": "medium"})
        self.assertFalse(client.calls[0]["store"])
        self.assertEqual(client.calls[0]["tools"][0]["type"], "function")
        self.assertNotIn("function", client.calls[0]["tools"][0])
        history = json.loads(json.dumps([llm.assistant_message(response), {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_a", "content": "result"}]}]))
        self.call(client, messages=history)
        self.assertEqual(client.calls[1]["input"][:3], output)
        self.assertEqual(client.calls[1]["input"][3], {"type": "function_call_output", "call_id": "call_a", "output": "result"})

    def test_stream_requires_terminal_and_max_tokens_is_not_success(self):
        item = NS(type="function_call", id="fc_a", call_id="call_a", name="probe", arguments="{}")
        events = [NS(type="response.output_item.done", output_index=0, item=item)]
        with self.assertRaises(llm.TornStreamError):
            self.call(SDK([iter(events)]))
        output = [vars(item)]
        events.append(NS(type="response.incomplete", response=self.result(status="incomplete", output=output,
                          incomplete_details={"reason": "max_output_tokens"})))
        response = self.call(SDK([iter(events)]))
        self.assertEqual(response.stop_reason, "max_tokens")

    def test_dispatch_direct_openai_and_relay_compatibility(self):
        for url, mode, target in (("https://api.openai.com/v1", None, "responses"),
                                  ("http://127.0.0.1:5011", None, "chat"),
                                  ("https://other.invalid/v1", "responses", "responses")):
            config = {"frameworks": {"voice_primary": {"protocol": "openai", "api_mode": mode}}}
            with patch.object(llm, "_config", return_value=config), patch.object(llm, "_client_for", return_value=SDK([], url=url)), \
                 patch.object(llm, "_call_responses", return_value="responses"), patch.object(llm, "_call_openai", return_value="chat"):
                self.assertEqual(llm._call("voice_primary", "gpt-6.1-sol", _resolved=True), target)

    def test_invalid_direct_effort_is_explicit_not_paid_failure(self):
        client = SDK([])
        with self.assertRaisesRegex(ValueError, "does not support"):
            self.call(client, reasoning_effort="none")
        self.assertEqual(client.calls, [])

    def test_installed_sdks_serialize_replay_over_mock_transport(self):
        script = textwrap.dedent('''
            import os, sys, json, importlib
            from unittest.mock import patch
            sys.path[:0] = [sys.argv[1], sys.argv[2]]
            os.environ['PRAXIS_TEST'] = '1'
            import _sandbox
            _sandbox.activate_if_testing()
            import llm, openai, anthropic
            from types import SimpleNamespace
            bodies = []
            output = [{'type':'reasoning','id':'rs_a','summary':[],'encrypted_content':'opaque'},
                      {'type':'function_call','id':'fc_a','call_id':'call_a','name':'probe','arguments':'{}','status':'completed'}]
            def respond(request):
                body = json.loads(request.content)
                bodies.append(body)
                result = {'id':'resp_a','object':'response','created_at':1,'status':'completed',
                          'model':'gpt-6.1-sol','output':output,'usage':{'input_tokens':12,'output_tokens':3,'total_tokens':15,
                          'input_tokens_details':{'cached_tokens':2},'output_tokens_details':{'reasoning_tokens':1}}}
                events = 'event: response.completed\\ndata: '+json.dumps({'type':'response.completed','response':result})+'\\n\\n'
                return http.Response(200, text=events, headers={'content-type':'text/event-stream'})
            http = importlib.import_module(openai.DefaultHttpxClient.__mro__[1].__module__.split('.')[0])
            with openai.OpenAI(api_key='fixture',base_url='https://api.openai.com/v1',
                              http_client=http.Client(transport=http.MockTransport(respond), trust_env=False)) as client:
                kwargs = dict(system='',tools=[{'name':'probe','input_schema':{'type':'object','properties':{}}}],
                              max_tokens=1024,thinking=None,reasoning_effort='medium')
                first = llm._call_responses(client,'gpt-6.1-sol',messages=[{'role':'user','content':'hi'}],**kwargs)
                llm._call_responses(client,'gpt-6.1-sol',messages=[llm.assistant_message(first),
                                   {'role':'user','content':[{'type':'tool_result','tool_use_id':'call_a','content':'ok'}]}],**kwargs)
            assert bodies[0]['max_output_tokens'] == 1024
            assert bodies[0]['tools'][0]['strict'] is True
            assert bodies[1]['input'][:2] == output, bodies[1]
            assert bodies[1]['input'][2]['type'] == 'function_call_output'
            raw = [{'type':'thinking','thinking':'opaque','signature':'sig'},
                   {'type':'tool_use','id':'call_a','name':'probe','input':{}}]
            bodies.clear()
            http = importlib.import_module(anthropic.DefaultHttpxClient.__mro__[1].__module__.split('.')[0])
            def respond_anthropic(request):
                bodies.append(json.loads(request.content))
                message = {'id':'msg_a','type':'message','role':'assistant','model':'claude-opus-4-7',
                           'content':[],'stop_reason':None,'stop_sequence':None,
                           'usage':{'input_tokens':12,'output_tokens':3}}
                events = [('message_start', {'type':'message_start','message':message})]
                for index, block in enumerate(raw):
                    events.extend([('content_block_start',{'type':'content_block_start','index':index,'content_block':block}),
                                   ('content_block_stop',{'type':'content_block_stop','index':index})])
                events.extend([('message_delta',{'type':'message_delta','delta':{'stop_reason':'tool_use','stop_sequence':None},
                                                'usage':{'output_tokens':3}}),('message_stop',{'type':'message_stop'})])
                text = ''.join('event: '+name+'\\ndata: '+json.dumps(value)+'\\n\\n' for name,value in events)
                return http.Response(200,text=text,headers={'content-type':'text/event-stream'})
            with anthropic.Anthropic(api_key='fixture',base_url='https://api.anthropic.com',
                                      http_client=http.Client(transport=http.MockTransport(respond_anthropic),trust_env=False)) as client:
                kwargs = dict(system='',tools=[],max_tokens=1024,thinking=4096,reasoning_effort='high')
                first = llm._call_anthropic(client,'claude-opus-4-7',messages=[{'role':'user','content':'hi'}],**kwargs)
                llm._call_anthropic(client,'claude-opus-4-7',messages=[llm.assistant_message(first),
                                    {'role':'user','content':[{'type':'tool_result','tool_use_id':'call_a','content':'ok'}]}],**kwargs)
            assert bodies[0]['thinking'] == {'type':'adaptive'}, bodies[0]
            assert bodies[0]['output_config'] == {'effort':'medium'}, bodies[0]
            assert bodies[1]['messages'][0]['content'] == raw, bodies[1]
            print('installed-sdk-wire-ok',openai.__version__,anthropic.__version__)
        ''')
        result = subprocess.run([sys.executable, "-X", "utf8", "-I", "-c", script,
                                 str(ROOT / "helene/core"), str(ROOT / "praxis")],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("installed-sdk-wire-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
