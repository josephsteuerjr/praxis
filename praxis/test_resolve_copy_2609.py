"""26.09 — разрешение свёртки не копирует ~60 тыс. id текущих событий на каждого предка.

Замер прода 26.09 (py-spy после рестарта, ЦП ~100 % полчаса): поток плательщика долга стоял в
`_resolve_compact`. Там по месту стояло `set(evidence.get("current_event_ids") or ())` —
копия всех текущих id (у неё 59 704, 4,2 мс) на КАЖДОЕ разрешение, включая каждого предка в
рекурсии. Родословные AbstractDL — 44 731 узел за проход: 186 с одной копии на режим,
`refresh_debt` места — 529 с, а плательщик зовёт его на каждое из 242 мест после старта.

Прибито: членство проверяется в самом frozenset индекса (`current_ids_view`) — в рекурсии,
в уликах утверждений и в индексе FTS. Ответы те же (сверено со старой реализацией на её
индексе: все свёртки, оба режима, ответы и основания memo).
"""
from __future__ import annotations

import contextlib
import unittest
from unittest import mock

import memory_provenance as mp


class CountingIds(frozenset):
    """Множество текущих id индекса; копии считает `_copies_of` ниже.

    Счёт через `__iter__` не годится: `set(frozenset)` в CPython копирует внутреннюю
    таблицу напрямую, без питоновских методов, — первая версия этого стенда молча
    проходила на старом коде."""
    walks = 0


@contextlib.contextmanager
def _copies_of(ids_box: list):
    """Подменить `set` в модуле подклассом, который считает копии именно `ids_box[0]`."""
    class CountingSet(set):
        def __init__(self, *args):
            if args and ids_box and args[0] is ids_box[0]:
                CountingIds.walks += 1
            super().__init__(*args)

    with mock.patch.object(mp, "set", CountingSet, create=True):
        yield


def E(k: int) -> str:
    return f"evt-20260926T00{k // 60:02d}{k % 60:02d}000000Z-{k:08x}"


def C(k: int) -> str:
    return f"cmp-20260926T00{k // 60:02d}{k % 60:02d}000000Z-{k:08x}"


def _event(event_id: str, ts: str) -> dict:
    return {"schema": "praxis.life.event.v1", "id": event_id, "chat_id": "-100777",
            "kind": "note", "source": "self", "ts": ts, "direction": "internal"}


def _chain(depth: int) -> dict:
    """Свёртка C(k) стоит на своём событии E(k) и на предке C(k-1): родословная глубины depth."""
    events, compacts = {}, {}
    for k in range(depth):
        ts = f"2026-09-26T00:{k // 60:02d}:{k % 60:02d}Z"
        events[E(k)] = _event(E(k), ts)
        compacts[C(k)] = {
            "chat_id": "-100777", "source_event_ids": [E(k)],
            "source_compact_ids": [C(k - 1)] if k else [],
            "event_count": k + 1, "first_ts": "2026-09-26T00:00:00Z", "last_ts": ts,
        }
    return {"events": events, "compacts": compacts, "places": {},
            "current_event_ids": CountingIds(events)}


class ResolutionDoesNotCopyCurrentIds(unittest.TestCase):
    def setUp(self):
        CountingIds.walks = 0

    def test_deep_lineage_resolves_without_copying_the_current_ids(self):
        index = _chain(40)
        with _copies_of([index["current_event_ids"]]):
            strict = mp.compact_evidence(C(39), index)
            cover = mp.compact_coverage(C(39), index)
        self.assertTrue(strict["valid"], strict)
        self.assertTrue(cover["valid"], cover)
        self.assertEqual(len(strict["leaves"]), 40)
        self.assertEqual(CountingIds.walks, 0,
                         "членство обязано проверяться в самом frozenset, без копии")

    def test_superseded_event_still_fails_the_strict_path(self):
        index = _chain(5)
        index["current_event_ids"] = CountingIds(set(index["events"]) - {E(2)})
        with _copies_of([index["current_event_ids"]]):
            self.assertFalse(mp.compact_evidence(C(4), index)["valid"])
            # Не телеграмное событие вне текущих — отказ и в покрытии (ослаблено только
            # вытеснение ревизией сообщения).
            self.assertFalse(mp.compact_coverage(C(4), index)["valid"])
        self.assertEqual(CountingIds.walks, 0)

    def test_claim_evidence_checks_membership_without_a_copy(self):
        index = _chain(3)
        with _copies_of([index["current_event_ids"]]):
            ok = mp._resolve_claim_evidence({"evidence_ids": [E(0), C(2)]}, index)
            # E(0) уже внутри C(2) — сдвоенная улика не годится.
            self.assertFalse(ok["valid"], ok)
            ok = mp._resolve_claim_evidence({"evidence_ids": [C(2)]}, index)
            self.assertTrue(ok["valid"], ok)
        index["current_event_ids"] = CountingIds(set(index["events"]) - {E(1)})
        with _copies_of([index["current_event_ids"]]):
            self.assertFalse(mp._resolve_claim_evidence({"evidence_ids": [E(1)]}, index)["valid"])
            self.assertTrue(mp._resolve_claim_evidence({"evidence_ids": [E(2)]}, index)["valid"])
        self.assertEqual(CountingIds.walks, 0)

    def test_non_set_container_is_still_honoured(self):
        index = _chain(3)
        index["current_event_ids"] = [E(0), E(1), E(2)]
        self.assertTrue(mp.compact_evidence(C(2), index)["valid"])
        index = _chain(3)
        index["current_event_ids"] = [E(0), E(2)]
        self.assertFalse(mp.compact_evidence(C(2), index)["valid"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
