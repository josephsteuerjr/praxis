# -*- coding: utf-8 -*-
"""Личный git агента заводится при рождении дома — и держит слово кадра.

    python tests/t_seed_git.py

Стенд: пустое дерево, раскладка `boot.ensure_layout`, потом вопросы не к
файлам, а к git: есть ли репозиторий, что в первом снимке, чего в нём НЕТ
(память и ключи), кто автор, и не плодит ли повторная раскладка коммитов.
Git берётся тот, что найдёт `boot.arm_git` — в разработке системный, в
поставке `runtime/git`.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
LIVE = DESK.parent / "live"
sys.path.insert(0, str(DESK / "localharness"))
sys.path.insert(0, str(LIVE))          # self_model живёт в дереве агента

import boot  # noqa: E402

CFG = {"agent": {"name": "Проба"}, "owner": {"name": "Егор"}}


def git(tree: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(tree), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


@unittest.skipUnless(boot.arm_git(), "git не найден ни в поставке, ни в системе")
class SeedGit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="helene-git-"))
        # то, чего в снимке быть не должно: ключ и память, как в живом дереве
        (self.tmp / "memory").mkdir()
        (self.tmp / "memory" / "llm.json").write_text('{"api_key": "sk-secret"}', encoding="utf-8")
        boot.ensure_layout(self.tmp, CFG)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repo_with_day_zero_snapshot(self) -> None:
        self.assertTrue((self.tmp / ".git").is_dir(), "репозиторий не заведён")
        log = git(self.tmp, "log", "--format=%s|%an|%ae")
        self.assertEqual(log.count("\n"), 1, "ровно один снимок при рождении")
        subject, author, email = log.strip().split("|")
        self.assertIn("день ноль", subject)
        self.assertEqual(author, "Проба", "автор снимка — сам агент")
        self.assertEqual(email, "proba@helene.local".replace("proba", "agent"),
                         "нелатинское имя даёт адрес agent@helene.local")
        self.assertEqual(git(self.tmp, "branch", "--show-current").strip(), "main")

    def test_snapshot_holds_the_home_and_not_the_keys(self) -> None:
        files = set(git(self.tmp, "ls-files").split())
        self.assertIn("soul/SOUL.md", files)
        self.assertIn("soul/VOICE.md", files)
        self.assertIn("soul/self/CURRENT.md", files, "запись о себе должна попасть в первый снимок")
        self.assertIn(".gitignore", files)
        self.assertFalse(any(f.startswith("memory/") for f in files), "память и ключи — не снимок")
        # ключ не должен быть виден git даже как неотслеженный
        status = git(self.tmp, "status", "--porcelain", "--ignored=no")
        self.assertNotIn("llm.json", status)

    def test_second_layout_keeps_history(self) -> None:
        boot.ensure_layout(self.tmp, CFG)
        self.assertEqual(git(self.tmp, "rev-list", "--count", "HEAD").strip(), "1")

    def test_agent_edit_is_visible_to_git(self) -> None:
        # То, ради чего всё: правка навыка видна как изменение — autocommit
        # дерева (selfgit.changed_paths) увидит её и снимет.
        (self.tmp / "soul" / "skills" / "wanting.md").write_text("# моё\n", encoding="utf-8")
        status = git(self.tmp, "status", "--porcelain")
        self.assertIn("soul/skills/wanting.md", status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
