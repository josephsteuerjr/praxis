"""Правдивость предмета саморевью; без LLM, процессов и живой памяти."""
import _sandbox  # noqa: F401
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import immune
import selfdev


def git_result(code=0, text=""):
    return subprocess.CompletedProcess([], code, stdout=text, stderr="")


class ReviewIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name)
        for name, value in (("STATE_DIR", state), ("QUEUE_PATH", state / "queue.json")):
            p = patch.object(immune, name, value)
            p.start()
            self.addCleanup(p.stop)
        immune.enqueue("abc", "проверка")

    def test_failed_git_reads_remain_queued_then_retry_once(self):
        for calls in ([git_result(128)], [git_result(text="Praxis\n"), git_result(128)]):
            with self.subTest(calls=len(calls)), patch.object(immune, "_git", side_effect=calls), \
                    patch.object(immune, "review") as reviewer, patch.object(immune, "_journal") as journal:
                self.assertEqual(immune.process_queue(), 0)
                reviewer.assert_not_called()
                journal.assert_not_called()
                self.assertEqual(immune._load_json(immune.QUEUE_PATH, [])[0]["sha"], "abc")
        with patch.object(immune, "_git", side_effect=[git_result(text="Praxis\n"), git_result()]), \
                patch.object(immune, "_journal") as journal:
            self.assertEqual(immune.process_queue(), 1)
            self.assertEqual(immune.process_queue(), 0)
            self.assertIn("пустой дифф", journal.call_args.args[0])

    def test_foreign_commit_still_skipped(self):
        with patch.object(immune, "_git", return_value=git_result(text="Другой\n")), \
                patch.object(immune, "review") as reviewer:
            self.assertEqual(immune.process_queue(), 0)
            self.assertEqual(immune._load_json(immune.QUEUE_PATH, []), [])
            reviewer.assert_not_called()

    def test_limits_precede_evaluator_and_boundary_is_complete(self):
        with patch.object(immune.llm, "configured", return_value=True), \
                patch.object(immune.llm, "chat", return_value=SimpleNamespace(text="ok")) as call, \
                patch.object(immune, "second_opinion_enabled", return_value=False):
            for text in ("x" * (immune.MAX_DIFF_CHARS + 1), "x\n" * (immune.MAX_DIFF + 1)):
                self.assertEqual(immune.review(text)[0], "warn")
            call.assert_not_called()
            text = "x" * (immune.MAX_DIFF_CHARS - 4) + "TAIL"
            self.assertEqual(immune.review(text)[0], "ok")
            self.assertIn(text, call.call_args.kwargs["messages"][0]["content"])

    def test_full_diff_and_card_are_distinct(self):
        text = "x" * 13000 + "TAIL"
        with patch.object(selfdev, "get", return_value={"branch": "proposal"}), \
                patch.object(selfdev, "_main_branch", return_value="main"), \
                patch.object(selfdev, "worktree_path", return_value=Path(self.tmp.name) / "absent"), \
                patch.object(selfdev, "_git", return_value=git_result(text=text)):
            self.assertEqual(selfdev.diff_text("p", cap=None), text)
            self.assertTrue(selfdev.diff_text("p").endswith("(обрезано)"))
            self.assertNotIn("TAIL", selfdev.diff_text("p"))

    def test_unavailable_full_diff_is_not_empty_success(self):
        with patch.object(selfdev, "get", return_value={"branch": "proposal"}), \
                patch.object(selfdev, "_main_branch", return_value="main"), \
                patch.object(selfdev, "worktree_path", return_value=Path(self.tmp.name) / "absent"), \
                patch.object(selfdev, "_git", return_value=git_result(128)):
            with self.assertRaises(RuntimeError):
                selfdev.diff_text("p", cap=None)

    def test_apply_passes_full_diff_to_reviewer(self):
        self._apply_full_diff()

    def test_existing_worktree_git_failures_are_visible(self):
        for failure in range(3):
            results = [git_result(), git_result(text="base"), git_result(text="diff")]
            results[failure] = git_result(128)
            with self.subTest(command=failure), \
                    patch.object(selfdev, "get", return_value={"branch": "proposal"}), \
                    patch.object(selfdev, "_main_branch", return_value="main"), \
                    patch.object(selfdev, "worktree_path", return_value=Path(self.tmp.name)), \
                    patch.object(selfdev, "_git", side_effect=results):
                with self.assertRaises(RuntimeError):
                    selfdev.diff_text("p", cap=None)

    def _apply_full_diff(self):
        proposal = {"status": "proposed", "branch": "proposal", "title": "проверка"}
        text = "x" * 13000 + "TAIL"
        # Мёрж намеренно не исполняется: проверяем реальную границу потребителя.
        with patch.object(selfdev, "get", return_value=proposal), \
                patch.object(selfdev, "diff_text", return_value=text) as diff, \
                patch.object(immune, "review", return_value=("warn", "проверка")) as review, \
                patch.object(selfdev, "_update"), patch.object(selfdev, "_journal"), \
                patch.object(selfdev, "_git", return_value=git_result(1)):
            self.assertFalse(selfdev.apply("p", by="auto")["ok"])
            diff.assert_called_once_with("p", cap=None)
            self.assertEqual(review.call_args.args[0], text)


if __name__ == "__main__":
    unittest.main()
