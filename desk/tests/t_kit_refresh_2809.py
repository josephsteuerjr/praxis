# -*- coding: utf-8 -*-
"""Нетронутые тексты поставки обновляются, правленые — нет (1.2.5, 28.09).

Тексты стартового комплекта переписаны без рода и без чужих имён (слово Егора). У агента,
рождённого раньше, в доме лежит прежняя редакция: если файл совпадает с ней слово в слово
(после подстановки имён) — его не правил никто, обновление ставит новую; правленый файл
остаётся как есть. Живая проверка на копии души Джарвиса 28.09: 23 файла + запись дня ноль.

Запуск:  python tests/t_kit_refresh_2809.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))

import boot  # noqa: E402

CFG = {"agent": {"name": "Тест"}, "owner": {"name": "Владелец"}}


class KitRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        self.history = boot._kit_history()
        self.assertTrue(self.history, "resources/.shipped-history.json пуст — сборщик не запускался")

    def tearDown(self):
        self.tmp.cleanup()

    def _plant_old(self, rel: str) -> Path:
        dst = self.tree / ("soul/SOUL.md" if rel == "SOUL.md" else rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(boot._names(self.history[rel][-1], CFG), encoding="utf-8", newline="\n")
        return dst

    def test_untouched_old_text_gets_the_new_one(self):
        dst = self._plant_old("soul/VOICE.md")
        done = boot.refresh_kit(self.tree, CFG)
        self.assertIn("soul/VOICE.md", done)
        new = boot._names((boot._RESOURCES / "soul/VOICE.md").read_text(encoding="utf-8"), CFG)
        self.assertEqual(dst.read_text(encoding="utf-8"), new)
        self.assertEqual(boot.refresh_kit(self.tree, CFG), [], "повтор обязан ничего не трогать")

    def test_edited_text_is_never_touched(self):
        dst = self._plant_old("soul/skills/wanting.md")
        dst.write_text(dst.read_text(encoding="utf-8") + "\nмоя строка\n", encoding="utf-8")
        before = dst.read_text(encoding="utf-8")
        self.assertNotIn("soul/skills/wanting.md", boot.refresh_kit(self.tree, CFG))
        self.assertEqual(dst.read_text(encoding="utf-8"), before)

    def test_constitution_goes_through_the_same_rule(self):
        dst = self._plant_old("SOUL.md")
        self.assertIn("soul/SOUL.md", boot.refresh_kit(self.tree, CFG))
        self.assertEqual(dst.read_text(encoding="utf-8"), boot.soul_text(CFG))

    def test_missing_files_are_not_refreshed_here(self):
        # Отсутствующее кладёт `_seed_kit`; refresh_kit только сверяет лежащее.
        self.assertEqual(boot.refresh_kit(self.tree, CFG), [])

    def test_history_does_not_hold_the_current_text(self):
        for rel, olds in self.history.items():
            src = boot._SOUL_CANON if rel == "SOUL.md" else boot._RESOURCES / rel
            if src.is_file():
                self.assertNotIn(src.read_text(encoding="utf-8"), olds, rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
