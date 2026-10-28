"""Отпечаток файла жизни помнится по stat, а у свежих считается всегда.

`claim_evidence_index` берёт отпечаток КАЖДОГО файла жизни на КАЖДЫЙ вызов, а зовут его
сборка кадра каждого хода, свёртка, formation, identity и поиск. К 21.09.2026 файлов
стало 79 журналов событий и 4891 свёртка — около пяти тысяч open+sha256 за вызов.
ЗАМЕР на живом дереве: 0,49 с при целом кэше индекса, 13,0 с вхолодную; три потока
раннера разом сидели в этом, ядро держалось на 96 %, живой ход не мог собрать кадр.
После правки тот же замер — 0,13 с.

Твёрдое, что здесь проверяется: память не смеет отдать старый отпечаток файла, который
только что трогали, — иначе дозапись в тот же миллисекундный тик пройдёт мимо решения о
каноничности, ради чего хвост и читается.

Запуск:  python praxis_test.py test_signature_cache_2109 -v
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import memory_provenance as mp

FRESH = "свежее\n".encode("utf-8")
DECOY = ("метка", 0, 0, 0, "подменено")


class TheSignatureIsRememberedByStat(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        mp._SIGNATURE_CACHE.clear()

    def tearDown(self) -> None:
        mp._SIGNATURE_CACHE.clear()
        self._dir.cleanup()

    def _aged(self, name: str, body: bytes) -> Path:
        """Файл с прошлым mtime: «свежесть» тут мешает, её проверяет отдельный тест."""
        path = self.root / name
        path.write_bytes(body)
        old = time.time() - 3600
        os.utime(path, (old, old))
        return path

    def _plant_decoy(self, path: Path) -> None:
        """Подменить ЗАПОМНЕННЫЙ отпечаток, оставив отметку stat прежней."""
        stamp, _ = mp._SIGNATURE_CACHE[path.as_posix()]
        mp._SIGNATURE_CACHE[path.as_posix()] = (stamp, DECOY)

    def test_an_untouched_file_is_not_hashed_twice(self):
        path = self._aged("a.jsonl", b"one\ntwo\n")
        self.assertIsNotNone(mp._life_file_signature(path))
        self._plant_decoy(path)
        # Вернулась подмена — значит взята память, а не перечитан хвост.
        self.assertEqual(mp._life_file_signature(path)[4], "подменено")

    def test_a_changed_file_is_hashed_again(self):
        path = self._aged("b.jsonl", b"one\n")
        mp._life_file_signature(path)
        self._plant_decoy(path)
        path.write_bytes(b"one\ntwo\n")
        old = time.time() - 3600
        os.utime(path, (old, old))
        again = mp._life_file_signature(path)
        self.assertNotEqual(again[4], "подменено")
        self.assertEqual(again[1], 8)

    def test_a_freshly_touched_file_is_always_rehashed(self):
        """Дозапись в тот же тик обязана сбрасывать решение — свежих не кэшируем."""
        path = self.root / "c.jsonl"
        path.write_bytes(FRESH)
        self.assertIsNotNone(mp._life_file_signature(path))
        self._plant_decoy(path)
        self.assertNotEqual(mp._life_file_signature(path)[4], "подменено")

    def test_the_signature_itself_did_not_change_shape(self):
        path = self._aged("d.jsonl", b"x\n")
        sig = mp._life_file_signature(path)
        self.assertEqual(len(sig), 5)
        self.assertEqual(sig[0], path.as_posix())
        self.assertEqual(sig[1], 2)
        self.assertIsInstance(sig[2], int)
        self.assertIsInstance(sig[4], str)

    def test_a_missing_file_has_no_signature_and_leaves_no_trace(self):
        gone = self.root / "нет.jsonl"
        self.assertIsNone(mp._life_file_signature(gone))
        self.assertNotIn(gone.as_posix(), mp._SIGNATURE_CACHE)

    def test_the_cache_does_not_grow_without_bound(self):
        mp._SIGNATURE_CACHE.clear()
        for i in range(mp._SIGNATURE_CACHE_CAP + 2):
            mp._SIGNATURE_CACHE["/ghost/%d" % i] = ((0, 0, 0), DECOY)
        path = self._aged("e.jsonl", b"y\n")
        mp._life_file_signature(path)
        self.assertLessEqual(len(mp._SIGNATURE_CACHE), mp._SIGNATURE_CACHE_CAP)
        self.assertIn(path.as_posix(), mp._SIGNATURE_CACHE)


if __name__ == "__main__":
    unittest.main()
