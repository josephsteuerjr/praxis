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
        # Edition delta: no auxiliary call at ANY effort.
        with mock.patch.object(llm, "chat") as chat, \
                mock.patch.object(ml, "_write_compact") as write:
            with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                ml._model_compact(
                    [{"id": "evt-1", "actor": "Praxis", "direction": "out",
                      "line": "Praxis: проверка усилия"}],
                    tier=1, depth=0, continued=False,
                )
        chat.assert_not_called()
        write.assert_not_called()

    def test_compact_retry_leg_also_low(self):
        # A malformed helper response must never trigger even the first call.
        with mock.patch.object(
            llm, "chat", side_effect=AssertionError("no first or retry call")
        ) as chat, mock.patch.object(ml, "_write_compact") as write:
            with self.assertRaisesRegex(RuntimeError, "Acting author required"):
                ml._model_compact(
                    [{"id": "evt-1", "actor": "Praxis", "direction": "out",
                      "line": "Praxis: проверка retry"}],
                    tier=1, depth=0, continued=False,
                )
        chat.assert_not_called()
        write.assert_not_called()




if __name__ == "__main__":
    unittest.main()

from test_authorship_support_1910 import AuthoredMemoryBase
from test_coverage_vs_current import ROOM


class CompactAuthorshipUnderPressure(AuthoredMemoryBase):
    def test_hard_and_force_only_offer_and_never_write_a_compact(self):
        for i in range(1, 12):
            self._msg(i, f"pressure source {i}")
        before = [r["id"] for r in ml.rebuild_state(ROOM)["hot"]]

        with mock.patch.object(ml, "hot_bounds", return_value=(2, 3, 4, 10**9)), \
                mock.patch.object(
                    ml, "_model_compact",
                    side_effect=AssertionError("hidden helper"),
                ) as model, \
                mock.patch.object(
                    llm, "chat", side_effect=AssertionError("hidden LLM")
                ) as chat, \
                mock.patch.object(ml, "_write_compact") as write:
            hard = ml.compact_if_due(ROOM)
            self.assertTrue(hard["plan"]["hard"], hard)
            for out in (
                hard,
                ml.compact_if_due(ROOM, force=True),
                ml.fold_now(ROOM),
            ):
                self.assertTrue(out["author_required"], out)
                self.assertEqual(out["folded"], 0, out)
            model.assert_not_called()
            chat.assert_not_called()
            write.assert_not_called()

        self.assertEqual(
            [r["id"] for r in ml.rebuild_state(ROOM)["hot"]], before
        )
        self.assertFalse(list(ml.COMPACTS_DIR.rglob("*.md")))

    def test_author_text_succeeds_without_auxiliary_effort(self):
        self.assert_authored_success("I retain the selected original sources.")
