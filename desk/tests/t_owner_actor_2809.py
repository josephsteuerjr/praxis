# -*- coding: utf-8 -*-
"""Клик рукой `computer` в ходе владельца из окна (28.09).

Джарвис открыл установщик и хотел нажать «Обновить до 1.2.4»: `act_element` отказал
«не опознан principal для ключа действия». Дерево знает двоих действующих — самого
агента и человека по числовому Telegram-id; у хода из окна id нет. Харнесс подменял
права (`_computer_allowed`), а «кто действует» (`_computer_actor`) — нет.

Запуск:  python tests/t_owner_actor_2809.py
"""
from __future__ import annotations

import contextvars
import sys
import types
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESK))

from localharness import body  # noqa: E402


def _agent(tree_says: str = ""):
    mod = types.SimpleNamespace()
    mod._TURN_CHANNEL = contextvars.ContextVar("turn", default=None)
    mod._computer_actor = lambda: tree_says
    return mod


class OwnerActor(unittest.TestCase):
    def test_owner_turn_from_the_window_has_a_principal(self):
        mod = _agent("")
        self.assertTrue(body.install_owner_actor(mod))
        token = mod._TURN_CHANNEL.set(types.SimpleNamespace(owner=True))
        try:
            self.assertEqual(mod._computer_actor(), body.OWNER_PRINCIPAL)
        finally:
            mod._TURN_CHANNEL.reset(token)

    def test_stranger_without_id_stays_without_principal(self):
        mod = _agent("")
        body.install_owner_actor(mod)
        token = mod._TURN_CHANNEL.set(types.SimpleNamespace(owner=False))
        try:
            self.assertEqual(mod._computer_actor(), "")
        finally:
            mod._TURN_CHANNEL.reset(token)
        self.assertEqual(mod._computer_actor(), "")  # хода нет вовсе

    def test_tree_word_comes_first_and_install_is_idempotent(self):
        mod = _agent("telegram:42")
        self.assertTrue(body.install_owner_actor(mod))
        self.assertFalse(body.install_owner_actor(mod))
        token = mod._TURN_CHANNEL.set(types.SimpleNamespace(owner=True))
        try:
            self.assertEqual(mod._computer_actor(), "telegram:42")
        finally:
            mod._TURN_CHANNEL.reset(token)

    def test_principal_is_stable(self):
        # Ключ действия (idempotency) привязан к строке: повтор — тот же отпечаток.
        self.assertEqual(body.OWNER_PRINCIPAL, "helene:owner")


if __name__ == "__main__":
    unittest.main(verbosity=2)
