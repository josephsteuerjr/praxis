"""Два её решения 13.08: конфликт без победителя и честный список навыков.

Оба сформулированы ею дословно и оба про одно: механизм ПОКАЗЫВАЕТ, а не решает за неё.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import capabilities


class TheSkillListEqualsTheDisk(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-skills-")
        self.addCleanup(self._temp.cleanup)
        self.dir = Path(self._temp.name)
        self.index = self.dir / "INDEX.md"
        self.previous = capabilities.SKILLS_INDEX
        capabilities.SKILLS_INDEX = self.index
        self.addCleanup(setattr, capabilities, "SKILLS_INDEX", self.previous)

    def _skill(self, slug: str, body: str = "тело навыка\n") -> None:
        (self.dir / f"{slug}.md").write_text(body, encoding="utf-8")

    def test_a_skill_absent_from_the_index_is_still_hers(self):
        """Прежняя редакция теряла его молча: списка не было — значит навыка не было."""
        self._skill("holding_ground")
        self._skill("забытый_в_индексе")
        self.index.write_text(
            "| Skill | About |\n|---|---|\n"
            "| [holding_ground](holding_ground.md) | стоять на своём |\n", encoding="utf-8")
        rows = {r["name"]: r["line"] for r in capabilities._skills_list()}
        self.assertEqual(set(rows), {"holding_ground", "забытый_в_индексе"})
        self.assertEqual(rows["holding_ground"], "стоять на своём")
        self.assertEqual(rows["забытый_в_индексе"], capabilities.NO_PURPOSE)

    def test_the_address_is_the_file_not_the_caption(self):
        """Подпись в индексе можно переписать; адрес — нет. Ключ — цель ссылки."""
        self._skill("thread_discipline")
        self.index.write_text(
            "- [Дисциплина нитей, как я её называю](thread_discipline.md) — не крутить руминацию\n",
            encoding="utf-8")
        rows = capabilities._skills_list()
        self.assertEqual([r["name"] for r in rows], ["thread_discipline"])
        self.assertEqual(rows[0]["line"], "не крутить руминацию")

    def test_an_unusual_index_line_no_longer_deletes_a_skill(self):
        """Ровно прежняя дыра: строка написана иначе — навык исчезал из снимка."""
        self._skill("sleep")
        self.index.write_text("Про sleep я напишу позже, ссылка: soul/skills/sleep.md\n",
                              encoding="utf-8")
        rows = capabilities._skills_list()
        self.assertEqual([r["name"] for r in rows], ["sleep"])
        self.assertEqual(rows[0]["line"], capabilities.NO_PURPOSE)

    def test_the_index_naming_a_missing_file_does_not_stay_silent(self):
        self._skill("email")
        self.index.write_text(
            "| Skill | About |\n|---|---|\n"
            "| [email](email.md) | почта |\n"
            "| [призрак](призрак.md) | навык, которого нет |\n", encoding="utf-8")
        rows = {r["name"]: r for r in capabilities._skills_list()}
        self.assertEqual(set(rows), {"email", "призрак"})
        self.assertTrue(rows["призрак"].get("orphan"))
        self.assertNotIn("orphan", rows["email"])

    def test_a_missing_index_does_not_erase_her_skills(self):
        self._skill("one")
        self._skill("two")
        self.assertEqual(sorted(r["name"] for r in capabilities._skills_list()),
                         ["one", "two"])

    def test_the_purpose_is_never_written_for_her(self):
        """«Сами назначения навыков — мои слова.» Код не сочиняет описание никогда."""
        self._skill("no_performance_layer")
        self.index.write_text("", encoding="utf-8")
        line = capabilities._skills_list()[0]["line"]
        self.assertEqual(line, capabilities.NO_PURPOSE)
        self.assertNotIn("no_performance_layer", line,
                         "код придумал описание из имени файла")


class AContradictionHasNoWinner(unittest.TestCase):
    """Статус старого утверждения не меняется. Пишется симметричная связь и карточка."""

    def _seam(self) -> str:
        """Тело функции БЕЗ докстринга: история в нём цитирует прежнюю причину дословно,
        и сличать её со свежим кодом — значит краснеть на собственном объяснении."""
        source = Path(__file__).resolve().parent.joinpath("formation.py").read_text(
            encoding="utf-8")
        start = source.index("def _link_contradicting_claims")
        whole = source[start:source.index("\ndef ", start + 10)]
        head = whole.index('"""')
        return whole[whole.index('"""', head + 3) + 3:]

    def test_the_writer_of_contested_is_gone_from_this_seam(self):
        body = self._seam()
        self.assertNotIn('"status": "contested"', body,
                         "автомат снова назначает победителя")
        self.assertNotIn("superseded", body, "в причине вернулось «старое проиграло»")
        self.assertIn('str(meta.get("status")', body,
                      "статус обязан браться из самого утверждения")

    def test_the_old_downgrading_name_is_not_used_anywhere(self):
        root = Path(__file__).resolve().parent
        for name in ("formation.py",):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("_contest_referenced_claims", text,
                             "осталось имя, обещающее понижение")

    def test_the_card_still_says_she_decides(self):
        source = Path(__file__).resolve().parent.joinpath("formation.py").read_text(
            encoding="utf-8")
        self.assertIn("разбирает она", source)
        self.assertNotIn("автомат понизил старое", source)


if __name__ == "__main__":
    unittest.main()
