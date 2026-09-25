# -*- coding: utf-8 -*-
"""Уведомления в кадре — приём в издании (25.09, поток G, порт в Hélène).

Проверяется шов харнесса, а не накопитель дерева (он гейтится в дереве, `test_core_notices_2509`):
  * крючок `on_incoming` бота зовётся из приёма ДО постановки чата в очередь — и только для
    сообщений, которые родят ход (гейт «адресовано/разрешено» стоит раньше);
  * `runner._note_incoming` кладёт запись через `core.notices.note_incoming` с честными
    полями: личка → kind=dm и private, группа → mention; `owner_line` в издании не пишется;
  * `runner._run_turn` снимает уведомления этого чата (`clear_chat`) до хода;
  * без модуля `core.notices` (старое дерево) харнесс молчит, а не падает.

Запуск:  python tests/t_notices_intake.py
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "localharness")]
import botapi  # noqa: E402
import runner  # noqa: E402


def fake_notices():
    """Поддельный core.notices: пишет вызовы, чтобы стенд не зависел от дерева."""
    core = types.ModuleType("core")
    notices = types.ModuleType("core.notices")
    notices.calls = []
    notices.note_incoming = lambda **kw: notices.calls.append(("note", kw)) or {"id": "x"}
    notices.clear_chat = lambda chat_id: notices.calls.append(("clear", chat_id)) or 1
    core.notices = notices
    return core, notices


class Intake(unittest.TestCase):
    def setUp(self):
        self.core, self.notices = fake_notices()
        self.patch = mock.patch.dict(sys.modules, {"core": self.core, "core.notices": self.notices})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_dm_and_group_land_in_the_accumulator_with_honest_fields(self):
        runner._note_incoming(chat_id="777", text="ну их, не поднимаю", sender="Егор", sender_id="777",
                              is_dm=True, title="Егор", message_id="42", ts=1.0)
        runner._note_incoming(chat_id="-100", text="а что Феофан думает", sender="Hope",
                              sender_id="5", is_dm=False, title="абстракт", message_id="43")
        kinds = [(c[1]["kind"], c[1]["private"], c[1]["is_owner_dm"]) for c in self.notices.calls]
        self.assertEqual(kinds, [("dm", True, False), ("mention", False, False)],
                         "в издании owner_line не пишется: окон-долгожителей нет")
        first = self.notices.calls[0][1]
        self.assertEqual((first["chat_id"], first["who"], first["message_id"], first["gist"]),
                         ("777", "Егор", "42", "ну их, не поднимаю"))

    def test_turn_start_clears_that_chat(self):
        agent = types.SimpleNamespace(voice_turn_envelope=lambda *a, **kw: "envelope")
        with mock.patch.object(runner, "_agent", agent), \
             mock.patch.object(runner, "_dialogue", lambda chat_id: ([], "")), \
             mock.patch.object(runner, "_orient", lambda chat_id: ""):
            out = runner._run_turn("-100", "convo", "Hope", ctx=None)
        self.assertEqual(out, "envelope")
        self.assertIn(("clear", "-100"), self.notices.calls)

    def test_old_tree_without_notices_is_silent(self):
        with mock.patch.dict(sys.modules, {"core": None, "core.notices": None}):
            runner._note_incoming(chat_id="1", text="x", sender="a", sender_id="1", is_dm=True)
        self.assertEqual(self.notices.calls, [])


class BotHook(unittest.TestCase):
    def test_hook_is_called_before_enqueue_and_only_for_turn_worthy_messages(self):
        bot = botapi.BotTransport.__new__(botapi.BotTransport)
        seen = []
        enqueued = []
        bot.on_incoming = lambda **kw: seen.append(kw)
        bot._enqueue = lambda conversation: enqueued.append(conversation)
        bot.last_incoming = {}
        bot._topic_names = {}
        bot._muted = set()
        bot.allow_from = "owner"
        bot.owner_id = "777"
        bot.rooms = mock.Mock()
        bot.rooms.record = mock.Mock()
        bot.contacts = mock.Mock()
        bot.is_allowed = lambda sender_id: str(sender_id) == "777"
        bot._addressed = lambda message: False
        bot._ingest({"message": {"message_id": 5, "date": 100, "text": "привет",
                                 "chat": {"id": 777, "type": "private", "first_name": "Егор"},
                                 "from": {"id": 777, "first_name": "Егор"}}})
        self.assertEqual(enqueued, ["777"])
        self.assertEqual(len(seen), 1, "крючок зовётся ровно раз")
        self.assertEqual((seen[0]["chat_id"], seen[0]["is_dm"], seen[0]["message_id"], seen[0]["text"]),
                         ("777", True, "5", "привет"))
        # чужой в личке (allow_from=owner): записано, но хода нет — и уведомления нет
        bot._ingest({"message": {"message_id": 6, "date": 101, "text": "эй",
                                 "chat": {"id": 555, "type": "private", "first_name": "Кто-то"},
                                 "from": {"id": 555, "first_name": "Кто-то"}}})
        self.assertEqual(enqueued, ["777"])
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
