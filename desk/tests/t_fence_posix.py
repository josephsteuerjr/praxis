# -*- coding: utf-8 -*-
"""Стенд «ограда на POSIX»: то же обещание, другой механизм.

Запуск:  python tests/t_fence_posix.py
         (часть проверок — только на Linux с bubblewrap; на Windows они
          пропускаются, а разбор командной строки идёт везде)

Порт на Linux и macOS решено делать «основой», и ограда в основу входит
обязательно: без неё файловые руки и `shell` идут с правами пользователя, а
текст режима «Песочница» обещает обратное. Здесь стерегут ровно то, что делает
обещание правдой:

  * секреты владельца закрываются ВНУТРИ связанной памяти — иначе `cat
    memory/llm.json` из shell выдал бы ключи, при том что ограда «стоит»;
  * сеть выключается флагом, а не надеждой;
  * дом агента связан на запись, код продукта — только на чтение;
  * когда bubblewrap недоступен, ограда отказывается ВСЛУХ, и `fence.install`
    записывает причину, а не оставляет над незащищённой командой слово
    «bubblewrap».
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import fence  # noqa: E402
import fence_posix  # noqa: E402

LINUX = sys.platform.startswith("linux")
BWRAP = shutil.which("bwrap")


class CommandLine(unittest.TestCase):
    """Что уйдёт ядру. Разбирается на любой платформе: это чистый расчёт."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tree = self.root / "data"
        self.workspace = self.tree / "workspace"
        self.workspace.mkdir(parents=True)
        (self.tree / "memory").mkdir()
        self.addCleanup(self.tmp.cleanup)

    def make(self, network=True):
        box = fence_posix.Container(self.root, self.workspace, network, tree=self.tree,
                                    secrets=fence.secret_paths(self.root, self.tree))
        box.bwrap = "/usr/bin/bwrap"        # prepare() здесь не зовём: он про машину
        return box

    def test_дом_на_запись_код_на_чтение(self):
        line = " ".join(self.make().argv(["bash", "-lc", "ls"], self.workspace))
        self.assertIn(f"--bind {self.workspace} {self.workspace}", line)
        self.assertIn(f"--ro-bind-try {self.root / 'app'}", line)
        self.assertIn(f"--ro-bind-try {self.root / 'tree'}", line)

    def test_секреты_закрыты_поверх_памяти(self):
        line = self.make().argv(["bash", "-lc", "ls"], self.workspace)
        text = " ".join(line)
        # Память связана — иначе агент не увидит собственную память…
        self.assertIn(f"--bind-try {self.tree / 'memory'}", text)
        # …а ключи внутри неё закрыты пустышкой.
        self.assertIn(f"--bind-try /dev/null {self.tree / 'memory' / 'llm.json'}", text)
        self.assertIn(f"--bind-try /dev/null {self.root / 'helene.json'}", text)
        # Папки секретов — пустой tmpfs поверх (если они есть на диске).
        (self.tree / "relay").mkdir()
        text2 = " ".join(self.make().argv(["bash", "-lc", "ls"], self.workspace))
        self.assertIn(f"--tmpfs {self.tree / 'relay'}", text2)

    def test_сеть_выключается_флагом(self):
        self.assertIn("--unshare-net", self.make(network=False).argv(["true"], self.workspace))
        self.assertNotIn("--unshare-net", self.make(network=True).argv(["true"], self.workspace))

    def test_рабочая_папка_и_среда(self):
        line = " ".join(self.make().argv(["true"], self.tree / "workspace" / "проект"))
        self.assertIn("--chdir", line)
        self.assertIn("проект", line)
        self.assertIn("--setenv HELENE_SANDBOX 1", line)
        self.assertIn(f"--setenv HOME {self.workspace}", line)

    def test_смонтированная_папка_по_праву_доступа(self):
        box = self.make()
        box.sync_mounts([
            {"path": "/home/yegor/docs", "link": str(self.workspace / "mnt" / "docs"),
             "access": "read"},
            {"path": "/home/yegor/work", "link": str(self.workspace / "mnt" / "work"),
             "access": "write"},
        ])
        line = " ".join(box.argv(["true"], self.workspace))
        self.assertIn("--ro-bind-try /home/yegor/docs", line)
        self.assertIn("--bind-try /home/yegor/work", line)

    def test_дети_умирают_вместе_с_нами(self):
        line = self.make().argv(["true"], self.workspace)
        self.assertIn("--die-with-parent", line)
        self.assertIn("--unshare-pid", line,
                      "без своего пространства процессов таймаут обрывал бы только "
                      "родителя, а внуки жили бы дальше")


