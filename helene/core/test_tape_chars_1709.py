# -*- coding: utf-8 -*-
"""Потолок ленты в знаках — в издании (КЕАТ, перенос 17.09).

ПОЧЕМУ СВОЙ СТЕНД, А НЕ ЯДЕРНЫЙ. `test_frame_levers_1309.py` ядра проверяет заодно две
вещи, которых в издании нет и быть не должно: русский текст промпта-хроникёра (здесь он
английский — решение издания 15.09, «официально GLM русский не поддерживает») и
`compact_if_due` на живом дереве (ограда издания не пускает стенд писать в дом агента).
Перенести его как есть — значит получить красный гейт на верных правках.

ЧТО ЗАКРЕПЛЕНО ЗДЕСЬ:

* лента в кадре режется по ЗНАКАМ, а не только по числу записей: сто двадцать пять
  записей — потолок ПАМЯТИ, и замер ядра 12.09 дал 43,5 тыс. знаков при договорённых
  5 500;
* у группы потолок СВОЙ: ночь 12→13.09 в комнате на тысячу человек показала, что общий
  порог оставляет от разговора десять реплик за девятнадцать минут;
* последняя запись едет всегда — то, на что отвечает реплика, из кадра не выпадает;
* причина свёртки называется своим именем (`tape_chars`), а не прячется в `hard_window`.

Запуск: python praxis_test.py test_tape_chars_1709 -v
"""
from __future__ import annotations

import unittest
from unittest import mock

import memory_life as ml


def _rows(n: int, size: int, *, out: bool = False) -> list[dict]:
    return [{"line": "x" * size, "tokens": max(1, size // 4), "ts": 1_700_000_000 + i * 60,
             "direction": "out" if out else "in", "id": f"evt-{i}"} for i in range(n)]


class TapeWindow(unittest.TestCase):
    def test_the_window_keeps_the_newest_rows_within_the_budget(self):
        rows = _rows(10, 100)
        kept = ml.tape_window(rows, 250)
        self.assertLess(len(kept), len(rows))
        self.assertEqual(kept[-1]["id"], "evt-9", "самая свежая запись выпала из ленты")
        self.assertLessEqual(sum(len(r["line"]) + 1 for r in kept), 250 + 101)

    def test_a_single_huge_row_still_travels(self):
        """Реплика длиннее всего потолка едет одна: без неё ход отвечает в пустоту."""
        kept = ml.tape_window(_rows(1, 9000), 500)
        self.assertEqual(len(kept), 1)

    def test_zero_means_no_ceiling(self):
        rows = _rows(40, 300)
        self.assertEqual(len(ml.tape_window(rows, 0)), 40)


class TapeLevers(unittest.TestCase):
    def test_the_default_ceiling_is_the_agreed_one(self):
        self.assertEqual(ml.TAPE_CHARS, 5500)

    def test_a_group_has_its_own_lever_and_it_is_off_by_default(self):
        self.assertEqual(ml.GROUP_TAPE_CHARS, 0)
        self.assertEqual(ml.tape_chars_for("-1001240718803"), 0)
        self.assertEqual(ml.tape_chars_for("809306689"), 5500)
        self.assertEqual(ml.tape_chars_for("-100123__topic__7"), 0,
                         "тема форума — то же место группы, а не личка")

    def test_the_window_of_a_group_is_not_cut_at_the_private_ceiling(self):
        rows = _rows(40, 300)
        self.assertEqual(len(ml.tape_window(rows, ml.tape_chars_for("-1001240718803"))), 40)


class CharPressure(unittest.TestCase):
    def test_a_long_tape_folds_by_chars_and_says_so(self):
        plan = ml.plan_hot_fold(_rows(20, 600, out=True), tape_chars=2000)
        self.assertTrue(plan["due"], "лента втрое выше потолка, а свёртка не назначена")
        self.assertEqual(plan["reason"], "tape_chars",
                         "причина свёртки спрятана под общим именем")
        self.assertGreater(plan["fold"], 0)

    def test_no_char_pressure_when_the_lever_is_zero(self):
        plan = ml.plan_hot_fold(_rows(20, 600, out=True), tape_chars=0)
        self.assertNotEqual(plan.get("reason"), "tape_chars")

    def test_a_short_tape_is_left_alone(self):
        plan = ml.plan_hot_fold(_rows(4, 100), tape_chars=5500)
        self.assertFalse(plan["due"])
        self.assertEqual(plan["reason"], "within_window")

    def test_the_plan_reports_the_chars_it_measured(self):
        plan = ml.plan_hot_fold(_rows(6, 200), tape_chars=5500)
        self.assertEqual(plan["chars"], 1200)


class WindowFrameUsesTheCeiling(unittest.TestCase):
    """Кадр окна берёт ленту ЧЕРЕЗ потолок, иначе правка бесполезна."""

    def test_runner_cuts_the_tape_before_building_the_frame(self):
        # ⚠ Раннер окна живёт в ПОСТАВКЕ (`desk/localharness/`), а не в дереве агента:
        # в гейте, который гоняют внутри одного дерева, его рядом нет. Тогда случай
        # пропускается — честнее, чем красный гейт на верной правке.
        from pathlib import Path
        runner = Path(__file__).resolve().parent.parent / "praxis-repo" / "desk" / "localharness" / "runner.py"
        if not runner.is_file():
            self.skipTest("поставка рядом не лежит: этот случай проверяется на машине сборки")
        source = runner.read_text(encoding="utf-8")
        self.assertIn("tape_window", source,
                      "раннер издания берёт горячий слой мимо потолка в знаках")
        self.assertIn("tape_chars_for", source,
                      "раннер издания не спрашивает потолок у МЕСТА")


if __name__ == "__main__":
    unittest.main()
