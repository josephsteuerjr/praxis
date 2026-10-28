"""The image hand crosses both voice routes, config, artifact and media seams."""
import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
import agent
import imagegen
import llm
import media
import panel
import run_context
from run_manager import RunManager
from test_llm import Base


def png():
    output = io.BytesIO()
    Image.new("RGB", (3, 2), "blue").save(output, format="PNG")
    return output.getvalue()


class ImageGenerationTests(Base):
    def setUp(self):
        super().setUp()
        self.usage_patch = mock.patch.object(llm, "USAGE_PATH", self.tmp / "usage.json")
        self.usage_patch.start(); self.addCleanup(self.usage_patch.stop)
        self.spool = media.MediaSpool(self.tmp / "media")
        self.raw = png()
        self.body = {"data": [{"b64_json": base64.b64encode(self.raw).decode(),
                               "generation_id": "generation-test"}],
                     "usage": {"input_tokens": 21, "output_tokens": 515}}

    def configure(self, framework="anthropic"):
        cfg = llm._from_env()
        cfg["roles"]["voice"].update(framework=framework, model="glm-5.3" if framework == "anthropic" else "gpt-5.6-sol")
        cfg["frameworks"]["openai"] = {"base_url": "http://localhost:5011/v1", "api_key": "fixture-key"}
        cfg["images"] = {**imagegen.DEFAULTS, "enabled": True, "quality": "low"}
        llm.save_config(cfg)
        llm._CACHE.update(mtime=None, cfg=None)
        return cfg

    def test_normalization_and_panel_write_keep_text_roles(self):
        cfg = self.configure()
        with mock.patch.object(llm, "_available_models", return_value=[]), mock.patch.object(panel, "_journal_panel"):
            got = panel.llm_set({"images": {"quality": "high", "background": "transparent"}})
        self.assertTrue(got["ok"])
        actual = llm._config()
        self.assertEqual(actual["roles"], cfg["roles"])
        self.assertEqual(actual["images"]["quality"], "high")
        self.assertEqual(got["state"]["images"]["background"], "transparent")
        self.assertNotIn("fixture-key", json.dumps(got))
        self.assertFalse(imagegen.normalize(None)["enabled"])
        clean, errors = panel._llm_validate({"images": {"enabled": "yes"}})
        self.assertTrue(errors)
        for value in (None, True, 5):
            with self.assertRaises(ValueError):
                imagegen.validate({"model": value})

    def test_generate_hand_queues_real_image_on_glm_and_relay(self):
        for framework in ("anthropic", "openai"):
            with self.subTest(framework=framework):
                self.configure(framework)
                ctx = agent.ChannelContext(chat_id="777", is_dm=True, owner=True)
                pending = []
                with mock.patch.object(agent, "_media_spool", return_value=self.spool), \
                     mock.patch.object(llm, "can_see", return_value=framework == "openai"), \
                     mock.patch.object(agent, "_model_view_image", side_effect=lambda path, **kw: (path, "image/png")), \
                     mock.patch.object(imagegen, "_post", return_value=(self.body, "request-test")) as post:
                    token = agent._TURN_CHANNEL.set(ctx)
                    outbound = agent._TURN_OUTBOUND.set(pending)
                    try:
                        out = agent.TOOL_IMPL["generate_image"](prompt="draw a blue square", caption="готово", send=None)
                    finally:
                        agent._TURN_CHANNEL.reset(token); agent._TURN_OUTBOUND.reset(outbound)
                result = json.loads(str(out))
                self.assertTrue(result["ok"])
                self.assertEqual(result["delivery"], "staged")
                self.assertEqual((result["width"], result["height"]), (3, 2))
                self.assertEqual(result["sha256"], hashlib.sha256(self.raw).hexdigest())
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0].target_chat_id, "777")
                self.assertEqual(pending[0].kind, "photo")
                self.assertEqual(pending[0].path.read_bytes(), self.raw)
                self.assertEqual(post.call_count, 1)
                self.assertTrue(post.call_args.args[0].endswith("/v1/images/generations"))
                self.assertEqual(post.call_args.args[2]["model"], "gpt-image-2")
                self.assertEqual(isinstance(out, agent.ToolObservation), framework == "openai")

    def test_edition_image_channel_keeps_primary_api_endpoint_and_key(self):
        cfg = self.configure("openai")
        cfg["frameworks"]["openai"] = {"base_url": "https://text.invalid/v1", "api_key": "voice-secret"}
        cfg["image_channel"] = {"base_url": "http://127.0.0.1:5123", "api_key": "image-loop"}
        llm.save_config(cfg); llm._CACHE.update(mtime=None, cfg=None)
        self.assertEqual(llm._config()["image_channel"], cfg["image_channel"])
        ctx = agent.ChannelContext(chat_id="777", is_dm=True, owner=True)
        with mock.patch.object(agent, "_media_spool", return_value=self.spool), \
             mock.patch.object(llm, "can_see", return_value=False), \
             mock.patch.object(imagegen, "_post", return_value=(self.body, "request-test")) as post:
            token = agent._TURN_CHANNEL.set(ctx)
            try: result = json.loads(str(agent.tool_generate_image("draw", send=False)))
            finally: agent._TURN_CHANNEL.reset(token)
        self.assertTrue(result["ok"])
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:5123/images/generations")
        self.assertEqual(post.call_args.args[1], "image-loop")
        self.assertEqual(llm._config()["frameworks"]["openai"]["api_key"], "voice-secret")
        self.assertNotIn("image-loop", json.dumps(panel.llm_get()))

    def test_edit_contract_and_disabled_and_invalid_artifact(self):
        cfg = self.configure()
        source = self.tmp / "source.png"; source.write_bytes(self.raw)
        with mock.patch.object(imagegen, "_post", return_value=(self.body, "request-test")) as post:
            result = imagegen.generate("make it green", refs=[source], spool=self.spool, scope="owner", chat_id="777", config=cfg["images"], framework=cfg["frameworks"]["openai"], turn_id="turn-test")
        self.assertEqual(result["operation"], "edits")
        self.assertTrue(post.call_args.args[0].endswith("/images/edits"))
        self.assertEqual(post.call_args.args[2]["images"][0]["image_url"], "data:image/png;base64," + base64.b64encode(self.raw).decode())
        for body in ({"data": []}, {"data": [{"b64_json": "aGVsbG8="}]}):
            with mock.patch.object(imagegen, "_post", return_value=(body, "")) as post, self.assertRaises(ValueError):
                imagegen.generate("draw", refs=[], spool=self.spool, scope="owner", chat_id="777", config=cfg["images"], framework=cfg["frameworks"]["openai"], turn_id="turn-test")
            self.assertEqual(post.call_count, 1)
        with mock.patch.object(imagegen, "_post") as post, self.assertRaises(ValueError):
            imagegen.generate("draw", refs=[], spool=self.spool, scope="owner", chat_id="777", config={}, framework=cfg["frameworks"]["openai"], turn_id="turn-test")
        post.assert_not_called()

    def test_uncertain_outcome_is_not_retried_or_queued(self):
        self.configure()
        with mock.patch.object(imagegen, "_post", side_effect=ValueError("исход неизвестен")) as post, \
             mock.patch.object(agent, "_media_spool", return_value=self.spool), \
             mock.patch.object(agent, "_stage_turn_media") as queue:
            result = json.loads(agent.tool_generate_image("draw", send=False))
        self.assertFalse(result["ok"])
        self.assertEqual(post.call_count, 1)
        queue.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "durable evidence requires POSIX openat")
    def test_durable_generated_pixels_resume_without_generation_and_reject_tamper(self):
        self.configure("openai")
        manager = RunManager(self.tmp)
        context = run_context.RunContext.create(kind="chat", goal="draw", principal_id="owner", scope="owner")
        manager.create(context, "image test")
        with mock.patch.object(agent, "_runs", return_value=manager), \
             mock.patch.object(agent, "_media_spool", return_value=self.spool), \
             mock.patch.object(llm, "can_see", return_value=True), \
             mock.patch.object(agent, "_model_view_image", side_effect=lambda path, **kw: (path, "image/png")), \
             mock.patch.object(imagegen, "_post", return_value=(self.body, "request-test")) as post, \
             run_context.bind_run(context):
            output = agent.tool_generate_image("draw", send=False)
            result = json.loads(str(output))
            ref = manager.store_result(context.run_id, str(output), call_id="image-call", name="generate_image")
            pixels = agent._resume_result_image(context.run_id, "generate_image", {}, ref)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(pixels[0]["origin"], "generated-image")
            self.assertEqual(Path(pixels[0]["path"]).read_bytes(), self.raw)
            self.assertTrue(result["artifact"]["path"].startswith("artifacts/"))
            Path(pixels[0]["path"]).write_bytes(b"changed")
            with self.assertRaises(agent.DurableExecutionError):
                agent._resume_result_image(context.run_id, "generate_image", {}, ref)
            self.assertEqual(post.call_count, 1)

    def test_tool_survives_pointer_and_provider_schema_translations(self):
        schema = next(t for t in agent.BASE_TOOLS if t["name"] == "generate_image")
        self.assertEqual(schema["input_schema"]["required"], ["prompt"])
        self.assertIn("generate_image", agent.HAND_PURPOSE)
        self.assertTrue(callable(agent.TOOL_IMPL["generate_image"]))
        openai_schema = llm._openai_strict_schema(schema["input_schema"])
        self.assertIn("image_paths", openai_schema["required"])
        self.assertTrue(any(branch.get("type") == "null" for branch in
                            openai_schema["properties"]["image_paths"]["anyOf"]))


if __name__ == "__main__":
    unittest.main()
