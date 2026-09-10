# -*- coding: utf-8 -*-
"""Стенд «ядро и слой»: сходится ли объявленное издание с делом.

Запуск:  python tests/t_core_src.py

Раскладка 10.09 объявила продукт так: ядро в `praxis/`, Windows-издание — СЛОЕМ
в `helene/core`. Пока это объявление никто не проверял, оно значило столько же,
сколько до 09.09 значило происхождение реле, — то есть ничего, и разъехалось бы
молча. Проверка живьём 10.09: 47 файлов расходятся, не будучи объявленными, и 7
объявлены зря.

Здесь на синтетических деревьях (три папки во временном каталоге) проверяется,
что прибор ловит ОБА вида расхождения и что сборка из ядра на них отказывается.
Настоящее дерево сюда не подмешивается намеренно: стенд обязан быть зелёным и
тогда, когда рабочая копия у человека другая.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "installer"))
sys.path.insert(0, str(HERE.parent))

import core_src  # noqa: E402


class Ground(unittest.TestCase):
    """Три дерева на время одного теста: ядро, слой, рабочая копия."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core-src-"))
        self.core = self.tmp / "praxis"
        self.layer = self.tmp / "helene" / "core"
        self.tree = self.tmp / "live"
        for d in (self.core, self.layer, self.tree):
            d.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, root: Path, rel: str, text: str) -> Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def compare(self) -> dict:
        return core_src.compare(self.core, self.layer, self.tree)


class Verdict(Ground):

    def test_an_honest_layer_reports_nothing(self):
        """Ядро + объявленный слой = рабочая копия: расхождения нет."""
        self.put(self.core, "agent.py", "ядро\n")
        self.put(self.core, "llm.py", "общий\n")
        self.put(self.layer, "agent.py", "издание\n")
        self.put(self.tree, "agent.py", "издание\n")
        self.put(self.tree, "llm.py", "общий\n")
        res = self.compare()
        self.assertEqual(res["undeclared"], [])
        self.assertEqual(res["stale"], [])
        self.assertEqual(res["declared_ok"], ["agent.py"])

    def test_a_file_that_differs_without_being_declared_is_named(self):
        """Главный случай: ядро отстало от живого, а слой об этом молчит.

        Прибор НЕ решает, чья это правка: отличить её невыложенную починку от
        нашей может только человек. Он лишь не даёт принять расхождение за
        пустоту — именно это и случилось с зеркалом, отставшим на месяц.
        """
        self.put(self.core, "bootguard.py", "старое\n")
        self.put(self.tree, "bootguard.py", "её починка 28.08\n")
        res = self.compare()
        self.assertEqual(res["undeclared"], ["bootguard.py"])

    def test_a_declared_file_that_does_not_differ_is_named_too(self):
        """Слой объявляет файл, а расхождения нет — запись протухла.

        Так вышло у семи файлов: числа `EDITION.md` мерились против её ЖИВОГО
        дерева, а против выложенного ядра этих различий нет вовсе. То есть
        объявлено было не «мы несём иначе», а «мы отстали».
        """
        self.put(self.core, "canary.py", "одно и то же\n")
        self.put(self.layer, "canary.py", "одно и то же\n")
        self.put(self.tree, "canary.py", "одно и то же\n")
        res = self.compare()
        self.assertEqual(res["stale"], ["canary.py"])
        self.assertEqual(res["declared_ok"], [])

    def test_a_file_only_we_have_is_not_called_a_difference(self):
        """Наш файл, которого в ядре нет, — это не расхождение редакций."""
        self.put(self.core, "agent.py", "ядро\n")
        self.put(self.tree, "agent.py", "ядро\n")
        self.put(self.tree, "sitecustomize.py", "только наш\n")
        res = self.compare()
        self.assertEqual(res["only_ours"], ["sitecustomize.py"])
        self.assertEqual(res["undeclared"], [])

    def test_the_layer_remembering_a_file_the_tree_lost_is_named(self):
        self.put(self.core, "agent.py", "ядро\n")
        self.put(self.layer, "снесён.py", "издание\n")
        self.put(self.tree, "agent.py", "ядро\n")
        self.assertEqual(self.compare()["gone"], ["снесён.py"])


