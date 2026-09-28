# -*- coding: utf-8 -*-
"""`admit` в издании: владелец опознан, впуск открывает Telegram (баг Дмитрия 28.09).

Йоно по прямой просьбе Дмитрия не впустила @tyannojokes: «впускать людей может только
владелец». Дерево узнаёт владельца по PRAXIS_OWNER_ID, которого у издания нет, — отказ был
у всех и всегда. И даже удачный впуск не открывал шлюз Telegram. Стенд держит обе половины
и то, что служебный ход («Hélène») владельцем не считается.

Запуск:  python tests/t_owner_circle_2809.py
"""
from __future__ import annotations

import contextvars
import sys
import tempfile
import types
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))

import owner_circle  # noqa: E402


def _agent(known: dict):
    """Поддельное дерево: `_is_human_owner` как в издании (без PRAXIS_OWNER_ID — всегда
    False) и `admit`, который проверяет владельца ТЕМ ЖЕ вызовом, что дерево."""
    mod = types.SimpleNamespace()
    mod._TURN_CHANNEL = contextvars.ContextVar("turn", default=None)
    mod._is_human_owner = lambda: False

    def tool_admit(name, id=None, role=""):
        if not mod._is_human_owner():
            return "Отказ: впускать людей может только владелец."
        known[str(id)] = name
        return f"Впуск: {name} (id {id}) — в своих."

    mod.TOOL_IMPL = {"admit": tool_admit}
    return mod


class OwnerIdentity(unittest.TestCase):
    def setUp(self):
        self.spoke = [False]
        self.known: dict = {}
        self.mod = _agent(self.known)
        owner_circle.install_owner_identity(self.mod, owner_spoke=lambda: self.spoke[0])

    def _turn(self, owner: bool, spoke: bool):
        self.spoke[0] = spoke
        return self.mod._TURN_CHANNEL.set(types.SimpleNamespace(owner=owner))

    def test_owner_speaking_in_the_window_is_the_owner(self):
        token = self._turn(owner=True, spoke=True)
        try:
            self.assertTrue(self.mod._is_human_owner())
        finally:
            self.mod._TURN_CHANNEL.reset(token)

    def test_service_turn_in_the_window_is_not_the_owner(self):
        # Рождение, будильник, записка обновления: owner=True в окне, но повод — «Hélène».
        token = self._turn(owner=True, spoke=False)
        try:
            self.assertFalse(self.mod._is_human_owner())
        finally:
            self.mod._TURN_CHANNEL.reset(token)

    def test_stranger_is_never_the_owner(self):
        token = self._turn(owner=False, spoke=True)
        try:
            self.assertFalse(self.mod._is_human_owner())
        finally:
            self.mod._TURN_CHANNEL.reset(token)
        self.assertFalse(self.mod._is_human_owner())    # хода нет вовсе

    def test_install_twice_is_harmless(self):
        self.assertFalse(owner_circle.install_owner_identity(self.mod, owner_spoke=lambda: True))


