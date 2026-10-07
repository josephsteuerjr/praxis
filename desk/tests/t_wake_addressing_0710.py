# -*- coding: utf-8 -*-
"""Пробуждение по имени + приоритетная реплика (07.10, Джарвис).

Живой инцидент: в общем чате агент-тестер отвечал только владельцу — гейт
адресации требовал имя ЦЕЛЫМ СЛОВОМ в форме профиля («Jarvis»), люди писали
«Джарвиса»/«Джарвис, глянь» и не будили; чужие id к тому же резал allow_from.
Здесь: парсер (addressing.py), гейт транспорта (botapi.is_named/_addressed),
фокус-окно (флаг → баннер первой секцией orient хода).

Запуск:  python tests/t_wake_addressing_0710.py
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))

import addressing  # noqa: E402


def _lever(name, value):
    """Установить рычаг на время теста; вернуть функцию восстановления."""
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    def restore():
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old
    return restore


class Parser(unittest.TestCase):
    def test_prefix_default_and_custom_and_off(self):
        restore = _lever("PRAXIS_PRIORITY_PREFIX", None)
        try:
            self.assertEqual(addressing.priority_prefix(), "!")
            urgent, rest = addressing.split_priority("! собери отчёт")
            self.assertTrue(urgent)
            self.assertEqual(rest, "собери отчёт")
        finally:
            restore()
        restore = _lever("PRAXIS_PRIORITY_PREFIX", "СРОЧНО:")
        try:
            urgent, rest = addressing.split_priority("  СРОЧНО: позвони Роме")
            self.assertTrue(urgent)
            self.assertEqual(rest, "позвони Роме")
        finally:
            restore()
        restore = _lever("PRAXIS_PRIORITY_PREFIX", "off")
        try:
            self.assertEqual(addressing.priority_prefix(), "")
            urgent, rest = addressing.split_priority("! всё равно обычная реплика")
            self.assertFalse(urgent)
            self.assertEqual(rest, "! всё равно обычная реплика")
        finally:
            restore()

    def test_prefix_only_at_start_and_never_bare(self):
        # «!!» — приоритет (прецедент её живого парсера: «!» + «!»-хвост считается
        # репликой); голый «!» и «! » без слов — нет; в середине строки — нет.
        urgent, rest = addressing.split_priority("!! перезапуск")
        self.assertTrue(urgent)
        for raw in ("привет ! мир", "!", "  !", "! "):
            urgent, rest = addressing.split_priority(raw)
            self.assertFalse(urgent, raw)
            self.assertEqual(rest, raw, raw)

    def test_loose_name_folds_case_yo_and_forms(self):
        restore = _lever("PRAXIS_WAKE_ON_NAME", None)
        try:
            # off: loose-матчинг не зовётся вовсе
            self.assertFalse(addressing.wake_on_name() == "loose")
        finally:
            restore()
        self.assertTrue(addressing.name_matches_loose(
            "Джарвиса спросили про отчёт", ["Jarvis"]))
        self.assertTrue(addressing.name_matches_loose(
            "джарвисом закрыли вопрос", ["Jarvis"]))
        self.assertTrue(addressing.name_matches_loose(
            "ЕЛЕНЕ передай", ["Hélène"]))
        self.assertFalse(addressing.name_matches_loose(
            "джарвисами не отделаешься", ["Неттакого"]))
        # имя-префикс не ловит середину чужого слова
        self.assertFalse(addressing.name_matches_loose(
            "поддержка джарвисаппорт", ["Jarvis"]))

    def test_banner_names_who_and_gist(self):
        banner = addressing.priority_banner("Егор", "перезапусти прод")
        self.assertIn("[ПРИОРИТЕТ — срочное действие]", banner)
        self.assertIn("Егор", banner)
        self.assertIn("перезапусти прод", banner)


class BotGate(unittest.TestCase):
    """Гейт адресации бота без живого Telegram: только is_named/_addressed."""

    def setUp(self):
        import botapi
        self.botapi = botapi
        self.transport = botapi.BotTransport.__new__(botapi.BotTransport)
        self.transport.me = {"id": 42, "first_name": "Jarvis",
                             "last_name": "", "username": "jarvis_bot"}
        self.transport._cfg = {"agent": {"name": "Джарвис"}}
        self.transport.username = "jarvis_bot"

    def test_strict_whole_word_still_wakes(self):
        restore = _lever("PRAXIS_WAKE_ON_NAME", None)
        try:
            self.assertTrue(self.transport.is_named("Jarvis, глянь логи"))
            self.assertTrue(self.transport.is_named("напиши @jarvis_bot"))
        finally:
            restore()

    def test_off_keeps_inflections_silent(self):
        restore = _lever("PRAXIS_WAKE_ON_NAME", None)
        try:
            self.assertFalse(self.transport.is_named("Джарвиса спросили"))
        finally:
            restore()

    def test_loose_wakes_inflections_of_profile_and_config_names(self):
        restore = _lever("PRAXIS_WAKE_ON_NAME", "loose")
        try:
            self.assertTrue(self.transport.is_named("Джарвиса спросили про отчёт"))
            self.assertTrue(self.transport.is_named("джарвисом решили"))
        finally:
            restore()

    def test_priority_prefix_addresses_in_group(self):
        restore = _lever("PRAXIS_PRIORITY_PREFIX", None)
        try:
            msg = {"text": "! перезапусти прод", "reply_to_message": None}
            self.assertTrue(self.transport._addressed(msg))
            msg_plain = {"text": "просто болтовня", "reply_to_message": None}
            self.assertFalse(self.transport._addressed(msg_plain))
        finally:
            restore()
        restore = _lever("PRAXIS_PRIORITY_PREFIX", "off")
        try:
            msg = {"text": "! теперь это не адресация", "reply_to_message": None}
            self.assertFalse(self.transport._addressed(msg))
        finally:
            restore()


class FocusWindow(unittest.TestCase):
    """Флаг приоритета доезжает до orient хода баннером (шов целиком)."""

    def test_flag_to_banner_through_handle_bot_seam(self):
        # Шов: botapi фиксирует флаг+гист в момент приёма, handle_bot снимает и
        # строит баннер. Здесь — те же шаги без движка.
        import botapi
        transport = botapi.BotTransport.__new__(botapi.BotTransport)
        transport._queue_lock = __import__("threading").Lock()
        transport._wake_priority = {}
        transport._wake_priority_gist = {}
        restore = _lever("PRAXIS_PRIORITY_PREFIX", None)
        try:
            urgent, gist = addressing.split_priority("! перезапусти прод")
            self.assertTrue(urgent)
            with transport._queue_lock:
                if urgent:
                    transport._wake_priority["chat7"] = True
                    transport._wake_priority_gist["chat7"] = str(gist)[:600]
            # handle_bot-часть
            with transport._queue_lock:
                priority_note = ""
                if transport._wake_priority.pop("chat7", False):
                    g = str(transport._wake_priority_gist.pop("chat7", "") or "")
                    if g:
                        priority_note = addressing.priority_banner("Егор", g)
            self.assertIn("[ПРИОРИТЕТ", priority_note)
            self.assertIn("перезапусти прод", priority_note)
            # флаг снят — повторный ход обычный
            with transport._queue_lock:
                self.assertFalse(transport._wake_priority.pop("chat7", False))
        finally:
            restore()


if __name__ == "__main__":
    unittest.main(verbosity=2)
