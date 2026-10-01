# -*- coding: utf-8 -*-
"""Провенанс ручных правок владельца: марка в тексте, тул агента её убирает.

Слово владельца 01.10: правка файлов агента из окна без пометки — «дичайшее
нарушение провенанса». Марка — одна строка-цитата рядом с изменённым участком;
убирает её агент сам тулом clear_owner_marks. Все записи здесь реалистичны:
окно шлёт текст КАК ОН ЛЕЖИТ, вместе с уже стоящими марками.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'desk')]
os.environ['PRAXIS_TEST'] = '1'
import deskd.readers as readers


class OwnerMarks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        (self.tree / "memory/notes").mkdir(parents=True)
        (self.tree / "soul").mkdir(parents=True)
        env = patch.dict(os.environ, {"HELENE_TREE": str(self.tree)})
        env.start()
        self.addCleanup(env.stop)
        self.assertEqual(readers.tree(), self.tree, "тестовое дерево подхвачено")

    def file(self, rel):
        return self.tree / rel

    def test_edit_gets_marks_with_diff_words_and_old_marks_survive(self):
        first = readers.safe_write_md("memory/notes/a.md", "# Заметка\n\nСтарая мысль.\n")
        self.assertEqual(first.get("marks"), 1)
        self.assertIn("файл создан владельцем", self.file("memory/notes/a.md").read_text(encoding="utf8"))
        # Окно перечитало файл (вместе с маркой создания) и поменяло мысль.
        current = self.file("memory/notes/a.md").read_text(encoding="utf8")
        second = readers.safe_write_md(
            "memory/notes/a.md", current.replace("Старая мысль.", "Новая мысль."),
            mtime_ns=first["mtime_ns"])
        self.assertEqual(second.get("marks"), 1)
        text = self.file("memory/notes/a.md").read_text(encoding="utf8")
        self.assertIn("удалено: «Старая мысль.»", text)
        self.assertIn("вставлено: «Новая мысль.»", text)
        self.assertEqual(text.count("> [правка владельца"), 2,
                         "марка создания живёт до тул-а, марка правки встала рядом")

    def test_mark_sits_attached_above_the_changed_span(self):
        readers.safe_write_md("memory/notes/b.md", "# B\n\nПервый.\n\nВторой.\n\nТретий.\n")
        current = self.file("memory/notes/b.md").read_text(encoding="utf8")
        readers.safe_write_md("memory/notes/b.md", current.replace("Второй.", "Изменённый второй."))
        after = self.file("memory/notes/b.md").read_text(encoding="utf8").split("\n")
        idx = next(i for i, l in enumerate(after)
                   if l.startswith("> [правка владельца") and "Второй." in l)
        self.assertEqual(after[idx - 1], "Первый.", "марка приклеена к тексту над участком")
        self.assertEqual(after[idx + 2], "Изменённый второй.", "под маркой — изменённая строка")

    def test_no_content_change_no_new_mark(self):
        first = readers.safe_write_md("memory/notes/c.md", "# C\n\nТекст.\n")
        path = self.file("memory/notes/c.md")
        mtime = path.stat().st_mtime_ns
        second = readers.safe_write_md("memory/notes/c.md", path.read_text(encoding="utf8"),
                                       mtime_ns=mtime)
        self.assertEqual(second.get("marks"), 0)
        self.assertEqual(path.read_text(encoding="utf8").count("> [правка"), 1,
                         "прежняя марка не дублируется, новой нет")

    def test_deletion_only_names_what_is_gone(self):
        first = readers.safe_write_md("memory/notes/d.md", "# D\n\nОстав.\nЛишняя.\n")
        current = self.file("memory/notes/d.md").read_text(encoding="utf8")
        readers.safe_write_md("memory/notes/d.md", current.replace("\nЛишняя.\n", "\n"),
                              mtime_ns=first["mtime_ns"])
        text = self.file("memory/notes/d.md").read_text(encoding="utf8")
        self.assertIn("удалено: «Лишняя.»", text)
        mark = next(l for l in text.split("\n") if l.startswith("> [правка владельца") and "удалено" in l)
        self.assertNotIn("вставлено", mark)


class ClearTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        (self.tree / "memory/notes").mkdir(parents=True)
        (self.tree / "soul").mkdir(parents=True)
        (self.tree / "memory/notes/a.md").write_text(
            "> [правка владельца · 2026-10-01 10:00 · окно] вставлено: «привет»\n# A\n\nТекст.\n",
            encoding="utf8")
        (self.tree / "soul/SOUL.md").write_text("# Душа\n\nОснова.\n", encoding="utf8")
        sys.path[:0] = [str(ROOT / 'helene/core'), str(ROOT / 'praxis')]
        import _sandbox
        assert _sandbox.activate_if_testing()
        import agent
        self.agent = agent
        p = patch.object(agent, "BASE", self.tree)
        p.start()
        self.addCleanup(p.stop)

    def test_list_and_clear_leave_text_intact(self):
        listed = self.agent.tool_clear_owner_marks()
        self.assertIn("memory/notes/a.md", listed)
        self.assertIn("1 марк", listed)
        cleared = self.agent.tool_clear_owner_marks(action="clear")
        self.assertIn("убраны", cleared)
        self.assertEqual((self.tree / "memory/notes/a.md").read_text(encoding="utf8"),
                         "# A\n\nТекст.\n")
        self.assertEqual((self.tree / "soul/SOUL.md").read_text(encoding="utf8"),
                         "# Душа\n\nОснова.\n")
        self.assertIn("нет ни в одном файле", self.agent.tool_clear_owner_marks())

    def test_clear_single_path_only_that_file(self):
        (self.tree / "soul/VOICE.md").write_text(
            "> [правка владельца · 2026-10-01 11:00 · окно] вставлено: «шёпот»\n# Голос\n",
            encoding="utf8")
        self.agent.tool_clear_owner_marks(action="clear", path="soul/VOICE.md")
        self.assertNotIn("правка владельца",
                         (self.tree / "soul/VOICE.md").read_text(encoding="utf8"))
        self.assertIn("правка владельца",
                      (self.tree / "memory/notes/a.md").read_text(encoding="utf8"))

    def test_escape_and_foreign_paths_are_refused(self):
        self.assertIn("..", self.agent.tool_clear_owner_marks(action="clear", path="../x.md"))
        self.assertIn("не .md", self.agent.tool_clear_owner_marks(action="clear", path="memory/notes"))


if __name__ == "__main__":
    unittest.main()
