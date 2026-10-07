"""Вспомогательные одиночные вызовы не наследуют reasoning effort роли voice.

05.10: роль voice переведена на reasoning high (разговорный выбор). Все вспомогательные
одиночные вызовы — компакты памяти (memory_life), РЕМ сна (sleep), explain/ask панели,
саммари контекста (agent._summarize_history), консолидация — шли через ту же роль и
наследовали high. glm-5.3 проецирует effort в output_config.effort и тратит max_tokens
на thinking: за утро 05.10 десять падений компактов («сводка не разобрана», часть —
0 знаков при потолке 12000), три degraded-заглушки, каждый провал — двойной расход
12k+20k токенов.

Правка: явный reasoning_effort="low" на каждом вспомогательном сайте (тот же приём,
что ffb58ba4 сделал для узкого взгляда). Тест фиксирует контракт с двух сторон:
(1) компактный вызов посылает низкий effort в канал независимо от effort роли;
(2) обе ноги (первая и retry) одинаково низкие.

Запуск: python praxis_test.py test_aux_low_effort_0510 -v
"""

from __future__ import annotations

import json
import types
import unittest
from unittest import mock

import llm
import memory_life as ml
from test_llm import Base


def _resp_ok():
    return types.SimpleNamespace(
        text=json.dumps({"summary": "За вечер обсуждали лимиты релея.",
                         "open_threads": [], "claims": [], "episodes": []},
                        ensure_ascii=False),
        stop_reason="end_turn", model="glm-5.3", usage=None)


class CompactSendsLowEffortTests(Base):
    """Компакт памяти зовёт канал с reasoning_effort='low' при высоком effort роли."""

    def setUp(self):
        super().setUp()
        # как в проде: голос — high (образец: test_vision_prepass_1609.py:216),
        # иначе имя "..._under_high_role" обещает больше, чем тест доказывает
        cfg = llm._from_env()
        cfg["roles"]["voice"]["reasoning_effort"] = "high"
        llm.save_config(cfg)

    def test_compact_low_effort_under_high_role(self):
        seen = {}

        def fake_chat(role, **kw):
            seen.update(kw, role=role)
            return _resp_ok()

        with mock.patch.object(llm, "configured", return_value=True), \
             mock.patch.object(llm, "chat", side_effect=fake_chat), \
             mock.patch.object(ml, "_memory_role", return_value="voice"):
            out = ml._model_compact([{"id": "evt-1", "actor": "Praxis", "direction": "out",
                                      "line": "Praxis: проверка усилия"}],
                                    tier=1, depth=0, continued=False)
        self.assertIn("summary", out)
        self.assertEqual(seen.get("reasoning_effort"), "low",
                         "компакт обязан явно посылать низкий effort, а не наследовать роль")

    def test_compact_retry_leg_also_low(self):
        """Retry-нога после неразобранного ответа тоже обязана идти с low."""

        calls = []

        def fake_chat(role, **kw):
            calls.append(kw.get("reasoning_effort"))
            if len(calls) == 1:
                return types.SimpleNamespace(text="{обрубок без json", stop_reason="max_tokens",
                                             model="glm-5.3", usage=None)
            return _resp_ok()

        with mock.patch.object(llm, "configured", return_value=True), \
             mock.patch.object(llm, "chat", side_effect=fake_chat), \
             mock.patch.object(ml, "_memory_role", return_value="voice"):
            out = ml._model_compact([{"id": "evt-1", "actor": "Praxis", "direction": "out",
                                      "line": "Praxis: проверка retry"}],
                                    tier=1, depth=0, continued=False)
        self.assertIn("summary", out)
        self.assertEqual(calls, ["low", "low"], "обе ноги компакта — low")


if __name__ == "__main__":
    unittest.main()
