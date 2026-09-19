# -*- coding: utf-8 -*-
"""Лента показывает её прошлые ходы ВЫЗОВАМИ руки `reply`, а не прозой (16.09).

ЖИВОЙ СЛУЧАЙ. За 16.09 пятнадцать ходов из тридцати шести кончились
`turn ended without a reply hand`: `stop_reason: end_turn`, 800–2200 знаков готовой прозы,
ни одного `tool_use`. Наружу не уходило ничего.

КОРЕНЬ — СНОВА ФОРМА КАДРА, НЕ СЛОВА ПРАВИЛА. 15.09 правкой `ad89ee80` с её реплик сняли
машинный конверт (`test_role_envelope_1509`), но САМА ПРОЗА осталась: в ленте её прошлые
ходы лежали обычным текстом в роли `assistant` — на 122 815 знаков ни одного `tool_use`.
Это единственная демонстрация ассистентского хода во всём кадре, и модель ей следовала:
средняя длина её реплик в ленте 1115 знаков, средняя длина недоставленной прозы — 1165,
расхождение 4 %. Контракт речи при этом сам ссылается на ленту: «That is the shape of
speech here: the words, and `reply` to carry them» — и кадр подтверждал первую половину
фразы девять раз, а вторую ноль раз.

ЗАМЕР (`desk-notes/РУКИ-16.09-ФАНАУТ.md`; режим прозы = `end_turn` при нуле рук,
`max_tokens` 32768 во всех плечах, пять кадров, две панели):

    база, ступень low            34/48 = 71 %
    плацебо (правка «ни о чём»)  17/30 = 57 %   p=0,23 — ШУМ
    лента вызовами руки          0/42  =  0 %   p=3e-13

Контроль на её праве молчать: три кадра, где она осознанно промолчала, — `reply` 0/12 и
без правки, и с ней. Правка не заставляет её говорить.

ЧЕСТНОСТЬ РАСПИСКИ проверена до правки: 13 недоставленных прозаических ходов 16.09
сверены с лентами 42 последующих кадров — недоставленного в ленте нет ничего, значит
`delivered` не врёт.

Запуск: python praxis_test.py test_tape_hands_1609 -v
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import frame_layout  # noqa: E402


HER = "Разобралась. Сначала кусочки, потом предсказания."
HIS = "[root; message #105612] а глм у вас есть?"
ROWS = [
    {"role": "user", "content": HIS},
    {"role": "assistant", "content": HER},
    {"role": "user", "content": "и что дальше"},
]


def _on():
    return mock.patch.dict(os.environ, {frame_layout.ENV_TAPE_HANDS: "on"})


def _off():
    return mock.patch.dict(os.environ, {frame_layout.ENV_TAPE_HANDS: "off"})


class LeverDefault(unittest.TestCase):
    """Без рычага лента обязана быть прежней байт-в-байт."""

    def test_default_is_off(self):
        self.assertNotIn(frame_layout.ENV_TAPE_HANDS, os.environ)
        self.assertFalse(frame_layout.tape_hands())

    def test_tape_unchanged_without_lever(self):
        with _off():
            got = frame_layout.tape(ROWS)
        self.assertEqual(len(got), len(ROWS))
        self.assertEqual([m["role"] for m in got], ["user", "assistant", "user"])
        self.assertEqual(got[1]["content"], HER)

    def test_lever_reads_environment(self):
        with _on():
            self.assertTrue(frame_layout.tape_hands())
        self.assertNotIn(frame_layout.ENV_TAPE_HANDS, os.environ)


class HandPair(unittest.TestCase):
    """Её реплика едет вызовом руки и распиской о доставке."""

    def setUp(self):
        with _on():
            self.got = frame_layout.tape(ROWS)

    def test_one_reply_becomes_two_messages(self):
        self.assertEqual([m["role"] for m in self.got],
                         ["user", "assistant", "user", "user"])

    def test_call_is_the_reply_hand_with_her_words_verbatim(self):
        call = self.got[1]["content"][0]
        self.assertEqual(call["type"], "tool_use")
        self.assertEqual(call["name"], "reply")
        self.assertEqual(call["input"], {"text": HER})

    def test_receipt_points_at_that_call(self):
        call = self.got[1]["content"][0]
        receipt = self.got[2]["content"][0]
        self.assertEqual(receipt["type"], "tool_result")
        self.assertEqual(receipt["tool_use_id"], call["id"])
        self.assertEqual(receipt["content"], frame_layout.TAPE_RECEIPT)

    def test_every_result_answers_the_call_right_before_it(self):
        """Требование протокола: осиротевший tool_result роняет запрос целиком."""
        seen = None
        for message in self.got:
            content = message.get("content")
            if not isinstance(content, list):
                seen = None
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    seen = block["id"]
                elif block.get("type") == "tool_result":
                    self.assertEqual(block["tool_use_id"], seen)
                    seen = None

    def test_her_words_carry_no_gutter(self):
        """Гуттер на её собственных словах был бы подменой авторства."""
        self.assertNotIn(">", self.got[1]["content"][0]["input"]["text"])

    def test_foreign_text_still_quoted(self):
        self.assertTrue(self.got[0]["content"].lstrip().startswith(">"))


class CallIdIsContentAddressed(unittest.TestCase):
    """id считается ОТ ТЕКСТА: лента едет окном, позиционный id убил бы префиксный кэш."""

    def test_same_words_keep_the_same_id_in_a_shifted_window(self):
        shifted = [{"role": "user", "content": "новое сверху"}] + ROWS
        with _on():
            first = frame_layout.tape(ROWS)
            second = frame_layout.tape(shifted)
        a = [b for m in first for b in (m["content"] if isinstance(m["content"], list) else [])
             if b.get("type") == "tool_use"][0]
        b = [x for m in second for x in (m["content"] if isinstance(m["content"], list) else [])
             if x.get("type") == "tool_use"][0]
        self.assertEqual(a["id"], b["id"])

    def test_different_words_get_different_ids(self):
        other = [{"role": "assistant", "content": "совсем другое"}]
        with _on():
            a = frame_layout.tape([{"role": "assistant", "content": HER}])[0]
            b = frame_layout.tape(other)[0]
        self.assertNotEqual(a["content"][0]["id"], b["content"][0]["id"])


class WhatWeRefuseToInvent(unittest.TestCase):
    """Реплику, которую нельзя честно положить в руку, не трогаем."""

    def test_empty_reply_passes_through(self):
        rows = [{"role": "assistant", "content": "   "}]
        with _on():
            self.assertEqual(frame_layout.tape(rows), rows)

    def test_reply_with_a_picture_passes_through(self):
        rows = [{"role": "assistant", "content": [
            {"type": "text", "text": "вот"},
            {"type": "image", "source": {"type": "base64", "data": "..."}}]}]
        with _on():
            got = frame_layout.tape(rows)
        self.assertEqual(got, rows)

    def test_text_blocks_are_joined_into_one_call(self):
        rows = [{"role": "assistant", "content": [
            {"type": "text", "text": "первая"}, {"type": "text", "text": "вторая"}]}]
        with _on():
            got = frame_layout.tape(rows)
        self.assertEqual(got[0]["content"][0]["input"]["text"], "первая\nвторая")

    def test_non_dict_rows_survive(self):
        with _on():
            self.assertEqual(frame_layout.tape(["мусор"]), ["мусор"])


class InstrumentDoesNotCallOurScaffolding_A_Guest(unittest.TestCase):
    """`assay_tape` судит ДОСТАВЛЕННЫЙ чужой текст. Расписка ленты — наша разметка."""

    def test_receipt_is_not_counted_as_a_guest(self):
        with _off():
            plain = frame_layout.assay_tape(frame_layout.tape(ROWS))
        with _on():
            handed = frame_layout.assay_tape(frame_layout.tape(ROWS))
        self.assertEqual(handed["guest_messages"], plain["guest_messages"])

    def test_receipt_never_reads_as_a_leak(self):
        with _on():
            seen = frame_layout.assay_tape(frame_layout.tape(ROWS))
        self.assertEqual(seen["tape_leaked_sample"], "")


if __name__ == "__main__":
    unittest.main()
