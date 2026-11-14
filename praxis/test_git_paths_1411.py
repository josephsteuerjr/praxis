"""git.paths is an array of exact paths; recorded string calls keep their historical split."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import tool_report
import tool_text_en


class GitPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        for key, value in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(self.root), "config", key, value], check=True)
        (self.root / "seed.txt").write_text("seed\n")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "seed"], check=True)
        for p in (mock.patch.object(agent, "BASE", self.root),
                  mock.patch.object(agent, "tool_journal", lambda *a, **k: None)):
            p.start(); self.addCleanup(p.stop)

    def staged(self):
        out = subprocess.run(["git", "-C", str(self.root), "diff", "--cached", "--name-only", "-z"],
                             capture_output=True, check=True).stdout.decode()
        return sorted(x for x in out.split("\0") if x)

    def call(self, **args):
        report = tool_report.new(); token = tool_report.attach(report)
        try:
            return agent._call_tool_with_ceiling("git", agent.TOOL_IMPL["git"], args), report
        finally:
            tool_report.detach(token)

    def test_array_stages_exactly_the_named_paths_with_spaces_and_unicode(self):
        for name in ("a b.txt", "a", "b.txt", "кир ✓.md"):
            (self.root / name).write_text(name)
        out, _ = self.call(repo="self", action="add", paths=["a b.txt", "кир ✓.md"])
        self.assertEqual(self.staged(), ["a b.txt", "кир ✓.md"])
        self.assertNotIn("старая строка", out)

    def test_array_diff_treats_a_dash_name_as_a_path(self):
        (self.root / "-weird.txt").write_text("one\n")
        subprocess.run(["git", "-C", str(self.root), "add", "--", "-weird.txt"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "w"], check=True)
        (self.root / "-weird.txt").write_text("two\n")
        out, _ = self.call(repo="self", action="diff", paths=["-weird.txt"])
        self.assertIn("-weird.txt", out)
        self.assertNotIn("⚠ код", out)

    def test_recorded_string_keeps_its_historical_whitespace_split(self):
        for name in ("a b.txt", "a", "b.txt"):
            (self.root / name).write_text(name)
        out, _ = self.call(repo="self", action="add", paths="a b.txt")
        self.assertEqual(self.staged(), ["a", "b.txt"])
        self.assertIn(json.dumps(["a", "b.txt"]), out)
        self.assertIn("старая строка", out)

    def test_empty_means_everything_as_before(self):
        (self.root / "new.txt").write_text("x")
        self.call(repo="self", action="add", paths=[])
        self.assertEqual(self.staged(), ["new.txt"])

    def test_unusable_items_are_refused_before_git_runs(self):
        with mock.patch.object(agent, "_git_run") as run:
            for bad in ([""], ["ok", 1], ["a\0b"], {"a": 1}):
                with self.subTest(paths=bad):
                    out, report = self.call(repo="self", action="add", paths=bad)
                    self.assertTrue(out.startswith("refused:"), out)
                    self.assertEqual(report["outcome"], "refused")
        run.assert_not_called()

    def test_schema_offers_only_an_array(self):
        spec = agent.GIT_TOOL["input_schema"]["properties"]["paths"]
        self.assertEqual((spec["type"], spec["items"]), ("array", {"type": "string"}))
        english = next(t for t in tool_text_en.apply([agent.GIT_TOOL]) if t["name"] == "git")
        text = english["input_schema"]["properties"]["paths"]["description"]
        self.assertIn("array item", text)
        self.assertFalse(any("Ѐ" <= c <= "ӿ" for c in text))
        self.assertNotIn("space-separated", json.dumps(english))


if __name__ == "__main__":
    unittest.main()
