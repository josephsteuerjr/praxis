# -*- coding: utf-8 -*-
"""K рассказывает о руках правду: раздел есть в реестре, называет только настоящие руки,
а описания рук говорят про версию, три исхода и неполный поиск (09.11.2026).

Слово Егора 09.11: отсутствие схем новых рук в K — почти авария.

Запуск: python praxis_test.py test_hands_contract_k -v
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import frame_trace  # noqa: E402
import workshop  # noqa: E402

HERE = Path(__file__).resolve().parent


def catalog() -> dict:
    ctx = agent.ChannelContext(chat_id="42", room_id="42", is_dm=True, owner=True, known=True)
    return {t["name"]: t for t in agent.catalog_tools_for(ctx) if t.get("name")}


class TheContractIsInK(unittest.TestCase):
    def test_section_is_registered_in_the_closed_roster(self):
        self.assertIn("contract.hands_contract", frame_trace.SYSTEM_ROSTER)

    def test_section_is_assembled_into_the_system_prefix(self):
        source = (HERE / "agent.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'tail\.append\(frame_trace\.mark\("contract\.hands_contract"')

    def test_every_frame_carries_the_contract_whoever_speaks(self):
        marker = "## Мои руки: договор"
        self.assertIn(marker, agent.build_system_prompt(speaker="Егор", owner=True))
        self.assertIn(marker, agent.build_system_prompt(speaker="Кто-то", owner=False))

    def test_every_named_hand_exists(self):
        named = {name for _, names in agent.HAND_GROUPS for name in names}
        self.assertGreaterEqual(named, {"fs_read", "edit", "fs_search", "shell", "python",
                                        "proposal", "project", "coding_run", "coding_inspect"})
        self.assertEqual(sorted(named - set(agent.TOOL_IMPL)), [])
        for name in named:
            self.assertIn(name, agent._hands_contract_text())

    def test_numbers_are_the_real_limits(self):
        text = agent._hands_contract_text()
        for value in (f"{workshop.SEARCH_SECONDS:.0f} с", f"{workshop.SEARCH_CAP} совпадений",
                      f"{workshop.SEARCH_SCAN_CAP} путей"):
            self.assertIn(value, text)

    def test_the_outcome_words_are_the_real_replies(self):
        code = (HERE / "editor.py").read_text(encoding="utf-8") + (HERE / "tool_report.py").read_text(encoding="utf-8")
        for word in ("confirmed", "refused", "in_doubt", "partial"):
            self.assertIn(word, code, word)
            self.assertIn(word, agent._hands_contract_text(), word)

    def test_section_is_stable(self):
        self.assertEqual(agent._hands_contract_text(), agent._hands_contract_text())


class TheDescriptionsSpeak(unittest.TestCase):
    def setUp(self):
        self.tools = catalog()

    def text(self, name: str) -> str:
        return self.tools[name]["description"]

    def test_read_says_it_gives_the_version(self):
        self.assertIn("sha256", self.text("fs_read"))
        self.assertIn("expected_sha256", self.text("fs_read"))

    def test_writers_name_the_version_and_three_outcomes(self):
        for name in ("edit",):
            for word in ("expected_sha256", "confirmed", "refused", "outcome unknown", "NEVER retry blind"):
                self.assertIn(word, self.text(name), f"{name}: {word}")

    def test_search_says_incomplete_is_not_absent(self):
        self.assertIn("UNKNOWN, not absent", self.text("fs_search"))
        self.assertIn(str(workshop.SEARCH_CAP), self.text("fs_search"))

    def test_coding_edit_names_the_unknown_outcome(self):
        self.assertIn("NEVER retry blind", self.text("edit"))

    def test_shell_names_what_is_missing(self):
        self.assertIn("NOT installed", self.text("shell"))


if __name__ == "__main__":
    unittest.main()
