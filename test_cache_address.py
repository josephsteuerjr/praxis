# -*- coding: utf-8 -*-
"""Ключ кэша — АДРЕС разговора, а не хэш его содержимого.

Замер 10.08 на 2 823 записанных кадрах: общий префикс двух соседних системных промптов —
12 886 знаков вперемешку и 13 478…15 767 внутри одной аудитории. Реле берёт
`prompt_cache_key` из запроса и лишь при его отсутствии хэширует весь `messages[0]`,
из-за чего 79 из 84 промптов за сутки получали уникальный ключ.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import llm


OWNER = "…contract…\n\nYou're in the private owner channel with Yegor. You…"
ROOM = "…contract…\n\nThe human owner is the actor in this public room; room_id=-1001240718803; members=42"
RUN = "…contract…\n\nThis is your own run: there is no room and no interlocutor."
GUEST = "…contract…\n\nAuthority fact: this interlocutor is not in the known circle."


class AddressIsStableAndSpecific(unittest.TestCase):
    def test_same_audience_same_address_even_if_body_moved(self):
        a = llm.cache_address("gpt-5.6-sol", OWNER + "\nuptime_minutes: 11")
        b = llm.cache_address("gpt-5.6-sol", OWNER + "\nuptime_minutes: 12")
        self.assertTrue(a)
        self.assertEqual(a, b, "подвижный хвост не имеет права менять адрес")

    def test_different_rooms_get_different_addresses(self):
        a = llm.cache_address("gpt-5.6-sol", ROOM)
        b = llm.cache_address("gpt-5.6-sol", ROOM.replace("-1001240718803", "-1004301095307"))
        self.assertNotEqual(a, b)

    def test_audiences_do_not_collide(self):
        keys = {llm.cache_address("m", t) for t in (OWNER, ROOM, RUN, GUEST)}
        self.assertEqual(len(keys), 4, "четыре разных адреса, а не один")

    def test_model_is_part_of_the_address(self):
        self.assertNotEqual(llm.cache_address("sol", OWNER), llm.cache_address("luna", OWNER))

    def test_no_address_means_no_key_at_all(self):
        self.assertEqual(llm.cache_address("m", "просто текст без адреса"), "")
        self.assertEqual(llm.cache_address("m", ""), "")

    def test_lever_off(self):
        with mock.patch.dict(os.environ, {"PRAXIS_CACHE_KEY": "off"}):
            self.assertEqual(llm.cache_address("m", OWNER), "")

    def test_address_carries_no_content(self):
        """Адрес не имеет права нести её текст: в ключ уезжает только модель, роль и id."""
        addr = llm.cache_address("gpt-5.6-sol", OWNER + "\nсекретная строка про Егора")
        self.assertNotIn("секрет", addr)
        self.assertNotIn("Yegor", addr)
        self.assertTrue(addr.startswith("praxis:gpt-5.6-sol:"))


class ItReachesTheRequest(unittest.TestCase):
    class _Cli:
        def __init__(self):
            self.seen = {}
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            self.seen = kw
            raise RuntimeError("стоп: полезен только собранный запрос")

    def _payload(self, system):
        cli = self._Cli()
        try:
            llm._call_openai(cli, "gpt-5.6-sol", system=system, messages=[], tools=[],
                             max_tokens=16, thinking=0)
        except RuntimeError:
            pass
        return cli.seen

    def test_key_lands_in_extra_body(self):
        kw = self._payload(OWNER)
        self.assertEqual(kw.get("extra_body", {}).get("prompt_cache_key"),
                         llm.cache_address("gpt-5.6-sol", OWNER))

    def test_no_address_no_field(self):
        kw = self._payload("текст без адреса")
        self.assertNotIn("prompt_cache_key", kw.get("extra_body", {}))

    def test_effort_and_key_coexist(self):
        """Прежняя редакция клала extra_body целиком и затирала бы соседа."""
        cli = self._Cli()
        try:
            llm._call_openai(cli, "gpt-5.6-sol", system=OWNER, messages=[], tools=[],
                             max_tokens=16, thinking=4096)
        except RuntimeError:
            pass
        extra = cli.seen.get("extra_body", {})
        self.assertIn("prompt_cache_key", extra)
        self.assertIn("reasoning_effort", extra)


if __name__ == "__main__":
    unittest.main()
