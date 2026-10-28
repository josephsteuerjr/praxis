"""Budget exhaustion is evidence, never executable success or a transport retry."""
import types
from unittest import mock

import llm
from test_llm import (Base, FakeAnthResp, FakeAnthropic, FakeOpenAI,
                      FakeStreamOpenAI, _chunk, _openai_resp)


def call_delta(name="send", arguments='{"text":"unfinished', index=0):
    return types.SimpleNamespace(index=index, id="call-cut",
                                 function=types.SimpleNamespace(name=name, arguments=arguments))


class TestTruncatedToolUse(Base):
    def test_stream_budget_reason_dominates_every_tool_shape(self):
        for name, raw in [("send", '{"text":"unfinished'),
                          ("send", '{"text":"complete"}'),
                          ("send", "[]"), ("", '{"text":"unfinished')]:
            with self.subTest(name=name, raw=raw):
                out = llm._openai_from_stream(iter([
                    _chunk(tool_calls=[call_delta(name, raw)]),
                    _chunk(finish="length")]), "fake")
                self.assertEqual(out.stop_reason, "max_tokens")
                self.assertIs(llm._guard_answer(out), out)
                self.assertEqual(len(out.blocks), 1)
                block = out.blocks[0]
                if not name:
                    self.assertEqual(block["type"], "tool_use_fragment")
                    self.assertEqual(block["arguments"], raw)
                elif raw != '{"text":"complete"}':
                    self.assertEqual(block["input"], {llm.MALFORMED_JSON_KEY: raw})

    def test_completion_keeps_budget_reason_and_malformed_evidence(self):
        out = llm._openai_from_completion(_openai_resp(
            text="partial", tool_calls=[call_delta()], finish="length"), "fake")
        self.assertEqual(out.stop_reason, "max_tokens")
        self.assertTrue(llm.is_malformed_json_input(out.blocks[-1]["input"]))

    def test_chat_never_retries_or_falls_back_even_empty_or_after_speech(self):
        for framework in ("anthropic", "openai", "stream"):
            for has_tool in (False, True):
                for spoken in (False, True):
                    with self.subTest(framework=framework, tool=has_tool, spoken=spoken):
                        fw = "anthropic" if framework == "anthropic" else "openai"
                        self._write_cfg(voice={"framework": fw, "model": "fake"})
                        if framework == "anthropic":
                            response = FakeAnthResp("", stop="max_tokens")
                            response.content = ([types.SimpleNamespace(type="tool_use",
                                id="call-cut", name="send", input={"text": "partial"})]
                                if has_tool else [])
                            fake = FakeAnthropic([response])
                        elif framework == "openai":
                            fake = FakeOpenAI(_openai_resp(text="", finish="length",
                                tool_calls=[call_delta()] if has_tool else []))
                        else:
                            fake = FakeStreamOpenAI([
                                _chunk(tool_calls=[call_delta()] if has_tool else []),
                                _chunk(finish="length")])
                        llm.use_test_client(fake, fw)
                        with mock.patch.object(llm, "_note_truncation") as note, \
                             mock.patch.object(llm, "_resolve_fallback_model",
                                               side_effect=AssertionError("unsafe fallback")):
                            out = llm.chat("voice", messages=[], end_after_spoken=spoken)
                        self.assertEqual(out.stop_reason, "max_tokens")
                        self.assertEqual(len(fake.calls), 1)
                        note.assert_called_once_with(out, "voice")

    def test_ordinary_complete_tools_still_execute_and_errors_stay_torn(self):
        for finish, expected in [("tool_calls", "tool_use"), ("error", "error")]:
            out = llm._openai_from_stream(iter([
                _chunk(tool_calls=[call_delta(arguments='{"text":"ok"}')]),
                _chunk(finish=finish)]), "fake")
            self.assertEqual(out.stop_reason, expected)
            if finish == "error":
                with self.assertRaises(llm.TornStreamError):
                    llm._guard_answer(out)
            else:
                self.assertIs(llm._guard_answer(out), out)

    def test_empty_end_turn_still_is_retryable_transport_emptiness(self):
        with self.assertRaises(llm.EmptyResponseError):
            llm._guard_answer(llm.LLMResponse())


import json
import tempfile
import unittest

import agent
import run_context
import run_manager


class TestTruncatedRunBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = run_manager.RunManager(self.tmp.name)
        patch = mock.patch.object(agent, "_RUN_MANAGER", self.manager)
        patch.start()
        self.addCleanup(patch.stop)
        ctx = run_context.RunContext.create(kind="test", goal="budget cut",
            principal_id="owner", scope="owner", origin_chat_id="1", delivery_chat_id="1")
        self.ctx = self.manager.create(ctx, "test context")
        self.manager.transition(self.ctx.run_id, "running")

    def test_cut_stops_failed_and_never_executes_parseable_prefix_or_continues(self):
        response = llm.LLMResponse(text="partial", stop_reason="max_tokens", blocks=[
            {"type": "tool_use", "id": "cut", "name": "test_effect", "input": {}}])
        effect = mock.Mock()
        with run_context.bind_run(self.ctx), \
             mock.patch.object(llm, "chat", return_value=response) as chat, \
             mock.patch.dict(agent.TOOL_IMPL, {"test_effect": effect}), \
             mock.patch.object(agent, "_work_loop_continue") as continuation:
            with self.assertRaises(agent.RunStopped) as caught:
                agent._terminal_tool_loop(system="test", messages=[], tools=[])
        self.assertEqual(caught.exception.status, "failed")
        self.assertEqual(self.manager.manifest(self.ctx.run_id)["status"], "failed")
        effect.assert_not_called()
        continuation.assert_not_called()
        chat.assert_called_once()
        events = list(self.manager.iter_events(self.ctx.run_id))
        self.assertTrue(any(e["kind"] == "model_output" for e in events))
        self.assertTrue(any(e["kind"] == "model_completed" and
                            e.get("stop_reason") == "max_tokens" for e in events))

    def test_prior_uncertain_effect_remains_in_doubt_without_replay(self):
        self.manager.start_tool(self.ctx.run_id, "prior", "test_effect", {})
        response = llm.LLMResponse(stop_reason="max_tokens")
        with run_context.bind_run(self.ctx), mock.patch.object(llm, "chat", return_value=response):
            with self.assertRaises(agent.RunStopped) as caught:
                agent._terminal_tool_loop(system="test", messages=[], tools=[])
        self.assertEqual(caught.exception.status, "in_doubt")
        self.assertEqual(self.manager.manifest(self.ctx.run_id)["status"], "in_doubt")
        self.assertIn("prior", self.manager.outstanding_tools(self.ctx.run_id))

    def test_partial_arguments_only_persist_opaque_commitment(self):
        secret = "synthetic-partial-private-value"
        for block in [
            {"type": "tool_use_fragment", "arguments": secret},
            {"type": "tool_use", "name": "telegram_account", "input": {
                llm.MALFORMED_JSON_KEY: '{"params":{"password":"' + secret}},
        ]:
            safe = agent._durable_model_blocks([block])
            self.assertNotIn(secret, json.dumps(safe))
            self.assertTrue(safe[0]["redacted"])
            self.assertEqual(len(safe[0]["commitment"]), 64)
            self.assertIn(secret, json.dumps(block), "live evidence not mutated")
