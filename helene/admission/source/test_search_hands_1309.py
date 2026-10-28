"""
13.09 — руки поиска: `inbox_list("from-helene")` и `fs_search` без root по большому дому.

Два дефекта одного дня, оба «Ничего не нашла» при лежащем на месте файле:
  • `_resolve_inbox` клеил относительное имя к дому, а не к ящику → «Нет такой папки»;
  • компилируемый пол смотрит 5 000 файлов за вызов и молчал об этом → «Ничего не нашла
    (5000 файлов по **/*.md)» за 0,3 с при 29 928 .md в доме.

Запуск:  python praxis_test.py test_search_hands_1309 -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import workshop


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_search_"))
        for d in ("workspace/inbox/from-helene", "workspace/inbox/groups/abstract",
                  "workspace/projects", "soul/skills", "docs", "memory/dossiers",
                  "memory/runs/run-1", "memory/.state", ".proposals/x", "node_modules/y"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "workspace/inbox/from-helene/записка.md").write_text(
            "# Кадр как документ\nтекст\n", encoding="utf-8")
        (self.tmp / "workspace/inbox/groups/abstract/файл.txt").write_text("x\n", encoding="utf-8")
        (self.tmp / "memory/runs/run-1/manifest.md").write_text("иголка в прогоне\n", encoding="utf-8")
        (self.tmp / "memory/dossiers/егор.md").write_text("иголка в досье\n", encoding="utf-8")
        (self.tmp / "soul/HOME.md").write_text("иголка в душе\n", encoding="utf-8")
        (self.tmp / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._orig = [(workshop, k, getattr(workshop, k))
                      for k in ("BASE", "REPO", "PROJECTS", "INBOX")]
        workshop.BASE = workshop.REPO = self.tmp
        workshop.PROJECTS = self.tmp / "workspace" / "projects"
        workshop.INBOX = self.tmp / "workspace" / "inbox"
        self._env = os.environ.get("PRAXIS_HANDS")
        os.environ["PRAXIS_HANDS"] = "off"  # питоновый пол; бинарь подменяем моком где нужен

    def tearDown(self):
        for m, k, v in self._orig:
            setattr(m, k, v)
        if self._env is None:
            os.environ.pop("PRAXIS_HANDS", None)
        else:
            os.environ["PRAXIS_HANDS"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestInboxResolver(Base):
    def test_relative_name_is_resolved_from_the_inbox(self):
        # ровно её вызов из абстракта: inbox_list("from-helene") → раньше «Нет такой папки»
        out = workshop.inbox_list("from-helene")
        self.assertIn("записка.md", out, out)
        out = workshop.inbox_list("groups/abstract")
        self.assertIn("файл.txt", out, out)

    def test_full_and_absolute_paths_still_work(self):
        self.assertIn("записка.md", workshop.inbox_list("workspace/inbox/from-helene"))
        self.assertIn("записка.md",
                      workshop.inbox_list(str(self.tmp / "workspace/inbox/from-helene")))
        self.assertIn("from-helene/", workshop.inbox_list(""))

    def test_inbox_read_by_relative_name(self):
        out = workshop.inbox_read("from-helene/записка.md")
        self.assertIn("Кадр как документ", out)

    def test_boundary_of_the_inbox_is_kept(self):
        # имя, совпадающее с папкой дома, но не лежащее в ящике — не выпускает наружу
        self.assertIsNone(workshop._resolve_inbox("../../soul"))
        self.assertIsNone(workshop._resolve_inbox(str(self.tmp / "soul")))
        # «soul» — папка дома, а не ящика: наружу не выпускает, как и раньше
        self.assertIsNone(workshop._resolve_inbox("soul"))
        self.assertEqual(workshop.inbox_list("soul"), "Нет такой папки внутри workspace/inbox.")
        self.assertIn("должен быть внутри", workshop.inbox_read("../../core.py"))
        self.assertIn("должен быть внутри", workshop.inbox_read("core.py"))

    def test_missing_names_are_reported_honestly(self):
        self.assertEqual(workshop.inbox_list("нет-такой"),
                         "Нет такой папки внутри workspace/inbox.")
        # первая папка имени есть в ящике → это опечатка в ящике, а не выход из него
        self.assertIn("Нет файла", workshop.inbox_read("from-helene/опечатка.md"))
        self.assertEqual(workshop.inbox_list("from-helene/нет"),
                         "Нет такой папки внутри workspace/inbox.")


class TestSearchRoots(Base):
    def test_order_and_exclusions(self):
        roots = workshop._search_roots(self.tmp)
        self.assertEqual(roots[:3], ["workspace", "soul", "docs"])
        self.assertIn("memory/dossiers", roots)
        self.assertNotIn("memory/runs", roots)
        self.assertNotIn("memory/.state", roots)
        self.assertNotIn(".proposals", roots)
        self.assertNotIn("node_modules", roots)
        self.assertNotIn("memory", roots, "память идёт по папкам, не целиком")
        self.assertEqual(len(roots), len(set(roots)))


class _FakeFloor:
    """Подмена `hands.search`: помнит вызовы, отдаёт заданные ответы по root."""

    def __init__(self, answers: dict, default=None):
        self.answers = answers
        self.default = default
        self.calls: list[dict] = []

    def __call__(self, literal, glob="**/*.py", root="", cap=60, ignore_case=False, base=None):
        self.calls.append({"literal": literal, "glob": glob, "root": root, "cap": cap})
        if root in self.answers:
            return self.answers[root]
        return self.default


class TestFloorSearchHome(Base):
    def test_truncated_part_is_named_and_hits_carry_their_root(self):
        floor = _FakeFloor(
            {"workspace": {"ok": True, "files_seen": 5000, "capped": False, "hits": []},
             "soul": {"ok": True, "files_seen": 12, "capped": False,
                      "hits": ["HOME.md:1: иголка в душе"]}},
            default={"ok": True, "files_seen": 3, "capped": False, "hits": []})
        with mock.patch.object(workshop.hands, "search", floor):
            out = workshop.fs_search("иголка", glob="**/*.md")
        self.assertIn("soul/HOME.md:1: иголка в душе", out)
        self.assertIn("workspace", out.split("…")[-1], "усечённая часть названа")
        self.assertIn("5000", out)
        self.assertIn("memory/runs не искала", out)
        roots = [c["root"] for c in floor.calls]
        self.assertEqual(roots[0], "workspace")
        self.assertIn("memory/dossiers", roots)
        self.assertNotIn("memory/runs", roots)
        self.assertNotIn("", roots, "без root пол по всему дому не зовётся — бюджет 5000")
        self.assertEqual(roots[-1], ".", "файлы корня дома — отдельным нерекурсивным вызовом")
        self.assertEqual(floor.calls[-1]["glob"], "*.md")

    def test_nothing_found_still_names_truncation(self):
        floor = _FakeFloor(
            {"workspace": {"ok": True, "files_seen": 5000, "capped": False, "hits": []}},
            default={"ok": True, "files_seen": 1, "capped": False, "hits": []})
        with mock.patch.object(workshop.hands, "search", floor):
            out = workshop.fs_search("Кадр как документ", glob="**/*.md")
        self.assertTrue(out.startswith("Ничего не нашла"), out)
        self.assertIn("workspace", out)
        self.assertIn("сузь маску или дай root", out)

    def test_floor_silence_falls_back_to_python(self):
        floor = _FakeFloor({}, default=None)
        with mock.patch.object(workshop.hands, "search", floor):
            out = workshop.fs_search("иголка", glob="**/*.md").replace("\\", "/")
        self.assertIn("soul/HOME.md:1", out)
        self.assertIn("memory/dossiers/егор.md:1", out)
        self.assertNotIn("run-1", out, "прогоны без root не читаются")
        self.assertIn("memory/runs не искала", out)

    def test_explicit_root_reports_floor_budget(self):
        floor = _FakeFloor(
            {"memory/runs": {"ok": True, "files_seen": 5000, "capped": False,
                             "hits": ["run-1/manifest.md:1: иголка в прогоне"]}})
        with mock.patch.object(workshop.hands, "search", floor):
            out = workshop.fs_search("иголка", glob="**/*.md", root="memory/runs")
        self.assertIn("run-1/manifest.md:1", out)
        self.assertIn("первые 5000 файлов под memory/runs", out)
        self.assertEqual([c["root"] for c in floor.calls], ["memory/runs"])

    def test_refused_part_is_skipped_not_fatal(self):
        floor = _FakeFloor(
            {"docs": {"ok": False, "msg": "путь вне дома"},
             "soul": {"ok": True, "files_seen": 1, "capped": False,
                      "hits": ["HOME.md:1: иголка в душе"]}},
            default={"ok": True, "files_seen": 1, "capped": False, "hits": []})
        with mock.patch.object(workshop.hands, "search", floor):
            out = workshop.fs_search("иголка", glob="**/*.md")
        self.assertIn("soul/HOME.md:1", out)
        self.assertIn("пропустила docs: путь вне дома", out)
        self.assertIn("memory/dossiers", [c["root"] for c in floor.calls], "обход продолжился")

    def test_cap_stops_the_walk(self):
        many = [f"f{i}.md:1: иголка" for i in range(workshop.SEARCH_CAP)]
        floor = _FakeFloor(
            {"workspace": {"ok": True, "files_seen": 70, "capped": True, "hits": many}},
            default={"ok": True, "files_seen": 1, "capped": False, "hits": []})
        with mock.patch.object(workshop.hands, "search", floor):
            out = workshop.fs_search("иголка", glob="**/*.md")
        self.assertEqual([c["root"] for c in floor.calls], ["workspace"])
        self.assertIn(f"потолок {workshop.SEARCH_CAP}", out)


class TestSearchCoverage(Base):
    """Real files, with a floor double honoring Rust's **/-only recursion."""

    def setUp(self):
        super().setUp()
        for rel in ("ROOT.md", "workspace/CHILD.md", "workspace/projects/NESTED.md",
                    "memory/README.md",
                    "memory/appetite.md", "memory/dossiers/nested.md",
                    "memory/runs/run-1/hidden.md"):
            (self.tmp / rel).write_text("coverage needle\n", encoding="utf-8")
        self.calls = []
        self.visited = []

    def floor(self, literal, glob="**/*.py", root="", cap=60, **kwargs):
        import fnmatch
        self.calls.append((root, glob))
        base = Path(kwargs["base"]) / root
        recursive = glob.startswith("**/")
        mask = glob[3:] if recursive else glob
        files = []

        def walk(directory):
            self.visited.append(directory.relative_to(self.tmp).as_posix())
            # Catch accidental broad recursion, not just filtering its returned hits.
            if root != "memory/runs":
                self.assertNotIn("memory/runs", self.visited[-1])
            for child in directory.iterdir():
                if child.is_dir():
                    if recursive and child.name not in workshop._SKIP_DIRS:
                        walk(child)
                elif child.is_file() and fnmatch.fnmatchcase(child.name, mask):
                    files.append(child)

        walk(base)
        hits = [f"{p.relative_to(base).as_posix()}:{i}: {line.strip()}"
                for p in sorted(files)
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if literal in line]
        return {"ok": True, "files_seen": len(files), "hits": hits[:cap],
                "capped": len(hits) >= cap}

    @staticmethod
    def hits(out):
        return {line for line in out.replace("\\", "/").splitlines() if ":1:" in line}

    def check_backends(self, glob, expected, root=""):
        with mock.patch.object(workshop.hands, "search", self.floor):
            floor_out = workshop.fs_search("coverage needle", glob=glob, root=root)
        with mock.patch.object(workshop.hands, "search", return_value=None):
            fallback_out = workshop.fs_search("coverage needle", glob=glob, root=root)
        expected_hits = {f"{rel}:1: coverage needle" for rel in expected}
        self.assertEqual(self.hits(floor_out), expected_hits, floor_out)
        self.assertEqual(self.hits(fallback_out), expected_hits, fallback_out)

    def test_plain_glob_searches_only_base_immediate_files(self):
        self.check_backends("*.md", {"ROOT.md"})
        self.assertEqual(self.calls, [(".", "*.md")])
        self.assertEqual(self.visited, ["."])

    def test_recursive_glob_includes_direct_memory_without_walking_runs(self):
        self.check_backends("**/*.md", {"ROOT.md", "workspace/CHILD.md",
                            "workspace/projects/NESTED.md",
                            "memory/README.md", "memory/appetite.md",
                            "memory/dossiers/nested.md"})
        self.assertIn(("memory", "*.md"), self.calls)
        self.assertNotIn(("memory", "**/*.md"), self.calls)
        self.assertEqual(self.visited.count("memory/dossiers"), 1)
        self.assertEqual(self.calls[-1], (".", "*.md"))

    def test_plain_glob_preserves_explicit_root_semantics(self):
        self.check_backends("*.md", {"CHILD.md"}, root="workspace")
        self.assertEqual(self.calls, [("workspace", "*.md")])
        self.assertEqual(self.visited, ["workspace"])

    def test_explicit_runs_root_is_searchable_in_both_backends(self):
        self.check_backends("**/*.md", {"run-1/hidden.md"}, root="memory/runs")
        self.assertEqual(self.calls, [("memory/runs", "**/*.md")])
        self.assertIn("memory/runs/run-1", self.visited)


class TestPythonWalk(Base):
    def test_runs_are_searched_only_with_explicit_root(self):
        out = workshop.fs_search("иголка", glob="**/*.md").replace("\\", "/")
        self.assertNotIn("run-1", out)
        self.assertIn("memory/runs не искала — укажи root=memory/runs", out)
        out = workshop.fs_search("иголка", glob="**/*.md", root="memory/runs").replace("\\", "/")
        self.assertIn("run-1/manifest.md:1", out)
        self.assertNotIn("не искала", out)

    def test_regex_path_finds_the_note(self):
        out = workshop.fs_search("Кадр как докум.нт", glob="**/*.md").replace("\\", "/")
        self.assertIn("workspace/inbox/from-helene/записка.md:1", out)


if __name__ == "__main__":
    unittest.main()
