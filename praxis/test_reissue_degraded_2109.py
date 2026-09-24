"""Перевыпуск обрубка: та же пачка событий, но сводка от модели.

Обрубок — свёртка, написанная без модели (`_fallback_compact`): механическая выжимка
вместо синтеза. К 21.09 их 688 из 5000, почти все от потолка `COMPACT_MAX_TOKENS=4000`,
который держался до `98b6bc71`. Потолок починен, но написанные обрубки остались в
памяти навсегда.

Закреплены три вещи: перевыпуск НЕ трогает свёртку, у которой есть родитель (иначе
родитель остаётся с висящей ссылкой, разрешение его не принимает, и ярус пишет его
заново — та самая мельница); при молчащей модели старая свёртка остаётся на месте;
после удачи старого файла нет, новый принимается своим же разрешением, а горячее кольцо
не растёт.

Запуск:  python praxis_test.py test_reissue_degraded_2109 -v
"""

from __future__ import annotations

import unittest

import memory_life as ml
import memory_provenance
from test_coverage_vs_current import Base, ROOM


class TheStumpIsReissued(Base):
    def setUp(self):
        super().setUp()
        self._model = ml._model_compact
        self.calls = []
        ml._model_compact = self._stub

    def tearDown(self):
        ml._model_compact = self._model
        super().tearDown()

    def _stub(self, inputs, **kw):
        self.calls.append(len(inputs))
        return {"summary": "живая сводка", "open_threads": [], "claims": [], "episodes": []}

    def _stump(self, rows, *, chat: str = ROOM) -> dict:
        """Свёртка без модели — ровно как её пишет `_fallback_compact`."""
        result = ml._fallback_compact(rows, continued=False)
        return ml._write_compact(
            chat, result, tier=1, depth=1, source_events=[r["id"] for r in rows],
            source_compacts=[], event_count=len(rows), continued=False,
            first_ts=min(str(r["ts"]) for r in rows),
            last_ts=max(str(r["ts"]) for r in rows))

    def _rows(self, count: int = 4) -> list[dict]:
        return [self._msg(mid, "реплика %d" % mid) for mid in range(1, count + 1)]

    def test_a_stump_becomes_a_real_summary(self):
        stump = self._stump(self._rows())
        self.assertTrue(stump["degraded"])
        out = ml.reissue_degraded_compact(ROOM, stump["id"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["events"], 4)
        self.assertFalse((ml.BASE / stump["path"]).exists(), "старый обрубок остался")
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        fresh = evidence["compacts"][out["new_id"]]
        self.assertFalse(fresh["degraded"])
        self.assertEqual(fresh["source_event_ids"], stump["source_event_ids"])
        self.assertTrue(memory_provenance.compact_coverage(out["new_id"], evidence)["valid"])

    def test_the_hot_ring_does_not_grow_back(self):
        """`_load_state` тут не годится: он отдаёт СОХРАНЁННОЕ, а не пересобранное."""
        rows = self._rows()
        stump = self._stump(rows)
        before = len(ml.rebuild_state(ROOM).get("hot") or [])
        self.assertEqual(before, 0, "обрубок обязан покрывать свои события")
        out = ml.reissue_degraded_compact(ROOM, stump["id"])
        self.assertTrue(out["ok"], out)
        after = len(ml.rebuild_state(ROOM).get("hot") or [])
        self.assertEqual(after, 0, "события вернулись в горячее")

    def test_a_compact_with_a_parent_is_refused(self):
        rows = self._rows()
        stump = self._stump(rows)
        ml._write_compact(ROOM, {"summary": "родитель", "open_threads": [], "claims": [],
                                 "episodes": []},
                          tier=2, depth=2, source_events=[], source_compacts=[stump["id"]],
                          event_count=len(rows), continued=False,
                          first_ts=stump["first_ts"], last_ts=stump["last_ts"])
        out = ml.reissue_degraded_compact(ROOM, stump["id"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "has_parent")
        self.assertTrue((ml.BASE / stump["path"]).exists(), "обрубок с родителем снесён")

    def test_a_healthy_compact_is_left_alone(self):
        rows = self._rows()
        good = self._compact(rows)
        out = ml.reissue_degraded_compact(ROOM, good["id"])
        self.assertEqual(out["reason"], "not_degraded")
        self.assertTrue((ml.BASE / good["path"]).exists())

    def test_a_silent_model_keeps_the_stump(self):
        stump = self._stump(self._rows())
        ml._model_compact = lambda *a, **k: {}
        out = ml.reissue_degraded_compact(ROOM, stump["id"])
        self.assertEqual(out["reason"], "model_unavailable")
        self.assertTrue((ml.BASE / stump["path"]).exists(), "обрубок снесли без замены")

    def test_an_unknown_compact_is_named_not_guessed(self):
        out = ml.reissue_degraded_compact(ROOM, "cmp-20260921T000000000000Z-deadbeef")
        self.assertEqual(out["reason"], "unknown_compact")


if __name__ == "__main__":
    unittest.main()
