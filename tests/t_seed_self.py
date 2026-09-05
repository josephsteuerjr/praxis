# -*- coding: utf-8 -*-
"""Запись о себе в день ноль доезжает до кадра — через self_model, с провенансом.

    python tests/t_seed_self.py

Стенд: пустое дерево, раскладка `boot.ensure_layout`, потом проверка не файла,
а того, что ЧИТАЕТ КАДР — `self_model.current_prompt`. Файл без шапки кадр
отвергает молча, и именно эту дыру стенд ловит: положить CURRENT.md копией
можно, увидеть его в кадре — нет.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
LIVE = DESK.parent / "live"
sys.path.insert(0, str(DESK / "localharness"))
sys.path.insert(0, str(LIVE))          # self_model живёт в дереве агента

import boot  # noqa: E402
import self_model  # noqa: E402

CFG = {"agent": {"name": "Проба"}, "owner": {"name": "Егор"}}


class SeedSelf(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="helene-self-"))

    def test_day_zero_lands_in_frame(self) -> None:
        boot.ensure_layout(self.tmp, CFG)
        current = self.tmp / "soul" / "self" / "CURRENT.md"
        self.assertTrue(current.is_file(), "CURRENT.md не создан")
        # Кадр, а не файл: ровно то, что видит модель.
        seen = self_model.current_prompt(self.tmp)
        self.assertIn("Кто я сейчас", seen)
        self.assertIn("Проба", seen, "имя агента не подставлено")
        self.assertNotIn("{{", seen, "остался плейсхолдер")
        info = self_model.current_prompt_info(self.tmp)
        self.assertEqual(info.source, "current")
        # Письмо от собравших дом — первая версия истории, байт в байт.
        legacy = (self.tmp / "soul" / "self.md").read_bytes()
        self.assertEqual((self.tmp / "soul" / "self" / "history" / "0000.md").read_bytes(), legacy)
        self.assertIn("Письмо в день ноль", legacy.decode("utf-8"))

    def test_idempotent_keeps_agent_revision(self) -> None:
        boot.ensure_layout(self.tmp, CFG)
        # Агент переписал себя — обновление продукта не должно вернуть день ноль.
        r = self_model.revise(
            "Я уже не день ноль: за неделю переписал три навыка и завёл первое желание. "
            "Пишу коротко, потому что этот текст читается каждый ход.",
            base=self.tmp, reason="тест: первая своя версия",
            evidence_refs=["soul/SOUL.md"], trigger="test",
        )
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(boot.seed_self(self.tmp, CFG))
        boot.ensure_layout(self.tmp, CFG)
        self.assertIn("уже не день ноль", self_model.current_prompt(self.tmp))

    def test_voice_and_skills_land_too(self) -> None:
        boot.ensure_layout(self.tmp, CFG)
        voice = self.tmp / "soul" / "VOICE.md"
        self.assertTrue(voice.is_file(), "VOICE.md не лёг — зона K без голоса")
        self.assertNotIn("{{", voice.read_text(encoding="utf-8"))
        skills = sorted(p.name for p in (self.tmp / "soul" / "skills").glob("*.md") if p.name != "INDEX.md")
        self.assertEqual(len(skills), 20, skills)
        index = (self.tmp / "soul" / "skills" / "INDEX.md").read_text(encoding="utf-8")
        for name in skills:
            self.assertIn(f"({name})", index, f"навык {name} не назван в INDEX")


if __name__ == "__main__":
    unittest.main(verbosity=2)
