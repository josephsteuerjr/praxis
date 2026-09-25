"""GLM Anthropic wire dialect; hermetic, no provider calls."""
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

import llm
from test_llm import FakeAnthropic, FakeAnthResp


class GlmEffortWireTests(unittest.TestCase):
    def call(self, client, model="glm-5.3", effort="low", thinking=None):
        return llm._call_anthropic(
            client, model, system="", messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=128, thinking=thinking, reasoning_effort=effort)

    def test_all_steps_and_explicit_budget_precedence(self):
        for effort, expected in (("none", "low"), ("minimal", "low"),
                                 ("low", "low"), ("medium", "high"),
                                 ("high", "high"), ("xhigh", "max")):
            for budget, projected in ((None, expected), (1024, "low"),
                                      (4096, "high"), (16384, "high")):
                with self.subTest(effort=effort, budget=budget):
                    client = FakeAnthropic([FakeAnthResp("ok")])
                    self.call(client, effort=effort, thinking=budget)
                    kw = client.calls[0]
                    # ИЗДАНИЕ: родное поле SDK (≥1.4), не extra_body — одна правда в запросе
                    # (llm._call_anthropic); что оно доезжает до провода, держит стенд ниже.
                    self.assertEqual(kw["output_config"], {"effort": projected})
                    self.assertNotIn("extra_body", kw)
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
                        self.assertEqual(llm._call_anthropic(
                            target, "glm-5.3", system="",
                            messages=[{"role": "user", "content": "hi"}],
                            tools=[], max_tokens=128, thinking=None,
                            reasoning_effort="low").text, "ok")
                        self.assertEqual(len(bodies), 1)
                        self.assertEqual(bool(bodies[0].get("stream")), streaming)
                        self.assertEqual(bodies[0]["output_config"], {"effort": "low"})
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


if __name__ == "__main__":
    unittest.main()
