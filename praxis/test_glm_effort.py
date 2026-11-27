"""GLM Anthropic wire dialect; hermetic, no provider calls."""
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

import llm
from test_llm import Base, FakeAnthropic, FakeAnthResp, FakeOpenAI


class GlmEffortWireTests(unittest.TestCase):
    def call(self, client, model="glm-5.3", effort="low", thinking=None):
        return llm._call_anthropic(
            client, model, system="", messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=128, thinking=thinking, reasoning_effort=effort)

    def test_all_steps_and_explicit_budget_precedence(self):
        for effort, expected in (("none", "low"), ("minimal", "low"),
                                 ("low", "low"), ("medium", "low"),
                                 ("high", "high"), ("xhigh", "max")):
            for budget, projected in ((None, expected), (1024, "low"),
                                      (4096, "low"), (16384, "high")):
                with self.subTest(effort=effort, budget=budget):
                    client = FakeAnthropic([FakeAnthResp("ok")])
                    self.call(client, effort=effort, thinking=budget)
                    kw = client.calls[0]
                    self.assertEqual(kw["extra_body"], {"output_config": {"effort": projected}})
                    self.assertNotIn("reasoning_effort", kw)
                    if budget:
                        self.assertEqual(kw["thinking"]["budget_tokens"], budget)
                        self.assertEqual(kw["max_tokens"], budget + 1024)

    def test_omitted_effort_and_non_glm_unchanged(self):
        for model, effort, budget in (("glm-5.3", None, None),
                                      ("claude-sonnet-4-6", "xhigh", None),
                                      ("claude-sonnet-4-6", "low", 4096)):
            with self.subTest(model=model, budget=budget):
                client = FakeAnthropic([FakeAnthResp("ok")])
                self.call(client, model=model, effort=effort, thinking=budget)
                kw = client.calls[0]
                self.assertNotIn("extra_body", kw)
                self.assertNotIn("output_config", kw)
                if budget:
                    self.assertEqual(kw["thinking"], {"type": "enabled", "budget_tokens": budget})

    def test_sdk_merges_output_config_at_wire_top_level_create_and_stream(self):
        # Other suites install fake SDK modules during collection. A fresh interpreter
        # exercises the installed SDK without changing the parent's sys.modules.
        script = textwrap.dedent(r'''
            import sys
            from unittest import TestCase, mock
            sys.path.insert(0, sys.argv[1])
            with mock.patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
                 mock.patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
                import json
                from types import SimpleNamespace
                import anthropic
                import httpx
                import llm

                self = TestCase()
                message = {"id": "msg_test", "type": "message", "role": "assistant",
                           "model": "glm-5.3", "content": [{"type": "text", "text": "ok"}],
                           "stop_reason": "end_turn", "stop_sequence": None,
                           "usage": {"input_tokens": 1, "output_tokens": 1}}
                for streaming in (False, True):
                    bodies = []

                    def respond(request):
                        body = json.loads(request.content)
                        bodies.append(body)
                        if body.get("stream"):
                            events = [("message_start", {"type": "message_start", "message": message}),
                                      ("message_stop", {"type": "message_stop"})]
                            text = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
                            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
                        return httpx.Response(200, json=message)

                    with anthropic.Anthropic(
                            api_key="fake-test-key", base_url="https://test.invalid",
                            http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)) as client:
                        target = client if streaming else SimpleNamespace(messages=SimpleNamespace(create=client.messages.create))
                        for model in ("glm-5.3", "glm-5.4"):
                            for effort, expected in (("medium", "low"), ("xhigh", "max")):
                                bodies.clear()
                                message["model"] = model
                                self.assertEqual(llm._call_anthropic(
                                    target, model, system="",
                                    messages=[{"role": "user", "content": "hi"}],
                                    tools=[], max_tokens=128, thinking=None,
                                    reasoning_effort=effort).text, "ok")
                                self.assertEqual(len(bodies), 1)
                                self.assertEqual(bool(bodies[0].get("stream")), streaming)
                                self.assertEqual(bodies[0]["output_config"], {"effort": expected})
                                self.assertNotIn("reasoning_effort", bodies[0])
                                self.assertNotIn("extra_body", bodies[0])
            print("wire-create-and-stream-ok")
        ''')
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(Path(__file__).resolve().parent)],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).resolve().parent,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "wire-create-and-stream-ok")


