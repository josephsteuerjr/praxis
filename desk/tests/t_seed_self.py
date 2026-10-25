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
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
import layout  # noqa: E402 — где на диске лежит рабочая копия дерева

LIVE = layout.tree()
sys.path.insert(0, str(DESK / "localharness"))
sys.path.insert(0, str(LIVE))          # self_model живёт в дереве агента

import boot  # noqa: E402
import self_model  # noqa: E402
import kit_proposals  # noqa: E402

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
        expected = sorted(p.name for p in (boot._RESOURCES / "soul" / "skills").glob("*.md") if p.name != "INDEX.md")
        self.assertEqual(skills, expected)
        index = (self.tmp / "soul" / "skills" / "INDEX.md").read_text(encoding="utf-8")
        for name in skills:
            self.assertIn(f"({name})", index, f"навык {name} не назван в INDEX")

    def test_postbirth_current_requires_agent_revision_and_acknowledgement(self) -> None:
        boot.ensure_layout(self.tmp, CFG)
        before = (self.tmp / "soul/self/CURRENT.md").read_bytes()
        old_body = self_model.current_prompt_info(self.tmp).text
        new_body = ("# Кто я теперь\n\nЯ смотрю на прошлые дела, сохраняю их точный след "
                    "и сама выбираю, какая редакция текста о себе мне подходит.\n")
        offered = self.tmp / "offered-dayzero.md"
        offered.write_text(new_body, encoding="utf-8", newline="\n")
        with patch.object(boot, "_SELF_DAY_ZERO", offered), \
             patch.object(boot, "_kit_history", return_value={"self-day-zero.md": [old_body]}):
            boot.ensure_layout(self.tmp, CFG)
        self.assertEqual((self.tmp / "soul/self/CURRENT.md").read_bytes(), before)
        pending = [r for r in kit_proposals.active(self.tmp)
                   if r["target"] == "soul/self/CURRENT.md"]
        self.assertEqual(len(pending), 1)
        proposal = kit_proposals.review(self.tmp, "read", pending[0]["id"])
        self.assertEqual(proposal["candidate"], new_body)
        self.assertEqual(proposal["previous"].encode("utf-8"), before)
        revised = self_model.revise(new_body, base=self.tmp, reason="выбрала текст сама",
                                    evidence_refs=["soul/SOUL.md"], trigger="tool")
        self.assertTrue(revised.get("ok"), revised)
        decision = kit_proposals.review(self.tmp, "accept", pending[0]["id"], "я это выбрала")
        self.assertFalse(decision["exact_bytes"], "CURRENT wraps body in revision metadata")
        self.assertEqual(decision["observed_sha256"], kit_proposals._sha(
            (self.tmp / "soul/self/CURRENT.md").read_bytes()))

    def test_production_birth_receipt_is_complete_only_with_current_and_git(self) -> None:
        receipt = self.tmp.parent / (self.tmp.name + "-kit-birth.json")
        receipt.write_text(json.dumps({"schema": 1, "state": "prepared",
                                       "source": "setup", "tree": str(self.tmp.resolve())}),
                           encoding="utf-8")
        self.addCleanup(receipt.unlink, missing_ok=True)
        self.addCleanup(receipt.with_name(receipt.name + ".claim").unlink, missing_ok=True)
        boot.ensure_layout(self.tmp, CFG, birth_receipt=receipt)
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["state"], "complete")
        self.assertEqual(self_model.current_prompt_info(self.tmp).source, "current")
        self.assertTrue((self.tmp / ".git").is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