class AdmitOpensTelegram(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        self.known: dict = {}
        self.mod = _agent(self.known)
        self.spoke = [True]
        owner_circle.install_owner_identity(self.mod, owner_spoke=lambda: self.spoke[0])
        sys.modules["social"] = types.SimpleNamespace(known_ids=lambda: dict(self.known))
        owner_circle.install_admit(self.mod, self.tree, telegram_on=lambda: True)

    def tearDown(self):
        sys.modules.pop("social", None)
        self.tmp.cleanup()

    def test_owner_admits_and_the_gate_opens(self):
        token = self.mod._TURN_CHANNEL.set(types.SimpleNamespace(owner=True))
        try:
            out = self.mod.TOOL_IMPL["admit"](name="Тян", id="555")
        finally:
            self.mod._TURN_CHANNEL.reset(token)
        self.assertIn("Впуск", out)
        self.assertIn("может писать", out)
        self.assertEqual(owner_circle.admitted_ids(self.tree), {"555"})

    def test_refusal_writes_nothing(self):
        self.spoke[0] = False     # служебный ход
        token = self.mod._TURN_CHANNEL.set(types.SimpleNamespace(owner=True))
        try:
            out = self.mod.TOOL_IMPL["admit"](name="Тян", id="555")
        finally:
            self.mod._TURN_CHANNEL.reset(token)
        self.assertIn("только владелец", out)
        self.assertEqual(owner_circle.admitted_ids(self.tree), set())


class OwnerUnset(unittest.TestCase):
    """Уточнение Арет 29.09: пустой telegram.owner_id — отказ с настоящей причиной."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        self.known: dict = {}
        self.mod = _agent(self.known)
        self.configured = [False]
        owner_circle.install_owner_identity(self.mod, owner_spoke=lambda: True)
        sys.modules["social"] = types.SimpleNamespace(known_ids=lambda: dict(self.known))
        owner_circle.install_admit(self.mod, self.tree, telegram_on=lambda: True,
                                   owner_configured=lambda: self.configured[0])

    def tearDown(self):
        sys.modules.pop("social", None)
        self.tmp.cleanup()

    def _admit(self, owner: bool) -> str:
        token = self.mod._TURN_CHANNEL.set(types.SimpleNamespace(owner=owner))
        try:
            return self.mod.TOOL_IMPL["admit"](name="Тян", id="555")
        finally:
            self.mod._TURN_CHANNEL.reset(token)

    def test_no_owner_in_telegram_is_said_plainly(self):
        out = self._admit(owner=False)
        self.assertIn("telegram.owner_id", out)
        self.assertIn("из окна", out)
        self.assertEqual(owner_circle.admitted_ids(self.tree), set())

    def test_with_owner_configured_a_stranger_gets_the_plain_refusal(self):
        self.configured[0] = True
        self.assertTrue(self._admit(owner=False).startswith(owner_circle.NOT_OWNER))

    def test_the_owner_in_telegram_admits_without_praxis_owner_id(self):
        # Транспорт сверил отправителя с telegram.owner_id -> ctx.owner (runner.handle_bot);
        # PRAXIS_OWNER_ID не нужен: дерево без неё ответило бы отказом всем.
        self.configured[0] = True
        self.assertIn("Впуск", self._admit(owner=True))
        self.assertEqual(owner_circle.admitted_ids(self.tree), {"555"})


class TelegramOwner(unittest.TestCase):
    def test_handle_bot_takes_the_owner_from_telegram_owner_id(self):
        # Владелец хода в Telegram = отправитель, равный telegram.owner_id транспорта (и бота,
        # и аккаунта: MtprotoTransport наследует BotTransport). Строка — из runner.handle_bot.
        src = (DESK / "localharness" / "runner.py").read_text("utf-8")
        self.assertIn("owner = bool(_bot.owner_id) and str(sender_id) == str(_bot.owner_id)", src)
        import botapi
        import mtproto
        self.assertTrue(issubclass(mtproto.MtprotoTransport, botapi.BotTransport))
        # владелец транспорта читается из telegram.owner_id — строкой
        self.assertIn('self.owner_id = str(tg.get("owner_id") or "").strip()',
                      (DESK / "localharness" / "botapi.py").read_text("utf-8"))


class Gate(unittest.TestCase):
    def test_bot_gate_lets_admitted_in_and_nobody_else(self):
        import botapi
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            owner_circle.record(tree, "777", "Тян")
            bot = botapi.BotTransport.__new__(botapi.BotTransport)
            bot.allow_from, bot.owner_id, bot.allowed_ids = "owner", "111", set()
            bot.rooms = types.SimpleNamespace(tree=tree)
            self.assertTrue(bot.is_allowed("111"))
            self.assertTrue(bot.is_allowed("777"))
            self.assertFalse(bot.is_allowed("888"))
            self.assertFalse(bot.is_allowed(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
