"""Верхний ярус не сворачивает детей, которые делят события, и ставит границы по min/max.

Замер 21.09, комната Ouroboros. После привязки веточных ключей к месту (bind_place в
refresh, правка 70e7c5da) во фронтире одного места встретились свёртки разных веток — и
у них нашлись ОБЩИЕ события: 161 лист у четырёх детей, из них уникальных 157. Разрешение
свёртки запрещает повтор листа целиком (`len(unique_leaves) != len(leaves)` →
невалидна), поэтому родитель над такими детьми невалиден НАВСЕГДА: пересборка состояния
не принимает его, дети остаются во фронтире, и каскад сворачивает их снова — 23 родителя
подряд, по вызову модели в минуту, при нулевом движении памяти.

Вторая мина того же рода — границы: родитель писался с `first_ts` первого и `last_ts`
ПОСЛЕДНЕГО источника, а разрешение считает min/max по всем детям. Пока место было одной
лентой, это совпадало; у места из нескольких веток дети идут внахлёст, и родитель снова
не принимается — то же вечное колесо.

Запуск:  python praxis_test.py test_tier_fold_overlap_2109 -v
"""

from __future__ import annotations

import unittest

import memory_life as ml

ROOM = "-1003701205730"


def meta(cid: str, *, events=(), compacts=(), first: str = "", last: str = "",
         tier: int = 1) -> dict:
    return {
        "id": cid,
        "tier": tier,
        "depth": tier,
        "continued": False,
        "created_at": first or "2026-09-01T00:00:00.000Z",
        "first_ts": first,
        "last_ts": last,
        "event_count": len(events),
        "source_event_ids": [str(x) for x in events],
        "source_compact_ids": [str(x) for x in compacts],
    }


def day(n: int, hour: int = 0) -> str:
    return "2026-09-%02dT%02d:00:00.000Z" % (n, hour)


class TheWarmTierRefusesToFoldOverlappingChildren(unittest.TestCase):
    def setUp(self) -> None:
        self._text = ml.compact_text
        ml.compact_text = lambda cid, chat_id=None: "текст " + str(cid)

    def tearDown(self) -> None:
        ml.compact_text = self._text

    def _plain(self, count: int) -> list[dict]:
        """Непересекающиеся свёртки первого яруса, по два события в каждой."""
        return [meta("cmp-%02d" % i, events=("evt-%02d-a" % i, "evt-%02d-b" % i),
                     first=day(i), last=day(i, 12)) for i in range(1, count + 1)]

    def test_disjoint_children_are_folded_as_before(self):
        state = {"frontier": self._plain(9)}
        cand = ml._tier_fold_candidate(ROOM, state)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["source_ids"],
                         ["cmp-01", "cmp-02", "cmp-03", "cmp-04"])

    def test_a_child_sharing_an_event_is_left_out(self):
        rows = self._plain(9)
        rows[1]["source_event_ids"] = ["evt-01-b", "evt-02-b"]  # ветка делит событие
        cand = ml._tier_fold_candidate(ROOM, {"frontier": rows})
        self.assertNotIn("cmp-02", cand["source_ids"])
        self.assertEqual(cand["source_ids"],
                         ["cmp-01", "cmp-03", "cmp-04", "cmp-05"])

    def test_the_chosen_children_never_share_a_leaf(self):
        rows = self._plain(9)
        for i in (1, 2, 3):
            rows[i]["source_event_ids"] = ["evt-01-a", "evt-%02d-b" % (i + 1)]
        cand = ml._tier_fold_candidate(ROOM, {"frontier": rows})
        leaves = [e for cid in cand["source_ids"]
                  for e in next(r for r in rows if r["id"] == cid)["source_event_ids"]]
        self.assertEqual(len(leaves), len(set(leaves)))

    def test_nothing_is_folded_when_almost_everything_overlaps(self):
        """Лучше не свернуть ничего, чем писать заведомо невалидного родителя."""
        rows = self._plain(9)
        for row in rows[1:]:
            row["source_event_ids"] = ["evt-01-a"]
        self.assertIsNone(ml._tier_fold_candidate(ROOM, {"frontier": rows}))

    def test_a_higher_tier_child_without_resolvable_leaves_is_not_taken(self):
        """Листья верхнего яруса из шапки не видны и покрытие не разрешается — таких детей
        в родителя не берём (25.09, ревью V4 N1): родитель над ними был бы отвергнут
        разрешением, и ярус молол бы его по кругу."""
        rows = [meta("cmp-t2-%02d" % i, compacts=("cmp-x-%02d" % i,),
                     first=day(i), last=day(i, 12), tier=2) for i in range(1, 9)]
        cand = ml._tier_fold_candidate(ROOM, {"frontier": rows})
        self.assertIsNone(cand)
        self.assertIsNone(ml._tier_fold_own_leaves(rows[0]))


class TheParentBordersAreMinAndMax(unittest.TestCase):
    def test_bounds_are_taken_over_all_children(self):
        sources = [
            meta("a", first=day(1, 21), last=day(3, 9)),
            meta("b", first=day(2, 19), last=day(3, 19)),
            meta("c", first=day(3, 10), last=day(3, 15)),
        ]
        self.assertEqual(ml._tier_fold_bounds(sources), (day(1, 21), day(3, 19)))

    def test_the_last_child_does_not_own_the_right_border(self):
        """Живой случай: последний ребёнок кончается РАНЬШЕ соседа."""
        sources = [meta("a", first=day(1), last=day(9)),
                   meta("b", first=day(2), last=day(4))]
        self.assertEqual(ml._tier_fold_bounds(sources)[1], day(9))

    def test_missing_borders_do_not_crash(self):
        self.assertEqual(ml._tier_fold_bounds([meta("a")]), ("", ""))
        self.assertEqual(ml._tier_fold_bounds([]), ("", ""))


if __name__ == "__main__":
    unittest.main()
