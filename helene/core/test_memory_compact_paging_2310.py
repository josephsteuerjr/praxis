"""Complete compact pages must survive the durable 4000-character model view."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import memory_life as ml
import tool_text_en
from run_context import RunContext
from run_manager import RunManager
from test_coverage_vs_current import Base, ROOM


COMPACT = "cmp-20261023T000000000000Z-abcdef12"
META = {"id": COMPACT, "tier": 1, "first_ts": "2026-10-22T00:00:00Z",
        "last_ts": "2026-10-23T00:00:00Z", "events": 3}


def unpack(response):
    header, body = response.split("\n\n", 1)
    _, metadata = header.split("\n", 1)
    return json.loads(metadata), body


def read(**kwargs):
    return agent.tool_memory_compact("read", place=ROOM, compact_id=COMPACT, **kwargs)


class PageAssertions:
    def collect(self, compact_id=COMPACT, manager=None, context=None):
        offset, pinned, pieces = 0, "", []
        for index in range(1000):
            response = agent.tool_memory_compact(
                "read", place=ROOM, compact_id=compact_id,
                start_char=offset, expected_sha256=pinned)
            self.assertLessEqual(len(response), 4000)
            if manager is not None:
                ref = manager.store_result(context.run_id, response,
                                           call_id=f"compact-page-{index}", inline_chars=4000)
                self.assertFalse(ref["inline"]["truncated"])
                visible = agent._result_for_model(ref)
                self.assertTrue(visible.startswith(response + "\n[ResultRef "))
                self.assertNotIn("chars externalized", visible)
            page, body = unpack(response)
            self.assertTrue(page["ok"])
            self.assertEqual(page["compact_id"], compact_id)
            self.assertEqual(page["start_char"], offset)
            self.assertEqual(page["end_char"], offset + len(body))
            if pinned:
                self.assertEqual(page["sha256"], pinned)
            pinned = page["sha256"]
            pieces.append(body)
            if page["eof"]:
                self.assertIsNone(page["next_start_char"])
                result = "".join(pieces)
                self.assertEqual(len(result), page["total_chars"])
                self.assertEqual(hashlib.sha256(result.encode("utf-8")).hexdigest(), pinned)
                return result, pieces
            self.assertEqual(page["next_start_char"], offset + len(body))
            self.assertGreater(page["next_start_char"], offset)
            offset = page["next_start_char"]
        self.fail("compact pagination did not terminate")


class CompactReadPages(PageAssertions, unittest.TestCase):
    def setUp(self):
        self.source = ""
        patcher = mock.patch.object(ml, "read_compact", side_effect=lambda *_: {
            "ok": True, "meta": dict(META), "text": self.source})
        self.backend = patcher.start()
        self.addCleanup(patcher.stop)

    def test_long_unicode_single_line_and_whitespace_tail_survive_model_store(self):
        # More than the former 12000 cap, no line boundary to hide cursor errors.
        self.source = ('Я🐺漢字e\u0301"\\\u2028\u0085' * 2400) + "\n\nХВОСТ\t  \n \t"
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(Path(directory))
            context = manager.create(RunContext.create(
                kind="computer", goal="read complete synthetic compact",
                principal_id="telegram:1", scope="owner"), "# Synthetic read\n")
            result, pieces = self.collect(manager=manager, context=context)
        self.assertGreater(len(pieces), 3)
        self.assertEqual(result, self.source)

    def test_first_page_is_complete_in_the_actual_inline_view(self):
        self.source = "середина🐺" * 2000 + "КОНЕЦ"
        # Old signatures support this call, but their 12000-character answer loses
        # its middle at the real 4000-character durable result boundary.
        response = read()
        self.assertLessEqual(len(response), 4000)
        inline = RunManager._inline_text(response, 4000)
        self.assertFalse(inline["truncated"])
        page, body = unpack(response)
        self.assertEqual(body, self.source[:page["next_start_char"]])
        self.assertFalse(page["eof"])

    def test_budget_edges_and_empty_text_reconstruct_exactly(self):
        for size in (0, 1, 3500, 3600, 3999, 4000, 4001, 9999, 10000, 12000, 12001):
            with self.subTest(size=size):
                self.source = "Ж" * size + (" \n\t" if size else "")
                result, _ = self.collect()
                self.assertEqual(result, self.source)

    def test_same_length_rewrite_rejects_continuation_and_can_restart(self):
        self.source = "А" * 15000
        page, _ = unpack(read())
        self.source = "Б" * len(self.source)
        out = json.loads(read(start_char=page["next_start_char"], expected_sha256=page["sha256"]))
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "version_conflict")
        self.assertNotEqual(out["sha256"], page["sha256"])
        self.assertNotIn("text", out)
        result, _ = self.collect()
        self.assertEqual(result, self.source)

    def test_continuation_requires_hash_before_backend_read(self):
        self.assertEqual(json.loads(read(start_char=1))["reason"], "expected_sha256_required")
        self.backend.assert_not_called()

    def test_invalid_offsets_and_hashes_fail_before_backend_read(self):
        for offset in (-1, True, False, 1.5, "2", None):
            with self.subTest(offset=offset):
                self.assertEqual(json.loads(read(start_char=offset))["reason"], "invalid_start_char")
        for digest in ("no", "a" * 63, "a" * 65, "z" * 64):
            with self.subTest(digest=digest):
                self.assertEqual(json.loads(read(expected_sha256=digest))["reason"], "invalid_expected_sha256")
        self.backend.assert_not_called()

    def test_exact_eof_and_out_of_range(self):
        self.source = "хвост🐺 \n"
        digest = hashlib.sha256(self.source.encode()).hexdigest()
        page, body = unpack(read(start_char=len(self.source), expected_sha256=digest.upper()))
        self.assertEqual(body, "")
        self.assertTrue(page["eof"])
        self.assertIsNone(page["next_start_char"])
        out = json.loads(read(start_char=len(self.source) + 1, expected_sha256=digest))
        self.assertEqual(out["reason"], "start_char_out_of_range")
        self.assertEqual(out["total_chars"], len(self.source))

    def test_short_legacy_call_keeps_header_and_complete_body(self):
        self.source = "Я сама записала.\n  "
        response = agent.tool_memory_compact("read", ROOM, COMPACT)
        page, body = unpack(response)
        self.assertTrue(response.startswith(COMPACT + " · tier 1 · "))
        self.assertEqual(body, self.source)
        self.assertTrue(page["eof"])
        self.backend.assert_called_once_with(COMPACT, ROOM)

    def test_missing_unknown_and_unreadable_compacts_keep_existing_responses(self):
        self.assertEqual(agent.tool_memory_compact("read", place=ROOM), "Для read нужен compact_id.")
        for reason in ("unknown_compact", "compact_unreadable"):
            self.backend.side_effect = None
            self.backend.return_value = {"ok": False, "reason": reason}
            response = read()
            self.assertIn(COMPACT, response)
            self.assertIn(reason, response)

    def test_paging_cannot_mutate_a_compact(self):
        with mock.patch.object(ml, "rewrite_compact_text") as rewrite:
            out = agent.tool_memory_compact("rewrite", ROOM, COMPACT, "replacement", start_char=1)
            self.assertEqual(json.loads(out)["reason"], "paging_only_for_read")
            rewrite.assert_not_called()
        with mock.patch.object(ml, "prepare_authored_fold", return_value={"ok": True}) as prepare:
            self.assertEqual(json.loads(agent.tool_memory_compact("prepare", ROOM)), {"ok": True})
            prepare.assert_called_once_with(ROOM, limit=50, compact_id="")

    def test_schema_and_english_description_explain_the_same_cursor(self):
        tool = agent.MEMORY_COMPACT_TOOL
        props = tool["input_schema"]["properties"]
        self.assertEqual(props["start_char"]["type"], "integer")
        self.assertEqual(props["start_char"]["minimum"], 0)
        self.assertEqual(props["expected_sha256"]["type"], "string")
        self.assertEqual(tool["input_schema"]["required"], ["action"])
        for language in (tool, tool_text_en.apply([tool])[0]):
            for term in ("next_start_char", "expected_sha256", "eof", "version_conflict"):
                self.assertIn(term, language["description"])
        coverage = tool_text_en.coverage([tool])
        for field in ("ru_descriptions", "undescribed_params", "stale_translations"):
            self.assertEqual(coverage[field], [])


class CanonicalCompactRead(PageAssertions, Base):
    def test_real_compact_pages_preserve_canonical_bytes_and_sources(self):
        meta = self._compact([self._msg(1, "источник"), self._msg(2, "продолжение")],
                             summary=("Моя память🐺漢字\n" * 1800) + "последние слова")
        canonical = ml.BASE / meta["path"]
        before = canonical.read_bytes()
        expected = ml.read_compact(meta["id"], ROOM)["text"]
        result, pieces = self.collect(meta["id"])
        self.assertGreater(len(pieces), 3)
        self.assertEqual(result, expected)
        self.assertEqual(canonical.read_bytes(), before)

    def test_canonical_rewrite_invalidates_old_cursor(self):
        meta = self._compact([self._msg(1, "источник")], summary="старое " * 2200)
        first, _ = unpack(agent.tool_memory_compact("read", ROOM, meta["id"]))
        changed = ml.rewrite_compact_text(meta["id"], "новое " * 2200, chat_id=ROOM)
        self.assertTrue(changed["ok"], changed)
        out = json.loads(agent.tool_memory_compact(
            "read", ROOM, meta["id"], start_char=first["next_start_char"],
            expected_sha256=first["sha256"]))
        self.assertEqual(out["reason"], "version_conflict")
        result, _ = self.collect(meta["id"])
        self.assertEqual(result, ml.read_compact(meta["id"], ROOM)["text"])
        self.assertTrue((ml.BASE / changed["history"]).exists())


if __name__ == "__main__":
    unittest.main()
