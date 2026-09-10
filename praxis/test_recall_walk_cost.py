"""Обход корпуса стал дешевле. Здесь доказывается, что он не стал ДРУГИМ.

Обе правки — про цену вопроса к памяти, а не про ответ. Значит и проверять надо не
скорость (она среде не обязана), а то, что множество источников и разрешение свёрток
остались прежними до последнего элемента.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import memory_fts
import memory_provenance


def _reference_walk(root: Path, pattern: str, *, prune: set[str], prune_under: Path):
    """Прежняя семантика словами документации: `rglob` минус подрезанное поддерево.

    Намеренно НЕ копия прежней реализации: если сверять новый код со старым кодом,
    общая ошибка обоих останется невидимой. Здесь независимо взят `rglob` — то, чем
    `_walk_pruned` обязан быть по своему же docstring.
    """
    inside = prune_under.resolve() if prune_under.exists() else None
    for path in root.rglob(pattern):
        if inside is not None and any(
            part == p for p in prune for part in path.relative_to(root).parts[:-1]
        ):
            try:
                path.relative_to(inside)
            except ValueError:
                pass
            else:
                continue
        yield path


class TheWalkFindsExactlyWhatItFoundBefore(unittest.TestCase):
    """`scandir` вместо `iterdir` и суффикс вместо glob — только цена, не смысл."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        # обычная память
        (self.root / "journal").mkdir()
        (self.root / "journal" / "2026-08-28.md").write_text("j", encoding="utf-8")
        (self.root / "people").mkdir(parents=True)
        (self.root / "people" / "someone.md").write_text("p", encoding="utf-8")
        (self.root / "people" / "notes.jsonl").write_text("{}\n", encoding="utf-8")
        (self.root / "people" / "photo.png").write_bytes(b"\x89PNG")
        # дерево прогонов: `results` под ним подрезается
        deep = self.root / "runs" / "2026-08" / "abc" / "results"
        deep.mkdir(parents=True)
        (deep / "buried.md").write_text("не должен найтись", encoding="utf-8")
        (self.root / "runs" / "2026-08" / "abc" / "recap.md").write_text("r", encoding="utf-8")
        # ⚑ такой же `results` ВНЕ runs подрезаться НЕ должен
        outside = self.root / "artifacts" / "results"
        outside.mkdir(parents=True)
        (outside / "kept.md").write_text("должен найтись", encoding="utf-8")

    def _walk(self, pattern):
        return sorted(memory_fts._walk_pruned(
            self.root, pattern, prune={"results"}, prune_under=self.root / "runs"))

    def test_markdown_matches_the_reference_exactly(self):
        expected = sorted(_reference_walk(
            self.root, "*.md", prune={"results"}, prune_under=self.root / "runs"))
        self.assertEqual(self._walk("*.md"), expected)

    def test_jsonl_matches_the_reference_exactly(self):
        expected = sorted(_reference_walk(
            self.root, "*.jsonl", prune={"results"}, prune_under=self.root / "runs"))
        self.assertEqual(self._walk("*.jsonl"), expected)

    def test_the_pruned_subtree_is_actually_pruned(self):
        found = {p.name for p in self._walk("*.md")}
        self.assertNotIn("buried.md", found, "results под runs обязан быть подрезан")
        self.assertIn("recap.md", found, "соседний artifacts/ не должен пострадать")

    def test_a_results_directory_outside_runs_is_kept(self):
        """Подрезка структурная, а не по имени: `results` вне `runs` — обычный каталог."""
        self.assertIn("kept.md", {p.name for p in self._walk("*.md")})

    def test_a_prefix_sibling_of_runs_is_not_pruned(self):
        """`runs-old` не является частью `runs` только потому, что так начинается."""
        sibling = self.root / "runs-old" / "results"
        sibling.mkdir(parents=True)
        (sibling / "must_stay.md").write_text("x", encoding="utf-8")
        self.assertIn("must_stay.md", {p.name for p in self._walk("*.md")})

    def test_files_of_other_kinds_are_not_yielded(self):
        self.assertNotIn("photo.png", {p.name for p in self._walk("*.md")})

    def test_a_symlinked_directory_is_followed_as_before(self):
        """`DirEntry.is_dir()` следует по ссылке ровно как `Path.is_dir()`.
        Разойдись они — часть памяти молча выпала бы из поиска."""
        target = self.root / "linked_target"
        target.mkdir()
        (target / "through_link.md").write_text("x", encoding="utf-8")
        try:
            os.symlink(target, self.root / "via_link")
        except (OSError, NotImplementedError):
            self.skipTest("символические ссылки недоступны в этой среде")
        found = [p for p in self._walk("*.md") if "via_link" in p.parts]
        self.assertTrue(found, "обход перестал заходить в каталог по ссылке")


