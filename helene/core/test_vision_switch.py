"""Offline adversarial vision-routing regressions; no provider calls."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agent
import llm
from test_llm import Base, RateLimitError

IMAGE_MESSAGES = [{"role": "user", "content": [
    {"type": "text", "text": "inspect"},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
]}]


class VisionRoutingTests(Base):
    def _response(self, model, framework="anthropic"):
        return llm.LLMResponse(text="ok", blocks=[{"type": "text", "text": "ok"}],
                               stop_reason="end_turn", usage={"in": 3, "out": 1},
                               framework=framework, model=model)

    @staticmethod
    def _catalog(framework):
        return (["glm-5.3", "glm-5.2", "glm-4.6v", "glm-5.3v"]
                if framework == "anthropic" else
                ["gpt-primary", "gpt-fallback", "gpt-5.6-sol", "gpt-5.6-terra"])

    def test_image_routes_glm_and_persists_actual_markers(self):
        self._write_cfg(voice={"framework": "anthropic", "model": "glm-5.3",
                               "vision_model": "glm-4.6v", "reasoning_effort": "high"})
        seen = {}
        trace, usage = self.tmp / "llm_calls.jsonl", self.tmp / "usage.json"
        def call(fw, model, **kwargs):
            seen.update(fw=fw, model=model, kwargs=kwargs)
            return self._response(model), 0
        with mock.patch.object(llm, "_available_models", side_effect=self._catalog), \
             mock.patch.object(llm, "_call_retrying_empty", side_effect=call), \
             mock.patch.object(llm, "_CALL_TRACE", trace), \
             mock.patch.object(llm, "USAGE_PATH", usage):
            response = llm.chat("voice", messages=IMAGE_MESSAGES)
        self.assertEqual((response.model, response.vision), ("glm-4.6v", True))
        self.assertEqual((seen["fw"], seen["model"]), ("anthropic", "glm-4.6v"))
        self.assertEqual(seen["kwargs"]["reasoning_effort"], "high")
        row = json.loads(trace.read_text().splitlines()[-1])
        self.assertEqual((row["model"], row["vision"]), ("glm-4.6v", 1))
        day = next(iter(json.loads(usage.read_text()).values()))["voice"]
        self.assertEqual((day["last"]["model"], day["last"]["vision"]),
                         ("glm-4.6v", True))
        self.assertEqual(day["models"]["glm-4.6v"]["vision"], 1)

    def test_text_call_and_current_gpt_path_unchanged(self):
        for model, framework in (("glm-5.3", "anthropic"), ("gpt-5.6-sol", "openai")):
            self._write_cfg(voice={"framework": framework, "model": model,
                                   "vision_model": "glm-4.6v"})
            seen = {}
            def call(fw, sent, **kw):
                seen["model"] = sent
                return self._response(sent, fw), 0
            with mock.patch.object(llm, "_available_models", side_effect=self._catalog), \
                 mock.patch.object(llm, "_call_retrying_empty", side_effect=call):
                response = llm.chat("voice", messages=[{"role": "user", "content": "plain"}])
            self.assertEqual(seen["model"], model)
            self.assertFalse(response.vision)

    def test_capability_policy_conservative_with_verified_families(self):
        for name in ("gpt-5.6-sol", "gpt-4o", "claude-3-7-sonnet", "claude-sonnet-4",
                     "glm-4.6v", "glm-4.6v-flash", "glm-5.3v"):
            self.assertTrue(llm.accepts_images(model=name), name)
        for name in ("", "mystery", "glm-5.3", "glm-999-flash", "glm-6-flash-madeup",
                     "glm-4.6v-madeup", "gpt-5-text-only",
                     "gpt-5.6-sol-actually-text", "claude-sonnet-text-only"):
            self.assertFalse(llm.accepts_images(model=name), name)

    def test_missing_catalog_or_text_only_replacement_fails_closed(self):
        for replacement, catalog in (("definitely-absent", ["glm-5.3", "glm-4.6v"]),
                                     ("glm-5.2", ["glm-5.3", "glm-5.2"])):
            self._write_cfg(voice={"framework": "anthropic", "model": "glm-5.3",
                                   "vision_model": replacement})
            seen = {}
            def call(fw, model, **kw):
                seen.update(kw)
                return self._response(model), 0
            with mock.patch.object(llm, "_available_models", return_value=catalog), \
                 mock.patch.object(llm, "_call_retrying_empty", side_effect=call):
                response = llm.chat("voice", messages=IMAGE_MESSAGES)
            payload = json.dumps(seen["messages"])
            self.assertNotIn("AA==", payload)
            self.assertIn("NO pixels", payload)
            self.assertIn("NO pixels", response.text)
            self.assertFalse(response.vision)

    def test_no_hardcodedFlash_default_and_catalog_driven_pick(self):
        # glm-4.6v is retired: no hardcoded default may exist or mention it.
        self.assertFalse(hasattr(llm, "_DEFAULT_VISION_MODEL"))
        self.assertNotIn("glm-4.6v", Path(llm.__file__).read_text(encoding="utf-8"))
        # No explicit vision config: catalog decides, cross-framework (openai) leg allowed.
        self._write_cfg(voice={"framework": "anthropic", "model": "glm-5.3"})
        with mock.patch.object(llm, "_available_models",
                               side_effect=lambda fw: {"anthropic": ["glm-5.3"],
                                                       "openai": ["gpt-5.6-sol", "gpt-5.6-terra"]}.get(fw, [])):
            self.assertEqual(llm.vision_model("voice", "glm-5.3", "anthropic"),
                             "gpt-5.6-sol")
            # Non-z.ai base_url no longer matters: the catalog is the authority now.
            self.assertEqual(llm.vision_model("voice", "glm-5.3", "openai"),
                             "gpt-5.6-sol")
        # Same-framework sighted catalog entry wins over the cross-leg.
        with mock.patch.object(llm, "_available_models",
                               side_effect=lambda fw: {"anthropic": ["glm-5.3", "glm-4.6v"],
                                                       "openai": ["gpt-5.6-sol"]}.get(fw, [])):
            self.assertEqual(llm.vision_model("voice", "glm-5.3", "anthropic"), "glm-4.6v")
        # Catalog with no sighted model on either leg fails closed (no flash fallback).
        with mock.patch.object(llm, "_available_models",
                               return_value=["glm-5.3", "glm-5.2"]):
            self.assertEqual(llm.vision_model("voice", "glm-5.3", "anthropic"), "")
        # Missing catalog is not authorization.
        with mock.patch.object(llm, "_available_models", return_value=[]):
            self.assertEqual(llm.vision_model("voice", "glm-5.3", "anthropic"), "")

    def test_cross_framework_does_not_reuse_primary_vision_slug(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "a"
        cfg["frameworks"]["openai"]["api_key"] = "o"
        cfg["roles"]["voice"].update(framework="anthropic", model="glm-5.3",
                                      vision_model="glm-4.6v", fallback_framework="openai",
                                      fallback_model="unknown-openai")
        llm.save_config(cfg)
        llm.use_test_client(object(), "anthropic")
        llm.use_test_client(object(), "openai")
        calls = []
        with mock.patch.object(llm, "_available_models", side_effect=lambda fw: (
                ["glm-5.3", "glm-4.6v"] if fw == "anthropic" else ["unknown-openai"])), \
             mock.patch.object(llm, "_call_retrying_empty", side_effect=RateLimitError("429")), \
             mock.patch.object(llm, "_call", side_effect=lambda fw, m, **kw: (
                 calls.append((fw, m, kw["messages"])) or self._response(m, fw))), \
             mock.patch.object(llm, "_journal"):
            response = llm.chat("voice", messages=IMAGE_MESSAGES)
        self.assertEqual(calls[0][0:2], ("openai", "unknown-openai"))
        self.assertIn("NO pixels", json.dumps(calls[0][2]))
        self.assertIn("NO pixels", response.text)

    def test_framework_specific_fallback_vision_allowed(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "a"
        cfg["frameworks"]["openai"]["api_key"] = "o"
        cfg["roles"]["voice"].update(framework="openai", model="gpt-primary",
                                      fallback_framework="anthropic", fallback_model="glm-5.3",
                                      vision_models={"anthropic": "glm-5.3v"})
        llm.save_config(cfg)
        llm.use_test_client(object(), "openai")
        llm.use_test_client(object(), "anthropic")
        with mock.patch.object(llm, "_available_models", side_effect=self._catalog), \
             mock.patch.object(llm, "_call_retrying_empty", side_effect=RateLimitError("429")), \
             mock.patch.object(llm, "_call", return_value=self._response("glm-5.3v")) as call, \
             mock.patch.object(llm, "_journal"):
            response = llm.chat("voice", messages=IMAGE_MESSAGES)
        self.assertEqual(call.call_args.args[:2], ("anthropic", "glm-5.3v"))
        self.assertTrue(response.vision)

    def test_sighted_fallback_keeps_original_pixels_after_primary_omission(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "a"
        cfg["frameworks"]["openai"]["api_key"] = "o"
        cfg["roles"]["voice"].update(framework="anthropic", model="unknown-text",
                                      fallback_framework="openai", fallback_model="gpt-5.6-terra")
        llm.save_config(cfg)
        llm.use_test_client(object(), "anthropic")
        llm.use_test_client(object(), "openai")
        with mock.patch.object(llm, "_available_models", side_effect=self._catalog), \
             mock.patch.object(llm, "_call_retrying_empty", side_effect=RateLimitError("429")), \
             mock.patch.object(llm, "_call", return_value=self._response("gpt-5.6-terra", "openai")) as call, \
             mock.patch.object(llm, "_journal"):
            response = llm.chat("voice", messages=IMAGE_MESSAGES)
        sent = call.call_args.kwargs["messages"]
        self.assertTrue(llm._has_image_blocks(sent))
        self.assertNotIn("NO pixels", json.dumps(sent))
        self.assertFalse(response.vision)  # natively sighted, not a substitution

    def test_fallback_effective_collapse_not_retried(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "a"
        cfg["roles"]["voice"].update(framework="anthropic", model="glm-5.3",
                                      fallback_framework="anthropic", fallback_model="glm-5.2",
                                      vision_model="glm-4.6v")
        llm.save_config(cfg)
        llm.use_test_client(object(), "anthropic")
        with mock.patch.object(llm, "_available_models", side_effect=self._catalog), \
             mock.patch.object(llm, "_call_retrying_empty", side_effect=RateLimitError("429")), \
             mock.patch.object(llm, "_call") as fallback, mock.patch.object(llm, "_journal"):
            with self.assertRaises(RateLimitError):
                llm.chat("voice", messages=IMAGE_MESSAGES)
        fallback.assert_not_called()

    def test_sighted_adapter_native_shape_is_normalized_or_rejected_locally(self):
        self._write_cfg(voice={"framework": "openai", "model": "gpt-5.6-sol"})
        seen = {}
        native = [{"role": "user", "content": [{"type": "input_image",
                   "image_url": "data:image/png;base64,QUE="}]}]
        def call(fw, model, **kw):
            seen.update(kw)
            return self._response(model, fw), 0
        with mock.patch.object(llm, "_available_models", side_effect=self._catalog), \
             mock.patch.object(llm, "_call_retrying_empty", side_effect=call):
            llm.chat("voice", messages=native)
        self.assertEqual(seen["messages"][0]["content"][0]["type"], "image")
        self.assertEqual(seen["messages"][0]["content"][0]["source"]["data"], "QUE=")
        with mock.patch.object(llm, "_available_models", side_effect=self._catalog), \
             mock.patch.object(llm, "_call_retrying_empty") as provider:
            with self.assertRaises(ValueError):
                llm.chat("voice", messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "https://private.invalid/x"}}]}])
        provider.assert_not_called()

    def test_all_adapter_image_shapes_sanitized_without_payload(self):
        shapes = (("image", {"source": {"type": "base64", "media_type": "image/png",
                                        "data": "SECRET_IMAGE"}}),
                  ("image_url", {"image_url": {"url": "data:image/png;base64,SECRET_URL"}}),
                  ("input_image", {"image_url": "SECRET_INPUT"}))
        for kind, payload in shapes:
            self._write_cfg(voice={"framework": "anthropic", "model": "unknown-text"})
            seen = {}
            messages = [{"role": "user", "content": [{"type": kind, **payload}]}]
            def call(fw, model, **kw):
                seen.update(kw)
                return self._response(model), 0
            with mock.patch.object(llm, "_available_models", return_value=["unknown-text"]), \
                 mock.patch.object(llm, "_call_retrying_empty", side_effect=call):
                llm.chat("voice", messages=messages)
            serialized = json.dumps(seen["messages"])
            self.assertIn("NO pixels", serialized)
            self.assertNotIn("SECRET", serialized)
            self.assertNotIn("SECRET", json.dumps(llm.messages_to_anthropic(seen["messages"])))
            self.assertNotIn("SECRET", json.dumps(llm.messages_to_openai(seen["messages"])))


class PixelGateTests(unittest.TestCase):
    def test_observe_returns_accurate_no_pixels_for_invalid_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "screen.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nDATA")
            with mock.patch.object(agent.run_context, "current_run", return_value=None), \
                 mock.patch.object(agent.llm, "can_see", return_value=False), \
                 mock.patch.object(agent.llm, "role_model", return_value="glm-5.3"), \
                 mock.patch.object(agent, "_model_view_image") as convert:
                result = agent._observe_image_pixels(
                    image, None, mime="image/png", safe_name="screen.png",
                    sha256="a" * 64, size=image.stat().st_size)
        self.assertIn("same-framework catalog-valid", result)
        self.assertIn("NO pixels", result)
        convert.assert_not_called()

    def test_model_call_receipts_include_actual_model_and_vision(self):
        stored, events = [], []
        manager = SimpleNamespace(store_result=lambda *a, **kw: stored.append((a, kw)))
        response = llm.LLMResponse(text="ok", blocks=[], stop_reason="end_turn", usage={"in": 1},
                                   framework="anthropic", model="glm-4.6v", vision=True)
        current = SimpleNamespace(run_id="run-vision")
        with mock.patch.object(agent.run_context, "current_run", return_value=current), \
             mock.patch.object(agent, "_runs", return_value=manager), \
             mock.patch.object(agent, "_run_status_gate"), \
             mock.patch.object(agent, "_run_event_strict",
                               side_effect=lambda kind, **kw: events.append((kind, kw))), \
             mock.patch.object(agent.llm, "chat", return_value=response):
            agent._model_call("system", [{"role": "user", "content": "x"}], None)
        payload = json.loads(next(a[1] for a, kw in stored if kw["name"] == "model-output"))
        completed = next(kw for kind, kw in events if kind == "model_completed")
        self.assertEqual((payload["model"], payload["vision"]), ("glm-4.6v", True))
        self.assertEqual((completed["model"], completed["vision"]), ("glm-4.6v", True))


if __name__ == "__main__":
    unittest.main()


class SightedFlashAllowlistTests(Base):
    """21.09: glm-5.3-flash verified sighted live on z.ai /api/anthropic."""

    def test_glm_flash_is_sighted(self):
        self.assertTrue(llm.accepts_images(model="glm-5.3-flash"))

    def test_glm_flashx_stays_blind(self):
        self.assertFalse(llm.accepts_images(model="glm-5.3-flashx"))

    def test_catalog_pick_prefers_flash_over_v_family(self):
        # catalog without v-slugs: flash must be picked from the catalog itself
        with mock.patch.object(llm, "_available_models",
                               return_value=["glm-5.3", "glm-5.3-flash"]):
            self.assertEqual(llm.vision_model("voice", "glm-5.3", "anthropic"),
                             "glm-5.3-flash")

    def test_flash_replacement_does_not_leave_leg(self):
        with mock.patch.object(llm, "_available_models",
                               return_value=["glm-5.3", "glm-5.3-flash"]):
            with mock.patch.object(llm, "_catalog_has_model",
                                   side_effect=lambda fw, m: m == "glm-5.3-flash"):
                self.assertEqual(llm.vision_model("voice", "glm-5.3", "anthropic"),
                                 "glm-5.3-flash")
