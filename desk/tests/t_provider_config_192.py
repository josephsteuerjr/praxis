"""Provider projection and catalog requests without accounts or live profiles."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import boot, python_desktop_commands as commands


class ProviderProjection(unittest.TestCase):
    def test_roles_and_same_protocol_fallback_have_independent_accounts(self):
        for protocol in ("openai", "anthropic"):
            config = {"model": {"framework": protocol, "base_url": "https://voice.invalid/v2",
                                "key": "voice-key", "model": "voice-model",
                                "fallback_framework": protocol, "fallback_model": "fallback-model",
                                "fallback_base_url": "https://fallback.invalid/v2", "fallback_key": "fallback-key"},
                      "evaluator": {"framework": protocol, "base_url": "https://judge.invalid/v2",
                                    "key": "judge-key", "model": "judge-model"}}
            built = boot._brain_config(config)
            self.assertEqual(built["roles"]["voice"]["framework"], "voice_primary")
            self.assertEqual(built["roles"]["evaluator"]["framework"], "evaluator_primary")
            self.assertEqual(built["roles"]["voice"]["fallback_framework"], "voice_fallback")
            for alias, secret in (("voice_primary", "voice-key"), ("evaluator_primary", "judge-key"), ("voice_fallback", "fallback-key")):
                self.assertEqual(built["frameworks"][alias]["api_key"], secret)
                self.assertEqual(built["frameworks"][alias]["protocol"], protocol)
            self.assertEqual(built["frameworks"][protocol]["api_key"], "voice-key")

    def test_compact_model_inherits_endpoint_not_role_limits(self):
        built = boot._brain_config({"model": {"framework": "anthropic", "base_url": "https://api.z.ai/api/anthropic",
                                               "key": "voice-key", "model": "glm-5.3", "compact_model": "glm-compact",
                                               "max_tokens": 10000, "reasoning_effort": "high"}})
        judge = built["roles"]["evaluator"]
        self.assertEqual(built["frameworks"][judge["framework"]]["api_key"], "voice-key")
        self.assertEqual(judge["max_tokens"], 1024)
        self.assertNotIn("reasoning_effort", judge)

    def test_endpoint_protocol_and_api_mode_are_separate(self):
        built = boot._brain_config({"model": {"framework": "openai", "provider": "local", "base_url": "http://127.0.0.1:1234/v1",
                                               "key": "explicit-token", "model": "custom", "api_mode": "responses"}})
        spec = built["frameworks"]["voice_primary"]
        self.assertEqual(spec["api_mode"], "responses")
        self.assertEqual(spec["protocol"], "openai")
        self.assertEqual(spec["provider"], "compatible")
        self.assertEqual(spec["api_key"], "explicit-token")
        self.assertEqual(built["roles"]["evaluator"]["framework"], "voice_primary")

    def test_evaluator_other_address_does_not_borrow_primary_secret(self):
        built = boot._brain_config({"model": {"framework": "openai", "base_url": "https://primary.invalid/v1", "key": "private-primary", "model": "main"},
                                    "evaluator": {"base_url": "https://judge.invalid/v1", "model": "judge"}})
        self.assertEqual(built["frameworks"]["evaluator_primary"]["api_key"], "")

    def test_real_projection_updates_and_removes_its_old_fallback_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            config = {"model": {"framework": "openai", "base_url": "https://voice.invalid/v1", "key": "voice-key",
                                 "model": "voice", "fallback_framework": "openai", "fallback_model": "spare",
                                 "fallback_base_url": "https://spare.invalid/v1", "fallback_key": "spare-key"}}
            boot.project_brain(tree, config)
            self.assertIn("не трогаю", boot.project_brain(tree, config))
            config["model"].pop("fallback_model")
            config["model"].pop("fallback_key")
            config["model"].pop("fallback_base_url")
            boot.project_brain(tree, config)
            result = json.loads((tree / "memory/llm.json").read_text(encoding="utf-8"))
            self.assertFalse(result["frameworks"]["voice_fallback"].get("api_key"))
            self.assertNotIn("fallback_framework", result["roles"]["voice"])


class ProviderCatalog(unittest.TestCase):
    def capture(self, base, key="", protocol="openai", payload=None):
        requests = []
        handlers = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return io.StringIO(json.dumps(payload if payload is not None else {"data": [{"id": "fixture"}]}))
        def build(*selected):
            handlers.extend(selected)
            return Opener()
        with patch.object(commands.urllib.request, "build_opener", side_effect=build):
            result = commands.probe_model(base, key, protocol)
        return result, requests[0], handlers

    def test_catalog_paths_authentication_and_empty_key(self):
        for base, protocol, expected in (("http://127.0.0.1:5011", "openai", "/v1/models"),
                                          ("https://api.example/v2/", "openai", "/v2/models"),
                                          ("https://api.example/anthropic", "anthropic", "/anthropic/v1/models"),
                                          ("https://api.example/v1", "anthropic", "/v1/models")):
            result, request, handlers = self.capture(base, "fixture-key", protocol)
            self.assertTrue(result["ok"])
            self.assertTrue(request.full_url.endswith(expected))
            headers = dict((name.lower(), value) for name, value in request.header_items())
            self.assertEqual(headers["x-api-key" if protocol == "anthropic" else "authorization"],
                             "fixture-key" if protocol == "anthropic" else "Bearer fixture-key")
            self.assertIn("не проверялись", result["note"])
        result, request, handlers = self.capture("http://127.0.0.1:1234/v1")
        self.assertNotIn("Authorization", request.headers)

    def test_only_local_destinations_bypass_environment_proxy(self):
        for host in ("127.0.0.1", "localhost", "192.168.1.10", "100.101.1.2", "agent.local", "[::1]"):
            result, request, handlers = self.capture("http://" + host + "/v1")
            self.assertTrue(any(isinstance(handler, commands.urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers))
        result, request, handlers = self.capture("https://api.openai.com/v1")
        self.assertFalse(any(isinstance(handler, commands.urllib.request.ProxyHandler) for handler in handlers))
        redirect = next(handler for handler in handlers if isinstance(handler, commands.urllib.request.HTTPRedirectHandler))
        self.assertIsNone(redirect.redirect_request(request, None, 302, "redirect", {}, "https://other.invalid/models"))

    def test_malformed_or_missing_catalog_is_not_generation_proof(self):
        for payload in ([], {"data": None}, {"data": "wrong"}, {"data": [{}]}, {"data": []}):
            result, request, handlers = self.capture("https://api.example/v1", payload=payload)
            self.assertFalse(result["ok"])
            self.assertEqual(result["models"], [])
        with self.assertRaises(ValueError):
            commands.probe_model("https://api.example/v1?key=hidden")


if __name__ == "__main__":
    unittest.main()
