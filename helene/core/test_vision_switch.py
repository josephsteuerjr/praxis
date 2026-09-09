# -*- coding: utf-8 -*-
"""Зрение: изображение в кадре → зрячая модель того же фреймворка, до передачи в модель.

09.09, слово владельца: «при приёме сообщений с картинками должна включаться glm-5.3-flash,
переключение — до передачи в модель, это хирургия». Проба 09.09: glm-5.3 картинку не видит и
выдумывает содержимое; glm-5.3-flash описывает верно. Тесты герметичны: фейковые клиенты.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

import llm
from test_llm import Base, FakeAnthResp, FakeAnthropic, FakeOpenAI

IMAGE = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": "aGVsbG8="}}
WITH_IMAGE = [{"role": "user", "content": [{"type": "text", "text": "что на картинке?"}, IMAGE]}]
TEXT_ONLY = [{"role": "user", "content": "привет"}]


class VisionSwitchTests(Base):
    def _glm_voice(self, **over):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["roles"]["voice"].update({"framework": "anthropic", "model": "glm-5.3",
                                       "reasoning_effort": "low", **over})
        cfg["roles"]["evaluator"].update({"framework": "anthropic", "model": "glm-5.3"})
        llm.save_config(cfg)
        return llm._config()

    def test_image_in_frame_switches_text_only_glm_to_flash(self):
        self._glm_voice()
        fake = FakeAnthropic([FakeAnthResp("красный квадрат", model="glm-5.3-flash"),
                              FakeAnthResp("ок", model="glm-5.3")])
        llm.use_test_client(fake)
        rows: list[dict] = []
        with mock.patch.object(llm, "_call_trace", side_effect=lambda *a, **k: rows.append((a, k))):
            resp = llm.chat("voice", messages=WITH_IMAGE)
            llm.chat("voice", messages=TEXT_ONLY)
        self.assertEqual(fake.calls[0]["model"], "glm-5.3-flash", "картинка → зрячая модель")
        self.assertEqual(fake.calls[0]["output_config"], {"effort": "low"},
                         "ступень роли едет и зрячей модели того же эндпойнта")
        self.assertEqual(fake.calls[1]["model"], "glm-5.3", "без картинки — модель роли")
        self.assertEqual(llm._config()["roles"]["voice"]["model"], "glm-5.3",
                         "конфиг роли не мутирует: переключение на один вызов")
        self.assertEqual(resp.model, "glm-5.3-flash")
        self.assertTrue(rows[0][1].get("vision"), "в след вызова — признак зрения")
        self.assertFalse(rows[1][1].get("vision"))

    def test_configured_vision_model_beats_default(self):
        self._glm_voice(vision_model="glm-4.6v")
        self.assertEqual(llm._config()["roles"]["voice"].get("vision_model"), "glm-4.6v",
                         "ручка переживает нормализацию конфига")
        fake = FakeAnthropic([FakeAnthResp("ок", model="glm-4.6v")])
        llm.use_test_client(fake)
        llm.chat("voice", messages=WITH_IMAGE)
        self.assertEqual(fake.calls[0]["model"], "glm-4.6v")
        self.assertEqual(llm.vision_model("voice"), "glm-4.6v")

    def test_sighted_model_keeps_itself_and_can_see(self):
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "k"
        cfg["roles"]["voice"].update({"framework": "openai", "model": "gpt-5.6-sol"})
        llm.save_config(cfg)
        fake = FakeOpenAI()
        llm.use_test_client(fake, "openai")
        llm.chat("voice", messages=WITH_IMAGE)
        self.assertEqual(fake.calls[0]["model"], "gpt-5.6-sol")
        self.assertEqual(llm.vision_model("voice"), "", "зрячей модели замена не нужна")
        self.assertTrue(llm.can_see("voice"))

    def test_can_see_for_text_only_glm_means_default_flash(self):
        self._glm_voice()
        self.assertFalse(llm.accepts_images("voice"))
        self.assertEqual(llm.vision_model("voice"), "glm-5.3-flash")
        self.assertTrue(llm.can_see("voice"), "у glm есть зрячая замена — пиксели класть можно")
        self.assertEqual(llm.vision_model(model="claude-4-sonnet"), "")
        self.assertTrue(llm.can_see("evaluator"), "оценщик с картинкой тоже переключится")

    def test_evaluator_with_image_switches_too(self):
        self._glm_voice()
        fake = FakeAnthropic([FakeAnthResp("ок", model="glm-5.3-flash")])
        llm.use_test_client(fake)
        llm.chat("evaluator", messages=WITH_IMAGE)
        self.assertEqual(fake.calls[0]["model"], "glm-5.3-flash")

    def test_fallback_leg_with_image_is_sighted_too(self):
        # Основная (openai/gpt) упала, фолбэк роли — текстовая glm-5.3 того же реле:
        # картинку ей не отдаём, плечо фолбэка идёт на glm-5.3-flash.
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "k"
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["roles"]["voice"].update({"framework": "openai", "model": "gpt-5.6-sol",
                                       "fallback_model": "glm-5.3", "fallback_framework": "anthropic"})
        llm.save_config(cfg)
        broken = FakeOpenAI()
        # Фолбэк берут только «фолбэчные» отказы (_fallbackable): пустота/обрыв канала, 5xx,
        # известные классы SDK. Голый RuntimeError уходит наружу — и это правильно.
        broken.create = mock.Mock(side_effect=llm.BrokenChannelError("upstream torn"))
        llm.use_test_client(broken, "openai")
        fb = FakeAnthropic([FakeAnthResp("вижу", model="glm-5.3-flash")])
        llm.use_test_client(fb, "anthropic")
        with mock.patch.object(llm, "_resolve_fallback_model", return_value="glm-5.3"):
            resp = llm.chat("voice", messages=WITH_IMAGE)
        self.assertEqual(fb.calls[0]["model"], "glm-5.3-flash")
        self.assertEqual(resp.text, "вижу")

    def test_has_image_blocks_reads_only_content_lists(self):
        self.assertTrue(llm._has_image_blocks(WITH_IMAGE))
        self.assertFalse(llm._has_image_blocks(TEXT_ONLY))
        self.assertFalse(llm._has_image_blocks([{"role": "user", "content": [{"type": "text", "text": "x"}]}]))
        self.assertFalse(llm._has_image_blocks(None))


if __name__ == "__main__":
    unittest.main()
