"""Рычаги кадра после ночи 12→13.09 в AbstractDL (слово Егора 13.09).

* лента ГРУППЫ — свой потолок `PRAXIS_GROUP_TAPE_CHARS` (0 = потолок комнаты), а не общий
  `PRAXIS_TAPE_CHARS`: 5 500 знаков в комнате на тысячу человек = 10 сообщений за 19 минут;
* давление свёртки по знакам считается по потолку МЕСТА: группе при 0 его нет;
* сводка в кадре — рычаг `PRAXIS_SUMMARY_FRAME_CHARS` (умолчание 12 000, было 4 000);
* свёртка — хроникёр: дословные цитаты, кто с кем спорил, пути и id, а не «обсуждали».

Запуск: python praxis_test.py test_frame_levers_1309 -v
"""
from __future__ import annotations

import importlib
import os
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import memory_life  # noqa: E402


def _rows(n: int, chars: int) -> list[dict]:
    return [{"id": f"evt{i}", "line": "x" * chars, "ts": "2026-09-13T00:00:00.000Z",
             "tokens": max(1, chars // 4), "direction": "in"} for i in range(n)]


class TapeLevers(unittest.TestCase):
    def test_group_place_uses_its_own_tape_lever(self):
        with mock.patch.object(memory_life, "TAPE_CHARS", 5500), \
                mock.patch.object(memory_life, "GROUP_TAPE_CHARS", 0):
            self.assertEqual(memory_life.tape_chars_for("-1001240718803"), 0)
            self.assertEqual(memory_life.tape_chars_for("809306689"), 5500)
            self.assertEqual(memory_life.tape_chars_for("-100123__topic__7"), 0)
        self.assertTrue(memory_life.is_group_place("-4301095307"))
        self.assertFalse(memory_life.is_group_place("412244782"))

    def test_no_char_pressure_for_a_group_at_zero(self):
        hot = _rows(20, 600)                      # 12 000 знаков, 20 событий — ниже HOT_HI
        with mock.patch.object(memory_life, "TAPE_CHARS", 5500):
            dm = memory_life.plan_hot_fold(hot, tape_chars=5500)
            group = memory_life.plan_hot_fold(hot, tape_chars=0)
            default = memory_life.plan_hot_fold(hot)
        self.assertTrue(dm["due"], "личка под потолком 5500 сворачивается по знакам")
        self.assertEqual(dm["reason"], "tape_chars")
        self.assertFalse(group["due"], "группа при 0 по знакам не сворачивается")
        self.assertEqual(group["reason"], "within_window")
        self.assertTrue(default["due"], "без аргумента — общий TAPE_CHARS, как раньше")

    def test_compact_if_due_asks_the_place_for_its_lever(self):
        seen = {}

        def fake_plan(hot, *, force=False, tape_chars=None, place=None):
            seen["tape_chars"] = tape_chars
            seen["place"] = place          # 25.09: план знает, чьё окно (hot_bounds)
            return {"due": False, "reason": "within_window", "count": len(hot), "tokens": 0}
        with mock.patch.object(memory_life, "plan_hot_fold", fake_plan), \
                mock.patch.object(memory_life, "_load_state", lambda cid, rebuild=True: {"hot": []}), \
                mock.patch.object(memory_life, "adopt_place", lambda cid: str(cid)), \
                mock.patch.object(memory_life, "GROUP_TAPE_CHARS", 0), \
                mock.patch.object(memory_life, "TAPE_CHARS", 5500):
            memory_life.compact_if_due("-1001240718803")
            self.assertEqual(seen["tape_chars"], 0)
            self.assertEqual(seen["place"], "-1001240718803")
            memory_life.compact_if_due("809306689")
            self.assertEqual(seen["tape_chars"], 5500)
            self.assertEqual(seen["place"], "809306689")

    def test_group_tape_lever_defaults_to_room_budget(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRAXIS_GROUP_TAPE_CHARS", None)
            importlib.reload(memory_life)
            self.assertEqual(memory_life.GROUP_TAPE_CHARS, 0)
        with mock.patch.dict(os.environ, {"PRAXIS_GROUP_TAPE_CHARS": "24000"}):
            importlib.reload(memory_life)
            self.assertEqual(memory_life.GROUP_TAPE_CHARS, 24000)
        importlib.reload(memory_life)


class SummaryLever(unittest.TestCase):
    def test_summary_frame_chars_is_a_lever_with_a_wider_default(self):
        import agent
        # 25.09: 12 000 → 40 000 (слово Егора; см. test_room_memory_2509)
        self.assertEqual(agent.SUMMARY_FRAME_CHARS, 40000)
        with mock.patch.dict(os.environ, {"PRAXIS_SUMMARY_FRAME_CHARS": "3000"}):
            self.assertEqual(max(0, int(os.getenv("PRAXIS_SUMMARY_FRAME_CHARS", "40000") or 0)), 3000)


class Chronicler(unittest.TestCase):
    """Промпт свёртки — контракт хроникёра, а не протоколиста."""

    def test_prompt_demands_verbatim_quotes_people_and_paths(self):
        p = memory_life._COMPACT_SYSTEM
        for word in ("ЦИТИРУЮ ДОСЛОВНО", "кто с кем спорил", "«…»", "пути, имена файлов",
                     "От первого лица", "не выдумывать", "continued", "\"summary\""):
            self.assertIn(word, p, word)
        self.assertNotIn("4-10 concise lines", p, "старая просьба о протоколе снята")
        self.assertIn("1 500–3 000 знаков", p, "объём задан, а не «коротко»")

    def test_model_compact_sends_the_chronicler_prompt(self):
        captured = {}

        class _Resp:
            text = '{"summary": "Я — хроника.", "open_threads": [], "claims": [], "episodes": []}'

        class _LLM:
            @staticmethod
            def configured(role):
                return True

            @staticmethod
            def chat(role, system, messages, max_tokens):
                captured.update(role=role, system=system, max_tokens=max_tokens)
                return _Resp()
        import sys
        had = sys.modules.get("llm")
        sys.modules["llm"] = _LLM()
        try:
            out = memory_life._model_compact(_rows(3, 50), tier=1, depth=1, continued=False)
        finally:
            if had is None:
                sys.modules.pop("llm", None)
            else:
                sys.modules["llm"] = had
        self.assertEqual(out.get("summary"), "Я — хроника.")
        # 25.09: свёртку пишет она — роль memory/voice (не evaluator), персона в system.
        self.assertIn(captured["role"], ("memory", "voice"))
        self.assertNotIn("Ты — память Praxis", captured["system"], "хроникёр снят")
        self.assertIn("Это моя память", captured["system"])
        self.assertGreaterEqual(captured["max_tokens"], 4000, "хронике нужен потолок ответа не ниже 4000")


if __name__ == "__main__":
    unittest.main()
