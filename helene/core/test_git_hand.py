"""Её рука к git: своё дерево и публичное зеркало.

ПОВОД. 13.08 она трижды за час выдала подготовленную работу за опубликованную. Механика
публикации была — не было ЗНАНИЯ, что она у неё есть и по какому адресу. Знание живёт в
списке рук, а не в чужой памяти о том, что кому-то это однажды объяснили.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent


def _repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "readme.md").write_text("раз\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "первый"], check=True)


class TheHandNamesWhatIsMissing(unittest.TestCase):
    def test_an_unmounted_mirror_is_an_absent_path_not_a_ban(self):
        with mock.patch.object(agent, "PUBLIC_REPO_PATH", Path("/нет-такого")):
            out = agent.tool_git(repo="public", action="status")
        self.assertIn("не смонтировано", out)
        self.assertIn("не запрет", out)

    def test_an_unknown_repo_says_the_two_it_knows(self):
        self.assertIn("self | public", agent.tool_git(repo="чужой", action="status"))

    def test_an_unknown_action_lists_the_real_ones(self):
        out = agent.tool_git(repo="self", action="сплясать")
        for act in ("status", "commit", "push"):
            self.assertIn(act, out)


class ItDoesNotWriteWithoutAReason(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-git-hand-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "repo"
        self.root.mkdir()
        _repo(self.root)
        patch = mock.patch.object(agent, "BASE", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        journal = mock.patch.object(agent, "tool_journal", lambda *a, **k: None)
        journal.start()
        self.addCleanup(journal.stop)

    def test_a_commit_without_a_message_is_refused_by_name(self):
        out = agent.tool_git(repo="self", action="commit")
        self.assertIn("без сообщения", out)
        self.assertIn("Назови", out)

    def test_status_and_log_read_the_real_repository(self):
        self.assertIn("main", agent.tool_git(repo="self", action="status"))
        self.assertIn("первый", agent.tool_git(repo="self", action="log"))

    def test_add_then_commit_lands_a_real_commit(self):
        (self.root / "второй.md").write_text("два\n", encoding="utf-8")
        agent.tool_git(repo="self", action="add")
        out = agent.tool_git(repo="self", action="commit", message="второй файл")
        self.assertNotIn("⚠", out)
        log = agent.tool_git(repo="self", action="log")
        self.assertIn("второй файл", log)


class PushLooksBeforeItThrows(unittest.TestCase):
    """⚠ Ловушка, стоившая ей прогона 12.08: `status` сравнивает с УСТАРЕВШЕЙ ссылкой."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-git-push-")
        self.addCleanup(self._temp.cleanup)
        base = Path(self._temp.name)
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.root = base / "work"
        self.root.mkdir()
        _repo(self.root)
        subprocess.run(["git", "-C", str(self.root), "remote", "add", "origin",
                        str(self.remote)], check=True)
        subprocess.run(["git", "-C", str(self.root), "push", "-q", "origin", "main"], check=True)
        patch = mock.patch.object(agent, "BASE", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        journal = mock.patch.object(agent, "tool_journal", lambda *a, **k: None)
        journal.start()
        self.addCleanup(journal.stop)
        self.other = base / "other"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.other)], check=True)
        subprocess.run(["git", "-C", str(self.other), "config", "user.email", "o@o"], check=True)
        subprocess.run(["git", "-C", str(self.other), "config", "user.name", "o"], check=True)

    def _push_from_elsewhere(self) -> None:
        (self.other / "чужое.md").write_text("не мной\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.other), "commit", "-qm", "со стороны"], check=True)
        subprocess.run(["git", "-C", str(self.other), "push", "-q", "origin", "main"], check=True)

    def test_a_clean_push_goes_through(self):
        (self.root / "моё.md").write_text("мной\n", encoding="utf-8")
        agent.tool_git(repo="self", action="add")
        agent.tool_git(repo="self", action="commit", message="моя правка")
        out = agent.tool_git(repo="self", action="push")
        self.assertNotIn("⚠", out)

    def test_a_stale_reference_is_named_before_the_push_is_thrown(self):
        self._push_from_elsewhere()
        (self.root / "моё.md").write_text("мной\n", encoding="utf-8")
        agent.tool_git(repo="self", action="add")
        agent.tool_git(repo="self", action="commit", message="моя правка")
        out = agent.tool_git(repo="self", action="push")
        self.assertIn("ушла вперёд", out)
        self.assertIn("pull", out)
        self.assertIn("устаревшей", out)

    def test_pulling_first_then_working_is_the_flow_that_works(self):
        """Порядок, который проходит: сначала подтянуть, потом делать, потом слать."""
        self._push_from_elsewhere()
        agent.tool_git(repo="self", action="pull")
        (self.root / "моё.md").write_text("мной\n", encoding="utf-8")
        agent.tool_git(repo="self", action="add")
        agent.tool_git(repo="self", action="commit", message="моя правка")
        out = agent.tool_git(repo="self", action="push")
        self.assertNotIn("ушла вперёд", out)
        self.assertNotIn("⚠", out)

    def test_diverged_history_is_named_as_her_decision_not_a_failure(self):
        """`ff-only` не «сломался» — он отказался слить за неё. Выбор её.

        Свои коммиты плюс чужие на удалёнке — fast-forward невозможен по построению.
        Rebase или merge меняет то, как её работа выглядит в истории, и решать это должна
        она, а не умолчание руки.
        """
        self._push_from_elsewhere()
        (self.root / "моё.md").write_text("мной\n", encoding="utf-8")
        agent.tool_git(repo="self", action="add")
        agent.tool_git(repo="self", action="commit", message="моя правка")
        out = agent.tool_git(repo="self", action="pull")
        self.assertIn("РАЗОШЛИСЬ", out)
        self.assertIn("не сбой", out)
        self.assertIn("тебе", out)


class TheHandIsOfferedToHer(unittest.TestCase):
    def test_git_is_in_her_tools_and_says_where_the_mirror_is(self):
        names = [t.get("name") for t in agent.OWNER_TOOLS if isinstance(t, dict)]
        self.assertIn("git", names)
        self.assertIn("git", [t.get("name") for t in agent.PRAXIS_SELF_TOOLS
                              if isinstance(t, dict)])
        desc = agent.GIT_TOOL["description"]
        self.assertIn("публичное зеркало", desc)
        self.assertIn("fetch", desc, "молчание про устаревшую ссылку вернулось")


if __name__ == "__main__":
    unittest.main()
