# -*- coding: utf-8 -*-
"""Смена фреймворка снимает прибитый `fallback_framework` (15.09).

ЖИВОЙ СЛУЧАЙ. 15.09 в 18:13 голос ушёл с `anthropic/glm-5.3` на `openai/gpt-6-astra` —
по просьбе Егора в Абстракте, её же рукой `switch_brain`. Запасной стала `glm-5.3`, это
верно. Но в роли с прежних времён был прибит `"fallback_framework": "openai"`, и смену он
пережил: запасная нога поехала искать glm у реле, которое знает пять моделей и ни одной
glm. Код это молча заглаживал — ротировал имя в основную модель, ловил схлопывание и брал
`gpt-5.6-sol`:

```
llm: модель glm-5.3 пропала у openai — ротация имени на gpt-6-astra
llm: fallback openai/glm-5.3 схлопнулся в primary gpt-6-astra — беру отличающуюся модель gpt-5.6-sol
```

То есть конфиг обещал запас на z.ai (отдельный провайдер, отдельные деньги, отдельный
канал), а запас сидел на ТОМ ЖЕ реле и той же подписке: отвалилось бы реле — отвалился бы
и он. `llm.swap_fallback` этот случай знает с самого начала и пин снимает («прибитый
fallback_framework стал бы ложью»); в `brain.switch` и `brain.apply_profile` его просто
забыли — согласовывались три поля из четырёх.

Запуск: python praxis_test.py test_brain_fallback_pin_1509 -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import brain
import llm


class PinBase(unittest.TestCase):
    """Тот же стенд, что у PASS 22, но роль голоса приходит с ПРИБИТЫМ фреймворком."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_pin_"))
        mem = self.tmp / "memory"
        (mem / ".state").mkdir(parents=True)
        (mem / "journal").mkdir(parents=True)
        self._orig = []

        def patch(module, **attrs):
            for key, val in attrs.items():
                self._orig.append((module, key, getattr(module, key)))
                setattr(module, key, val)

        self.patch = patch
        patch(brain, BASE=self.tmp, STATS_PATH=mem / ".state" / "brain_stats.json",
              JOURNAL_DIR=mem / "journal")
        patch(llm, CONFIG_PATH=mem / "llm.json", USAGE_PATH=mem / ".state" / "usage.json")
        llm._CACHE.update(mtime=None, cfg=None)
        cfg = llm._normalize({
            "frameworks": {"anthropic": {"base_url": "https://api.z.ai/api/anthropic",
                                         "api_key": "test-key-a"},
                           "openai": {"base_url": "http://127.0.0.1:5012",
                                      "api_key": "test-key-o"}},
            # Голос на z.ai, запасной — на реле, и фреймворк запасной ПРИБИТ.
            # Ровно то состояние, из которого 15.09 уехали на астру.
            "roles": {"voice": {"framework": "anthropic", "model": "glm-5.2",
                                "fallback_model": "gpt-5.6-sol",
                                "fallback_framework": "openai", "max_tokens": 1024},
                      # Свёртка — случай, где пин ВЕРЕН: обе модели на одном реле.
                      # ⚠ Её запасная не должна совпадать с целью свитча голоса:
                      # `brain.allowlist` приписывает запасную ПРОТИВОПОЛОЖНОМУ
                      # фреймворку, и общая модель втащила бы цель в чужой список.
                      "evaluator": {"framework": "openai", "model": "gpt-5.6-terra",
                                    "fallback_model": "gpt-5.5",
                                    "fallback_framework": "openai", "max_tokens": 400}},
            "limits": {}})
        llm.save_config(cfg)
        llm._CACHE.update(mtime=None, cfg=None)
        patch(llm, _available_models=lambda fw: {
            "anthropic": ["glm-5.2", "glm-4.7"],
            "openai": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5"]}.get(fw, []))
        self.mem = mem

    def tearDown(self):
        for module, key, val in reversed(self._orig):
            setattr(module, key, val)
        llm._CACHE.update(mtime=None, cfg=None)

    # ── что читает сам код при фолбэке ──────────────────────────────────────
    def resolved(self, role: str = "voice"):
        rc = llm._config()["roles"][role]
        other = ((rc.get("fallback_framework") or "").strip()
                 or ("openai" if rc["framework"] == "anthropic" else "anthropic"))
        return other, llm._resolve_fallback_model(
            rc["framework"], rc["model"], other, (rc.get("fallback_model") or "").strip())

    def voice(self) -> dict:
        return llm._config()["roles"]["voice"]


class SwitchDropsThePin(PinBase):
    def test_a_cross_framework_switch_drops_the_pin(self):
        self.patch(llm, ping=lambda role: (True, ""))
        self.assertEqual(self.voice().get("fallback_framework"), "openai", "стенд собран не так")
        res = brain.switch("voice", "gpt-5.6-sol", why="сравнить на неделе")
        self.assertTrue(res["ok"], res)
        rc = self.voice()
        self.assertEqual((rc["framework"], rc["model"]), ("openai", "gpt-5.6-sol"))
        self.assertEqual(rc["fallback_model"], "glm-5.2", "прежняя основная не стала запасной")
        self.assertNotIn("fallback_framework", rc, "пин пережил смену фреймворка")

    def test_after_the_switch_the_fallback_goes_where_the_config_says(self):
        """Главное свойство: объявленное и поедущее совпадают."""
        self.patch(llm, ping=lambda role: (True, ""))
        brain.switch("voice", "gpt-5.6-sol", why="сравнить на неделе")
        other, got = self.resolved()
        self.assertEqual((other, got), ("anthropic", "glm-5.2"),
                         "запасная нога снова поехала не туда, куда обещал конфиг")

    def test_a_same_framework_switch_keeps_the_pin(self):
        """Пин осмыслен, когда фреймворк НЕ менялся: terra → luna одним реле."""
        self.patch(llm, ping=lambda role: (True, ""))
        res = brain.switch("evaluator", "gpt-5.6-sol", why="дешевле на рутине")
        self.assertTrue(res["ok"], res)
        rc = llm._config()["roles"]["evaluator"]
        self.assertEqual(rc["framework"], "openai", "фреймворк не должен был меняться")
        self.assertEqual(rc.get("fallback_framework"), "openai", "снят пин, который был верным")
        self.assertEqual(self.resolved("evaluator"), ("openai", "gpt-5.5"))

    def test_a_failed_handshake_restores_the_pin_too(self):
        """Откат обязан вернуть прежнее состояние целиком, а не почти целиком."""
        self.patch(llm, ping=lambda role: (False, "APIConnectionError: refused"))
        res = brain.switch("voice", "gpt-5.6-sol", why="проверка")
        self.assertFalse(res["ok"])
        rc = self.voice()
        self.assertEqual((rc["framework"], rc["model"]), ("anthropic", "glm-5.2"))
        self.assertEqual(rc.get("fallback_framework"), "openai", "откат потерял прежний пин")

    def test_the_pin_never_reaches_the_file_as_an_empty_string(self):
        """Снятие сделано пустой строкой через слияние; нормализация её выбрасывает."""
        self.patch(llm, ping=lambda role: (True, ""))
        brain.switch("voice", "gpt-5.6-sol", why="сравнить на неделе")
        raw = (self.mem / "llm.json").read_text(encoding="utf-8")
        self.assertNotIn('"fallback_framework": ""', raw)
        self.assertNotIn('"fallback_framework"', raw.split('"evaluator"')[0])


class ProfileDropsThePin(PinBase):
    def test_a_profile_that_crosses_frameworks_drops_the_pin(self):
        self.patch(llm, ping=lambda role: (True, ""))
        crossing = [name for name, spec in brain.PROFILES.items()
                    if spec.get("model") in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5")]
        if not crossing:
            self.skipTest("ни один профиль не уводит голос на другой фреймворк")
        res = brain.apply_profile(crossing[0], why="проверка пина")
        self.assertTrue(res.get("ok"), res)
        rc = self.voice()
        self.assertEqual(rc["framework"], "openai")
        self.assertNotIn("fallback_framework", rc, "пин пережил смену фреймворка профилем")
        other, got = self.resolved()
        self.assertEqual(other, "anthropic")
        self.assertEqual(got, rc["fallback_model"])


if __name__ == "__main__":
    unittest.main()
