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
sys.path.insert(0, str(DESK))
import layout
LIVE = layout.tree()
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
        # Birth is an empty home. An already-populated memory with no SOUL is
        # ambiguous (it could be a damaged existing agent) and may no longer
        # authorize automatic kit seeding. The secret arrives after birth and
        # must still remain outside every later snapshot.
        boot.ensure_layout(self.tmp, CFG)
        (self.tmp / "memory" / "llm.json").write_text('{"api_key": "sk-secret"}', encoding="utf-8")

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
        # `branch --show-current` есть только с git 2.22, а Debian 10 (порог
        # поставки) держит 2.20; symbolic-ref отвечает тем же и везде.
        self.assertEqual(git(self.tmp, "symbolic-ref", "--short", "HEAD").strip(), "main")

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

    def test_first_snapshot_succeeds_while_the_desktop_owner_lock_is_held(self) -> None:
        from localharness.phone_tunnel.platform_support import acquire_file_lock,release_file_lock
        candidate=self.tmp/'new-agent';candidate.mkdir()
        (candidate/'authored.md').write_text('private fixture source\n',encoding='utf8')
        held=acquire_file_lock(candidate/'.desktop-owner.lock',timeout_sec=0)
        try:
            self.assertTrue(boot.seed_git(candidate,CFG))
            tracked=set(git(candidate,'ls-files').splitlines())
            self.assertIn('authored.md',tracked)
            self.assertNotIn('.desktop-owner.lock',tracked)
        finally:release_file_lock(held)

    def test_owner_backups_moved_into_the_home_leave_the_index_not_the_disk(self) -> None:
        """2.8.3: a migrated `backup.dir` put 5 GB zips under retained-assets/ in the home.

        `add -A` before every agent command then timed out (Jarvis, 252 times since 08.11).
        An old home is healed at the next start: ignored and dropped from the index only.
        """
        ignore = self.tmp / ".gitignore"
        old_text = ignore.read_text(encoding="utf-8")
        old_text = old_text.replace("retained-assets/\n", "").replace("backups/\n", "")
        ignore.write_text(old_text, encoding="utf-8", newline="\n")
        backup = self.tmp / "retained-assets" / "a786" / "backups" / "auto.zip"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"PK\x05\x06" + bytes(18))
        (self.tmp / "backups").mkdir()
        (self.tmp / "backups" / "manual.zip").write_bytes(b"PK\x05\x06" + bytes(18))
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "old home tracked the backups")
        before = git(self.tmp, "rev-list", "--count", "HEAD").strip()
        boot.seed_git(self.tmp, CFG)
        tracked = git(self.tmp, "ls-files").split()
        self.assertFalse([f for f in tracked if f.startswith(("retained-assets/", "backups/"))])
        self.assertTrue(backup.is_file(), "the owner's backup stays on disk")
        # 2.8.4: one service commit takes them out of HEAD too (git_hygiene.drop_from_head);
        # the small store keeps its history, and automatic gc is off.
        self.assertEqual(int(git(self.tmp, "rev-list", "--count", "HEAD")), int(before) + 1)
        self.assertEqual(git(self.tmp, "ls-tree", "-r", "--name-only", "HEAD", "--", "retained-assets", "backups"), "")
        self.assertEqual(git(self.tmp, "config", "--get", "gc.auto").strip(), "0")
        self.assertIn("retained-assets/", ignore.read_text(encoding="utf-8"))
        status = git(self.tmp, "status", "--porcelain")
        self.assertNotIn("retained-assets", status)
        self.assertNotIn("backups", status)


class IgnoreTopUp(unittest.TestCase):
    """`.gitignore` пишется ОДИН раз, а список растёт — старый дом надо лечить.

    Прямой повод: голос (0.5.2) кладёт модели в `data/models` — от 480 МБ до
    1,6 ГБ. Снимок правок агента делает `git add -A`; у того, кто завёл дом
    раньше, эти гигабайты уехали бы в его личную историю и остались бы там
    навсегда. Поэтому существующий файл дополняется недостающим — и только им.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".gitignore"
        self.addCleanup(self.tmp.cleanup)

    def test_дописывает_только_недостающее(self):
        self.path.write_text("memory/\nrelay/\nсвоё-агента/\n", encoding="utf-8")
        added = boot._top_up_ignore(self.path)
        self.assertIn("models/", added)
        self.assertNotIn("memory/", added)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("своё-агента/", text, "правки агента остаются на месте")
        self.assertEqual(text.count("memory/"), 1, "уже бывшая строка не задваивается")

    def test_второй_раз_молчит(self):
        self.path.write_text("\n".join(boot._IGNORE_MUST) + "\n", encoding="utf-8")
        self.assertEqual(boot._top_up_ignore(self.path), [])

    def test_модели_голоса_в_обязательном_списке(self):
        self.assertIn("models/", boot._IGNORE_MUST)
        self.assertIn("models/", boot._GIT_IGNORE)

    def test_бэкапы_в_обязательном_списке(self):
        for item in ("retained-assets/", "backups/"):
            self.assertIn(item, boot._IGNORE_MUST)
            self.assertIn(item, boot._GIT_IGNORE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