class KimiEffortWireTests(unittest.TestCase):
    """Kimi K3.1 Pro Anthropic dialect; hermetic, no provider calls."""

    def call(self, client, model="k3.1-pro", effort="low", thinking=None):
        return llm._call_anthropic(
            client, model, system="", messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=128, thinking=thinking, reasoning_effort=effort)

    def test_k31_reasoning_is_always_on_without_anthropic_thinking_switch(self):
        """Official K3.1 contract: no request `thinking`; preserve it via replay."""
        for effort in ("low", "high", "max"):
            with self.subTest(effort=effort):
                client = FakeAnthropic([FakeAnthResp("ok")])
                self.call(client, effort=effort)
                kw = client.calls[0]
                self.assertEqual(kw["extra_body"], {"output_config": {"effort": effort}})
                self.assertNotIn("thinking", kw)

    def test_explicit_budget_remains_legacy_anthropic_only(self):
        # K3.1 always reasons; its request `thinking` field is unsupported. A
        # caller's legacy budget stays on ordinary Anthropic, never sent to Kimi.
        for model in ("k3.1-pro", "claude-sonnet-4-6"):
            with self.subTest(model=model):
                client = FakeAnthropic([FakeAnthResp("ok")])
                self.call(client, model=model, effort="low", thinking=4096)
                kw = client.calls[0]
                if model == "k3.1-pro":
                    self.assertNotIn("thinking", kw)
                    self.assertNotIn("extra_body", kw)
                    self.assertEqual(kw["max_tokens"], 5120)
                else:
                    self.assertEqual(kw["thinking"], {"type": "enabled", "budget_tokens": 4096})
                    self.assertEqual(kw["max_tokens"], 5120)
                    self.assertNotIn("extra_body", kw)

    def test_sdk_merges_messages_output_config_create_and_stream(self):
        # Check the JSON produced by the installed SDK, not merely our kwargs fake.
        script = textwrap.dedent(r'''
            import sys
            from unittest import TestCase, mock
            sys.path.insert(0, sys.argv[1])
            with mock.patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
                 mock.patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
                import json
                from types import SimpleNamespace
                import anthropic
                import httpx
                import llm

                self = TestCase()
                message = {"id": "msg_test", "type": "message", "role": "assistant",
                           "model": "k3.1-pro", "content": [{"type": "text", "text": "ok"}],
                           "stop_reason": "end_turn", "stop_sequence": None,
                           "usage": {"input_tokens": 1, "output_tokens": 1}}
                for streaming in (False, True):
                    bodies = []

                    def respond(request):
                        body = json.loads(request.content)
                        bodies.append(body)
                        if body.get("stream"):
                            events = [("message_start", {"type": "message_start", "message": message}),
                                      ("message_stop", {"type": "message_stop"})]
                            text = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
                            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
                        return httpx.Response(200, json=message)

                    with anthropic.Anthropic(
                            api_key="fake-test-key", base_url="https://test.invalid",
                            http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)) as client:
                        target = client if streaming else SimpleNamespace(messages=SimpleNamespace(create=client.messages.create))
                        self.assertEqual(llm._call_anthropic(
                            target, "k3.1-pro", system="",
                            messages=[{"role": "user", "content": "hi"}],
                            tools=[], max_tokens=128, thinking=None,
                            reasoning_effort="low").text, "ok")
                        self.assertEqual(len(bodies), 1)
                        self.assertEqual(bool(bodies[0].get("stream")), streaming)
                        self.assertEqual(bodies[0]["output_config"], {"effort": "low"})
                        self.assertNotIn("thinking", bodies[0])
                        self.assertNotIn("reasoning_effort", bodies[0])
                        self.assertNotIn("extra_body", bodies[0])
            print("kimi-wire-create-and-stream-ok")
        ''')
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(Path(__file__).resolve().parent)],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).resolve().parent,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "kimi-wire-create-and-stream-ok")

    def test_sdk_second_call_replays_exact_signed_thinking(self):
        # A real installed-SDK request/response cycle; MockTransport is the only peer.
        script = textwrap.dedent(r'''
            import sys
            from unittest import TestCase, mock
            sys.path.insert(0, sys.argv[1])
            with mock.patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
                 mock.patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
                import json
                import anthropic
                import httpx
                import llm

                self = TestCase()
                think = {"type": "thinking", "thinking": "private reasoning", "signature": "signed+/="}
                redacted = {"type": "redacted_thinking", "data": "opaque+/="}
                use = {"type": "tool_use", "id": "call-1", "name": "inspect", "input": {"value": 0}}
                first_message = {"id": "msg_first", "type": "message", "role": "assistant",
                    "model": "k3.1-pro", "content": [think, redacted, use],
                    "stop_reason": "tool_use", "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 2}}
                second_message = {"id": "msg_second", "type": "message", "role": "assistant",
                    "model": "k3.1-pro", "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 2, "output_tokens": 1}}
                replies = iter((first_message, second_message))
                bodies = []

                def respond(request):
                    bodies.append(json.loads(request.content))
                    return httpx.Response(200, json=next(replies))

                with anthropic.Anthropic(
                        api_key="fake-test-key", base_url="https://test.invalid",
                        http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)) as client:
                    tools = [{"name": "inspect", "input_schema": {"type": "object"}}]
                    target = type("CreateOnly", (), {"messages": type("Messages", (), {
                        "create": client.messages.create})()})()
                    messages = [{"role": "user", "content": "inspect"}]
                    first = llm._call_anthropic(target, "k3.1-pro", system="", messages=messages,
                        tools=tools, max_tokens=128, thinking=None, reasoning_effort="low")
                    self.assertEqual(first.blocks, [think, redacted, use])
                    messages.extend([{"role": "assistant", "content": first.blocks},
                        {"role": "user", "content": [{"type": "tool_result",
                            "tool_use_id": "call-1", "content": "done"}]}])
                    second = llm._call_anthropic(target, "k3.1-pro", system="", messages=messages,
                        tools=tools, max_tokens=128, thinking=None, reasoning_effort="low")
                    self.assertEqual(second.text, "done")
                self.assertEqual(len(bodies), 2)
                self.assertEqual(bodies[1]["messages"][-2], {"role": "assistant",
                    "content": [think, redacted, use]})
                self.assertEqual(bodies[1]["output_config"], {"effort": "low"})
            print("kimi-second-call-signed-thinking-ok")
        ''')
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(Path(__file__).resolve().parent)],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).resolve().parent,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "kimi-second-call-signed-thinking-ok")


class CodexEffortCompatibilityTests(Base):
    def test_openai_codex_path_keeps_its_own_effort_field(self):
        self._write_cfg(voice={"framework": "openai", "model": "gpt-6.2-sol"})
        client = FakeOpenAI()
        llm.use_test_client(client, "openai")
        llm.chat("voice", messages=[{"role": "user", "content": "hi"}], thinking=4096)
        self.assertEqual(client.calls[0]["extra_body"], {"reasoning_effort": "medium"})
        self.assertNotIn("output_config", client.calls[0])
        self.assertNotIn("thinking", client.calls[0])


if __name__ == "__main__":
    unittest.main()
