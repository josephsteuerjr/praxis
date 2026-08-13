# -*- coding: utf-8 -*-
"""Простой текст в кадре: её документы едут без markdown, слова целы.

Свойство, а не формулировка: набор слов до и после совпадает побайтово. Именно поэтому
нумерация списков намеренно НЕ снимается — её цифры неотличимы от слов.

Основание: замер 09–10.08 (пять заходов контрфакта). Снятие 292 знаков разметки роняет
долю жирного в её ответе с 70,4% до 42,3% при неизменной длине; шумовое плечо уходит в
другую сторону. Разметка на 99,2% — её собственная, из досье, заметок и RECAP.
Её решение 10.08: включить.
"""
from __future__ import annotations

import os
import re
import unittest
from unittest import mock

import frame_layout


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text or "", re.U)


class PlainBodyTests(unittest.TestCase):
    def test_strips_markup_and_keeps_every_word(self):
        src = "## Заголовок\n\n- **важно** и *важнее*\nобычная строка\n"
        out = frame_layout.plain_body(src)
        self.assertNotIn("**", out)
        self.assertNotIn("## ", out)
        self.assertFalse(out.lstrip().startswith("-"))
        self.assertEqual(words(src), words(out), "слова обязаны совпадать побайтово")

    def test_numbering_is_left_alone_on_purpose(self):
        src = "1. первый\n2. второй\n"
        self.assertEqual(frame_layout.plain_body(src), src)

    def test_plain_text_is_untouched(self):
        src = "просто текст без разметки"
        self.assertEqual(frame_layout.plain_body(src), src)

    def test_lever_off_disables(self):
        with mock.patch.dict(os.environ, {frame_layout.ENV_PLAIN: "off"}):
            self.assertFalse(frame_layout.plain_on())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(frame_layout.ENV_PLAIN, None)
            self.assertTrue(frame_layout.plain_on(), "по умолчанию включено — её слово")


class SectionScopeTests(unittest.TestCase):
    """Режем ТОЛЬКО её документы и только тело тира."""

    BODY = "О нём: **важно** помнить\n- **пункт** списка\n"

    def _section(self, title, **kw):
        return frame_layout.section(title, self.BODY, **kw)[0]

    def test_her_document_tier_loses_markup(self):
        block = self._section("Мои досье на людей")
        self.assertNotIn("**", block)
        self.assertIn("важно", block)
        self.assertIn("пункт", block)

    def test_core_tier_loses_markup(self):
        self.assertNotIn("**", self._section("Визитка"))

    def test_instrument_tier_is_untouched(self):
        """Причина «прибор» — наши показания, markdown в них не её."""
        self.assertIn("**", self._section("Mutable operational continuity"))

    def test_assembled_search_tier_is_untouched(self):
        """Тело поиска собрано нами из паспортов — туда не лезем."""
        self.assertIn("**", self._section("ВНУТРЕННЯЯ память, всплывшая по теме"))

    def test_jsonl_body_is_untouched(self):
        """У jsonl форма и есть смысл."""
        self.assertIn("**", self._section("Мои досье на людей", kind="jsonl"))

    def test_lever_off_restores_previous_frame(self):
        with mock.patch.dict(os.environ, {frame_layout.ENV_PLAIN: "off"}):
            self.assertIn("**", self._section("Мои досье на людей"))

    def test_levers_are_independent(self):
        """Откат ФОРМЫ не смеет молча выключать простой текст.

        Первая редакция стояла внутри ветки `form_new()` — и `PRAXIS_FRAME_FORM=old`
        выключал бы её решение заодно, а цена формы в `test_now_zone` вбирала в себя
        экономию разметки. Тот же класс, что события прогонов на рычаге цельных документов.
        """
        with mock.patch.dict(os.environ, {frame_layout.ENV_LEVER: "old"}):
            block = self._section("Мои досье на людей")
        self.assertNotIn("**", block, "простой текст обязан работать и в старой форме")
        self.assertIn("важно", block)
        with mock.patch.dict(os.environ, {frame_layout.ENV_LEVER: "old",
                                          frame_layout.ENV_PLAIN: "off"}):
            self.assertIn("**", self._section("Мои досье на людей"))

    def test_words_survive_the_whole_section(self):
        block = self._section("Мои досье на людей")
        for w in ("важно", "помнить", "пункт", "списка"):
            self.assertIn(w, block)

    def test_broken_strip_is_refused_rather_than_applied(self):
        """Если правка съела бы слово — тир едет как был. Молча испортить нельзя."""
        with mock.patch.object(frame_layout, "plain_body", lambda t: "съедено"):
            block = self._section("Мои досье на людей")
        self.assertIn("важно", block)
        self.assertNotIn("съедено", block)


if __name__ == "__main__":
    unittest.main()
