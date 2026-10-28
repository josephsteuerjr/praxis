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

from test_authorship_support_1910 import assert_reissue_refused

import unittest

import memory_life as ml
import memory_provenance
from test_coverage_vs_current import Base, ROOM


class TheStumpIsReissued(Base):



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

    def test_a_stump_requires_an_acting_author_and_is_preserved(self):
        stump = self._stump(self._rows())
        self.assertTrue(stump["degraded"])
        assert_reissue_refused(self, ROOM, stump["id"])
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        self.assertTrue(
            memory_provenance.compact_coverage(stump["id"], evidence)["valid"]
        )

    def test_refusal_does_not_grow_the_hot_ring(self):
        stump = self._stump(self._rows())
        before = [r["id"] for r in ml.rebuild_state(ROOM)["hot"]]
        self.assertEqual(before, [])
        assert_reissue_refused(self, ROOM, stump["id"])
        self.assertEqual(
            [r["id"] for r in ml.rebuild_state(ROOM)["hot"]], before
        )

    def test_a_compact_with_a_parent_and_its_parent_are_preserved(self):
        rows = self._rows()
        stump = self._stump(rows)
        parent = ml._write_compact(
            ROOM,
            {"summary": "родитель", "open_threads": [],
             "claims": [], "episodes": []},
            tier=2, depth=2,
            source_events=[], source_compacts=[stump["id"]],
            event_count=len(rows), continued=False,
            first_ts=stump["first_ts"], last_ts=stump["last_ts"],
        )
        assert_reissue_refused(self, ROOM, stump["id"])
        evidence = memory_provenance.claim_evidence_index(ml.MEM_DIR)
        self.assertTrue(
            memory_provenance.compact_coverage(parent["id"], evidence)["valid"]
        )

    def test_a_healthy_compact_is_left_alone(self):
        good = self._compact(self._rows())
        assert_reissue_refused(self, ROOM, good["id"])

    def test_no_model_availability_can_enable_reissue(self):
        from unittest import mock
        import llm

        stump = self._stump(self._rows())
        for configured in (False, True):
            with self.subTest(configured=configured), \
                    mock.patch.object(
                        llm, "configured", return_value=configured
                    ):
                assert_reissue_refused(self, ROOM, stump["id"])

    def test_an_unknown_compact_does_not_create_memory(self):
        assert_reissue_refused(
            self, ROOM, "cmp-20260921T000000000000Z-deadbeef"
        )








if __name__ == "__main__":
    unittest.main()
