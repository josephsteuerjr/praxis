"""Product configuration through normalization, real client selection and wire adapters."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
os.environ["PRAXIS_TEST"] = "1"
sys.path[:0] = [str(ROOT / "desk"), str(ROOT / "helene/core"), str(ROOT / "praxis")]
import _sandbox
assert _sandbox.activate_if_testing()
import llm
from localharness import boot


class ProjectionToWire(unittest.TestCase):
    def test_three_same_protocol_accounts_survive_real_consumer(self):
        for protocol in ("openai", "anthropic"):
            config = {"model": {"framework": protocol, "base_url": "https://primary.invalid/v1", "key": "primary-token",
                                 "model": "primary-model", "fallback_framework": protocol, "fallback_model": "fallback-model",
                                 "fallback_base_url": "https://fallback.invalid/v1", "fallback_key": "fallback-token"},
                      "evaluator": {"framework": protocol, "base_url": "https://judge.invalid/v1", "key": "judge-token", "model": "judge-model"}}
            normalized = llm._normalize(boot._brain_config(config))
            clients = []
            class SDK:
                def __init__(self, **kwargs):
                    self.base_url = kwargs["base_url"]
                    self.api_key = kwargs["api_key"]
                    self.calls = []
                    self.chat = NS(completions=self)
                    self.messages = self
                    clients.append(self)
                def create(self, **kwargs):
                    self.calls.append(kwargs)
                    if protocol == "anthropic":
                        return NS(content=[NS(type="text", text="fixture")], stop_reason="end_turn", model=kwargs["model"], usage=None)
                    return NS(choices=[NS(finish_reason="stop", message=NS(content="fixture", tool_calls=[]))], usage=None)
            environment = dict(os.environ)
            environment.pop("PRAXIS_TEST", None)
            with patch.dict(os.environ, environment, clear=True), patch.object(llm, "_config", return_value=normalized), \
                 patch.dict(llm._CLIENTS, {}, clear=True), patch.dict(llm._TEST_CLIENTS, {}, clear=True), \
                 patch.dict(sys.modules, {"openai": NS(OpenAI=SDK), "anthropic": NS(Anthropic=SDK)}):
                for alias, model in (("voice_primary", "primary-model"), ("evaluator_primary", "judge-model"), ("voice_fallback", "fallback-model")):
                    response = llm._call(alias, model, _resolved=True, system="", messages=[{"role": "user", "content": "fixture"}],
                                         tools=[], max_tokens=128, thinking=None)
                    self.assertEqual(response.text, "fixture")
                    self.assertIs(llm._client_for(alias), clients[-1])
                self.assertEqual(len(clients), 3)
                self.assertEqual([client.api_key for client in clients], ["primary-token", "judge-token", "fallback-token"])
                self.assertEqual([client.calls[0]["model"] for client in clients], ["primary-model", "judge-model", "fallback-model"])
                self.assertEqual(len({client.base_url for client in clients}), 3)


if __name__ == "__main__":
    unittest.main()
