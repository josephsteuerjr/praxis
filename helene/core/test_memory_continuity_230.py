"""Edition continuity regressions through real rebuild, fold and evidence readers."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import memory_life as ml
import memory_provenance as mp
import run_manager as rm
import run_retention as rr
from test_coverage_vs_current import Base, ROOM
from test_unprovable_events_2109 import event_row


class ResidualContinuity(Base):
    def seed(self):
        for i in range(1, 4):
            self._msg(i, f"canonical {i}")
        return ml._load_state(ROOM)

    def test_changed_bytes_same_event_id_survive_repeated_rebuild(self):
        state = self.seed()
        original = state["hot"][0]
        state["hot"][0] = dict(original, line="CHANGED EXACT BYTES \u00a0")
        ml._save_state(state)
        for _ in range(3):
            rebuilt = ml.rebuild_state(ROOM)
            residuals = [row for row in rebuilt["hot"] if row.get("state_residual")]
            self.assertEqual(len(residuals), 1)
            self.assertEqual(residuals[0]["line"], "CHANGED EXACT BYTES \u00a0")
            self.assertTrue(residuals[0]["unprovable"])
            selected = ml.prepare_authored_fold(ROOM, limit=1)
            self.assertEqual(selected["sources"][0]["line"], original["line"])
            self.assertFalse(selected["sources"][0].get("state_residual"))

    def test_fold_preserves_residual_sharing_selected_event_id(self):
        state = self.seed()
        state["hot"].append(dict(state["hot"][0], line="SAME ID DIFFERENT BYTES \u00a0"))
        ml._save_state(state)
        selected = ml.prepare_authored_fold(ROOM, limit=1)
        applied = ml.apply_authored_fold(ROOM, selected["selection"], "First source recap.")
        self.assertTrue(applied["ok"], applied)
        # Inspect immediately too: a later rebuild must not hide fold-time loss.
        for _ in range(3):
            current = ml._load_state(ROOM)
            self.assertEqual([row["line"] for row in current["hot"]
                              if row.get("state_residual")],
                             ["SAME ID DIFFERENT BYTES \u00a0"])
            ml.rebuild_state(ROOM)

    def test_unrelated_fold_preserves_state_only_hot_reader_content(self):
        state = self.seed()
        ghost = dict(state["hot"][0], id="ghost", source_id="999",
                     line="EXACT STATE ONLY BYTES \u00a0")
        state["hot"].append(ghost)
        ml._save_state(state)
        selected = ml.prepare_authored_fold(ROOM, limit=1)
        self.assertNotIn("ghost", [row["id"] for row in selected["sources"]])
        self.assertTrue(ml.apply_authored_fold(
            ROOM, selected["selection"], "Unrelated canonical source.")["ok"])
        ml.rebuild_state(ROOM)
        self.assertIn(ghost["line"], [row["line"] for row in ml.hot_records(ROOM)])

    def test_exact_covered_and_superseded_rows_do_not_resurrect(self):
        state = self.seed()
        original = dict(state["hot"][0])
        selected = ml.prepare_authored_fold(ROOM, limit=1)
        self.assertTrue(ml.apply_authored_fold(
            ROOM, selected["selection"], "Canonical first source.")["ok"])
        state = ml._load_state(ROOM)
        state["hot"].append(original)
        ml._save_state(state)
        rebuilt = ml.rebuild_state(ROOM)
        self.assertNotIn(original["id"], [row["id"] for row in rebuilt["hot"]])
        old = dict(rebuilt["hot"][0])
        self._edit(2, "new revision")
        state = ml._load_state(ROOM)
        state["hot"].append(old)
        ml._save_state(state)
        self.assertNotIn(old["id"], [row["id"] for row in ml.rebuild_state(ROOM)["hot"]])

    def test_changed_provenance_is_not_covered_by_event_id(self):
        state = self.seed()
        changed = dict(state["hot"][0], actor="Another actor", source="local_import")
        state["hot"].extend([changed, dict(changed)])
        ml._save_state(state)
        for _ in range(3):
            rows = [row for row in ml.rebuild_state(ROOM)["hot"]
                    if row.get("state_residual")]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["actor"], "Another actor")
            self.assertEqual(rows[0]["source"], "local_import")
            self.assertTrue(rows[0]["unprovable"])


class ConversationContent(unittest.TestCase):
    def test_whitespace_is_exact_content_for_index_and_written_journal(self):
        raw = " first chunk \u00a0\n"
        row = event_row(raw)
        self.assertTrue(mp.event_row_indexable(row))
        self.assertEqual(row["text"], raw)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "life" / "events"
            events.mkdir(parents=True)
            (events / "2026-09-21.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            index = mp.claim_evidence_index(root)
            self.assertEqual(index["events"][row["id"]]["text"], raw)
            self.assertIn(row["id"], index["current_event_ids"])

    def test_identifier_actor_other_kinds_blank_and_limit_remain_strict(self):
        for field, value in (("actor", " Dmitry"), ("chat_id", " -100370"),
                             ("source_id", " 34828"), ("id", "bad"),
                             ("text", " \n\u00a0"), ("text", "x" * 20001)):
            with self.subTest(field=field, value=repr(value)[:60]):
                self.assertFalse(mp.event_row_indexable(dict(event_row("valid"), **{field: value})))
        self.assertFalse(mp.event_row_indexable(dict(event_row(" valid "), kind="run_episode")))


@unittest.skipUnless(rr._SAFE_OPENAT_SUPPORTED, "POSIX no-follow descriptor semantics")
class OpenEvidenceReplacement(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "manifest.json"
        self.path.write_text('{"generation": "old"}', encoding="utf-8")

    def test_atomic_replacement_keeps_opened_regular_manifest_readable(self):
        original_open = rr.os.open
        observed = []
        replaced = False

        def open_then_replace(path, flags, *args, **kwargs):
            nonlocal replaced
            descriptor = original_open(path, flags, *args, **kwargs)
            if path == self.path.name and not replaced:
                replaced = True
                replacement = self.path.with_suffix(".next")
                replacement.write_text('{"generation": "new"}', encoding="utf-8")
                os.replace(replacement, self.path)
                observed.append(os.fstat(descriptor).st_nlink)
            return descriptor

        with mock.patch.object(rr.os, "open", side_effect=open_then_replace):
            # Real consumer chain: strict JSON -> read_evidence_bytes -> safe FD.
            result = rm._read_json_strict(self.path)
        self.assertEqual(observed, [0], "reproduce the unlink, not an invented stat")
        self.assertEqual(result, {"generation": "old"})
        self.assertEqual(json.loads(self.path.read_text()), {"generation": "new"})

    def test_hardlink_symlink_and_fifo_remain_refused(self):
        twin = self.root / "twin.json"
        os.link(self.path, twin)
        with self.assertRaises(rr.ContractError):
            rr.read_evidence_bytes(self.path)
        twin.unlink()
        symlink = self.root / "link.json"
        symlink.symlink_to(self.path)
        with self.assertRaises(rr.ContractError):
            rr.read_evidence_bytes(symlink)
        fifo = self.root / "pipe.json"
        os.mkfifo(fifo)
        with self.assertRaises(rr.ContractError):
            rr.read_evidence_bytes(fifo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
