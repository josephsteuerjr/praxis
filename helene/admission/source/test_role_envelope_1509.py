# -*- coding: utf-8 -*-
"""Её собственная реплика в роли `assistant` — тело без машинного конверта (15.09).

ЖИВОЙ СЛУЧАЙ. 15.09 голос переключили на glm-5.3, и за сутки четырнадцать ходов в
AbstractDL кончились `delivery_skipped: turn ended without a reply hand`: модель писала
готовый, верный по смыслу ответ обычным текстом и не звала `reply` вовсе. Наружу не
уходило ничего; в мини-приложении это выглядело как «решила промолчать».

КОРЕНЬ — НЕ ЯЗЫК ПРАВИЛА И НЕ «СЛАБАЯ МОДЕЛЬ», А ФОРМА КАДРА. Её прошлые реплики ехали
ролью `assistant` с машинным конвертом без имени:

    [root; message #105385; 2026-09-15T15:32:01Z; reply_to=#105380] Разобралась, что …

В одном кадре таких строк было ПЯТЬ подряд — то есть кадр пять раз показывал, как
выглядит ответ ассистента. Модель образец воспроизводила, вместе с выдуманным номером
следующего сообщения (`[root; message #105408; …] Пробую`), и рук не звала.

ПРОБА НА ТОМ ЖЕ КАДРЕ (сорванный ход 16:09, ступень low, по 6 заходов):

    с конвертом   — рука звалась 4 раза из 6 (оба срыва дословно повторили шапку);
    без конверта  — 6 из 6.

Конверт в роли отвечал на вопросы, на которые роль уже ответила: кто говорит — сказано
ролью, когда — порядком, кому — строкой выше. В АРХИВНОМ виде (`line`, рука
`group_context`) он остаётся байт-в-байт: там роли нет, и там он вправду адрес.

Запуск: python praxis_test.py test_role_envelope_1509 -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import group_context  # noqa: E402


ROW = {
    "topic_id": None,
    "topic_title": None,
    "message_id": 553,
    "timestamp": "2026-09-15T15:32:01Z",
    "sender_id": 777,
    "sender_name": "Praxis",
    "reply_to_message_id": 551,
    "text": "мой ответ",
    "kind": "message",
    "outgoing": True,
}


class OneLine(unittest.TestCase):
    """`_format_message`: что именно уезжает ролью, а что остаётся архивом."""

    def test_her_role_line_is_the_body_and_only_the_body(self):
        line = group_context._format_message(dict(ROW), as_self=True)
        self.assertEqual(line, "мой ответ")

    def test_her_role_line_opens_with_her_word_not_with_a_bracket(self):
        """Образец для подделки — это именно открывающая скобка в начале строки."""
        line = group_context._format_message(dict(ROW), as_self=True)
        self.assertFalse(line.startswith("["), f"конверт на месте: {line[:60]!r}")
        for piece in ("message #553", "reply_to=#551", "2026-09-15T15:32:01Z", "root"):
            self.assertNotIn(piece, line, f"в роль уехал кусок конверта: {piece}")

    def test_the_archive_line_is_untouched(self):
        line = group_context._format_message(dict(ROW))
        self.assertTrue(line.startswith("[root; message #553; 2026-09-15T15:32:01Z;"))
        self.assertIn("Praxis [id 777]", line)
        self.assertIn("reply_to=#551", line)
        self.assertTrue(line.endswith("] мой ответ"))

    def test_the_edit_mark_stays_in_the_archive_line(self):
        row = dict(ROW, edited_at="2026-09-15T15:33:00Z")
        self.assertIn("edited=2026-09-15T15:33:00Z", group_context._format_message(row))
        self.assertEqual(group_context._format_message(row, as_self=True), "мой ответ")

    def test_truncation_is_still_announced_in_her_own_line(self):
        """Обрез без пометки — молчаливая ложь о полноте, и в роли тоже."""
        row = dict(ROW, text="я" * 5000)
        line = group_context._format_message(row, max_text=200, as_self=True)
        self.assertIn("…[ОБРЕЗАНО: показано 200 из 5000 символов", line)
        self.assertFalse(line.startswith("["))

    def test_media_prefix_survives_in_her_own_line(self):
        row = dict(ROW, media="[фото]")
        self.assertEqual(group_context._format_message(row, as_self=True),
                         "[фото] мой ответ")

    def test_a_deletion_keeps_its_service_head_in_both_renders(self):
        """Удаление — не её речь, а служебный факт: конверт там несёт сам факт."""
        row = dict(ROW, kind="deletion", deleted_at="2026-09-15T15:40:00Z")
        for line in (group_context._format_message(row),
                     group_context._format_message(row, as_self=True)):
            self.assertIn("deleted=2026-09-15T15:40:00Z", line)
            self.assertIn("[message deleted in Telegram]", line)


class Rows(unittest.TestCase):
    """Обе ленты записями: `context_rows` и `epoch_rows` — одно и то же решение."""

    PEER = "-1001"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        for name, value in (("BASE", base), ("MEM_DIR", base / "memory"),
                            ("GROUPS_DIR", base / "memory" / "groups"),
                            ("STATE_DIR", base / "memory" / ".state" / "group_context"),
                            ("_KEY_CACHE", {})):
            patcher = mock.patch.object(group_context, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.add(1, "вопрос")
        self.add(2, "мой ответ", outgoing=True, sender=777, name="Praxis", reply=1)

    def add(self, mid: int, text: str, *, sender=10, name="Alice", outgoing=False,
            reply=None):
        return group_context.observe_message(
            peer_id=self.PEER, topic_id=None, message_id=mid, sender_id=sender,
            sender_name=name, reply_to_message_id=reply,
            timestamp=f"2026-09-15T00:{mid:02d}:00Z", text=text, outgoing=outgoing)

    def _own(self, rows):
        own = [row for row in rows if row.get("self")]
        self.assertEqual(len(own), 1, "её реплика в ленте не одна")
        return own[0]

    def test_context_rows_send_her_body_and_keep_the_archive_line(self):
        rows = group_context.context_rows(self.PEER, topic_id=None, limit=50)
        own = self._own(rows)
        self.assertEqual(own["role_line"], "мой ответ")
        self.assertIn("Praxis [id 777]", own["line"])
        self.assertIn("message #2", own["line"])

    # ⚠ ПОРТ: `epoch_rows` — тема эпохи кадра (`frame_epoch`), которой в издании пока нет.
    # Тест ядра `test_epoch_rows_make_the_same_decision` снят вместе со вторым источником
    # строк ниже: судить отсутствующую функцию нечем, а зелёный тест над ней был бы ложью.
    def test_foreign_rows_are_identical_in_both_renders(self):
        for rows in (group_context.context_rows(self.PEER, topic_id=None, limit=50),):
            for row in rows:
                if row.get("self") or row.get("service"):
                    continue
                self.assertEqual(row["line"], row["role_line"])

    def test_the_flat_transcript_still_signs_every_line(self):
        """Плоская лента — архив, и подпись там обязана остаться."""
        text = group_context.context(self.PEER, topic_id=None, limit=50)
        self.assertIn("Praxis [id 777]", text)
        self.assertIn("Alice [id 10]", text)

    def test_no_role_line_of_hers_can_be_mistaken_for_a_header(self):
        """Итог всей правки одним числом: в роли нет ни одной строки-образца."""
        rows = group_context.context_rows(self.PEER, topic_id=None, limit=50)
        opening = [row["role_line"] for row in rows
                   if row.get("self") and "; message #" in str(row["role_line"])]
        self.assertEqual(opening, [], "кадр снова показывает, как подделать шапку")


if __name__ == "__main__":
    unittest.main()
