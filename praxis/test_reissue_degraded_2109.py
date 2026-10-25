"""Legacy repair requires the acting author; authored rewrites retain provenance.

Reissue/refold no longer generate a replacement or delete an old compact.
An explicit rewrite changes the recap in place, retaining its ID, metadata,
source coverage and old text in history. Rewriting a degraded compact does
not silently certify it as non-degraded.

Run: python praxis_test.py test_reissue_degraded_2109 -v
"""

from __future__ import annotations

import unittest
from unittest import mock

import llm
import memory_life as ml
import memory_provenance
from test_coverage_vs_current import Base, ROOM


class AuthoredRepair(Base):
    def _rows(self, count: int = 4) -> list[dict]:
        return [self._msg(mid, "реплика %d" % mid) for mid in range(1, count + 1)]

    def _stump(self, rows, *, chat: str = ROOM) -> dict:
        # Historical fixture only: this is not a production fallback invocation.
        result = ml._fallback_compact(rows, continued=False)
        return ml._write_compact(
            chat, result, tier=1, depth=1,
            source_events=[row["id"] for row in rows],
            source_compacts=[], event_count=len(rows), continued=False,
            first_ts=min(str(row["ts"]) for row in rows),
            last_ts=max(str(row["ts"]) for row in rows),
        )

    def _parent(self, child: dict) -> dict:
        return ml._write_compact(
            ROOM,
            {"summary": "родитель", "open_threads": [], "claims": [], "episodes": []},
            tier=2, depth=2,
            source_events=[], source_compacts=[child["id"]],
            event_count=child["event_count"], continued=False,
            first_ts=child["first_ts"], last_ts=child["last_ts"],
        )

    def _files(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.tmp).as_posix(): path.read_bytes()
            for path in self.tmp.rglob("*") if path.is_file()
        }

    def _assert_legacy_refused(self, compact_id: str) -> None:
        before = self._files()
        with mock.patch.object(llm, "configured") as configured, \
                mock.patch.object(llm, "chat") as chat, \
                mock.patch.object(ml, "_model_compact") as model, \
                mock.patch.object(ml, "_write_compact") as write_compact, \
                mock.patch.object(ml, "rewrite_compact_text") as rewrite, \
                mock.patch.object(ml, "append_event") as append_event:
            calls = [
                ("reissue-default", lambda: ml.reissue_degraded_compact(ROOM, compact_id)),
                ("reissue-foreign-self", lambda: ml.reissue_degraded_compact(
                    ROOM, compact_id, why="foreign_self")),
                ("reissue-unknown-reason", lambda: ml.reissue_degraded_compact(
                    ROOM, compact_id, why="because")),
                ("refold-one", lambda: ml.refold_one(ROOM, compact_id)),
            ]
            for name, call in calls:
                with self.subTest(api=name, compact_id=compact_id):
                    out = call()
                    self.assertFalse(out["ok"], out)
                    self.assertEqual(out["reason"], "acting_author_required")
                    self.assertNotIn("new_id", out)

            configured.assert_not_called()
            chat.assert_not_called()
            model.assert_not_called()
            write_compact.assert_not_called()
            rewrite.assert_not_called()
            append_event.assert_not_called()
        self.assertEqual(self._files(), before, "legacy repair changed stored files")

    def _assert_authored_rewrite(self, meta: dict) -> None:
        compact_id = meta["id"]
        path = ml.BASE / meta["path"]
        before_bytes = path.read_bytes()
        before_text = before_bytes.decode("utf-8")
        before_meta = ml.read_artifact_meta(path)
        before_index = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        before_ids = set(before_index["compacts"])
        before_coverage = memory_provenance.compact_coverage(compact_id, before_index)
        self.assertTrue(before_coverage["valid"], before_coverage)
        before_hot = [row["id"] for row in ml.rebuild_state(ROOM).get("hot", [])]

        summary = "Я записала своими словами: Николай оставил четыре реплики."
        with mock.patch.object(llm, "chat") as chat, \
                mock.patch.object(ml, "_model_compact") as model:
            out = ml.rewrite_compact_text(
                compact_id, summary, chat_id=ROOM,
                open_threads=["дождаться ответа"], why="test_authored_repair",
            )
            chat.assert_not_called()
            model.assert_not_called()

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["id"], compact_id)
        self.assertTrue(path.exists(), "rewrite deleted the original compact")
        after_text = path.read_text(encoding="utf-8")
        self.assertEqual(
            after_text.split("## Суть\n", 1)[0],
            before_text.split("## Суть\n", 1)[0],
            "ID/header/preamble changed",
        )
        self.assertEqual(ml.read_artifact_meta(path), before_meta)
        self.assertEqual(ml.compact_text(compact_id, ROOM), summary)
        self.assertIn("- дождаться ответа", after_text)
        self.assertEqual(
            after_text.split("## Происхождение\n", 1)[1],
            before_text.split("## Происхождение\n", 1)[1],
        )

        history = ml.BASE / out["history"]
        self.assertTrue(history.is_file())
        self.assertEqual(history.read_bytes(), before_bytes)

        after_index = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        self.assertEqual(set(after_index["compacts"]), before_ids,
                         "rewrite/history introduced another canonical compact")
        self.assertEqual(after_index["compacts"][compact_id],
                         before_index["compacts"][compact_id])
        coverage = memory_provenance.compact_coverage(compact_id, after_index)
        self.assertTrue(coverage["valid"], coverage)
        self.assertEqual(coverage["leaves"], before_coverage["leaves"])
        self.assertEqual(
            [row["id"] for row in ml.rebuild_state(ROOM).get("hot", [])],
            before_hot,
            "rewrite returned covered events to hot memory",
        )
        receipts = [
            event for event in ml.iter_events(chat_id=ROOM, kinds={"memory_compact"})
            if event.get("meta", {}).get("compact_id") == compact_id
            and event.get("meta", {}).get("rewritten")
        ]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["meta"]["history"], out["history"])
        self.assertEqual(receipts[0]["meta"]["why"], "test_authored_repair")

    def test_legacy_repair_leaves_a_degraded_leaf_untouched(self):
        stump = self._stump(self._rows())
        self.assertTrue(stump["degraded"])
        self._assert_legacy_refused(stump["id"])

    def test_legacy_repair_leaves_a_parented_leaf_and_parent_untouched(self):
        stump = self._stump(self._rows())
        parent = self._parent(stump)
        self._assert_legacy_refused(stump["id"])
        self._assert_legacy_refused(parent["id"])

    def test_legacy_repair_leaves_a_healthy_compact_untouched(self):
        healthy = self._compact(self._rows())
        self._assert_legacy_refused(healthy["id"])

    def test_legacy_repair_refuses_even_an_unknown_compact_without_writing(self):
        self._assert_legacy_refused("cmp-20260921T000000000000Z-deadbeef")

    def test_author_rewrites_a_stump_without_falsely_clearing_degraded(self):
        stump = self._stump(self._rows())
        self.assertEqual(len(ml.rebuild_state(ROOM).get("hot", [])), 0)
        self._assert_authored_rewrite(stump)
        fresh = ml.compact_meta(stump["id"], ROOM)
        self.assertTrue(fresh["degraded"],
                        "rewriting prose must not silently change provenance metadata")
        self.assertTrue(ml.read_compact(stump["id"], ROOM)["meta"]["degraded"])

    def test_author_rewrite_preserves_existing_parent_coverage(self):
        stump = self._stump(self._rows())
        parent = self._parent(stump)
        parent_bytes = (ml.BASE / parent["path"]).read_bytes()
        self._assert_authored_rewrite(stump)
        self.assertEqual((ml.BASE / parent["path"]).read_bytes(), parent_bytes)
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        self.assertEqual(
            evidence["compacts"][parent["id"]]["source_compact_ids"],
            [stump["id"]],
        )
        self.assertTrue(
            memory_provenance.compact_coverage(parent["id"], evidence)["valid"]
        )

    def test_author_can_rewrite_a_tier_recap_without_changing_its_sources(self):
        child = self._compact(self._rows())
        parent = self._parent(child)
        self._assert_authored_rewrite(parent)
        self.assertEqual(
            ml.compact_meta(parent["id"], ROOM)["source_compact_ids"],
            [child["id"]],
        )


if __name__ == "__main__":
    unittest.main()
