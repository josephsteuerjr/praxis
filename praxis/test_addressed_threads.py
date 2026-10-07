# -*- coding: utf-8 -*-
"""Адресный рефлекс памяти: collect() поднимает нити названных людей с источником.

desire-a03780224e. Проверяется сборщик, не рендер (рендер уже протестирован
своей пустотой и не менялся по сути — только комментарий).
"""

import json
import time
import unittest
from pathlib import Path
from unittest import mock

import addressed_threads
import people


class _Ctx:
    def __init__(self, chat_id=None, room_id=None, principal_id=None):
        self.chat_id = chat_id
        self.room_id = room_id
        self.principal_id = principal_id


class CollectTests(unittest.TestCase):
    def setUp(self):
        people.PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
        self._dossier = people.PEOPLE_DIR / "test-addr-reflex-person.md"
        self._dossier.write_text(
            "# Кто\nТестовый человек\n\n## Открытые нити\n"
            "- [ ] вернуть книгу про графы\n"
            "- [x] закрытая нить — не поднимается\n"
            "- [ ]  спор о негэнтропии: обещал пруф из хистори\n",
            encoding="utf-8",
        )
        self._tg = "999000111"
        people.set_telegram_id("test-addr-reflex-person", "Тестовый человек", self._tg)

    def tearDown(self):
        if self._dossier.exists():
            self._dossier.unlink()

    def test_empty_without_ctx(self):
        self.assertEqual(addressed_threads.collect(None), ())

    def test_empty_when_nobody_present(self):
        ctx = _Ctx(chat_id=-100123, principal_id=None)
        with mock.patch("agent._room_window", return_value=[], create=True):
            self.assertEqual(addressed_threads.collect(ctx), ())

    def test_loops_of_present_person(self):
        rows = [{"sender_id": int(self._tg)}, {"sender_id": 42}]
        ctx = _Ctx(chat_id=-100123)
        with mock.patch("agent._room_window", return_value=rows, create=True):
            out = addressed_threads.collect(ctx)
        self.assertEqual(len(out), 2, out)
        self.assertIn("(досье)", out[0])
        self.assertIn("вернуть книгу", out[0])
        self.assertIn("негэнтропии", out[1])
        self.assertNotIn("закрытая нить", " ".join(out))

    def test_pending_followup_of_present_person(self):
        ledger = {
            "version": 1,
            "items": [
                {
                    "id": "tgfu_test1",
                    "status": "pending",
                    "target_user_id": self._tg,
                    "target_peer_id": -100123,
                    "target_label": "Test Room",
                    "sent_excerpt": "обещала выжимку статьи",
                    "expires_at": time.time() + 3600,
                },
                {
                    "id": "tgfu_test2",
                    "status": "answered",
                    "target_user_id": self._tg,
                    "target_peer_id": -100123,
                    "target_label": "Test Room",
                    "sent_excerpt": "уже отвечено",
                    "expires_at": time.time() + 3600,
                },
                {
                    "id": "tgfu_test3",
                    "status": "pending",
                    "target_user_id": "777777",
                    "target_peer_id": -555,
                    "target_label": "Other",
                    "sent_excerpt": "чужая нить",
                    "expires_at": time.time() + 3600,
                },
            ],
            "pending_revisions": [],
        }
        self._dossier.write_text("# Кто\nТестовый человек\n", encoding="utf-8")
        people.set_telegram_id("test-addr-reflex-person", "Тестовый человек", self._tg)
        ctx = _Ctx(chat_id=-100123)
        with mock.patch("agent._room_window",
                        return_value=[{"sender_id": int(self._tg)}], create=True), \
             mock.patch("telegram_followups._load", return_value=ledger):
            out = addressed_threads.collect(ctx)
        self.assertEqual(len(out), 1, out)
        self.assertIn("follow-up", out[0])
        self.assertIn("выжимку статьи", out[0])
        self.assertNotIn("чужая нить", out[0])

    def test_cap(self):
        self._dossier.write_text(
            "# Кто\nТестовый человек\n\n## Открытые нити\n"
            + "\n".join(f"- [ ] нить номер {i}" for i in range(10)),
            encoding="utf-8",
        )
        people.set_telegram_id("test-addr-reflex-person", "Тестовый человек", self._tg)
        ctx = _Ctx(chat_id=-100123, principal_id=int(self._tg))
        with mock.patch("agent._room_window", return_value=[], create=True):
            out = addressed_threads.collect(ctx)
        self.assertLessEqual(len(out), addressed_threads.MAX_ITEMS)


if __name__ == "__main__":
    unittest.main()
