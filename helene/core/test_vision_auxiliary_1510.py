"""Edition routing regression: a sighted auxiliary must not take over the voice.

Run from repository root: python helene/core/test_vision_auxiliary_1510.py
Only fake model calls, an isolated config and an isolated vision cache are used.
"""
from __future__ import annotations

import os
import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import llm

VOICE = "glm-5.3"
SIGHT = "glm-5.3-flash"
FALLBACK = "glm-5.2"
IMAGE = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                    "data": "iVBORw0KGgo="}}


def text(messages):
    return "\n".join(str(block.get("text") or "")
                     for message in messages for block in message.get("content", [])
                     if isinstance(block, dict) and block.get("type") == "text")


class EditionVisionAuxiliary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edition-vision-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.original_cache = dict(llm._CACHE)
        self.original_state = {r: dict(s) for r, s in llm._STATE.items()}
        self.addCleanup(self.restore_state)
        for name, value in (("CONFIG_PATH", self.tmp / "llm.json"),
                            ("JOURNAL_DIR", self.tmp / "journal"),
                            ("VISION_PREPASS_CACHE", self.tmp / "vision.json"),
                            ("_CALL_TRACE", self.tmp / "calls.jsonl"),
                            ("USAGE_PATH", self.tmp / "usage.json")):
            p = mock.patch.object(llm, name, value)
            p.start()
            self.addCleanup(p.stop)
        llm._CACHE.update(mtime=None, cfg=None)
        for state in llm._STATE.values():
            state.update(on_fallback=False, last_error="")
        env = mock.patch.dict(os.environ, {llm.VISION_PREPASS_LEVER: ""}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        catalog = mock.patch.object(llm, "_available_models",
                                    side_effect=lambda fw: [VOICE, SIGHT, FALLBACK]
                                    if fw == "anthropic" else [])
        catalog.start()
        self.addCleanup(catalog.stop)
        client = mock.patch.object(llm, "_client_for", return_value=object())
        client.start()
        self.addCleanup(client.stop)
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "fixture-only"
        cfg["roles"]["voice"].update(framework="anthropic", model=VOICE,
                                    vision_model=SIGHT, fallback_framework="anthropic",
                                    fallback_model=FALLBACK, vision_prepass=True)
        llm.save_config(cfg)

    def restore_state(self):
        llm._CACHE.clear()
        llm._CACHE.update(self.original_cache)
        for role, state in self.original_state.items():
            llm._STATE[role].clear()
            llm._STATE[role].update(state)

    def initial_tape(self):
        return [{"role": "user", "content": [{"type": "text", "text": "describe"}, IMAGE]}]

    def test_image_tool_iteration_and_next_text_turn_keep_configured_voice(self):
        calls = []
        def transport(fw, model, **kwargs):
            calls.append((model, kwargs))
            return llm.LLMResponse(text=("blue button" if model == SIGHT else "voice answer"),
                                   framework=fw, model=model)
        tape = self.initial_tape()
        with mock.patch.object(llm, "_call", side_effect=transport):
            first = llm.chat("voice", messages=tape, tools=[{"name": "inspect"}])
            # The agent's next model iteration retains the original image in its tape.
            tape = tape + [{"role": "assistant", "content": [{"type": "text", "text": "tool call"}]},
                           {"role": "tool", "content": [{"type": "text", "text": "tool result"}]}]
            second = llm.chat("voice", messages=tape, tools=[{"name": "inspect"}])
            later = llm.chat("voice", messages=[{"role": "user", "content": [
                {"type": "text", "text": "new text-only turn"}]}])
        self.assertEqual((first.model, second.model, later.model), (VOICE, VOICE, VOICE))
        narrow = [(model, kw) for model, kw in calls if model == SIGHT]
        primary = [kw for model, kw in calls if model == VOICE]
        self.assertGreaterEqual(len(narrow), 1)
        self.assertTrue(all(len(kw["messages"]) == 1 and not kw.get("tools")
                            for _, kw in narrow))
        self.assertEqual(len(primary), 3)
        self.assertTrue(all(not llm._has_image_blocks(kw["messages"]) for kw in primary))
        self.assertIn(SIGHT, text(primary[0]["messages"]))
        self.assertIn("blue button", text(primary[1]["messages"]))
        self.assertIn("tool result", text(primary[1]["messages"]))
        self.assertEqual(llm.role_model("voice"), VOICE)
        self.assertEqual(len(calls[-1][1]["messages"]), 1)

    def test_helper_failure_preserves_primary_with_no_pixels_and_never_sends_flash_the_frame(self):
        calls = []
        def transport(fw, model, **kwargs):
            calls.append((model, kwargs))
            if model == SIGHT:
                raise RuntimeError("fixture helper unavailable")
            return llm.LLMResponse(text="I cannot inspect it", framework=fw, model=model)
        with mock.patch.object(llm, "_call", side_effect=transport):
            result = llm.chat("voice", messages=self.initial_tape())
        self.assertEqual([model for model, _ in calls], [SIGHT, VOICE])
        self.assertFalse(llm._has_image_blocks(calls[1][1]["messages"]))
        self.assertIn("NO pixels", text(calls[1][1]["messages"]))
        self.assertTrue(result.text.startswith(llm._NO_PIXELS_RESPONSE))
        self.assertEqual(result.model, VOICE)

    def test_no_sighted_helper_is_not_permission_to_replace_the_voice(self):
        with mock.patch.object(llm, "vision_model", return_value=""), \
                mock.patch.object(llm, "_call", return_value=llm.LLMResponse(
                    text="no image", model=VOICE, framework="anthropic")) as call:
            result = llm.chat("voice", messages=self.initial_tape())
        self.assertEqual(call.call_args.args[:2], ("anthropic", VOICE))
        self.assertFalse(llm._has_image_blocks(call.call_args.kwargs["messages"]))
        self.assertTrue(result.text.startswith(llm._NO_PIXELS_RESPONSE))

    def test_helper_failure_and_primary_channel_failure_never_restore_pixels_on_fallback(self):
        calls = []
        def transport(fw, model, **kwargs):
            calls.append((model, kwargs))
            if model == SIGHT:
                raise RuntimeError("fixture auxiliary unavailable")
            if model == VOICE:
                raise TimeoutError("fixture voice transport failed")
            return llm.LLMResponse(text="fallback has no pixels", model=model, framework=fw)
        with mock.patch.object(llm, "_call", side_effect=transport), \
                mock.patch.object(llm, "_is_network_error", return_value=False):
            result = llm.chat("voice", messages=self.initial_tape())
        self.assertEqual([model for model, _ in calls], [SIGHT, VOICE, FALLBACK])
        self.assertTrue(all(not llm._has_image_blocks(kw["messages"])
                            for model, kw in calls if model != SIGHT))
        self.assertIn("NO pixels", text(calls[-1][1]["messages"]))
        self.assertTrue(result.text.startswith(llm._NO_PIXELS_RESPONSE))
        self.assertEqual(result.model, FALLBACK)

    def test_composed_candidate_selects_this_edition_not_praxis(self):
        """Exercise the real package selector with a small source-bound inventory."""
        source = self.tmp / "package_source"
        edition = source / "helene/core"
        production = source / "praxis"
        edition.mkdir(parents=True)
        production.mkdir()
        edition_bytes = (Path(__file__).parent / "llm.py").read_bytes()
        (edition / "llm.py").write_bytes(edition_bytes)
        (production / "llm.py").write_bytes(b"legacy Praxis implementation")
        (source / "HELENE-SOURCE.json").write_text(json.dumps({
            "tree_inventory_scope": "published_payload", "version": "test", "frozen_tree": "test",
            "frozen_tree_files": {"llm.py": hashlib.sha256(edition_bytes).hexdigest()},
        }), encoding="utf-8")
        composer_path = Path(__file__).parents[2] / "desk/installer/candidate_tree.py"
        spec = importlib.util.spec_from_file_location("edition_vision_composer", composer_path)
        composer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(composer)
        composed = self.tmp / "composed"
        self.assertEqual(composer.stage(composed, root=source)["files"], 1)
        self.assertEqual((composed / "llm.py").read_bytes(), edition_bytes)

    def test_explicit_off_retains_legacy_whole_frame_image_route(self):
        calls = []
        def transport(fw, model, **kwargs):
            calls.append((model, kwargs))
            return llm.LLMResponse(text="flash answer", framework=fw, model=model)
        with mock.patch.dict(os.environ, {llm.VISION_PREPASS_LEVER: "off"}), \
                mock.patch.object(llm, "_call", side_effect=transport):
            result = llm.chat("voice", messages=self.initial_tape())
        self.assertEqual([m for m, _ in calls], [SIGHT])
        self.assertTrue(llm._has_image_blocks(calls[0][1]["messages"]))
        self.assertEqual(result.model, SIGHT)

    def test_absent_profile_setting_stays_off_until_the_deployment_migration(self):
        cfg = llm._config()
        cfg["roles"]["voice"].pop("vision_prepass")
        llm.save_config(cfg)
        self.assertNotIn("vision_prepass", llm._config()["roles"]["voice"])
        self.assertFalse(llm.vision_prepass_enabled("voice"))
        with mock.patch.object(llm, "_call", return_value=llm.LLMResponse(
                text="legacy", model=SIGHT, framework="anthropic")) as call:
            llm.chat("voice", messages=self.initial_tape())
        self.assertEqual(call.call_args.args[1], SIGHT)
        self.assertTrue(llm._has_image_blocks(call.call_args.kwargs["messages"]))


if __name__ == "__main__":
    unittest.main()
