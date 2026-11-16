"""Agent personal Git hygiene at start (2.8.4; Jarvis, 16.11: 21 GB store, five-minute commands).

A synthetic home whose history carries an owner backup and a service file is healed: gc off,
stale temporary packs gone, the paths out of HEAD and out of the whole history, every other
file and commit kept, working files untouched. A failed rewrite leaves the store as it was.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localharness"))
import git_hygiene as h  # noqa: E402


def git(tree: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode:
        raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout


class FilterStream(unittest.TestCase):
    def test_paths_are_dropped_and_messages_kept(self):
        message = b"keep M 100644 abc retained-assets/x.zip in the message\n"
        stream = (b"commit refs/heads/main\nmark :1\ncommitter A <a> 1 +0000\n"
                  b"data %d\n" % len(message) + message +
                  b"M 100644 1111111111111111111111111111111111111111 soul/SOUL.md\n"
                  b"M 100644 2222222222222222222222222222222222222222 retained-assets/a/backups/x.zip\n"
                  b'M 100644 3333333333333333333333333333333333333333 "backups/\\320\\261\\321\\215\\320\\272.zip"\n'
                  b"D .python-desktop-status\n"
                  b'M 100644 4444444444444444444444444444444444444444 "soul/\\320\\267\\320\\260\\320\\274.md"\n\n')
        out, dropped = h.filter_stream(stream)
        self.assertEqual(dropped, 3)
        self.assertIn(message, out)
        self.assertIn(b"soul/SOUL.md", out)
        self.assertIn(b"\\320\\267", out)
        self.assertNotIn(b"retained-assets/a/backups", out)
        self.assertNotIn(b".python-desktop-status", out)

    def test_unquote(self):
        self.assertEqual(h._unquote(b'"backups/\\320\\261 x.zip"'), "backups/б x.zip")
        self.assertEqual(h._unquote(b"soul/a b.md"), "soul/a b.md")


class Heal(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tree = Path(temp.name) / "data"
        self.tree.mkdir()
        git(self.tree, "init", "-q", "-b", "main")
        git(self.tree, "config", "user.name", "Agent")
        git(self.tree, "config", "user.email", "agent@helene.local")
        git(self.tree, "config", "core.autocrlf", "false")
        self.write("soul/SOUL.md", "v1\n")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "day zero")
        self.write("retained-assets/a786/backups/auto.zip", "PK" + "z" * 4000)
        self.write(".python-desktop-status", "{}")
        self.write("soul/skills/note.md", "skill\n")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "self-edit: backups crept in")
        git(self.tree, "update-ref", "refs/praxis-snapshots/20261116-aaaa", "HEAD")
        self.write("soul/SOUL.md", "v2\n")
        self.write(".python-desktop-status", '{"tick":1}')
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "self-edit: soul")
        self.write(".gitignore", "retained-assets/\nbackups/\n.python-desktop-status\n")
        git(self.tree, "add", ".gitignore")
        git(self.tree, "commit", "-q", "-m", "ignore")
        garbage = self.tree / ".git/objects/pack/tmp_pack_abc123"
        garbage.write_bytes(b"x" * 1000)
        old = time.time() - 3600
        os.utime(garbage, (old, old))
        self.fresh = self.tree / ".git/objects/pack/tmp_pack_live"
        self.fresh.write_bytes(b"y")

    def write(self, name, text):
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def test_heal_cleans_history_and_keeps_every_edit(self):
        commits_before = git(self.tree, "rev-list", "--count", "--all").strip()
        soul_history = git(self.tree, "log", "--format=%s", "--", "soul/SOUL.md")
        with mock.patch.object(h, "SHRINK_THRESHOLD", 0):
            result = h.heal(self.tree)
        self.assertEqual(git(self.tree, "config", "--get", "gc.auto").strip(), "0")
        self.assertEqual(git(self.tree, "config", "--get", "maintenance.auto").strip(), "false")
        self.assertGreater(result["garbage_bytes"], 0)
        self.assertFalse((self.tree / ".git/objects/pack/tmp_pack_abc123").exists())
        self.assertTrue(result["head"])
        self.assertIn("refs", result["shrink"])
        self.assertEqual(git(self.tree, "rev-list", "--all", "--", *h.EXCLUDED), "")
        self.assertEqual(git(self.tree, "log", "--format=%s", "--", "soul/SOUL.md"), soul_history)
        self.assertEqual(int(git(self.tree, "rev-list", "--count", "--all")), int(commits_before) + 1)
        self.assertEqual(git(self.tree, "show", "HEAD:soul/SOUL.md"), "v2\n")
        self.assertIn("soul/skills/note.md", git(self.tree, "ls-tree", "-r", "--name-only",
                                                 "refs/praxis-snapshots/20261116-aaaa"))
        git(self.tree, "fsck", "--full", "--no-dangling")
        self.assertTrue((self.tree / "retained-assets/a786/backups/auto.zip").is_file())
        self.assertEqual(git(self.tree, "status", "--porcelain"), "")
        self.assertFalse(list(self.tree.glob(".git-helene-*")))

    def test_only_stale_temporary_packs_are_deleted(self):
        freed = h.drop_garbage_packs(self.tree / ".git")
        self.assertEqual(freed, 1000)
        self.assertFalse((self.tree / ".git/objects/pack/tmp_pack_abc123").exists())
        self.assertTrue(self.fresh.exists(), "a pack still being written is not touched")

    def test_small_store_keeps_its_history(self):
        result = h.heal(self.tree)
        self.assertEqual(result["shrink"].get("skipped"), "small")
        self.assertTrue(git(self.tree, "rev-list", "--all", "--", "retained-assets"))
        self.assertEqual(git(self.tree, "ls-tree", "-r", "--name-only", "HEAD", "--", *h.EXCLUDED), "")

    def test_failed_verification_leaves_the_store_as_it_was(self):
        refs = git(self.tree, "for-each-ref")
        with mock.patch.object(h, "SHRINK_THRESHOLD", 0), \
                mock.patch.object(h, "_tree_listing", side_effect=[["a"], ["b"]] * 10):
            result = h.shrink_history(self.tree, self.tree / ".git", threshold=0)
        self.assertIn("failed", result)
        self.assertEqual(git(self.tree, "for-each-ref"), refs)
        self.assertFalse((self.tree / ".git-helene-shrink").exists())
        git(self.tree, "fsck", "--full", "--no-dangling")


if __name__ == "__main__":
    unittest.main()