class TheSuffixShortcutRefusesAnythingItCannotRead(unittest.TestCase):
    """Быстрый путь берётся только для шаблона, который он понимает целиком."""

    def test_a_plain_extension_is_understood(self):
        self.assertEqual(memory_fts._simple_suffix("*.md"), ".md")
        self.assertEqual(memory_fts._simple_suffix("*.jsonl"), ".jsonl")

    def test_anything_richer_falls_back_to_the_real_matcher(self):
        for pattern in ("*.m?", "*[0-9].md", "a*b*.md", "**/*.md", "exact.md"):
            self.assertIsNone(memory_fts._simple_suffix(pattern),
                              f"{pattern} нельзя понимать как простой суффикс")

    def test_the_fallback_still_finds_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a1.md").write_text("x", encoding="utf-8")
            (root / "ab.md").write_text("x", encoding="utf-8")
            found = {p.name for p in memory_fts._walk_pruned(
                root, "*[0-9].md", prune=set(), prune_under=root / "nope")}
            self.assertEqual(found, {"a1.md"},
                             "сложный шаблон обязан идти прежним matcher-ом")


class ResolvingACompactTwiceCostsOnce(unittest.TestCase):
    """Разрешение — чистая функция от индекса; помним ответ, пока жив индекс."""

    def _index(self):
        return {"events": {}, "compacts": {}, "current_event_ids": (), "places": {}}

    def test_the_answer_is_identical_to_the_direct_call(self):
        index = self._index()
        direct = memory_provenance._resolve_compact("нет-такого", self._index(), set())
        memoized = memory_provenance.compact_evidence("нет-такого", index)
        self.assertEqual(memoized, direct)

    def test_the_second_question_does_not_reach_the_resolver(self):
        index = self._index()
        calls = []
        original = memory_provenance._resolve_compact

        def counting(compact_id, evidence, stack, **kw):
            calls.append(compact_id)
            return original(compact_id, evidence, stack, **kw)

        memory_provenance._resolve_compact = counting
        self.addCleanup(setattr, memory_provenance, "_resolve_compact", original)
        memory_provenance.compact_evidence("c1", index)
        memory_provenance.compact_evidence("c1", index)
        self.assertEqual(calls, ["c1"], "второй вопрос снова пошёл считать заново")

    def test_display_and_coverage_are_remembered_apart(self):
        """Её решение 21.08: показ и покрытие — разные вопросы. Общая ячейка памяти
        ответила бы на один вопрос ответом на другой."""
        index = self._index()
        strict = memory_provenance.compact_evidence("c1", index)
        coverage = memory_provenance.compact_coverage("c1", index)
        memo = index[memory_provenance._RESOLUTION_MEMO_KEY]
        self.assertIn(("c1", True), memo)
        self.assertIn(("c1", False), memo)
        self.assertEqual(memo[("c1", True)], strict)
        self.assertEqual(memo[("c1", False)], coverage)

    def test_a_new_index_brings_a_new_memory(self):
        """Память живёт В индексе: сменились файлы жизни — сменился словарь, и старые
        ответы уходят вместе с ним. Отдельный кэш пришлось бы сбрасывать руками."""
        first = self._index()
        memory_provenance.compact_evidence("c1", first)
        self.assertIn(memory_provenance._RESOLUTION_MEMO_KEY, first)
        second = self._index()
        self.assertNotIn(memory_provenance._RESOLUTION_MEMO_KEY, second)

    def test_the_memory_key_does_not_disturb_the_index_itself(self):
        """Индекс читают по именам ключей, а не перебором — но проверим, что прежние
        ключи на месте и ни один не переписан."""
        index = self._index()
        before = {k: v for k, v in index.items()}
        memory_provenance.compact_evidence("c1", index)
        for key, value in before.items():
            self.assertEqual(index[key], value, f"ключ {key} пострадал")


if __name__ == "__main__":
    unittest.main()
