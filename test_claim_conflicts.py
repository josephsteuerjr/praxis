# -*- coding: utf-8 -*-
"""Карточка конфликта показывает и НЕ застревает.

Главные тесты здесь — отрицательные: у карточки не должно быть прогона, замка,
пробуждения и терминального состояния. Основание — четыре рецидива вечной петли
возобновления и `in_doubt`, ставший надгробием без руки закрыть.
"""
from __future__ import annotations

import importlib
import tempfile
import time
import unittest
from pathlib import Path


class ConflictCardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        import claim_conflicts
        self.cc = claim_conflicts
        self.cc.PATH = Path(self.dir.name) / "claim_conflicts.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def _note(self, old="clm-a", new="clm-b"):
        return self.cc.note(subject="Егор", field="город", old_id=old, new_id=new,
                            reason="новое поддержанное противоречит старому",
                            old_text="живёт в Самаре", new_text="живёт в Безенчуке")

    def test_card_is_created_and_visible(self):
        cid = self._note()
        self.assertTrue(cid.startswith("cfl-"))
        cards = self.cc.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["subject"], "Егор")
        self.assertIn("Самаре", cards[0]["old_text"])

    def test_repeat_does_not_multiply(self):
        first, second = self._note(), self._note()
        self.assertEqual(first, second)
        self.assertEqual(len(self.cc.cards()), 1)

    def test_resolution_is_a_new_line_not_a_rewrite(self):
        cid = self._note()
        self.assertTrue(self.cc.resolve(cid, verdict="оба верны, разные периоды"))
        self.assertEqual(self.cc.cards(), [], "разрешённая уходит из открытых")
        all_cards = self.cc.cards(only_open=False)
        self.assertEqual(len(all_cards), 1)
        self.assertEqual(all_cards[0]["resolution"]["verdict"], "оба верны, разные периоды")
        self.assertIn("Самаре", all_cards[0]["old_text"], "история спора осталась читаемой")

    def test_unknown_card_cannot_be_resolved(self):
        self.assertFalse(self.cc.resolve("cfl-нет-такой", verdict="что угодно"))

    def test_stale_card_disappears_from_view_but_not_from_disk(self):
        cid = self._note()
        rows = self.cc.PATH.read_text(encoding="utf-8").splitlines()
        import json
        row = json.loads(rows[0])
        row["at"] = time.time() - 400 * 86400
        self.cc.PATH.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(self.cc.cards(), [], "старая не показывается")
        self.assertEqual(len(self.cc.cards(fresh_days=None)), 1, "но с диска не исчезла")
        self.assertTrue(cid)

    def test_view_is_capped(self):
        for i in range(40):
            self._note(old="clm-o%d" % i, new="clm-n%d" % i)
        self.assertLessEqual(len(self.cc.cards()), self.cc.SHOW_LIMIT)

    def test_broken_line_does_not_break_reading(self):
        self._note()
        with self.cc.PATH.open("a", encoding="utf-8") as fh:
            fh.write("{это не json\n")
        self.assertEqual(len(self.cc.cards()), 1)

    def test_unwritable_store_does_not_raise(self):
        self.cc.PATH = Path("/proc/нельзя/сюда/писать.jsonl")
        self.assertEqual(self.cc.note(subject="x", old_id="a", new_id="b"), "")
        self.assertEqual(self.cc.cards(), [])


class ItIsNotARunTests(unittest.TestCase):
    """Отрицательные тесты: у карточки нет ничего от прогона."""

    def test_module_has_no_run_lifecycle(self):
        import claim_conflicts
        src = Path(claim_conflicts.__file__).read_text(encoding="utf-8")
        # Запрет про ПОВЕДЕНИЕ, а не про упоминание: докстринг как раз объясняет, почему
        # прогона здесь нет, и обязан называть вещи своими именами.
        src = src.split('"""', 2)[-1]
        for forbidden in ("RunManager", "run_manager", "wake_request", "follow_up",
                          "in_doubt", "lease", "acquire", "threading.Lock"):
            self.assertNotIn(forbidden, src,
                             "карточка обязана оставаться записью, а не прогоном: %s" % forbidden)

    def test_no_public_api_blocks_or_waits(self):
        import claim_conflicts
        src = Path(claim_conflicts.__file__).read_text(encoding="utf-8").split('"""', 2)[-1]
        self.assertNotIn("time.sleep", src)
        self.assertNotIn("while True", src)


if __name__ == "__main__":
    unittest.main()
