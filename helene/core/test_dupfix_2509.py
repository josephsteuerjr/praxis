"""Дубль ответа и не тот адресат (25.09).

1. Wake отвеченного адреса снимается по message_id: правка сообщения во время хода
   подменяет объект wake, и прежняя сверка «is wake» оставляла его жить — второй проход,
   второй ответ на тот же #id (AbstractDL 25.09: 109494 и 109499 на #109479).
2. Имя-тёзка: несколько равных кандидатов адресной книги — отказ со списком, а не «самый
   свежий» (25.09: статусы LRX трижды ушли не тому Ивану из восьми).
3. Замороженный чат не получает исходящих: заморозка режет разговор в обе стороны.

Запуск:  python praxis_test.py test_dupfix_2509 -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PRAXIS_TEST", "1")


os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("TELEGRAM_SESSION", ":memory:")


class _ImportClient:
    def __init__(self, *args, **kwargs):
        pass

    def on(self, *args, **kwargs):
        return lambda fn: fn


class _ImportEvents:
    @staticmethod
    def NewMessage(*args, **kwargs):
        return object()

    @staticmethod
    def MessageEdited(*args, **kwargs):
        return object()

    @staticmethod
    def MessageDeleted(*args, **kwargs):
        return object()

    @staticmethod
    def ChatAction(*args, **kwargs):
        return object()


if "mtproto_runner" not in sys.modules:
    prior = sys.modules.get("telethon")
    fake = types.ModuleType("telethon")
    fake.TelegramClient = _ImportClient
    fake.events = _ImportEvents
    sys.modules["telethon"] = fake
    try:
        import mtproto_runner as runner
    finally:
        if prior is None:
            sys.modules.pop("telethon", None)
        else:
            sys.modules["telethon"] = prior
else:
    import mtproto_runner as runner

import rooms


def _wake(message_id, **kw):
    base = dict(message_id=message_id, message_ts=0.0, kind="mention", speaker="Anatoli",
                sender_id=161413158, owner=False, known=True, family=False,
                context_snapshot="", reply_targets_snapshot=(), media_snapshot=(),
                addressed=True, query="q")
    base.update(kw)
    return runner.GroupWake(**base)


class AnsweredWakeIsReleasedByAddress(unittest.TestCase):
    def setUp(self):
        runner._group_wakes.clear()
        self.addCleanup(runner._group_wakes.clear)

    def test_same_object_is_released(self):
        w = _wake(109479)
        runner._group_wakes["-100"] = w
        self.assertTrue(runner._release_answered_wake("-100", w))
        self.assertNotIn("-100", runner._group_wakes)

    def test_revised_object_of_the_same_address_is_released(self):
        w = _wake(109479)
        runner._group_wakes["-100"] = w
        # правка сообщения во время хода: тот же адрес, новый объект (как _revise_group_wake)
        runner._group_wakes["-100"] = replace(w, context_snapshot="edited", query="q2")
        self.assertTrue(runner._release_answered_wake("-100", w), "адрес отвечен — wake снят")
        self.assertNotIn("-100", runner._group_wakes)

    def test_newer_address_survives(self):
        w = _wake(109479)
        runner._group_wakes["-100"] = w
        newer = _wake(109480)
        runner._group_wakes["-100"] = newer
        self.assertFalse(runner._release_answered_wake("-100", w))
        self.assertIs(runner._group_wakes["-100"], newer, "новый адрес ждёт своего прохода")

    def test_ambient_wake_without_id_is_released_only_by_identity(self):
        w = _wake(None, addressed=False, kind="reflective")
        runner._group_wakes["-100"] = w
        other = _wake(None, addressed=False, kind="reflective", query="другой")
        runner._group_wakes["-100"] = other
        self.assertFalse(runner._release_answered_wake("-100", w))
        runner._group_wakes["-100"] = w
        self.assertTrue(runner._release_answered_wake("-100", w))


class NamesakesAreRefusedNotGuessed(unittest.TestCase):
    def test_two_exact_namesakes_are_a_refusal_with_the_list(self):
        book = [
            {"id": "412244782", "display_name": "Иван", "username": "", "lexical": 140,
             "last_seen": 1790300000.0, "score": 190},
            {"id": "555", "display_name": "Ivan Litvak", "username": "ivan_l", "lexical": 140,
             "last_seen": 1790200000.0, "score": 180},
            {"id": "777", "display_name": "Иванов Пётр", "username": "", "lexical": 90,
             "last_seen": 1790100000.0, "score": 120},
        ]
        text = runner._ambiguous_book("Ivan", book)
        self.assertIsNotNone(text)
        self.assertIn("412244782", text)
        self.assertIn("@ivan_l", text)
        self.assertNotIn("777", text, "частичное совпадение в список тёзок не входит")
        self.assertIn("id или @username", text)

    def test_one_exact_among_partials_stays_unambiguous(self):
        book = [
            {"id": "1", "display_name": "Егор", "lexical": 140, "score": 200},
            {"id": "2", "display_name": "Егоров", "lexical": 90, "score": 100},
        ]
        self.assertIsNone(runner._ambiguous_book("Егор", book))

    def test_single_candidate_and_legacy_rows_without_tier(self):
        self.assertIsNone(runner._ambiguous_book("Егор", [{"id": "1", "lexical": 140}]))
        self.assertIsNone(runner._ambiguous_book("Егор", [{"id": "1"}, {"id": "2"}]))

    def test_candidates_carry_their_lexical_tier(self):
        import telegram_contacts as tc
        rows = {"412244782": {"id": "412244782", "display_name": "Иван", "username": "",
                              "aliases": ["Иван"], "last_seen": 1790300000.0},
                "555": {"id": "555", "display_name": "Ivan Litvak", "username": "ivan_l",
                        "aliases": ["Ivan", "Литвак"], "last_seen": 1790200000.0}}
        with patch.object(tc, "_load", return_value=rows):
            book = tc.candidates("Ivan")
        self.assertTrue(book)
        self.assertTrue(all("lexical" in row for row in book))
        tiers = {row["id"]: row["lexical"] for row in book}
        self.assertEqual(tiers.get("555"), 140, "точное имя из алиасов — ярус 140")


class FrozenChatGetsNoOutgoing(unittest.TestCase):
    def test_frozen_peer_is_refused_with_words(self):
        with patch.object(rooms, "is_frozen", return_value=True):
            text = runner._frozen_refusal("412244782", "Иван (id 412244782)")
        self.assertIsNotNone(text)
        self.assertIn("заморожен", text)
        self.assertIn("freeze_chat", text)

    def test_open_peer_passes(self):
        with patch.object(rooms, "is_frozen", return_value=False):
            self.assertIsNone(runner._frozen_refusal("809306689", "Егор"))

    def test_a_broken_freeze_check_does_not_block(self):
        with patch.object(rooms, "is_frozen", side_effect=OSError("disk")):
            self.assertIsNone(runner._frozen_refusal("1", "x"))

    def test_sync_send_refuses_before_any_transport(self):
        ent = types.SimpleNamespace(id=412244782, first_name="Иван", last_name=None,
                                    username=None, title=None)
        sent = []
        with patch.object(runner, "_threadsafe_result", lambda fn, timeout: ent), \
             patch.object(runner, "_direct_tool_execution", return_value={"run_id": "r", "call_id": "c", "tool": "send_message"}), \
             patch.object(runner, "_direct_tool_key", return_value="k"), \
             patch.object(runner, "_marked_peer_id", return_value="412244782"), \
             patch.object(rooms, "is_frozen", return_value=True), \
             patch.object(runner, "_direct_outbox", side_effect=lambda: sent.append("outbox") or None):
            out = runner._sync_send_message("412244782", "апдейт")
        self.assertIsInstance(out, runner.agent.DirectSendRefusal)
        self.assertIn("заморожен", str(out))
        self.assertEqual(sent, [], "до outbox дело не дошло")



class NamesakesAreCountedAcrossTheWholeBook(unittest.TestCase):
    """Ревью V4 F2 (25.09): частичное совпадение с бонусами вставало над двумя точными
    тёзками, и отказа не было — ярус брался у верха ранжирования."""

    def test_partial_with_bonuses_above_two_exact_is_still_a_refusal(self):
        book = [
            {"id": "3", "display_name": "Иван Петров", "username": "ipetrov", "lexical": 105, "score": 170},
            {"id": "1", "display_name": "Иван", "username": "", "lexical": 140, "score": 150},
            {"id": "2", "display_name": "Иван", "username": "ivan2", "lexical": 140, "score": 140},
        ]
        text = runner._ambiguous_book("Иван", book)
        self.assertIsNotNone(text)
        self.assertIn("@ivan2", text)
        self.assertIn("1", text)
        self.assertNotIn("Иван Петров", text, "частичное в список равных не входит")

    def test_partial_ranked_above_a_single_exact_is_named_not_guessed(self):
        book = [
            {"id": "3", "display_name": "Иван Петров", "username": "ipetrov", "lexical": 105, "score": 170},
            {"id": "1", "display_name": "Иван", "username": "", "lexical": 140, "score": 150},
        ]
        text = runner._ambiguous_book("Иван", book)
        self.assertIsNotNone(text, "верх ранжирования ниже точного имени — это свежесть, не выбор")
        self.assertIn("Иван Петров", text)
        self.assertIn("1", text)

    def test_other_layout_of_the_same_name_is_the_same_tier(self):
        book = [
            {"id": "1", "display_name": "Ivan", "username": "", "lexical": 140, "score": 160},
            {"id": "2", "display_name": "Иван", "username": "ivan2", "lexical": 135, "score": 150},
        ]
        self.assertIsNotNone(runner._ambiguous_book("Ivan", book))

    def test_exact_on_top_of_partials_stays_unambiguous(self):
        book = [
            {"id": "1", "display_name": "Егор", "lexical": 140, "score": 200},
            {"id": "2", "display_name": "Егоров", "lexical": 90, "score": 100},
            {"id": "3", "display_name": "Егорка", "lexical": 105, "score": 120},
        ]
        self.assertIsNone(runner._ambiguous_book("Егор", book))


if __name__ == "__main__":
    unittest.main()
