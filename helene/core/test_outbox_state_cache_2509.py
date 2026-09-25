"""Перечисления outbox не перечитывают нетронутые журналы (25.09).

py-spy на проде: тик outbox каждые 15 с читал и разбирал все 3934 журнала записей (18 МБ) —
60–90 % ЦП раннера уходило на это. Журнал append-only: неизменная подпись файла
(mtime_ns, size, ino) — неизменное состояние.

Запуск:  python praxis_test.py test_outbox_state_cache_2509 -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from telegram_outbox import TelegramOutbox


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


class StateCache(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.box = TelegramOutbox(Path(self.temp.name) / "outbox", clock=self.clock)
        for i in range(5):
            self.box.prepare_text(f"key-{i}", peer_id=-1009990000003, text=f"текст {i}",
                                  run_id="run-01", call_id=f"call-{i}", purpose="tool:reply")

    def _reads(self):
        calls = []
        real = self.box._load_state_locked

        def spy(path):
            calls.append(path.name)
            return real(path)
        return mock.patch.object(self.box, "_load_state_locked", side_effect=spy), calls

    def test_second_enumeration_reads_no_files(self):
        first = self.box.pending()
        self.assertEqual(len(first), 5)
        patcher, calls = self._reads()
        with patcher:
            second = self.box.pending()
            accepted = self.box.accepted()
            dead = self.box.dead_letters()
        self.assertEqual(calls, [], "нетронутые журналы не читаются повторно")
        self.assertEqual([r["key"] for r in second], [r["key"] for r in first])
        self.assertEqual(accepted, ())
        self.assertEqual(dead, ())

    def test_a_changed_journal_is_reread_and_reclassified(self):
        self.box.pending()
        self.box.mark_accepted("key-2", message_id=77)
        patcher, calls = self._reads()
        with patcher:
            pending = self.box.pending()
            accepted = self.box.accepted()
        self.assertEqual(sorted(r["key"] for r in pending), ["key-0", "key-1", "key-3", "key-4"])
        self.assertEqual([r["key"] for r in accepted], ["key-2"])
        self.assertEqual(len(set(calls)), 1, "перечитан только изменённый журнал")

    def test_cache_hands_out_copies(self):
        rows = self.box.pending()
        rows[0]["state"] = "hacked"
        self.assertEqual(self.box.pending()[0]["state"], "pending")

    def test_fresh_instance_sees_the_same_truth(self):
        self.box.pending()
        other = TelegramOutbox(Path(self.temp.name) / "outbox", clock=self.clock)
        self.assertEqual(len(other.pending()), 5)


if __name__ == "__main__":
    unittest.main()
