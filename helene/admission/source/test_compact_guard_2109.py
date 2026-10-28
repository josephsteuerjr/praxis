"""Свёртка, которую не принимает её собственное разрешение, не остаётся на диске.

Общая ограда, поставленная 21.09 после трёх мельниц подряд. У всех трёх форма одна:
свёртка написана, разрешение её не приняло, источники остались непокрытыми, следующий
проход написал её заново — вызов модели в минуту без движения памяти. Причины были
разные (недоказуемое событие; дети, делящие лист; границы не по min/max), и будут ещё.
Здесь закрыта не причина, а СПОСОБ, которым любая такая причина становится циклом.

Отдельно закреплено: молчащий прибор — не приговор. Если индекс доказательств не
собрался, свёртку оставляем; иначе первая же ошибка чтения начнёт стирать её память.

Запуск:  python praxis_test.py test_compact_guard_2109 -v
"""

from __future__ import annotations

import unittest

import memory_life as ml
import memory_provenance as mp


class TheFreshCompactMustBeProvable(unittest.TestCase):
    def setUp(self) -> None:
        self._index = mp.claim_evidence_index
        self._coverage = mp.compact_coverage
        mp.claim_evidence_index = lambda *_a, **_k: {"compacts": {}}
        self.rel = "memory/life/compacts/-1/cmp-20260921T090000000000Z-aaaabbbb.md"
        self.path = ml.BASE / self.rel
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("свёртка", encoding="utf-8")
        self.meta = {"id": "cmp-20260921T090000000000Z-aaaabbbb", "path": self.rel}

    def tearDown(self) -> None:
        mp.claim_evidence_index = self._index
        mp.compact_coverage = self._coverage
        self.path.unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
        except OSError:
            pass

    def test_an_unresolvable_compact_is_removed(self):
        mp.compact_coverage = lambda *_a, **_k: {"valid": False}
        self.assertTrue(ml._discard_unprovable_compact(self.meta, "стенд"))
        self.assertFalse(self.path.exists(), "негодная свёртка осталась на диске")

    def test_a_resolvable_compact_is_left_alone(self):
        mp.compact_coverage = lambda *_a, **_k: {"valid": True}
        self.assertFalse(ml._discard_unprovable_compact(self.meta, "стенд"))
        self.assertTrue(self.path.exists())

    def test_a_silent_instrument_is_not_a_verdict(self):
        def boom(*_a, **_k):
            raise RuntimeError("индекс не собрался")
        mp.claim_evidence_index = boom
        self.assertTrue(ml._compact_provable("cmp-20260921T090000000000Z-aaaabbbb"))
        self.assertFalse(ml._discard_unprovable_compact(self.meta, "стенд"))
        self.assertTrue(self.path.exists())

    def test_a_compact_without_an_id_is_not_touched(self):
        mp.compact_coverage = lambda *_a, **_k: {"valid": False}
        self.assertFalse(ml._discard_unprovable_compact({"path": self.rel}, "стенд"))
        self.assertTrue(self.path.exists())

    def test_a_missing_file_does_not_raise(self):
        mp.compact_coverage = lambda *_a, **_k: {"valid": False}
        self.path.unlink()
        self.assertTrue(ml._discard_unprovable_compact(self.meta, "стенд"))


if __name__ == "__main__":
    unittest.main()
