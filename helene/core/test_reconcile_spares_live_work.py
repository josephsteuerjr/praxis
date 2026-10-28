"""Уборщик оболочек не сносит дерево, в котором работают.

ЧТО СЛУЧИЛОСЬ 13.08.2026, ПО ЖУРНАЛАМ.

    12:40:08  proposal 66f6346b открыт — задача code-eeb2f170 «адресная отправка медиа»
    12:42     пять агентов отработали; воркер за 36 итераций написал патч в worktree
    12:51:03  selfdev.reconcile: closed=2   ← снёс /app/.proposals/66f6346b вместе с патчем
    12:52:31  task_abandoned: «корень задачи пропал»
    13:05     Егор: «а гномы ещё не вернулись?» — она: «значит, я не запускала»

Уборщик судил предложение ПО ВЕТКЕ: воркер ещё не коммитил, ветка стояла на HEAD,
`merge-tree` сказал «пусто» — и это прочли как брошенную оболочку. А работа лежала
некоммиченной в самом дереве, и `worktree remove --force` унёс её молча.

⚠ ДВА ПРЕДЕЛА, КОТОРЫЕ ОХРАНЯЮТ ЭТИ ТЕСТЫ.
  1. Некоммиченное — это работа. Уборщик не имеет права её оценивать.
  2. Живая coding-задача и брошенная оболочка выглядят одинаково; различает их форж,
     а не догадка по диффу.
Пропущенное называется вслух: молчаливая уборка печатается так же, как «ничего не было».
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("PRAXIS_TEST", "1")

import forge  # noqa: E402
import selfdev  # noqa: E402


class UncommittedWorkIsWork(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="praxis-reconcile-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.worktree = self.root / ".proposals" / "66f6346b"
        self.worktree.mkdir(parents=True)

    def _git(self, dirty: bool):
        def fake(*args):
            if args[:2] == ("-C", str(self.worktree)) and args[2] == "status":
                return SimpleNamespace(returncode=0,
                                       stdout=" M agent.py\n" if dirty else "",
                                       stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return fake

    def test_a_dirty_worktree_reads_as_live_work(self):
        with (
            patch.object(selfdev, "worktree_path", return_value=self.worktree),
            patch.object(selfdev, "_git", side_effect=self._git(dirty=True)),
        ):
            self.assertTrue(selfdev.worktree_has_work("66f6346b"))

    def _removals(self, *, dirty: bool):
        removed: list = []

        def fake(*args):
            if args[0] == "worktree" and args[1] == "remove":
                removed.append(args)
            return self._git(dirty=dirty)(*args)

        return removed, fake

    def test_the_automatic_tidier_refuses_to_remove_it(self):
        removed, fake = self._removals(dirty=True)
        with (
            patch.object(selfdev, "worktree_path", return_value=self.worktree),
            patch.object(selfdev, "_git", side_effect=fake),
        ):
            self.assertFalse(selfdev._cleanup("66f6346b", drop_branch=True,
                                              spare_live_work=True))
        self.assertEqual(removed, [], "дерево с правками снесено — это потеря кода")

    def test_her_own_decision_still_tidies_up(self):
        """`apply`/`reject` — названное вслух решение по конкретному предложению.

        Подменять его догадкой о содержимом дерева нельзя: разница не в том, грязно ли
        дерево, а в том, кто распорядился — человек или расписание.
        """
        removed, fake = self._removals(dirty=True)
        with (
            patch.object(selfdev, "worktree_path", return_value=self.worktree),
            patch.object(selfdev, "_git", side_effect=fake),
        ):
            self.assertTrue(selfdev._cleanup("66f6346b", drop_branch=True))
        self.assertEqual(len(removed), 1)

    def test_a_clean_shell_is_still_tidied_by_the_reaper(self):
        removed, fake = self._removals(dirty=False)
        with (
            patch.object(selfdev, "worktree_path", return_value=self.worktree),
            patch.object(selfdev, "_git", side_effect=fake),
        ):
            self.assertTrue(selfdev._cleanup("dead0001", drop_branch=True,
                                             spare_live_work=True))
        self.assertEqual(len(removed), 1)


class TheForgeKnowsWhoIsInTheTree(unittest.TestCase):
    def setUp(self):
        self.tasks = Path(tempfile.mkdtemp(prefix="praxis-forge-tasks-"))
        self.addCleanup(shutil.rmtree, self.tasks, True)

    def _task(self, task_id: str, status: str, proposal_id: str, agents: int = 0):
        import json
        directory = self.tasks / task_id
        directory.mkdir(parents=True)
        (directory / "task.json").write_text(json.dumps({
            "id": task_id, "status": status, "proposal_id": proposal_id,
            "goal": "адресная отправка медиа", "created": f"2026-08-13T12:4{agents}:00",
        }), encoding="utf-8")
        for index in range(agents):
            agent_dir = directory / "agents" / f"agent-{index}"
            agent_dir.mkdir(parents=True)
            (agent_dir / "result.json").write_text("{}", encoding="utf-8")

    def test_an_active_task_holds_its_proposal(self):
        self._task("code-eeb2f170", "active", "66f6346b")
        with patch.object(forge, "TASKS_DIR", self.tasks):
            self.assertIn("66f6346b", forge.active_proposal_ids())

    def test_a_submitted_task_still_holds_it(self):
        """`submitted` ждёт её решения по предложению — дерево трогать нельзя."""
        self._task("code-aaa", "submitted", "beef0001")
        with patch.object(forge, "TASKS_DIR", self.tasks):
            self.assertIn("beef0001", forge.active_proposal_ids())

    def test_a_finished_task_releases_it(self):
        self._task("code-bbb", "done", "dead0002")
        with patch.object(forge, "TASKS_DIR", self.tasks):
            self.assertNotIn("dead0002", forge.active_proposal_ids())

    def test_reconcile_skips_a_proposal_the_forge_is_working_on(self):
        self._task("code-eeb2f170", "active", "66f6346b")
        touched: list = []

        def fake_git(*args):
            touched.append(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(forge, "TASKS_DIR", self.tasks),
            patch.object(selfdev, "_load", return_value=[
                {"id": "66f6346b", "status": "building", "branch": "proposal/66f6346b"}]),
            patch.object(selfdev, "_main_branch", return_value="master"),
            patch.object(selfdev, "_git", side_effect=fake_git),
            patch.object(selfdev, "_update") as update,
            patch.object(selfdev, "_journal"),
        ):
            result = selfdev.reconcile()
        self.assertEqual(result["closed"], 0)
        self.assertEqual(result["spared"], 1)
        update.assert_not_called()
        self.assertFalse([a for a in touched if a[:2] == ("worktree", "remove")])


class TheSkippedWorkIsNamedOutLoud(unittest.TestCase):
    def test_the_journal_line_mentions_what_was_left_alone(self):
        lines: list[str] = []
        with (
            patch.object(forge, "active_proposal_ids", return_value=frozenset({"aa"})),
            patch.object(selfdev, "_load", return_value=[
                {"id": "aa", "status": "building", "branch": "proposal/aa"}]),
            patch.object(selfdev, "_main_branch", return_value="master"),
            patch.object(selfdev, "_git",
                         return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
            patch.object(selfdev, "_journal", side_effect=lines.append),
        ):
            selfdev.reconcile()
        self.assertTrue(lines)
        self.assertIn("живой работы не тронуто 1", lines[0])


class TheInstrumentShowsCodingTasks(unittest.TestCase):
    """«По живым прогонам я не вижу задачи со скаутами» — прибор смотрел в другой слой."""

    def test_live_tasks_are_listed_with_their_agent_count(self):
        tasks = Path(tempfile.mkdtemp(prefix="praxis-forge-brief-"))
        self.addCleanup(shutil.rmtree, tasks, True)
        import json
        directory = tasks / "code-eeb2f170"
        (directory / "agents" / "agent-1").mkdir(parents=True)
        (directory / "agents" / "agent-1" / "result.json").write_text("{}", encoding="utf-8")
        (directory / "agents" / "agent-2").mkdir(parents=True)
        (directory / "task.json").write_text(json.dumps({
            "id": "code-eeb2f170", "status": "active", "proposal_id": "66f6346b",
            "goal": "Исправить адресную отправку медиа", "created": "2026-08-13T12:40:08",
        }), encoding="utf-8")
        with patch.object(forge, "TASKS_DIR", tasks):
            rows = forge.live_tasks_brief()
        self.assertEqual(len(rows), 1)
        self.assertIn("code-eeb2f170", rows[0])
        self.assertIn("агентов 1/2", rows[0])

    def test_active_runs_answer_names_coding_tasks_too(self):
        import agent
        with patch.object(forge, "live_tasks_brief", return_value=["code-x [active] …"]):
            line = agent._coding_tasks_line()
        self.assertIn("code-x", line)

    def test_a_blind_instrument_says_so_instead_of_printing_zero(self):
        import agent
        with patch.object(forge, "live_tasks_brief", side_effect=RuntimeError("нет")):
            line = agent._coding_tasks_line()
        self.assertIn("про прибор", line)


if __name__ == "__main__":
    unittest.main()
