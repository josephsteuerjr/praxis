# -*- coding: utf-8 -*-
"""Агент на своём аккаунте Telegram входит в чат сам (1.2.5, слово Егора 28.09).

Йону (ботюзер Дмитрия) звали в чат агентов; Егор: «пусть заходит сама — дай ей тул».
Рука дерева `telegram_account join|leave` в издании отвечала «Telethon hook недоступен»:
крючков не клал никто. Теперь их кладёт транспорт аккаунта (MTProto); у бота их нет.
Живого Telegram здесь нет — стенд держит разбор адреса и то, кто кладёт крючки.

Запуск:  python tests/t_account_join_2809.py
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))

import mtproto  # noqa: E402


class Target(unittest.TestCase):
    def test_invite_links(self):
        for raw in ("https://t.me/+AbCd_12-3", "t.me/joinchat/AbCd_12-3", "+AbCd_12-3",
                    "telegram.me/+AbCd_12-3"):
            self.assertEqual(mtproto.join_target(raw), ("invite", "AbCd_12-3"), raw)

    def test_public_names_and_ids(self):
        self.assertEqual(mtproto.join_target("@AbstractDL_chat"), ("public", "AbstractDL_chat"))
        self.assertEqual(mtproto.join_target("https://t.me/AbstractDL_chat/"), ("public", "AbstractDL_chat"))
        self.assertEqual(mtproto.join_target("-1001240718803"), ("public", "-1001240718803"))

    def test_garbage_is_named_not_guessed(self):
        for raw in ("", "   ", "чат агентов", "@a", "t.me/"):
            self.assertEqual(mtproto.join_target(raw), ("", ""), raw)

    def test_bad_address_is_said_without_touching_telegram(self):
        transport = mtproto.MtprotoTransport.__new__(mtproto.MtprotoTransport)
        self.assertIn("не понял адрес", transport.join_chat("чат агентов"))
        self.assertIn("нужен @имя", transport.leave_chat("t.me/+AbCd"))


class Hooks(unittest.TestCase):
    def test_account_transport_offers_join_and_bot_does_not(self):
        import botapi
        self.assertTrue(callable(getattr(mtproto.MtprotoTransport, "join_chat", None)))
        self.assertTrue(callable(getattr(mtproto.MtprotoTransport, "leave_chat", None)))
        self.assertIsNone(getattr(botapi.BotTransport, "join_chat", None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
