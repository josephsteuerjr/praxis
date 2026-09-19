# -*- coding: utf-8 -*-
"""Рука `search_chats` больше не отвечает «нет» о комнате, которая есть в её памяти (15.09).

Живой случай 15.09.2026, 15:30 UTC: её спросили в AbstractDL про чат «Ouroboros AI». Она
позвала `search_chats("уробор")`, получила «(ничего не нашла)», ушла искать в веб и
ответила в группу «разобралась, что смогла извне». При этом комната лежала у неё в
`memory/rooms/-1003701205730.md` вместе со сводкой участников и тем, а входящих оттуда не
было пятый день — по её же решению заморозить её 10.09. Рука промахнулась дважды:
подстрочная сверка «уробор» с латинским «Ouroboros AI» не могла совпасть НИКОГДА, и пустой
ответ ничего не говорил ни о границах взгляда, ни о режиме комнаты.

Здесь закрепляется: сверка имён между алфавитами, профили комнат смотрятся всегда, режим
называется вслух, пустой ответ называет просмотренное и чем искать дальше, отсутствие
транспорта не отменяет ответа из её собственной памяти.

Запуск: python praxis_test.py test_search_chats_1509 -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import rooms  # noqa: E402

OUROBOROS = "-1003701205730"
ABSTRACT = "-1001240718803"


class LatinFold(unittest.TestCase):
    """Общий скелет строки: регистр снят, кириллица переложена латиницей."""

    def test_cyrillic_query_meets_a_latin_name(self):
        self.assertIn(rooms.latin_fold("уробор"), rooms.latin_fold("Ouroboros AI"))
        self.assertIn(rooms.latin_fold("Уроборос"), rooms.latin_fold("ouroboros ai"))

    def test_latin_query_meets_a_cyrillic_name(self):
        self.assertIn(rooms.latin_fold("badega"), rooms.latin_fold("Бадега"))
        self.assertIn(rooms.latin_fold("kurilka"), rooms.latin_fold("Курилка"))

    def test_same_sounding_letters_meet(self):
        # «абстракт» против «AbstractDL»: кириллическое «к» и латинское «c» — одна буква скелета.
        self.assertIn(rooms.latin_fold("абстракт"), rooms.latin_fold("AbstractDL Chat"))
        self.assertIn(rooms.latin_fold("Glasscale"), rooms.latin_fold("Glassscale"))

    def test_it_is_letters_not_meaning(self):
        # Перевод мерка не берёт и не должна: честный ноль лучше угаданного совпадения.
        self.assertNotIn(rooms.latin_fold("мицелий"), rooms.latin_fold("mycelium"))

    def test_case_and_empty(self):
        self.assertEqual(rooms.latin_fold("АбВ"), rooms.latin_fold("абв"))
        self.assertEqual(rooms.latin_fold(""), "")
        self.assertEqual(rooms.latin_fold(None), "")

    def test_it_is_a_yardstick_not_a_transliteration_for_humans(self):
        # Печатать этим нельзя: обратного преобразования нет, «щ» здесь не «shch», а после
        # сведения одинаково звучащих согласных — вовсе «skh». Зато «ч» и латинское «ch»
        # сходятся в ту же пару букв, а ради этого мерка и заведена.
        self.assertEqual(rooms.latin_fold("щ"), "skh")
        self.assertEqual(rooms.latin_fold("ч"), rooms.latin_fold("ch"))
        self.assertEqual(rooms.latin_fold("ь"), "")


class SearchProfiles(unittest.TestCase):
    """Её собственные профили комнат: по имени, по её тексту, с живым режимом."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "rooms"
        self.dir.mkdir()
        (self.dir / f"{OUROBOROS}.md").write_text(
            "# Ouroboros AI\n\nmode: frozen\nmode_reason: сама решила заморозить\n"
            "mode_set_by: praxis\ndisclosure: standard\n\n## Сводка предыстории\n"
            "- **Участники:** Аше, Glassscale, Arête.\n- **Темы:** агенты, DeepSeek Harness.\n",
            encoding="utf-8")
        (self.dir / f"{ABSTRACT}.md").write_text(
            "# AbstractDL Chat\n\nmode: normal\ndisclosure: open\n", encoding="utf-8")
        for patcher in (mock.patch.object(rooms, "ROOMS_DIR", self.dir),
                        mock.patch.object(rooms, "effective_mode",
                                          side_effect=lambda cid, *a, **k:
                                          "frozen" if str(cid) == OUROBOROS else "normal")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_finds_by_name_across_alphabets_and_reports_the_mode(self):
        hits = rooms.search_profiles("уробор")
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["chat_id"], OUROBOROS)
        self.assertEqual(hits[0]["title"], "Ouroboros AI")
        self.assertEqual(hits[0]["mode"], "frozen")
        self.assertEqual(hits[0]["where"], "название")

    def test_finds_by_her_own_text_about_the_room(self):
        hits = rooms.search_profiles("Glassscale")
        self.assertEqual([h["chat_id"] for h in hits], [OUROBOROS])
        self.assertEqual(hits[0]["where"], "её текст о комнате")

    def test_name_matches_come_before_body_matches(self):
        (self.dir / "-100777.md").write_text(
            "# Курилка\n\nmode: normal\n\n## Сводка\nтут говорят про Ouroboros иногда\n",
            encoding="utf-8")
        hits = rooms.search_profiles("ouroboros")
        self.assertEqual([h["where"] for h in hits], ["название", "её текст о комнате"])

    def test_id_is_searchable_and_empty_query_finds_nothing(self):
        self.assertEqual([h["chat_id"] for h in rooms.search_profiles(OUROBOROS)], [OUROBOROS])
        self.assertEqual(rooms.search_profiles(""), [])
        self.assertEqual(rooms.search_profiles("   "), [])

    def test_no_such_room_is_an_honest_empty(self):
        self.assertEqual(rooms.search_profiles("такой комнаты нет"), [])


