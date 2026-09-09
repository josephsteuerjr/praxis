"""Атомарная запись прогона переживает гонку с читателем на Windows.

08.09: Пульт читал manifest.json прогона ровно в момент `os.replace`, Windows ответил
PermissionError (sharing violation), и живой ход Миры встал в paused с «durability failure
during model intent persistence». Читатель держит файл миллисекунды — короткий повтор
закрывает гонку; после потолка ошибка уходит наверх, а не глотается.

Запуск:  python praxis_test.py test_atomic_replace_retry -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_manager


class AtomicReplaceRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "manifest.json"

    def test_transient_sharing_violation_is_retried_and_the_file_lands(self):
        real = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(5, "Отказано в доступе")
            return real(src, dst)

        with mock.patch.object(run_manager.os, "name", "nt"), \
                mock.patch.object(run_manager.os, "replace", side_effect=flaky), \
                mock.patch.object(run_manager.time, "sleep") as slept:
            run_manager._atomic_json(self.path, {"status": "running"})
        self.assertEqual(calls["n"], 3, "две гонки — два повтора, третья попытка легла")
        self.assertEqual(slept.call_count, 2)
        self.assertEqual(self.path.read_text(encoding="utf-8").strip(), '{\n  "status": "running"\n}')
        self.assertEqual([p for p in self.path.parent.iterdir()], [self.path], "временный файл убран")

    def test_a_persistent_denial_still_surfaces(self):
        with mock.patch.object(run_manager.os, "name", "nt"), \
                mock.patch.object(run_manager.os, "replace",
                                  side_effect=PermissionError(5, "Отказано в доступе")), \
                mock.patch.object(run_manager.time, "sleep"):
            with self.assertRaises(PermissionError):
                run_manager._atomic_json(self.path, {"status": "running"})
        self.assertFalse(self.path.exists())

    def test_posix_does_not_retry_permission_errors(self):
        # На POSIX PermissionError — это права, а не гонка: повтор только скрыл бы причину.
        with mock.patch.object(run_manager.os, "name", "posix"), \
                mock.patch.object(run_manager.os, "replace",
                                  side_effect=PermissionError(13, "Permission denied")) as rep, \
                mock.patch.object(run_manager.time, "sleep") as slept:
            with self.assertRaises(PermissionError):
                run_manager._atomic_json(self.path, {"status": "running"})
        self.assertEqual(rep.call_count, 1)
        self.assertEqual(slept.call_count, 0)


if __name__ == "__main__":
    unittest.main()
