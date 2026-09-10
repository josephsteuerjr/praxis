"""PASS 30 Этап 1: журнал событий core/events — durable append, идемпотентная доставка.

Ключевой инвариант (чинит живой at-most-once гэп старого wake-пути): событие durable
ДО любого потребления; курсор доставки двигается отдельно; краш между append и
доставкой ничего не теряет — событие остаётся undelivered.

Запуск:  python praxis_test.py test_core_events -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import events as core_events


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core_events_"))
        self._orig = (core_events.JOURNAL, core_events.DELIVERED)
        core_events.JOURNAL = self.tmp / "core_events.jsonl"
        core_events.DELIVERED = self.tmp / "core_events_delivered.json"

    def tearDown(self):
        core_events.JOURNAL, core_events.DELIVERED = self._orig


class EmitDeliverTests(Base):
    def test_emit_then_undelivered_then_marked(self):
        e = core_events.emit("subagent_result", "forge", {"x": 1}, dedup_key="k1")
        self.assertIsNotNone(e)
        self.assertEqual(e["schema"], core_events.SCHEMA)
        self.assertTrue(e["ts"])
        pend = core_events.undelivered()
        self.assertEqual([p["dedup_key"] for p in pend], ["k1"])
        core_events.mark_delivered(["k1"])
        self.assertEqual(core_events.undelivered(), [])

    def test_duplicate_dedup_key_delivered_once(self):
        core_events.emit("subagent_result", "forge", {"n": 1}, dedup_key="dup")
        core_events.emit("subagent_result", "forge", {"n": 2}, dedup_key="dup")
        pend = core_events.undelivered()
        self.assertEqual(len(pend), 1, "дубли строк в журнале не дают двойной доставки")

    def test_kinds_filter_and_limit(self):
        core_events.emit("subagent_result", "forge", {}, dedup_key="a")
        core_events.emit("other_kind", "x", {}, dedup_key="b")
        pend = core_events.undelivered(kinds={"subagent_result"})
        self.assertEqual([p["dedup_key"] for p in pend], ["a"])

    def test_event_without_dedup_key_uses_id(self):
        e = core_events.emit("subagent_result", "forge", {})
        pend = core_events.undelivered()
        self.assertEqual(len(pend), 1)
        core_events.mark_delivered([e["id"]])
        self.assertEqual(core_events.undelivered(), [])

    def test_emit_never_raises_on_broken_journal_dir(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("f", encoding="utf-8")
        core_events.JOURNAL = blocker / "sub" / "j.jsonl"  # путь под файлом — mkdir упадёт
        self.assertIsNone(core_events.emit("k", "s", {}, dedup_key="x"))

    def test_payload_clipped_on_write(self):
        e = core_events.emit("k", "s", {"big": "х" * 20000}, dedup_key="clip")
        raw = json.dumps(e["payload"], ensure_ascii=False)
        self.assertLessEqual(len(raw), core_events.PAYLOAD_CHARS + 200)

    def test_known_keys_sees_delivered_and_pending(self):
        core_events.emit("k", "s", {}, dedup_key="k1")
        core_events.emit("k", "s", {}, dedup_key="k2")
        core_events.mark_delivered(["k1"])
        self.assertEqual(core_events.known_keys(), {"k1", "k2"})

    def test_enabled_kill_switch(self):
        with mock.patch.dict(os.environ, {"PRAXIS_FORGE_EVENTS": "0"}):
            self.assertFalse(core_events.enabled())
        with mock.patch.dict(os.environ, {"PRAXIS_FORGE_EVENTS": "off"}):
            self.assertFalse(core_events.enabled())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRAXIS_FORGE_EVENTS", None)
            self.assertTrue(core_events.enabled(), "дефолт — включено")


class CompactTests(Base):
    def test_compact_keeps_undelivered_and_prunes_delivered(self):
        old_keep = core_events.KEEP
        core_events.KEEP = 5
        try:
            core_events.emit("k", "s", {"first": True}, dedup_key="undelivered-old")
            for i in range(4 * core_events.KEEP + 5):
                core_events.emit("k", "s", {"i": i}, dedup_key=f"d{i}")
                core_events.mark_delivered([f"d{i}"])
            core_events.compact()
            rows = [json.loads(l) for l in
                    core_events.JOURNAL.read_text(encoding="utf-8").splitlines()]
            keys = {r["dedup_key"] for r in rows}
            self.assertIn("undelivered-old", keys,
                          "недоставленное старше окна компакта не выбрасывается")
            self.assertLess(len(rows), 4 * core_events.KEEP,
                            "журнал реально сжат")
            pend = core_events.undelivered()
            self.assertEqual([p["dedup_key"] for p in pend], ["undelivered-old"])
        finally:
            core_events.KEEP = old_keep

    def test_compact_noop_under_threshold(self):
        core_events.emit("k", "s", {}, dedup_key="one")
        before = core_events.JOURNAL.read_text(encoding="utf-8")
        core_events.compact()
        self.assertEqual(core_events.JOURNAL.read_text(encoding="utf-8"), before)


class CrashOrderTests(Base):
    def test_crash_between_append_and_delivery_loses_nothing(self):
        """Сценарий старого бага: пометка ДО постановки окна + краш = потеря навсегда.
        Новый порядок: append durable, доставка не случилась -> событие всё ещё ждёт."""
        core_events.emit("subagent_result", "forge", {"goal": "жив"}, dedup_key="crash-k")
        # «краш»: процесс умер до mark_delivered — новый процесс видит событие
        self.assertEqual([p["dedup_key"] for p in core_events.undelivered()], ["crash-k"])

    def test_corrupt_tail_line_does_not_blind_journal(self):
        core_events.emit("k", "s", {}, dedup_key="ok")
        with core_events.JOURNAL.open("ab") as fh:
            fh.write(b'{"torn": tr')  # оборванный хвост (краш посреди записи)
        self.assertEqual([p["dedup_key"] for p in core_events.undelivered()], ["ok"])
        self.assertIsNotNone(core_events.emit("k", "s", {}, dedup_key="after"),
                             "запись после оборванного хвоста работает")

    def test_torn_multibyte_line_does_not_blind_journal_forever(self):
        """Разрыв ПО БАЙТАМ, не по строке — живая цепь 28.08: полный диск, короткая
        os.write ровно по границе 4096, двухбайтовый символ рассечён пополам.

        Прежний читатель падал UnicodeDecodeError В САМОМ текстовом итераторе — мимо
        построчного except — на каждом вызове и навсегда, даже когда место
        освободилось. Наверху это держало флаг приоритета, и она глохла во всех
        чатах до рестарта. Плата за битую строку — одна эта строка."""
        core_events.emit("k", "s", {}, dedup_key="до-обрыва")
        with core_events.JOURNAL.open("ab") as fh:
            fh.write('{"kind": "модерация'.encode("utf-8")[:-1])  # рассечённый символ
        after = core_events.emit("k", "s", {}, dedup_key="после-обрыва")
        self.assertIsNotNone(after, "запись после рваного байта обязана работать")
        keys = [p["dedup_key"] for p in core_events.undelivered()]
        self.assertEqual(keys, ["до-обрыва", "после-обрыва"],
                         "рваный байт ослепил журнал целиком")

    def test_short_write_is_reported_not_swallowed(self):
        """emit, записавший половину строки, не имеет права рапортовать успех.

        28.08: os.write на полном диске вернул часть байтов, emit вернул событие,
        а журнал получил огрызок. Теперь остаток дописывается; если не дописался —
        честный None. Огрызок в журнале читателя не слепит."""
        real_write = os.write
        cuts = []

        def half_then_fail(fd, data):
            if not cuts:
                cuts.append(len(data) // 2)
                return real_write(fd, data[:cuts[0]])
            raise OSError(28, "No space left on device")

        with mock.patch.object(os, "write", side_effect=half_then_fail):
            self.assertIsNone(core_events.emit("k", "s", {"поле": "значение"},
                                               dedup_key="обрыв"),
                              "короткая запись отрапортована успехом")
        self.assertEqual(core_events.undelivered(), [],
                         "огрызок короткой записи прочитался как событие")
        healed = core_events.emit("k", "s", {}, dedup_key="после")
        self.assertIsNotNone(healed)
        self.assertEqual([p["dedup_key"] for p in core_events.undelivered()],
                         ["после"], "журнал после огрызка не читается")

    def test_a_partial_write_that_can_continue_is_completed(self):
        """Дружелюбная короткая запись (сигнал, большой буфер) дописывается до конца."""
        real_write = os.write
        calls = []

        def dribble(fd, data):
            calls.append(len(data))
            return real_write(fd, data[:max(1, len(data) // 2)])

        with mock.patch.object(os, "write", side_effect=dribble):
            self.assertIsNotNone(core_events.emit("k", "s", {}, dedup_key="капли"))
        self.assertGreater(len(calls), 1, "остаток не дописывался")
        self.assertEqual([p["dedup_key"] for p in core_events.undelivered()],
                         ["капли"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