class ToolSearchChats(unittest.TestCase):
    """Рука: два источника, названный режим, названная граница взгляда."""

    def setUp(self) -> None:
        self.addCleanup(agent._TELETHON.pop, "search_chats", None)
        patcher = mock.patch.object(
            rooms, "search_profiles",
            return_value=[{"chat_id": OUROBOROS, "title": "Ouroboros AI",
                           "mode": "frozen", "where": "название"}])
        patcher.start()
        self.addCleanup(patcher.stop)
        status = mock.patch.object(agent, "telegram_transport_status", return_value="open")
        status.start()
        self.addCleanup(status.stop)

    def test_frozen_room_is_found_and_its_silence_is_explained(self):
        agent._TELETHON["search_chats"] = lambda q: ""
        out = agent.tool_search_chats("уробор")
        self.assertIn("Ouroboros AI", out)
        self.assertIn(OUROBOROS, out)
        self.assertIn("замри", out)
        self.assertIn("НЕ приходят", out)
        self.assertNotIn("ничего не нашла", out)

    def test_both_sources_are_shown_and_named(self):
        agent._TELETHON["search_chats"] = lambda q: "Ouroboros AI: -1003701205730"
        out = agent.tool_search_chats("уробор")
        self.assertIn("Диалоги Telegram по имени:", out)
        self.assertIn("Мои профили комнат", out)

    def test_without_telethon_her_own_memory_still_answers(self):
        out = agent.tool_search_chats("уробор")
        self.assertIn("Ouroboros AI", out)
        self.assertIn("нет связи с Telethon", out)

    def test_closed_transport_names_itself_and_does_not_swallow_the_answer(self):
        agent._TELETHON["search_chats"] = lambda q: "не должно вызваться"
        with (mock.patch.object(agent, "telegram_transport_status", return_value="closed"),
              mock.patch.dict(agent._TRANSPORT_CLOSED_LINES,
                              {"closed": "транспорт закрыт намеренно"}, clear=False)):
            out = agent.tool_search_chats("уробор")
        self.assertIn("Ouroboros AI", out)
        self.assertIn("транспорт закрыт намеренно", out)
        self.assertNotIn("не должно вызваться", out)

    def test_a_failing_hand_names_the_failure_and_keeps_the_local_answer(self):
        def boom(_q):
            raise RuntimeError("Telethon упал")
        agent._TELETHON["search_chats"] = boom
        out = agent.tool_search_chats("уробор")
        self.assertIn("Ouroboros AI", out)
        self.assertIn("Telethon упал", out)

    def test_nothing_found_names_what_was_searched_and_what_to_try(self):
        agent._TELETHON["search_chats"] = lambda q: ""
        with mock.patch.object(rooms, "search_profiles", return_value=[]):
            out = agent.tool_search_chats("такого нет")
        self.assertIn("именам диалогов", out)
        self.assertIn("профилям комнат", out)
        self.assertIn("search_private_messages", out)
        self.assertIn("read_chat", out)

    def test_normal_room_does_not_claim_a_silence_it_does_not_have(self):
        agent._TELETHON["search_chats"] = lambda q: ""
        with mock.patch.object(rooms, "search_profiles",
                               return_value=[{"chat_id": ABSTRACT, "title": "AbstractDL Chat",
                                              "mode": "normal", "where": "название"}]):
            out = agent.tool_search_chats("abstract")
        self.assertIn("AbstractDL Chat", out)
        self.assertIn("обычно", out)
        self.assertNotIn("НЕ приходят", out)


if __name__ == "__main__":
    unittest.main()
