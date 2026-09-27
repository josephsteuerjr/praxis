"""Записка первого запуска — служебной плашкой после первого хода (1.2.3).

27.09 у Егора «паспорт» первого запуска висел в чате длинным сообщением от Hélène, а
агент видел его в ленте каждого хода. Стенд держит: до переписки записка — в ленте модели
(первый ход её читает), после — строка `system`/`kind=birth`, лента её пропускает, прочие
строки не тронуты; повтор ничего не меняет.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "localharness"))

import transport  # noqa: E402

PREFIX = "Это твой первый запуск"


class BirthNoteTest(unittest.TestCase):
    def test_note_leaves_the_model_feed_after_first_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            desk = transport.Desk(Path(tmp), "window", "Егор", "Окно", agent_name="Джарвис")
            desk.archive(f"{PREFIX} на устройстве Love. Ты — Джарвис…", outgoing=False, sender="Hélène")
            desk.archive("Привет, Егор. Я Джарвис.", outgoing=True)
            desk.archive("привет!", outgoing=False, sender="Егор")
            self.assertTrue(any(PREFIX in line for line in desk.lines()))

            self.assertEqual(desk.retire_birth_note("Hélène", PREFIX), 1)
            self.assertFalse(any(PREFIX in line for line in desk.lines()))
            rows = desk.rows()
            self.assertEqual(len(rows), 3)
            self.assertEqual((rows[0].get("system"), rows[0].get("kind")), (True, "birth"))
            self.assertTrue(rows[0]["text"].startswith(PREFIX))  # окно раскроет полный текст
            self.assertNotIn("system", rows[1])
            self.assertEqual(rows[2]["text"], "привет!")
            self.assertEqual(desk.retire_birth_note("Hélène", PREFIX), 0)

    def test_owner_words_with_same_start_stay_his(self):
        with tempfile.TemporaryDirectory() as tmp:
            desk = transport.Desk(Path(tmp), "window", "Егор", "Окно")
            desk.archive(f"{PREFIX}? спрашиваю в шутку", outgoing=False, sender="Егор")
            self.assertEqual(desk.retire_birth_note("Hélène", PREFIX), 0)
            archive = Path(tmp) / "memory" / "groups" / "window.jsonl"
            self.assertNotIn("system", json.loads(archive.read_text(encoding="utf-8").splitlines()[0]))

    def test_no_archive_is_nothing_to_do(self):
        with tempfile.TemporaryDirectory() as tmp:
            desk = transport.Desk(Path(tmp), "window", "Егор", "Окно")
            self.assertEqual(desk.retire_birth_note("Hélène", PREFIX), 0)


if __name__ == "__main__":
    unittest.main()
