"""Реестр переноса — durable-дом ЕЁ слов о границе переноса (19.08, её №2).

Три обязательства: словарь — её (чужое слово не записывается); авторство и дата
пишутся всегда (код не выдаёт свой отбор за её — её №9 о том же классе); снятие —
тоже её решение и возвращает помеченное умолчание, а не тишину.
"""
from __future__ import annotations

import unittest
from unittest import mock

import agent
import rooms


class TheRegistryHoldsHerWord(unittest.TestCase):

    def test_the_dictionary_is_hers_and_foreign_words_are_refused(self):
        ok, note = rooms.set_own_transfer("-300100", "по месту")
        self.assertFalse(ok, "класс вне её словаря записался")
        self.assertIn("только источник", note, "отказ обязан назвать словарь целиком")

    def test_her_word_is_written_with_authorship_and_date(self):
        ok, note = rooms.set_own_transfer("-300101", "учитывать",
                                          presence_hidden="yes", now=1_787_000_000.0)
        self.assertTrue(ok, note)
        state = rooms.transfer_of("-300101")
        self.assertEqual(state["transfer"], "учитывать")
        self.assertEqual(state["set_by"], "praxis")
        self.assertTrue(state["at"].startswith("2026-08-"),
                        f"дата её слова не записана: {state}")
        self.assertTrue(state["presence_hidden"])

    def test_clearing_is_a_dated_decision_not_silence(self):
        rooms.set_own_transfer("-300102", "закрыто", now=1_787_000_000.0)
        ok, note = rooms.set_own_transfer("-300102", "", now=1_787_000_600.0)
        self.assertTrue(ok, note)
        self.assertIn("умолчание", note, "снятие не сказало, что вернулось умолчание")
        state = rooms.transfer_of("-300102")
        self.assertEqual(state["transfer"], "", "слово не снялось")
        self.assertTrue(state["at"], "снятие потеряло дату — решение без времени")

    def test_the_profile_body_survives_her_transfer_word(self):
        """Реестр живёт в шапке профиля; её текст (body) — неприкосновенен."""
        rooms.ROOMS_DIR.mkdir(parents=True, exist_ok=True)
        rooms.profile_path("-300103").write_text(
            "# Комната\nmode: normal\n\nеё собственные заметки о месте\n",
            encoding="utf-8")
        rooms.set_own_transfer("-300103", "свободно")
        raw = rooms.profile_path("-300103").read_text(encoding="utf-8")
        self.assertIn("её собственные заметки о месте", raw)
        self.assertIn("transfer: свободно", raw)


class TheHandSpeaksTheRegistry(unittest.TestCase):

    def _call(self, **kwargs):
        with mock.patch.object(agent, "_is_sovereign_actor", return_value=True):
            return agent.tool_manage_room(**kwargs)

    def test_query_names_the_default_honestly(self):
        out = self._call(action="transfer", chat_id="-300110")
        self.assertIn("умолчание", out)
        self.assertIn("учитывать", out, "словарь не показан")

    def test_set_and_query_round_trip(self):
        out = self._call(action="transfer", chat_id="-300111", transfer="только источник")
        self.assertIn("только источник", out)
        self.assertIn("моё слово", out)
        again = self._call(action="transfer", chat_id="-300111")
        self.assertIn("только источник", again)
        self.assertNotIn("действует умолчание", again)

    def test_clearing_via_the_hand(self):
        self._call(action="transfer", chat_id="-300112", transfer="закрыто")
        out = self._call(action="transfer", chat_id="-300112", transfer="снять")
        self.assertIn("умолчание", out)


if __name__ == "__main__":
    unittest.main()
