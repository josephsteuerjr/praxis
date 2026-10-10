# -*- coding: utf-8 -*-
"""Перенос правок агента в своём коде на ПК (1.2.5, слово Егора 28.09).

«Нужно ужесточить приёмку обновлений агентом — и на сервере, и на десктопе: Йоно может
вернуться… или её дом переедет с ней?» До 1.2.5 установщик ПК менял `tree/` и `app/`
целиком — правки агента в своём коде пропадали при каждом обновлении. Теперь установщик,
подменив программу, зовёт `server/updater/codecarry.py desk`: чистая копия новой версии —
у установки (база следующего раза), правки агента против чистой прежней — на новую
версию, что не легло — ему в `data/workspace/update-<версия>/`.

Запуск:  python tests/t_codecarry_2809.py
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "server" / "updater"))

import codecarry as cc  # noqa: E402

HAS_GIT = shutil.which("git") is not None


def put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


RELEASE_1 = {
    "tree/agent.py": "def hello():\n    return 1\n\n\ndef other():\n    return 2\n",
    "tree/llm.py": "MODEL = 'a'\n",
    "tree/old.py": "gone = True\n",
    "tree/keep.py": "x = 1\n",
    "app/localharness/runner.py": "RUN = 1\n",
    "app/static/index.html": "<p>1</p>\n",
}
RELEASE_2 = {
    "tree/agent.py": "def hello():\n    return 1\n\n\ndef other():\n    return 22\n",
    "tree/llm.py": "MODEL = 'b'\n",
    "tree/old.py": "gone = True\n",
    "tree/keep.py": "x = 1\n",
    "tree/fresh.py": "new = 1\n",
    "app/localharness/runner.py": "RUN = 2\n",
    "app/static/index.html": "<p>2</p>\n",
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-"))
        self.old = self.tmp / "Helene.old"      # прежняя программа (код агента)
        self.new = self.tmp / "Helene"          # установка после подмены
        for rel, text in RELEASE_1.items():
            put(self.old, rel, text)
        for rel, text in RELEASE_2.items():
            put(self.new, rel, text)
        (self.new / "data" / "workspace").mkdir(parents=True)
        put(self.new, "helene-build.json", json.dumps({"version": "1.1.0", "desk": {"flavor": "windows"}}))
        # Агент правил: hello (выпуск не трогал эту часть — сольётся), llm.py (конфликт),
        # keep.py (выпуск не трогал — как есть), удалил old.py, добавил mine.py.
        put(self.old, "tree/agent.py", "def hello():\n    return 100\n\n\ndef other():\n    return 2\n")
        put(self.old, "tree/llm.py", "MODEL = 'mine'\n")
        put(self.old, "tree/keep.py", "x = 42\n")
        (self.old / "tree" / "old.py").unlink()
        put(self.old, "tree/mine.py", "mine = 1\n")
        # Интерфейс окна у прежней уехал (установщик ведёт его сам) — это не «агент удалил».
        shutil.rmtree(self.old / "app" / "static")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, rel: str) -> str:
        return (self.new / rel).read_text("utf-8")


class ByPrints(Base):
    """1.2.4 -> 1.2.5: чистой копии у установки нет, есть отпечатки выпуска."""

    def carry(self):
        prints = {rel: sha(text) for rel, text in RELEASE_1.items() if not rel.startswith("app/static")}
        table = self.tmp / "shipped.json"
        table.write_text(json.dumps({"1.0.0/windows": prints}), "utf-8")
        return cc.carry_install(self.old, self.new, data=self.new / "data", from_version="1.0.0",
                                to_version="1.1.0", work=self.tmp / "work", prints_path=table)

    def test_untouched_by_release_is_carried_as_is(self):
        rep = self.carry()
        self.assertEqual(rep["base"], "prints")
        self.assertEqual(self.read("tree/keep.py"), "x = 42\n")
        self.assertEqual(self.read("tree/mine.py"), "mine = 1\n")
        self.assertFalse((self.new / "tree" / "old.py").exists(), "удалённое агентом — удалено")
        self.assertIn("tree/keep.py", rep["carried"])

    def test_touched_by_both_goes_to_materials_without_merge(self):
        rep = self.carry()
        conflicts = {row["path"] for row in rep["conflicts"]}
        self.assertEqual(conflicts, {"tree/agent.py", "tree/llm.py"})
        self.assertEqual(self.read("tree/llm.py"), "MODEL = 'b'\n", "в новой — вариант выпуска")
        folder = self.new / "data" / rep["folder"]
        self.assertEqual((folder / "tree" / "llm.py.mine").read_text("utf-8"), "MODEL = 'mine'\n")
        self.assertEqual((folder / "tree" / "llm.py.theirs").read_text("utf-8"), "MODEL = 'b'\n")
        self.assertIn("только её отпечаток", next(r["why"] for r in rep["conflicts"]
                                                 if r["path"] == "tree/llm.py"))
        self.assertTrue((folder / "README.md").is_file())

    def test_window_interface_is_not_an_agent_edit(self):
        rep = self.carry()
        self.assertFalse([p for p in rep["edited"] if "static" in p])
        self.assertEqual(self.read("app/static/index.html"), "<p>2</p>\n")

    def test_other_platform_prints_are_not_used(self):
        table = self.tmp / "shipped.json"
        table.write_text(json.dumps({"1.0.0/macos": {"tree/keep.py": sha("x = 1\n")}}), "utf-8")
        self.assertIsNone(cc.shipped_prints("1.0.0", "windows", table))
        self.assertIsNotNone(cc.shipped_prints("1.0.0", "macos", table))


class ByPristine(Base):
    """1.2.5 и дальше: чистая копия прежней версии лежит у установки zip-ом."""

    def setUp(self):
        super().setUp()
        clean = self.tmp / "clean1"
        for rel, text in RELEASE_1.items():
            put(clean, rel, text)
        cc.write_pristine(clean, "1.0.0", cc.pristine_path(self.new, "1.0.0"))

    def test_pristine_zip_skips_window_interface_and_keeps_prints(self):
        with zipfile.ZipFile(cc.pristine_path(self.new, "1.0.0")) as zf:
            names = set(zf.namelist())
            manifest = json.loads(zf.read(cc.PRISTINE_MANIFEST))
        self.assertIn("tree/agent.py", names)
        self.assertNotIn("app/static/index.html", names)
        self.assertEqual(manifest["files"]["tree/keep.py"], sha("x = 1\n"))

    @unittest.skipUnless(HAS_GIT, "нет git")
    def test_three_way_merge_with_pristine_base(self):
        rep = cc.carry_install(self.old, self.new, data=self.new / "data", from_version="1.0.0",
                               to_version="1.1.0", work=self.tmp / "work")
        self.assertEqual(rep["base"], "pristine")
        self.assertIn("tree/agent.py", rep["merged"])
        self.assertEqual(self.read("tree/agent.py"),
                         "def hello():\n    return 100\n\n\ndef other():\n    return 22\n")
        self.assertEqual({r["path"] for r in rep["conflicts"]}, {"tree/llm.py"})
        folder = self.new / "data" / rep["folder"]
        self.assertTrue((folder / "tree" / "llm.py.merged").is_file())
        self.assertTrue((folder / "edits.diff").read_text("utf-8").count("tree/keep.py"))


class DeskEntry(Base):
    """Вход установщика: `codecarry.py desk` целиком — одной командой, ответ — JSON."""

    def run_desk(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cc.desk_main(list(args))
        out = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 0, out)
        return out

    def test_writes_clean_copy_before_carrying(self):
        table = self.tmp / "shipped.json"
        table.write_text(json.dumps({}), "utf-8")
        out = self.run_desk("desk", "--old", str(self.old), "--new", str(self.new),
                            "--from", "0.9", "--to", "1.1.0")
        self.assertTrue(out["ok"])
        with zipfile.ZipFile(cc.pristine_path(self.new, "1.1.0")) as zf:
            # чистая 1.1.0 — как выпущена, без правок агента, которые легли следом
            self.assertEqual(zf.read("tree/llm.py").decode(), "MODEL = 'b'\n")
        self.assertTrue(cc.after_carry_path(self.new, "1.1.0").is_file())

    @unittest.skipUnless(HAS_GIT, "git merge-file required")
    def test_cli_proves_carried_and_merged_bytes_including_additions_and_deletions(self):
        clean = self.tmp / "clean"
        for rel, text in RELEASE_1.items():
            put(clean, rel, text)
        cc.write_pristine(clean, "1.0.0", cc.pristine_path(self.new, "1.0.0"))
        out = self.run_desk("desk", "--old", str(self.old), "--new", str(self.new),
                            "--from", "1.0.0", "--to", "1.1.0")
        self.assertIn("tree/agent.py", out["merged"])
        self.assertIn("tree/keep.py", out["carried"])
        self.assertIn("tree/mine.py", out["carried"])
        self.assertIn("tree/old.py", out["carried"])
        proof = out["applied_code_sha256"]
        self.assertEqual(set(proof), set(out["carried"] + out["merged"]))
        self.assertIsNone(proof["tree/old.py"])
        for rel, digest in proof.items():
            if digest is not None:
                self.assertEqual(digest, hashlib.sha256((self.new / rel).read_bytes()).hexdigest())
        self.assertNotIn("tree/llm.py", proof, "conflicts keep release bytes and saved agent materials")
        self.assertEqual(self.read("tree/agent.py"), "def hello():\n    return 100\n\n\ndef other():\n    return 22\n")
        self.assertEqual(self.read("tree/llm.py"), "MODEL = 'b'\n")

    def test_reinstall_of_the_same_version_other_build_is_not_an_agent_edit(self):
        # 29.09, ПК Егора: 1.2.5 поверх 1.2.5 другой сборки. Чистую копию 1.2.5 перезаписывали
        # новой сборкой ДО сверки — база совпала с новой, и разница двух сборок стала «правками
        # агента»: 13 файлов старой сборки легли поверх новой, а в 1.2.6 так и уехали.
        shutil.rmtree(self.old)
        for rel, text in RELEASE_1.items():
            if not rel.startswith("app/static"):
                put(self.old, rel, text)
        build_a = self.tmp / "build-a"
        for rel, text in RELEASE_1.items():
            put(build_a, rel, text)
        cc.write_pristine(build_a, "1.1.0", cc.pristine_path(self.new, "1.1.0"))
        out = self.run_desk("desk", "--old", str(self.old), "--new", str(self.new),
                            "--from", "1.1.0", "--to", "1.1.0")
        self.assertEqual(out["edited"], [], "агент ничего не правил")
        self.assertEqual(self.read("app/localharness/runner.py"), "RUN = 2\n")
        self.assertEqual(self.read("tree/llm.py"), "MODEL = 'b'\n")
        self.assertTrue((self.new / "tree" / "fresh.py").is_file())
        with zipfile.ZipFile(cc.pristine_path(self.new, "1.1.0")) as zf:
            self.assertEqual(zf.read("tree/llm.py").decode(), "MODEL = 'b'\n", "копия — новой сборки")

    def test_without_any_base_gives_differing_old_code(self):
        out = self.run_desk("desk", "--old", str(self.old), "--new", str(self.new),
                            "--from", "0.9", "--to", "1.1.0")
        self.assertIn("no_base", out)
        folder = self.new / "data" / out["folder"]
        self.assertEqual((folder / "old-code" / "tree" / "keep.py").read_text("utf-8"), "x = 42\n")
        self.assertFalse((folder / "old-code" / "tree" / "old.py").exists())
        self.assertIn("отличаются от 1.1.0", (folder / "README.md").read_text("utf-8"))
        self.assertEqual(self.read("tree/keep.py"), "x = 1\n", "без базы в новой — выпуск как есть")

    def test_trial_edits_go_to_the_agent_before_rollback(self):
        self.run_desk("desk", "--old", str(self.old), "--new", str(self.new), "--from", "0.9", "--to", "1.1.0")
        put(self.new, "tree/fresh.py", "new = 'edited on trial'\n")
        (self.new / "tree" / "keep.py").unlink()
        out = self.run_desk("trial-edits", "--install", str(self.new), "--to", "1.1.0")
        self.assertEqual(sorted(out["edited"]), ["tree/fresh.py", "tree/keep.py"])
        folder = self.new / "data" / out["folder"]
        self.assertEqual((folder / "tree" / "fresh.py").read_text("utf-8"), "new = 'edited on trial'\n")
        self.assertIn("удалён", (folder / "README.md").read_text("utf-8"))

    def test_old_clean_copies_are_pruned(self):
        stale = cc.pristine_path(self.new, "0.1")
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"x")
        self.run_desk("desk", "--old", str(self.old), "--new", str(self.new), "--from", "0.9", "--to", "1.1.0")
        self.assertFalse(stale.exists())
        self.assertTrue(cc.pristine_path(self.new, "1.1.0").exists())

    def test_cli_runs_as_a_script(self):
        done = subprocess.run([sys.executable, str(DESK / "server" / "updater" / "codecarry.py"), "desk",
                               "--old", str(self.old), "--new", str(self.new), "--from", "0.9",
                               "--to", "1.1.0"], capture_output=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertTrue(json.loads(done.stdout.decode("utf-8").splitlines()[-1])["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
