# -*- coding: utf-8 -*-
"""Указатель рук: дешёвый кадр И английский текст — вместе, а не вместо друг друга.

ЗАЧЕМ ЭТОТ СТЕНД. Владелец сказал два «да» подряд: «Кадр — да» (указатели вместо сотни
схем) и «Англ — да» (схемы рук по-английски). В ядре они отменяют друг друга: указатель
заменяет 101 схему своими строками, а строки эти РУССКИЕ — то есть включение указателей
выбрасывает английские описания 86 рук из 95. Здесь закреплено, что в издании обе вещи
работают вместе.

Что проверяется:
  · под указателями в `tools` уезжают только родные руки плюс `describe`/`call`;
  · каталог хода при этом остаётся полным — `describe` показывает схему любой руки;
  · текст указателя целиком английский: шапка, имена групп, назначения;
  · перевод не отстаёт молча — у каждой строки записан отпечаток русского оригинала;
  · рычаг `PRAXIS_TOOLS_POINTERS=off` возвращает прежний полный манифест;
  · рычаг `PRAXIS_TOOLS_EN=off` возвращает её собственный русский текст.

Запуск: python praxis_test.py test_pointer_en_1809 -v
"""
from __future__ import annotations

import os
import re
import unittest
from unittest import mock

import agent
import tool_text_en

CYR = re.compile(r"[а-яё]", re.I)
ON = {"PRAXIS_TOOLS_EN": "on", "PRAXIS_TOOLS_POINTERS": "on"}
OFF_EN = {"PRAXIS_TOOLS_EN": "off", "PRAXIS_TOOLS_POINTERS": "on"}
OFF_POINTERS = {"PRAXIS_TOOLS_EN": "on", "PRAXIS_TOOLS_POINTERS": "off"}


def ctx():
    return agent.ChannelContext(is_dm=True, owner=True, known=True)


def pointer(env) -> str:
    with mock.patch.dict(os.environ, env):
        return agent.hands_pointer_text(agent.catalog_tools_for(ctx()))


class WhatRidesInTools(unittest.TestCase):
    def test_pointers_shrink_the_offered_set_but_not_the_catalog(self):
        with mock.patch.dict(os.environ, ON):
            offered = [str(t.get("name") or "") for t in agent.offered_tools_for(ctx())]
            catalog = [str(t.get("name") or "") for t in agent.catalog_tools_for(ctx())]
        self.assertLess(len(offered), len(catalog),
                        "указатели не уменьшили набор — кадр не подешевел")
        self.assertIn("describe", offered)
        self.assertIn("call", offered)
        for name in offered:
            if name in ("describe", "call", ""):
                continue
            self.assertIn(name, agent.NATIVE_HAND_NAMES,
                          f"{name} уехал в tools, хотя не родной")

    def test_the_lever_gives_the_old_manifest_back(self):
        with mock.patch.dict(os.environ, OFF_POINTERS):
            offered = [str(t.get("name") or "") for t in agent.offered_tools_for(ctx())]
            catalog = [str(t.get("name") or "") for t in agent.catalog_tools_for(ctx())]
        self.assertEqual(sorted(offered), sorted(catalog),
                         "рычаг снят, а набор всё равно урезан")

    def test_the_closing_hand_stays_last(self):
        """`end_turn` ниже рабочих рук — решение 18.08, указатели его не двигают."""
        with mock.patch.dict(os.environ, ON):
            names = [str(t.get("name") or "") for t in agent.offered_tools_for(ctx())]
        if "end_turn" in names:
            self.assertEqual(names[-1], "end_turn")


class ThePointerSpeaksEnglish(unittest.TestCase):
    def test_no_russian_left_in_the_pointer(self):
        text = pointer(ON)
        russian = [line for line in text.split("\n") if CYR.search(line)]
        self.assertEqual(russian, [], "в указателе остался русский текст")

    def test_the_head_and_the_groups_are_translated(self):
        text = pointer(ON)
        self.assertIn("pointer", text.lower())
        self.assertIn("Name(arguments)", text)
        self.assertIn("memory and self", text, "имена групп остались русскими")

    def test_hand_purposes_are_translated(self):
        # Берём руку, которой ТОЧНО нет среди родных: родные едут со схемой, и их
        # назначения в указателе не печатаются вовсе.
        text = pointer(ON)
        self.assertIn("an honest snapshot", text)          # my_capabilities
        self.assertIn("my proposals and their fate", text)  # list_proposals

    def test_the_english_lever_gives_her_own_words_back(self):
        text = pointer(OFF_EN)
        self.assertIn("Мои руки — указатель", text,
                      "PRAXIS_TOOLS_EN=off обязан возвращать её собственный текст")

    def test_describe_and_call_are_english_too(self):
        """Две руки указателя едут в КАЖДОМ кадре: по-русски они рвут набор пополам."""
        with mock.patch.dict(os.environ, ON):
            offered = {str(t.get("name") or ""): t for t in agent.offered_tools_for(ctx())}
        for name in ("describe", "call"):
            self.assertFalse(CYR.search(str(offered[name].get("description") or "")),
                             f"{name} остался с русским описанием")


class TranslationDoesNotGoStaleSilently(unittest.TestCase):
    def test_every_purpose_has_a_translation_and_a_stamp(self):
        report = tool_text_en.pointer_coverage(
            agent.HAND_PURPOSE, agent.HAND_GROUPS, {})
        self.assertEqual(report["untranslated"], [],
                         "назначения без перевода: указатель покажет их по-русски")
        self.assertEqual(report["groups_untranslated"], [])
        self.assertEqual(report["russian_left"], [])

    def test_a_changed_russian_line_turns_the_instrument_red(self):
        """Правят её строку — прибор краснеет ИМЕНЕМ, а не общим «что-то не так»."""
        moved = dict(agent.HAND_PURPOSE)
        name = next(iter(moved))
        moved[name] = moved[name] + " (её правка)"
        report = tool_text_en.pointer_coverage(moved, agent.HAND_GROUPS, {})
        self.assertIn(name, report["stale"])


if __name__ == "__main__":
    unittest.main()