@unittest.skipUnless(LINUX and BWRAP, "нужен Linux с bubblewrap")
class Live(unittest.TestCase):
    """Живая ограда: то, что нельзя доказать разбором строки."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tree = self.root / "data"
        self.workspace = self.tree / "workspace"
        self.workspace.mkdir(parents=True)
        (self.tree / "memory").mkdir()
        (self.tree / "memory" / "llm.json").write_text('{"key": "sk-живой"}', encoding="utf-8")
        (self.root / "helene.json").write_text('{"model": {"key": "sk-живой"}}', encoding="utf-8")
        self.box = fence_posix.Container(self.root, self.workspace, True, tree=self.tree,
                                         secrets=fence.secret_paths(self.root, self.tree))
        try:
            self.box.prepare()
        except fence_posix.FenceUnavailable as exc:
            self.skipTest(f"ограда недоступна в этой среде: {exc}")
        self.addCleanup(self.tmp.cleanup)

    def test_команда_идёт_и_пишет_в_дом(self):
        out, code, timed = self.box.run(["bash", "-lc", "echo привет > note.txt && cat note.txt"],
                                        self.workspace, 30)
        self.assertEqual(code, 0, out)
        self.assertFalse(timed)
        self.assertIn("привет", out)
        self.assertTrue((self.workspace / "note.txt").is_file(),
                        "запись в доме обязана доехать до диска, а не остаться в песке")

    def test_ключи_владельца_не_читаются(self):
        out, code, _ = self.box.run(["bash", "-lc", f"cat {self.root / 'helene.json'}"],
                                    self.workspace, 30)
        self.assertNotIn("sk-живой", out, "ключ владельца прочитался ИЗ ОГРАДЫ")
        out2, _code2, _ = self.box.run(
            ["bash", "-lc", f"cat {self.tree / 'memory' / 'llm.json'}"], self.workspace, 30)
        self.assertNotIn("sk-живой", out2)

    def test_память_агента_видна(self):
        (self.tree / "memory" / "note.md").write_text("это моя память", encoding="utf-8")
        out, code, _ = self.box.run(["bash", "-lc", f"cat {self.tree / 'memory' / 'note.md'}"],
                                    self.workspace, 30)
        self.assertEqual(code, 0, out)
        self.assertIn("это моя память", out)

    def test_чужая_папка_не_видна(self):
        outside = self.root.parent / f"чужое-{os.getpid()}"
        outside.mkdir()
        (outside / "секрет.txt").write_text("не для агента", encoding="utf-8")
        self.addCleanup(shutil.rmtree, outside, True)
        out, code, _ = self.box.run(["bash", "-lc", f"cat {outside / 'секрет.txt'}"],
                                    self.workspace, 30)
        self.assertNotEqual(code, 0, "папка вне ограды прочиталась: " + out)

    def test_без_сети_наружу_не_ходит(self):
        box = fence_posix.Container(self.root, self.workspace, False, tree=self.tree)
        box.prepare()
        out, code, _ = box.run(["bash", "-lc", "getent hosts example.com || echo НЕТ-СЕТИ"],
                               self.workspace, 30)
        self.assertIn("НЕТ-СЕТИ", out, out)

    def test_таймаут_обрывает_дерево(self):
        out, code, timed = self.box.run(["bash", "-lc", "sleep 30"], self.workspace, 3)
        self.assertTrue(timed)
        self.assertEqual(code, 124)


@unittest.skipUnless(LINUX, "про отказ на машине без bubblewrap")
class Refusal(unittest.TestCase):
    def test_нет_bwrap_отказ_словами(self):
        box = fence_posix.Container(Path("/tmp"), Path("/tmp/ws"), True)
        real = shutil.which
        fence_posix.shutil.which = lambda name: None
        try:
            with self.assertRaises(fence_posix.FenceUnavailable) as caught:
                box.prepare()
        finally:
            fence_posix.shutil.which = real
        self.assertIn("bubblewrap", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
