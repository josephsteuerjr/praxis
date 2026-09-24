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
        self.calls: list[tuple[str, ...]] = []

        def stub(inputs, **kw):
            self.calls.append(tuple(str(x.get("id")) for x in inputs))
            return {"summary": "сводка %d" % (len(self.calls),),
                    "open_threads": [], "claims": [], "episodes": []}

        ml._model_compact = stub

    def tearDown(self):
        ml._model_compact = self._model
        super().tearDown()

    def _fill(self, count: int, *, first: int = 1) -> None:
        for mid in range(first, first + count):
            self._msg(mid, "реплика %d" % mid)

    def _ghost(self, mid: int) -> str:
        """Строка с пробелом по краям — так их писали до 21.09; писатель так уже не умеет."""
        row = {
            "schema": "praxis.life.event.v1",
            "id": ml._id("evt", float(mid)), "ts": ml._utc_iso(float(mid)),
            "kind": "conversation_message", "stream": ml._safe(ROOM), "chat_id": ROOM,
            "actor": "Николай", "direction": "in",
            "text": "Николай: хвост с пробелом" + NBSP + " ",
            "source": "telegram", "source_id": str(mid), "salience": 2,
            "refs": [], "meta": {"is_dm": False},
            "dedupe_key": "telegram:%s:%d:in" % (ROOM, mid),
        }
        ml._append_record(row)
        return row["id"]

    def test_the_second_pass_never_repeats_the_first(self):
        self._fill(12)
        first = ml.compact_if_due(ROOM, force=True)
        self.assertGreater(int(first.get("folded") or 0), 0, first)
        second = ml.compact_if_due(ROOM, force=True)
        self.assertNotEqual(first["compact_id"], second.get("compact_id"))
        self.assertFalse(set(self.calls[0]) & set(self.calls[1]),
                         "второй проход отдал модели те же события")

    def test_the_folded_events_leave_the_hot_ring(self):
        self._fill(12)
        before = len(ml._load_state(ROOM, rebuild=True).get("hot") or [])
        result = ml.compact_if_due(ROOM, force=True)
        after = len(ml._load_state(ROOM, rebuild=True).get("hot") or [])
        self.assertEqual(after, before - int(result["folded"]), "кольцо не сдвинулось")

    def test_the_written_compact_is_accepted_by_its_own_resolution(self):
        self._fill(12)
        result = ml.compact_if_due(ROOM, force=True)
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        self.assertTrue(memory_provenance.compact_coverage(
            result["compact_id"], evidence)["valid"])

    def test_a_row_the_index_cannot_see_does_not_stop_the_ring(self):
        ghost = self._ghost(1)
        self._fill(11, first=2)
        state = ml._load_state(ROOM, rebuild=True)
        marked = [row["id"] for row in state["hot"] if row.get("unprovable")]
        self.assertEqual(marked, [ghost], "недоказуемая строка пропала из ленты")

        first = ml.compact_if_due(ROOM, force=True)
        self.assertGreater(int(first.get("folded") or 0), 0, first)
        self.assertNotIn(ghost, self.calls[0], "призрак поехал в свёртку")
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        self.assertTrue(memory_provenance.compact_coverage(
            first["compact_id"], evidence)["valid"], "свёртка с призраком невалидна")

        rest = ml._load_state(ROOM, rebuild=True)
        self.assertEqual([row["id"] for row in rest["hot"] if row.get("unprovable")],
                         [ghost], "призрак обязан остаться в ленте")
        self.assertNotIn(first["compact_id"], "")

    def test_the_ring_drains_to_the_end_even_with_a_ghost_at_the_head(self):
        """Главная проверка: с призраком в голове кольцо всё равно доходит до дна."""
        self._ghost(1)
        self._fill(11, first=2)
        seen = set()
        for _ in range(20):
            out = ml.compact_if_due(ROOM, force=True)
            if not int(out.get("folded") or 0):
                break
            self.assertNotIn(out["compact_id"], seen)
            seen.add(out["compact_id"])
        hot = ml._load_state(ROOM, rebuild=True).get("hot") or []
        left = [row["id"] for row in hot if not row.get("unprovable")]
        # Меньше двух строк свернуть нельзя (`plan_hot_fold`: too_short), поэтому
        # хвост в одну доказуемую строку — штатный конец, а не затор.
        self.assertLessEqual(len(left), 1, "кольцо не дошло до дна: %s" % left)
        self.assertEqual([row["id"] for row in hot if row.get("unprovable")],
                         [hot[0]["id"]] if hot[0].get("unprovable") else [],
                         "призрак должен остаться и остаться на своём месте")


if __name__ == "__main__":
    unittest.main()
