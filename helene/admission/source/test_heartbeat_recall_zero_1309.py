"""Пульс: PRAXIS_AUTO_RECALL_K=0 значит «выключено», а не «хотя бы один» (13.09).

Кадр голоса трактует ноль как выключатель с 06.08 (`agent._auto_recall_k`), а часовое
окно `heartbeat._automatic_memory_context` делало `max(1, …)` — и при выключенном
авто-recall всё равно шло искать по всему корпусу (py-spy 13.09: `memory_fts._automatic_index`
под GIL внутри `window_context`).

Запуск: python praxis_test.py test_heartbeat_recall_zero_1309 -v
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import heartbeat  # noqa: E402


class AutoRecallZero(unittest.TestCase):
    def test_explicit_zero_does_not_search(self):
        with mock.patch.object(heartbeat.memory_index, "search",
                               side_effect=AssertionError("поиск не должен вызываться")) as search:
            self.assertEqual(heartbeat._automatic_memory_context(cap=0), "")
            self.assertEqual(heartbeat._automatic_memory_context(cap=-3), "")
        self.assertEqual(search.call_count, 0)

    def test_env_zero_does_not_search(self):
        with mock.patch.dict(os.environ, {"PRAXIS_AUTO_RECALL_K": "0"}):
            with mock.patch.object(heartbeat.memory_index, "search",
                                   side_effect=AssertionError("поиск не должен вызываться")) as search:
                self.assertEqual(heartbeat._automatic_memory_context(), "")
        self.assertEqual(search.call_count, 0)

    def test_positive_cap_still_searches_with_that_cap(self):
        with mock.patch.object(heartbeat.memory_index, "search", return_value=[]) as search:
            self.assertEqual(heartbeat._automatic_memory_context(cap=3), "")
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.kwargs.get("k"), 3)
        self.assertEqual(search.call_args.kwargs.get("purpose"), "automatic")


if __name__ == "__main__":
    unittest.main()
