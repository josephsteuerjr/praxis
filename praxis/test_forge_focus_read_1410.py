"""Bounded Forge read receipts and the real worker dispatch/model-delivery seam.

Run: python praxis_test.py test_forge_focus_read_1410 -v
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import forge
import forge_worker


class PagedRead(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.task = mock.patch.object(forge, "_task_root", return_value=(
            {"id": "code-read", "root": str(self.root), "scope": "self"}, self.root, ""))
        self.task.start()
        self.addCleanup(self.task.stop)

    def read(self, **kwargs):
        return forge_worker._dispatch({"task_id": "code-read", "role": "scout"},
                                      "inspect", {"action": "read", "path": "source.txt", **kwargs})

    def test_small_legacy_read_unchanged(self):
        raw = b"alpha\nbeta\n"
        (self.root / "source.txt").write_bytes(raw)
        out = self.read()
        self.assertEqual(out, f"source.txt · sha256={hashlib.sha256(raw).hexdigest()[:16]}\n    1\talpha\n    2\tbeta")

    def test_full_page_sha_binds_edit_and_legacy_prefix_still_reads(self):
        target = self.root / "source.txt"
        target.write_text("one\ntwo\n", encoding="utf-8")
        full = hashlib.sha256(target.read_bytes()).hexdigest()
        page = json.loads(self.read(start=1, expected_sha256=full))
        self.assertEqual(page["sha256"], full)
        with mock.patch.object(forge, "_event"):
            good = forge._edit_unlocked("code-read", "replace", path="source.txt",
                                        old="two", new="three", expected_sha256=full)
            stale = forge._edit_unlocked("code-read", "replace", path="source.txt",
                                         old="three", new="four", expected_sha256=full)
            legacy = forge._edit_unlocked("code-read", "replace", path="source.txt",
                                          old="three", new="four",
                                          expected_sha256=hashlib.sha256(target.read_bytes()).hexdigest()[:16])
        self.assertIn("sha256", good)
        self.assertIn("Конфликт версии", stale)
        self.assertIn("sha256", legacy)
        self.assertEqual(target.read_text(encoding="utf-8"), "one\nfour\n")

    def test_patch_does_not_silently_ignore_a_one_file_expected_hash(self):
        target = self.root / "source.txt"
        target.write_text("one\n", encoding="utf-8")
        original = target.read_bytes()
        reply = forge._edit_unlocked("code-read", "patch", patch="not a diff",
                                     expected_sha256=hashlib.sha256(original).hexdigest())
        self.assertIn("Patch не применён", reply)
        self.assertEqual(target.read_bytes(), original)

    def test_many_lines_round_trip_and_revision_conflict(self):
        raw = ("🇷🇺 Ελληνικά 🐍\r\n" * 600).encode("utf-8")
        target = self.root / "source.txt"
        target.write_bytes(raw)
        cursor = {"start": 1, "start_char": 0}
        seen, parts = set(), []
        for _ in range(15):
            page_text = self.read(**cursor)
            self.assertLessEqual(len(page_text), 8000)
            self.assertEqual(forge_worker._model_tool_result(page_text), page_text)
            page = json.loads(page_text)
            self.assertEqual(page["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(page["total_lines"], 600)
            parts.append(page["text"])
            if page["complete"]:
                self.assertFalse(page["omitted_after"])
                break
            self.assertTrue(page["omitted_after"])
            next_cursor = (page["next_start"], page["next_char"])
            self.assertNotIn(next_cursor, seen)
            seen.add(next_cursor)
            cursor = {"start": next_cursor[0], "start_char": next_cursor[1],
                      "expected_sha256": page["sha256"]}
        else:
            self.fail("cursor failed to reach EOF")
        self.assertEqual("".join(parts).encode("utf-8"), raw)
        target.write_text("changed", encoding="utf-8")
        self.assertIn("Конфликт версии", self.read(**cursor))

    def test_one_extremely_long_unicode_line_and_quoted_content(self):
        raw = ('\"\\🐍\t' * 5000 + '\r\nend\n').encode("utf-8")
        (self.root / "source.txt").write_bytes(raw)
        cursor = {"start": 1}
        segments = []
        for _ in range(30):
            page_text = self.read(**cursor)
            self.assertLessEqual(len(page_text), 8000)
            page = json.loads(page_text)
            segments.append(page["text"])
            if page["complete"]:
                break
            cursor = {"start": page["next_start"], "start_char": page["next_char"],
                      "expected_sha256": page["sha256"]}
        else:
            self.fail("oversized line could not be paged")
        self.assertEqual("".join(segments).encode("utf-8"), raw)
        self.assertIn("expected_sha256", str(forge_worker.INSPECT_TOOL["input_schema"]))
        self.assertIn("нужны start_char", self.read(start_char=10))

    def test_restricted_range_discloses_omission(self):
        (self.root / "source.txt").write_bytes(b"one\ntwo\nthree\n")
        sha = hashlib.sha256((self.root / "source.txt").read_bytes()).hexdigest()
        page = json.loads(self.read(start=2, end=2, expected_sha256=sha))
        self.assertEqual(page["text"], "two\n")
        self.assertTrue(page["omitted_before"])
        self.assertTrue(page["omitted_after"])
        self.assertFalse(page["complete"])
        self.assertEqual(page["next_start"], 3)

    def test_non_utf8_large_read_refuses_lossy_paging(self):
        (self.root / "source.txt").write_bytes((b"bad:\xff" * 40 + b"\n") * 3000)
        out = self.read()
        self.assertIn("requires valid UTF-8", out)
        self.assertIn("No lossy page was delivered", out)
        self.assertIn(hashlib.sha256((self.root / "source.txt").read_bytes()).hexdigest(), out)

    def test_worker_model_receives_page_not_silent_cap(self):
        (self.root / "source.txt").write_text("🍏" * 15000, encoding="utf-8")
        captured = []

        def chat(_request, *, messages, **_kwargs):
            captured.append(messages)
            if len(captured) == 1:
                return SimpleNamespace(model="fake", stop_reason="tool_use", text="",
                                       blocks=[{"type": "tool_use", "id": "read-1", "name": "inspect",
                                                "input": {"action": "read", "path": "source.txt"}}])
            return SimpleNamespace(model="fake", stop_reason="end_turn", text="done", blocks=[])

        req = {"task_id": "code-read", "role": "scout", "id": "agent-test", "brief": "read", "max_iters": 2}
        with mock.patch.object(forge_worker, "_system", return_value="system"), \
             mock.patch.object(forge_worker, "_chat_with_transport_retry", side_effect=chat), \
             mock.patch.object(forge_worker.forge, "_event"), \
             mock.patch.object(forge_worker.forge, "emit_unit_event"):
            self.assertEqual(forge_worker._run_request(self.root / "request.json", req), 0)
        delivered = captured[1][-1]["content"][0]["content"]
        page = json.loads(delivered)
        self.assertLessEqual(len(delivered), 8000)
        self.assertTrue(page["omitted_after"])
        self.assertEqual(page["next_start"], 1)
        self.assertGreater(page["next_char"], 0)

    def test_other_large_tool_reply_has_explicit_warning(self):
        result = forge_worker._model_tool_result("x" * 20000)
        self.assertLessEqual(len(result), 12000)
        self.assertIn("TOOL RESULT OMITTED", result)
        self.assertIn("NOT a complete result", result)
        omitted = int(re.search(r"OMITTED: (\d+) characters", result).group(1))
        self.assertEqual(omitted, 20000 - result.count("x"))

    def test_small_explicit_page_and_invalid_offsets(self):
        raw = b"alpha\nbeta\n"
        (self.root / "source.txt").write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        page = json.loads(self.read(expected_sha256=digest))
        self.assertEqual(page["text"].encode("utf-8"), raw)
        self.assertTrue(page["complete"])
        self.assertIn("Конфликт версии", self.read(expected_sha256=digest[:16]))
        self.assertIn("start_char >= 0", self.read(start_char=-1))
        self.assertIn("вне строки", self.read(start_char=100, expected_sha256=digest))
        self.assertIn("вне файла", self.read(start=99, start_char=1, expected_sha256=digest))

    def test_remote_refuses_cursor_or_revision_without_reading(self):
        remote = mock.Mock()
        with mock.patch.object(forge, "_remote_client", return_value=remote):
            for cursor in ({"start_char": 1}, {"expected_sha256": "a" * 64}):
                out = self.read(**cursor)
                self.assertIn("does not support paged reads", out)
                self.assertIn("No read was performed", out)
            remote.workspace_inspect.assert_not_called()

    def test_remote_legacy_read_still_forwards(self):
        remote = mock.Mock()
        remote.workspace_inspect.return_value = "legacy remote result"
        with mock.patch.object(forge, "_remote_client", return_value=remote), \
             mock.patch.object(forge, "_remote_observations"):
            self.assertEqual(self.read(start=2, end=3), "legacy remote result")
        remote.workspace_inspect.assert_called_once_with(
            str(self.root), "read", path="source.txt", query="", glob="**/*",
            start=2, end=3, base="")
