# -*- coding: utf-8 -*-
"""Переезд агента вместе с его правками в своём коде (1.2.5, слово Егора 28.09).

«Йоно может вернуться… или её дом переедет с ней?» До 1.2.5 архив переноса вёз только
`data/` — правки агента в `tree/` и `app/` оставались на старом месте. Теперь в архиве
`code/`: правленые файлы и их чистые версии (или отпечатки); на новом месте они ложатся
на его код тем же переносчиком, что при обновлении, а что не легло — агенту в workspace.

Запуск:  python tests/t_carry_code_2809.py
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))
sys.path.insert(0, str(DESK / "server" / "updater"))

import carry  # noqa: E402
import codecarry as cc  # noqa: E402

HAS_GIT = shutil.which("git") is not None
V1 = {"tree/agent.py": "def a():\n    return 1\n\n\ndef b():\n    return 2\n",
      "tree/llm.py": "M = 'a'\n", "tree/keep.py": "k = 1\n", "app/localharness/runner.py": "R = 1\n"}
V2 = {"tree/agent.py": "def a():\n    return 1\n\n\ndef b():\n    return 20\n",
      "tree/llm.py": "M = 'b'\n", "tree/keep.py": "k = 1\n", "app/localharness/runner.py": "R = 2\n"}


def put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def install(root: Path, version: str, files: dict, *, pristine: bool = True) -> Path:
    for rel, text in files.items():
        put(root, rel, text)
    put(root, "helene-build.json", json.dumps({"version": version, "desk": {"flavor": "windows"}}))
    put(root, "helene.json", json.dumps({"tree": "data", "installed": {"version": version},
                                         "agent": {"name": "Йона"}}))
    put(root, "data/soul/SOUL.md", "душа\n")
    if pristine:
        cc.write_pristine(root, version, cc.pristine_path(root, version))
    return root / "helene.json"


def edit_as_agent(root: Path) -> None:
    put(root, "tree/agent.py", "def a():\n    return 100\n\n\ndef b():\n    return 2\n")
    put(root, "tree/llm.py", "M = 'mine'\n")
    put(root, "tree/keep.py", "k = 42\n")
    put(root, "tree/mine.py", "mine = 1\n")


def quiet(fn, *a, **k):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class Move(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="carry-code-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_edits_travel_and_land_on_a_newer_home(self):
        src = install(self.tmp / "pc", "1.0.0", V1)
        edit_as_agent(self.tmp / "pc")
        passport = quiet(carry.export, src, self.tmp / "yona.zip")
        self.assertEqual(passport["code"]["base"], "pristine")
        self.assertEqual(passport["code"]["edited"], 4)
        with zipfile.ZipFile(self.tmp / "yona.zip") as zf:
            names = set(zf.namelist())
        self.assertIn("code/mine/tree/keep.py", names)
        self.assertIn("code/base/tree/keep.py", names)
        self.assertNotIn("code/mine/app/localharness/runner.py", names, "неправленое не едет")
        dst = install(self.tmp / "server", "1.1.0", V2)
        receipt = quiet(carry.import_, dst, self.tmp / "yona.zip")
        code = receipt["code"]
        home = self.tmp / "server"
        self.assertEqual((home / "tree" / "keep.py").read_text("utf-8"), "k = 42\n")
        self.assertEqual((home / "tree" / "mine.py").read_text("utf-8"), "mine = 1\n")
        self.assertEqual((home / "tree" / "llm.py").read_text("utf-8"), "M = 'b'\n", "не легло — вариант места")
        self.assertIn("tree/llm.py", {r["path"] for r in code["conflicts"]})
        folder = home / "data" / code["folder"]
        self.assertTrue(folder.name.startswith("carry-1.0.0"))
        self.assertEqual((folder / "tree" / "llm.py.mine").read_text("utf-8"), "M = 'mine'\n")
        self.assertIn("# Переезд", (folder / "README.md").read_text("utf-8"))
        if HAS_GIT:
            self.assertEqual((home / "tree" / "agent.py").read_text("utf-8"),
                             "def a():\n    return 100\n\n\ndef b():\n    return 20\n")

    def test_prints_only_base_still_carries_what_the_release_did_not_touch(self):
        src = install(self.tmp / "pc", "1.2.4", V1, pristine=False)
        edit_as_agent(self.tmp / "pc")
        prints = {rel: hashlib.sha256(text.encode()).hexdigest() for rel, text in V1.items()}
        table = self.tmp / "shipped.json"
        table.write_text(json.dumps({"1.2.4/windows": prints}), "utf-8")
        orig = cc.SHIPPED
        cc.SHIPPED = table
        try:
            passport = quiet(carry.export, src, self.tmp / "yona.zip")
        finally:
            cc.SHIPPED = orig
        self.assertEqual(passport["code"]["base"], "prints")
        dst = install(self.tmp / "pc2", "1.2.4", V1)
        code = quiet(carry.import_, dst, self.tmp / "yona.zip")["code"]
        home = self.tmp / "pc2"
        self.assertEqual((home / "tree" / "keep.py").read_text("utf-8"), "k = 42\n")
        self.assertEqual((home / "tree" / "llm.py").read_text("utf-8"), "M = 'mine'\n",
                         "тот же выпуск на новом месте — правка ложится как есть")
        self.assertEqual(code["conflicts"], [])

    def test_without_any_base_the_whole_old_code_goes_to_the_agent(self):
        src = install(self.tmp / "pc", "0.9.0", V1, pristine=False)
        edit_as_agent(self.tmp / "pc")
        passport = quiet(carry.export, src, self.tmp / "yona.zip")
        self.assertTrue(passport["code"]["whole"])
        dst = install(self.tmp / "srv", "1.1.0", V2)
        code = quiet(carry.import_, dst, self.tmp / "yona.zip")["code"]
        folder = self.tmp / "srv" / "data" / code["folder"]
        self.assertEqual((folder / "old-code" / "tree" / "keep.py").read_text("utf-8"), "k = 42\n")
        self.assertEqual((self.tmp / "srv" / "tree" / "keep.py").read_text("utf-8"), "k = 1\n")

    def test_old_archives_without_code_still_import(self):
        src = install(self.tmp / "pc", "1.0.0", V1)
        quiet(carry.export, src, self.tmp / "yona.zip")
        # архив до 1.2.5 — без code/
        with zipfile.ZipFile(self.tmp / "yona.zip") as zin, \
                zipfile.ZipFile(self.tmp / "old.zip", "w") as zout:
            for info in zin.infolist():
                if not info.filename.startswith("code/"):
                    zout.writestr(info, zin.read(info))
        dst = install(self.tmp / "srv", "1.1.0", V2)
        receipt = quiet(carry.import_, dst, self.tmp / "old.zip")
        self.assertEqual(receipt["code"], {})
        self.assertTrue((self.tmp / "srv" / "data" / "soul" / "SOUL.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