class WhatIsNotCompared(Ground):

    def test_her_own_writing_is_not_an_edition_difference(self):
        """`soul/` — конституция и навыки. Они ОБЯЗАНЫ отличаться, и объявлять
        это слоем значило бы объявлять слоем её личность."""
        self.put(self.core, "soul/SOUL.md", "её\n")
        self.put(self.tree, "soul/SOUL.md", "её, другая\n")
        self.assertEqual(self.compare()["undeclared"], [])

    def test_life_and_build_leftovers_are_not_compared(self):
        for rel in ("memory/событие.json", "workspace/черновик.md", "data/x.db",
                    "private/тайна.json", "__pycache__/agent.pyc", "body/target/x.rlib"):
            self.put(self.core, rel, "а\n")
            self.put(self.tree, rel, "б\n")
        self.assertEqual(self.compare()["undeclared"], [])

    def test_env_files_and_pre_edit_snapshots_are_not_compared(self):
        """Следы среды и снимки «до правки», которые дерево копит рядом с кодом.
        Их расхождение не значит ничего, а в отчёте они топят настоящее."""
        for rel in (".env", ".env.bak", "agent.py.pre-что-то-1784583545",
                    "docker-compose.yml.bak"):
            self.put(self.core, rel, "а\n")
            self.put(self.tree, rel, "б\n")
        self.assertEqual(self.compare()["undeclared"], [])

    def test_line_endings_alone_are_not_a_difference(self):
        """Копия на Windows и оригинал с Linux-сервера иначе разошлись бы КАЖДЫМ
        файлом, и сверка не значила бы ничего.

        ⚠ Это не теория: первая же сверка 10.09 без этой нормы насчитала 69
        расхождений вместо 57, и семь «различий» оказались одними переводами
        строк.
        """
        self.put(self.core, "agent.py", "строка\nдругая\n")
        (self.tree / "agent.py").write_bytes(b"\xd1\x81\xd1\x82\xd1\x80\xd0\xbe\xd0\xba\xd0\xb0\r\n"
                                             b"\xd0\xb4\xd1\x80\xd1\x83\xd0\xb3\xd0\xb0\xd1\x8f\r\n")
        self.assertEqual(self.compare()["undeclared"], [])


class Assembly(Ground):
    """Сборка дерева из ядра и слоя — и её отказ."""

    def build_dist(self):
        import build_dist
        return build_dist

    def test_assembling_over_a_broken_declaration_is_refused(self):
        """Иначе это не сборка из ядра, а сборка ЧУЖОГО дерева под нашим именем:
        нерасхожденный файл приехал бы в редакции ядра — молча и без следа."""
        bd = self.build_dist()
        self.put(self.core, "bootguard.py", "старое\n")
        self.put(self.tree, "bootguard.py", "её починка\n")
        # Подменяем ИМЕННО рабочую копию: без этого отказ сработал бы от
        # расхождения синтетического ядра с настоящим деревом на диске, и тест
        # зеленел бы у всех и всегда, ничего не проверяя.
        old_live = bd.live_root
        try:
            bd.live_root = lambda _cli=None: self.tree
            with self.assertRaises(SystemExit) as caught:
                bd.assemble_from_core(self.tmp / "out", self.core, self.layer)
        finally:
            bd.live_root = old_live
        said = str(caught.exception)
        self.assertIn("слой не описывает издание", said)
        self.assertIn("не объявлено", said)

    def test_a_consistent_declaration_assembles_core_then_layer(self):
        """Слой кладётся ПОВЕРХ ядра: иначе издание затрёт само себя."""
        bd = self.build_dist()
        self.put(self.core, "agent.py", "ядро\n")
        self.put(self.core, "llm.py", "общий\n")
        self.put(self.layer, "agent.py", "издание\n")
        self.put(self.tree, "agent.py", "издание\n")
        self.put(self.tree, "llm.py", "общий\n")
        out = self.tmp / "out"
        # `assemble_from_core` сверяется с рабочей копией, которую находит сам;
        # подменяем ровно то место, где он её ищет.
        old_live = bd.live_root
        try:
            bd.live_root = lambda _cli=None: self.tree
            n = bd.assemble_from_core(out, self.core, self.layer)
        finally:
            bd.live_root = old_live
        self.assertEqual(n, 3)      # два файла ядра + один файла слоя
        self.assertEqual((out / "agent.py").read_text(encoding="utf-8"), "издание\n")
        self.assertEqual((out / "llm.py").read_text(encoding="utf-8"), "общий\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
