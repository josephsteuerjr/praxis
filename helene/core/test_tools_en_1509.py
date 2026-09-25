# -*- coding: utf-8 -*-
"""Схемы рук уезжают модели по-английски — накладкой, не правкой её строк (15.09).

Слово Егора 15.09: «надо по-английски (официально глм русский не поддерживает) — и так,
чтобы у неё не обрывались ходы… чтобы понимала». Замер того же дня по живому набору из 101
руки: 55 описаний с кириллицей, 253 параметра из 390 без описания ВООБЩЕ, целиком готовых 7.
Живой промах того же часа: `search_chats` получила `query: string` без единого слова о том,
что в неё класть.

Здесь закрепляется: под рычагом ни одного русского описания и ни одного неописанного
параметра; имена, типы, enum, required и порядок НЕ меняются ни на байт; рычаг снят —
её текст возвращается дословно; перевод, отставший от её правки, называется по имени.

Запуск: python praxis_test.py test_tools_en_1509 -v
"""
from __future__ import annotations

import os
import re
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import tool_text_en  # noqa: E402

CYR = re.compile(r"[а-яё]", re.I)
ON = {"PRAXIS_TOOLS_EN": "on"}
OFF = {"PRAXIS_TOOLS_EN": "off"}


def room_ctx(owner: bool = True) -> agent.ChannelContext:
    return agent.ChannelContext(chat_id="-1001240718803", room_id="-1001240718803",
                                is_dm=False, owner=owner, known=True)


# ⚠ 18.09: сверяем КАТАЛОГ хода, а не то, что уезжает в `tools`. Под указателями в
# `tools` едут тридцать родных рук плюс `describe`/`call`, но `describe` показывает
# схему ЛЮБОЙ руки каталога — значит по-английски обязаны быть все, а не предложенные.
def raw_tools(ctx) -> list:
    with mock.patch.dict(os.environ, OFF):
        return agent.catalog_tools_for(ctx)


def en_tools(ctx) -> list:
    with mock.patch.dict(os.environ, ON):
        return agent.catalog_tools_for(ctx)


def prose(text: str) -> str:
    out = str(text or "")
    for literal in tool_text_en.ALLOWED_LITERALS:
        out = out.replace(literal, " ")
    return out


class TheModelGetsEnglish(unittest.TestCase):
    """Ни одного русского слова в прозе схем и ни одного немого параметра."""

    def setUp(self) -> None:
        self.ctx = room_ctx()

    def test_no_cyrillic_prose_in_any_description(self):
        bad = [t["name"] for t in en_tools(self.ctx)
               if CYR.search(prose(t.get("description")))]
        self.assertEqual(bad, [], f"описания остались русскими: {bad}")

    def test_every_parameter_is_described_in_english(self):
        bad = []
        for t in en_tools(self.ctx):
            props = ((t.get("input_schema") or {}).get("properties") or {})
            for name, spec in props.items():
                text = str((spec or {}).get("description") or "")
                if not text.strip() or CYR.search(prose(text)):
                    bad.append(f"{t['name']}.{name}")
        self.assertEqual(bad, [], f"параметры без английского описания: {bad}")

    def test_coverage_instrument_agrees_with_the_frame(self):
        cov = tool_text_en.coverage(raw_tools(self.ctx))
        self.assertEqual(cov["ru_descriptions"], [])
        self.assertEqual(cov["undescribed_params"], [])
        self.assertGreater(cov["tools"], 50)

    def test_allowed_literals_are_a_closed_named_list(self):
        # Кириллица остаётся только там, где это ЛИТЕРАЛ её кадра, и каждый назван.
        self.assertTrue(all(CYR.search(x) for x in tool_text_en.ALLOWED_LITERALS))
        # Сверка идёт по ТАБЛИЦЕ переводов, а не по одному ходу: часть рук в комнате
        # не предлагается вовсе, и контекстный список сделал бы проверку зависимой
        # от аудитории — красный цвет от состава кадра, а не от пропавшего литерала.
        text = "\n".join(str(v.get("d") or "") for v in tool_text_en.EN.values())
        for literal in tool_text_en.ALLOWED_LITERALS:
            self.assertIn(literal, text, f"литерал {literal!r} больше не используется — снять из списка")


class NothingButTextChanges(unittest.TestCase):
    """Накладка обязана быть невидимой для поведения."""

    def setUp(self) -> None:
        self.ctx = room_ctx()
        self.raw = raw_tools(self.ctx)
        self.en = en_tools(self.ctx)

    def test_names_order_types_enums_and_required_are_identical(self):
        self.assertEqual([t.get("name") for t in self.raw],
                         [t.get("name") for t in self.en])
        for a, b in zip(self.raw, self.en):
            sa, sb = a.get("input_schema") or {}, b.get("input_schema") or {}
            self.assertEqual(sa.get("required"), sb.get("required"), a.get("name"))
            pa, pb = sa.get("properties") or {}, sb.get("properties") or {}
            self.assertEqual(list(pa), list(pb), a.get("name"))
            for key in pa:
                for field in ("type", "enum", "items", "anyOf"):
                    self.assertEqual((pa[key] or {}).get(field), (pb[key] or {}).get(field),
                                     f"{a.get('name')}.{key}.{field}")

    def test_lever_off_returns_her_own_text_byte_for_byte(self):
        self.assertTrue(any(CYR.search(str(t.get("description") or "")) for t in self.raw),
                        "при снятом рычаге её русские описания должны остаться")
        again = raw_tools(self.ctx)
        self.assertEqual([t.get("description") for t in self.raw],
                         [t.get("description") for t in again])

    def test_apply_does_not_mutate_the_input(self):
        before = [dict(t) for t in self.raw]
        with mock.patch.dict(os.environ, ON):
            tool_text_en.apply(self.raw)
        self.assertEqual([t.get("description") for t in self.raw],
                         [t.get("description") for t in before])

    def test_unknown_tool_passes_through_untouched(self):
        mine = {"name": "не_моя_рука", "description": "чужое описание",
                "input_schema": {"type": "object", "properties": {}}}
        with mock.patch.dict(os.environ, ON):
            self.assertEqual(tool_text_en.apply([mine]), [mine])


class TranslationsDoNotGoStaleSilently(unittest.TestCase):
    """Она правит своё описание — перевод обязан назваться устаревшим."""

    def test_recorded_base_hashes_still_match_her_text(self):
        cov = tool_text_en.coverage(raw_tools(room_ctx()))
        self.assertEqual(cov["stale_translations"], [],
                         "её описание изменилось — догнать перевод в tool_text_en.EN")

    def test_a_changed_original_is_reported_by_name(self):
        moved = [{"name": "reply", "description": "она переписала это описание",
                  "input_schema": {"type": "object", "properties": {}}}]
        self.assertIn("reply", tool_text_en.coverage(moved)["stale_translations"])

    def test_every_translated_hand_has_a_recorded_hash(self):
        translated = {n for n, v in tool_text_en.EN.items() if v.get("d")}
        missing = sorted(translated - set(tool_text_en.BASE_SHA))
        self.assertEqual(missing, [], f"перевод без отпечатка оригинала: {missing}")


if __name__ == "__main__":
    unittest.main()
