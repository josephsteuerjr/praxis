"""Верхний ярус не сворачивает детей, делящих события (25.09).

Диагноз по проду: у AbstractDL четыре ребёнка яруса 3 делили 64 события, у Ouroboros — 4;
пересечение проверялось только у первого яруса, родитель писался, разрешение его
отвергало, и ярус писал ту же пачку по пять раз в день, не вырастая никогда.

Запуск:  python praxis_test.py test_tier_overlap_2509 -v
"""

from __future__ import annotations

import unittest
from unittest import mock

import memory_life as ml
import memory_provenance as mp


def _tier2(cid: str, first: str, last: str) -> dict:
    return {"id": cid, "tier": 2, "depth": 2, "first_ts": first, "last_ts": last,
            "created_at": first, "source_compact_ids": [f"{cid}-a", f"{cid}-b"],
            "source_event_ids": [], "event_count": 10, "continued": False}


class UpperTierChildrenMustNotShareLeaves(unittest.TestCase):
    def setUp(self):
        self.leaves = {
            "cmp-A": ["e1", "e2", "e3"],
            "cmp-B": ["e4", "e5"],
            "cmp-C": ["e3", "e6"],      # делит e3 с A
            "cmp-D": ["e7", "e8"],
            "cmp-E": ["e9"],
            "cmp-F": ["e10"],
            "cmp-G": ["e11"],
            "cmp-H": ["e12"],
        }
        self.index_calls = 0

        def fake_index(memory_dir):
            self.index_calls += 1
            return {"compacts": {}, "events": {}}

        def fake_coverage(compact_id, evidence):
            rows = self.leaves.get(compact_id)
            if rows is None:
                return {"valid": False, "leaves": []}
            return {"valid": True, "leaves": list(rows), "first_ts": "", "last_ts": ""}

        self.patches = [mock.patch.object(mp, "claim_evidence_index", side_effect=fake_index),
                        mock.patch.object(mp, "compact_coverage", side_effect=fake_coverage)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def _state(self, ids):
        return {"frontier": [_tier2(cid, f"2026-09-0{i + 1}T00:00:00Z", f"2026-09-0{i + 1}T12:00:00Z")
                             for i, cid in enumerate(ids)], "hot": []}

    def test_overlapping_child_is_skipped_on_the_upper_tier(self):
        with mock.patch.object(ml, "TIER_HI", 4), mock.patch.object(ml, "TIER_LO", 1):
            cand = ml._tier_fold_candidate("-100", self._state(["cmp-A", "cmp-B", "cmp-C", "cmp-D"]))
        self.assertIsNotNone(cand)
        self.assertEqual(cand["tier"], 2)
        self.assertNotIn("cmp-C", cand["source_ids"], "ребёнок, делящий e3 с A, не берётся")
        self.assertEqual(cand["source_ids"][:2], ["cmp-A", "cmp-B"])
        self.assertEqual(self.index_calls, 1, "индекс доказательств строится один раз на выбор")

    def test_all_children_overlapping_gives_no_candidate_instead_of_a_mill(self):
        self.leaves["cmp-B"] = ["e1"]
        self.leaves["cmp-D"] = ["e2"]
        with mock.patch.object(ml, "TIER_HI", 3), mock.patch.object(ml, "TIER_LO", 1):
            cand = ml._tier_fold_candidate("-100", self._state(["cmp-A", "cmp-B", "cmp-D"]))
        self.assertIsNone(cand, "одного источника не хватает на родителя — кандидата нет")

    def test_first_tier_still_uses_its_own_header(self):
        rows = [{"id": f"cmp-{i}", "tier": 1, "depth": 1, "first_ts": f"2026-09-0{i + 1}T00:00:00Z",
                 "last_ts": f"2026-09-0{i + 1}T01:00:00Z", "created_at": "",
                 "source_event_ids": [f"e{i}", f"e{i}x"], "source_compact_ids": [],
                 "event_count": 2} for i in range(4)]
        rows[2]["source_event_ids"] = ["e0", "e2"]  # делит e0 с первым
        with mock.patch.object(ml, "TIER_HI", 4), mock.patch.object(ml, "TIER_LO", 1):
            cand = ml._tier_fold_candidate("-100", {"frontier": rows, "hot": []})
        self.assertIsNotNone(cand)
        self.assertNotIn("cmp-2", cand["source_ids"])
        self.assertEqual(self.index_calls, 0, "первый ярус индекс не поднимает")

    def test_unresolvable_child_is_left_out(self):
        # 25.09 (ревью V4 N1): покрытие не разрешилось (valid=False) — листьев не знаем, и
        # родитель над таким ребёнком отвергается разрешением; берём только разрешимых
        with mock.patch.object(ml, "TIER_HI", 3), mock.patch.object(ml, "TIER_LO", 1):
            cand = ml._tier_fold_candidate("-100", self._state(["cmp-A", "cmp-X", "cmp-B"]))
        self.assertIsNotNone(cand)
        self.assertNotIn("cmp-X", cand["source_ids"])
        self.assertEqual(sorted(cand["source_ids"]), ["cmp-A", "cmp-B"])


if __name__ == "__main__":
    unittest.main()
