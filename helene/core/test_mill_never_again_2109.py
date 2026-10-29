"""Explicitly authored folds must progress without replaying covered sources.

The edition does not generate recaps from compact_if_due. Each successful fold
below selects real persisted sources and applies test-authored text. Provenance,
hot-ring movement, ghost retention, and replay rejection remain real consumers.

Run using the edition's composed test environment so memory_life resolves to
helene/core and shared provenance modules remain available.
"""

from __future__ import annotations

import unittest
from unittest import mock

import memory_life as ml
import memory_provenance
from test_coverage_vs_current import Base, ROOM


NBSP = "\u00a0"


class TheSameBlockIsNeverFoldedTwice(Base):
    def setUp(self):
        super().setUp()
        self.calls: list[tuple[str, ...]] = []
        self.kinds: list[str] = []
        self._automatic = mock.patch.object(
            ml, "_model_compact",
            side_effect=AssertionError("automatic compact generation forbidden"),
        )
        self._automatic.start()
        self.addCleanup(self._automatic.stop)

    def _fill(self, count: int, *, first: int = 1) -> None:
        for mid in range(first, first + count):
            self._msg(mid, "реплика %d" % mid)

    def _ghost(self, mid: int) -> str:
        """Historical whitespace-bearing row invisible to the evidence index."""
        row = {
            "schema": "praxis.life.event.v1",
            "id": ml._id("evt", float(mid)),
            "ts": ml._utc_iso(float(mid)),
            "kind": "conversation_message",
            "stream": ml._safe(ROOM),
            "chat_id": ROOM,
            "actor": " Николай ",
            "direction": "in",
            "text": "Николай: хвост с пробелом" + NBSP + " ",
            "source": "telegram",
            "source_id": str(mid),
            "salience": 2,
            "refs": [],
            "meta": {"is_dm": False},
            "dedupe_key": "telegram:%s:%d:in" % (ROOM, mid),
        }
        ml._append_record(row)
        return row["id"]

    def _fold(self):
        selected = ml.prepare_authored_fold(ROOM, limit=100)
        self.assertTrue(selected.get("ok"), selected)
        sources = selected.get("sources") or []
        if not sources:
            self.assertEqual(selected.get("reason"), "nothing_to_fold", selected)
            return {"ok": True, "folded": 0}

        self.assertTrue(selected.get("shown_complete"), selected)
        ids = tuple(str(row["id"]) for row in sources)
        self.assertEqual(len(ids), len(set(ids)), selected)
        self.calls.append(ids)
        self.kinds.append(selected["kind"])
        text = "Я сохраняю выбранные источники прохода %d." % len(self.calls)
        out = ml.apply_authored_fold(ROOM, selected["selection"], text)
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("compact_id"), out)
        self.assertEqual(out.get("source_ids"), list(ids), out)
        self.assertEqual(int(out.get("folded") or 0), len(ids), out)
        self.assertEqual(ml.compact_text(out["compact_id"], ROOM), text)
        return out

    def _assert_accepted(self, compact_id):
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        resolved = memory_provenance.compact_coverage(compact_id, evidence)
        self.assertTrue(resolved["valid"], resolved)
        return resolved

    def test_the_second_pass_never_repeats_the_first(self):
        self._fill(12)
        first = self._fold()
        self.assertGreater(int(first.get("folded") or 0), 0, first)
        second = self._fold()
        self.assertGreater(int(second.get("folded") or 0), 0, second)
        self.assertNotEqual(first["compact_id"], second["compact_id"])
        self.assertEqual(self.kinds, ["hot", "hot"])
        self.assertFalse(
            set(self.calls[0]) & set(self.calls[1]),
            "второй проход предложил автору те же события",
        )
        self._assert_accepted(first["compact_id"])
        self._assert_accepted(second["compact_id"])

    def test_the_folded_events_leave_the_hot_ring(self):
        self._fill(12)
        before = len(ml._load_state(ROOM, rebuild=True).get("hot") or [])
        result = self._fold()
        self.assertEqual(self.kinds, ["hot"])
        after = len(ml._load_state(ROOM, rebuild=True).get("hot") or [])
        self.assertEqual(
            after, before - int(result["folded"]), "кольцо не сдвинулось",
        )

    def test_the_written_compact_is_accepted_by_its_own_resolution(self):
        self._fill(12)
        result = self._fold()
        self._assert_accepted(result["compact_id"])

    def test_a_row_the_index_cannot_see_does_not_stop_the_ring(self):
        ghost = self._ghost(1)
        self._fill(11, first=2)
        state = ml._load_state(ROOM, rebuild=True)
        marked = [row["id"] for row in state["hot"] if row.get("unprovable")]
        self.assertEqual(marked, [ghost], "недоказуемая строка пропала из ленты")

        first = self._fold()
        self.assertGreater(int(first.get("folded") or 0), 0, first)
        self.assertEqual(self.kinds, ["hot"])
        self.assertNotIn(ghost, self.calls[0], "призрак поехал в свёртку")
        resolved = self._assert_accepted(first["compact_id"])
        self.assertNotIn(ghost, resolved["leaves"])

        rest = ml._load_state(ROOM, rebuild=True)
        self.assertEqual(
            [row["id"] for row in rest["hot"] if row.get("unprovable")],
            [ghost], "призрак обязан остаться в ленте",
        )
        canon, _ = ml._canonical_compact_graph(ROOM)
        self.assertIn(first["compact_id"], canon)

    def test_the_ring_drains_to_the_end_even_with_a_ghost_at_the_head(self):
        ghost = self._ghost(1)
        self._fill(11, first=2)
        seen = set()
        selected_hot = set()
        for _ in range(20):
            out = self._fold()
            if not int(out.get("folded") or 0):
                break
            self.assertNotIn(out["compact_id"], seen)
            seen.add(out["compact_id"])
            self._assert_accepted(out["compact_id"])
            # A higher-tier fold selects child compacts, not new hot events.
            # It may occur while draining; neither case may select the ghost.
            self.assertNotIn(ghost, self.calls[-1])
            if self.kinds[-1] == "hot":
                current = set(self.calls[-1])
                self.assertFalse(
                    selected_hot & current,
                    "уже свёрнутые события снова выбраны из горячего кольца",
                )
                selected_hot.update(current)
        else:
            self.fail("authoring did not reach the end within 20 passes")

        hot = ml._load_state(ROOM, rebuild=True).get("hot") or []
        left = [row["id"] for row in hot if not row.get("unprovable")]
        # plan_hot_fold requires at least two provable rows.
        self.assertLessEqual(len(left), 1, "кольцо не дошло до дна: %s" % left)
        self.assertEqual(
            [row["id"] for row in hot if row.get("unprovable")],
            [ghost], "призрак должен остаться и остаться на своём месте",
        )
        self.assertEqual(hot[0]["id"], ghost)

    def test_persisted_selection_survives_derived_state_loss_without_doublefold(self):
        self._fill(12)
        selected = ml.prepare_authored_fold(ROOM, limit=3)
        self.assertTrue(selected.get("shown_complete"), selected)
        self.assertEqual(selected.get("kind"), "hot", selected)
        ids = [str(row["id"]) for row in selected["sources"]]
        self.assertTrue(ids)

        # Model a derived-state rebuild boundary, not a claimed process restart.
        # Do not delete the authored selection or any source event/compact.
        state_path = ml._state_path(ROOM)
        if state_path.exists():
            state_path.unlink()
        ml.rebuild_state(ROOM)

        text = "Я сохраняю эти источники после восстановления производного состояния."
        out = ml.apply_authored_fold(ROOM, selected["selection"], text)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("source_ids"), ids)
        self._assert_accepted(out["compact_id"])

        state = ml.rebuild_state(ROOM)
        hot_before = [str(row["id"]) for row in state["hot"]]
        self.assertFalse(set(ids) & set(hot_before))
        files_before = {
            p.relative_to(ml.COMPACTS_DIR).as_posix(): p.read_bytes()
            for p in ml.COMPACTS_DIR.rglob("*.md")
        }

        replay = ml.apply_authored_fold(
            ROOM, selected["selection"], "повтор не должен создать вторую память",
        )
        self.assertFalse(replay.get("ok"), replay)
        self.assertEqual(replay.get("reason"), "state_changed", replay)
        self.assertEqual(replay.get("folded"), 0, replay)
        self.assertEqual(
            [str(row["id"]) for row in ml.rebuild_state(ROOM)["hot"]],
            hot_before,
        )
        self.assertEqual(
            {
                p.relative_to(ml.COMPACTS_DIR).as_posix(): p.read_bytes()
                for p in ml.COMPACTS_DIR.rglob("*.md")
            },
            files_before,
        )


if __name__ == "__main__":
    unittest.main()
