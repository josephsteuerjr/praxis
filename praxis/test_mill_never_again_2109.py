"""Один и тот же блок не сворачивается дважды. Стенд самой болезни, а не её причин.

21.09 мельница ловилась трижды за ночь, и каждый раз причина была новая: событие,
невидимое индексу доказательств; дети верхнего яруса, делящие лист; границы родителя,
взятые с краёв списка вместо min/max. Форма же была одна и та же: свёртка написана,
разрешение её не приняло, источники остались непокрытыми, следующий проход свернул тот
же блок заново — вызов модели в минуту при неподвижной памяти.

Здесь закреплена форма. Стенд не знает ни про одну из трёх причин: он сворачивает
дважды и требует, чтобы второй проход не повторял первый, а написанное принималось
собственным разрешением.

Запуск:  python praxis_test.py test_mill_never_again_2109 -v
"""

from __future__ import annotations

import unittest

import memory_life as ml
import memory_provenance
from test_coverage_vs_current import Base, ROOM

NBSP = " "


class TheSameBlockIsNeverFoldedTwice(Base):
    def setUp(self):
        super().setUp()
        self._model = ml._model_compact
        self.selections: list[tuple[str, ...]] = []

        def forbidden_auxiliary_writer(*args, **kwargs):
            self.fail("authored folds must not call an auxiliary recap model")

        ml._model_compact = forbidden_auxiliary_writer

    def tearDown(self):
        ml._model_compact = self._model
        super().tearDown()

    def _fill(self, count: int, *, first: int = 1) -> None:
        for mid in range(first, first + count):
            self._msg(mid, "реплика %d" % mid)

    def _ghost(self, mid: int) -> str:
        """A malformed actor remains visible but is not provable; text is exact."""
        row = {
            "schema": "praxis.life.event.v1",
            "id": ml._id("evt", float(mid)), "ts": ml._utc_iso(float(mid)),
            "kind": "conversation_message", "stream": ml._safe(ROOM),
            "chat_id": ROOM, "actor": " Николай ", "direction": "in",
            "text": "Николай: хвост с пробелом" + NBSP + " ",
            "source": "telegram", "source_id": str(mid), "salience": 2,
            "refs": [], "meta": {"is_dm": False},
            "dedupe_key": "telegram:%s:%d:in" % (ROOM, mid),
        }
        ml._append_record(row)
        return row["id"]

    def _apply_selection(self, selected: dict) -> dict:
        self.assertTrue(selected.get("ok"), selected)
        self.assertTrue(selected.get("sources"), selected)
        self.assertTrue(selected.get("shown_complete"), selected)
        self.assertEqual(selected["kind"], "hot")
        ids = tuple(str(row["id"]) for row in selected["sources"])
        self.assertEqual(len(ids), len(set(ids)), selected)
        result = ml.apply_authored_fold(
            ROOM, selected["selection"],
            "Я сохранила выбранный участок разговора %d."
            % (len(self.selections) + 1),
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["source_ids"], list(ids))
        self.assertEqual(result["folded"], len(ids))
        self.selections.append(ids)
        return result

    def _fold_once(self) -> dict:
        return self._apply_selection(
            ml.prepare_authored_fold(ROOM, limit=50)
        )

    def test_the_second_pass_never_repeats_the_first(self):
        self._fill(12)
        first = self._fold_once()
        self.assertGreater(first["folded"], 0, first)
        second = self._fold_once()
        self.assertNotEqual(first["compact_id"], second["compact_id"])
        self.assertFalse(
            set(self.selections[0]) & set(self.selections[1]),
            "второй проход снова выбрал уже свёрнутые события",
        )
        replay = ml.prepare_authored_fold(ROOM, limit=50)
        replay_ids = {row["id"] for row in replay.get("sources") or []}
        self.assertFalse(
            replay_ids & (set(self.selections[0]) | set(self.selections[1])),
            "подготовка следующей свёртки снова предлагает покрытые события",
        )

    def test_the_folded_events_leave_the_hot_ring(self):
        self._fill(12)
        before = {
            row["id"] for row in ml._load_state(ROOM, rebuild=True)["hot"]
        }
        result = self._fold_once()
        after = {
            row["id"] for row in ml._load_state(ROOM, rebuild=True)["hot"]
        }
        covered = set(result["source_ids"])
        self.assertEqual(after, before - covered, "кольцо не сдвинулось точно")
        self.assertEqual(len(after), len(before) - result["folded"])

    def test_the_written_compact_is_accepted_by_its_own_resolution(self):
        self._fill(12)
        result = self._fold_once()
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        resolved = memory_provenance.compact_coverage(
            result["compact_id"], evidence
        )
        self.assertTrue(resolved["valid"], resolved)
        self.assertEqual(set(resolved["leaves"]), set(result["source_ids"]))

    def test_a_row_the_index_cannot_see_does_not_stop_the_ring(self):
        ghost = self._ghost(1)
        self._fill(11, first=2)
        state = ml._load_state(ROOM, rebuild=True)
        marked = [row["id"] for row in state["hot"] if row.get("unprovable")]
        self.assertEqual(marked, [ghost], "недоказуемая строка пропала из ленты")

        first = self._fold_once()
        self.assertGreater(first["folded"], 0, first)
        self.assertNotIn(ghost, self.selections[0], "призрак поехал в свёртку")
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        resolved = memory_provenance.compact_coverage(
            first["compact_id"], evidence
        )
        self.assertTrue(resolved["valid"], resolved)
        self.assertEqual(set(resolved["leaves"]), set(first["source_ids"]))

        rest = ml._load_state(ROOM, rebuild=True)
        self.assertEqual(
            [row["id"] for row in rest["hot"] if row.get("unprovable")],
            [ghost],
            "призрак обязан остаться в ленте",
        )
        self.assertFalse(
            {row["id"] for row in rest["hot"]} & set(first["source_ids"]),
            "покрытые доказуемые события снова горячие",
        )

    def test_the_ring_drains_to_the_end_even_with_a_ghost_at_the_head(self):
        # Isolate hot draining from the separately tested recursive-tier task.
        old_tier_hi = ml.TIER_HI
        self.addCleanup(setattr, ml, "TIER_HI", old_tier_hi)
        ml.TIER_HI = 1000
        ghost = self._ghost(1)
        self._fill(11, first=2)
        compact_ids = set()
        covered = set()
        for _ in range(20):
            selected = ml.prepare_authored_fold(ROOM, limit=50)
            self.assertTrue(selected.get("ok"), selected)
            if not selected.get("sources"):
                self.assertEqual(selected["reason"], "nothing_to_fold")
                break
            self.assertEqual(selected["kind"], "hot")
            ids = {row["id"] for row in selected["sources"]}
            self.assertNotIn(ghost, ids)
            self.assertFalse(ids & covered, "один участок свёрнут повторно")
            out = self._apply_selection(selected)
            self.assertNotIn(out["compact_id"], compact_ids)
            compact_ids.add(out["compact_id"])
            covered.update(out["source_ids"])
        else:
            self.fail("кольцо не дошло до дна за 20 проходов")

        hot = ml._load_state(ROOM, rebuild=True).get("hot") or []
        left = [row["id"] for row in hot if not row.get("unprovable")]
        # The real planner deliberately leaves a single provable trailing row.
        self.assertLessEqual(len(left), 1, "кольцо не дошло до дна: %s" % left)
        self.assertTrue(hot)
        self.assertEqual(hot[0]["id"], ghost, "призрак сменил своё место")
        self.assertEqual(
            [row["id"] for row in hot if row.get("unprovable")],
            [ghost],
        )
        self.assertFalse({row["id"] for row in hot} & covered)
        every = {
            row["id"]
            for row in ml.iter_events(
                chat_id=ROOM, kinds={"conversation_message"}
            )
        }
        self.assertEqual(
            covered | {row["id"] for row in hot}, every,
            "часть исходной ленты потерялась при повторных свёртках",
        )


if __name__ == "__main__":
    unittest.main()
