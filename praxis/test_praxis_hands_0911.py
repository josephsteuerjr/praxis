# -*- coding: utf-8 -*-
"""Скрипт внутри run правит файлы теми же руками, что fs_edit (09.11.2026).

Запускается настоящий отдельный процесс: так работает скрипт из shell, run и coding_run.

Запуск: python praxis_test.py test_praxis_hands_0911 -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402

REPO = Path(__file__).resolve().parent


class TheScriptHands(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "workspace").mkdir()
        (self.home / "memory/.state").mkdir(parents=True)
        self.env = {**os.environ, "PRAXIS_BASE": str(self.home), "PRAXIS_HANDS_ROOT": str(REPO),
                    "PYTHONPATH": str(REPO / "pyhands"), "PYTHONDONTWRITEBYTECODE": "1"}

    def script(self, body: str) -> dict:
        code = "import json, praxis_hands as h\nout = {}\n" + body + "\nprint('@@' + json.dumps(out, ensure_ascii=False))\n"
        p = subprocess.run([sys.executable, "-B", "-c", code], cwd=self.home, env=self.env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return json.loads(p.stdout.rsplit("@@", 1)[1])

    def test_write_read_edit_with_version(self):
        out = self.script('''
out["written"] = h.write("workspace/a.txt", "alpha\\n")
text, version = h.read("workspace/a.txt")
out["text"], out["version"] = text, version
out["edited"] = h.edit("workspace/a.txt", "alpha", "bravo", expected_sha256=version)
''')
        self.assertEqual(out["text"], "alpha\n")
        self.assertEqual(out["written"], out["version"])
        self.assertNotEqual(out["edited"], out["version"])
        self.assertEqual((self.home / "workspace/a.txt").read_text(encoding="utf-8"), "bravo\n")

    def test_a_stale_version_is_refused_and_the_file_survives(self):
        (self.home / "workspace/a.txt").write_text("alpha\n", encoding="utf-8")
        out = self.script('''
try:
    h.edit("workspace/a.txt", "alpha", "bravo", expected_sha256="0" * 64)
except h.Refused as exc:
    out["refused"] = str(exc)
''')
        self.assertIn("refused", out)
        self.assertEqual((self.home / "workspace/a.txt").read_text(encoding="utf-8"), "alpha\n")

    def test_an_ambiguous_old_is_refused(self):
        (self.home / "workspace/a.txt").write_text("x\nx\n", encoding="utf-8")
        out = self.script('''
try:
    h.edit("workspace/a.txt", "x", "y")
except h.Refused as exc:
    out["refused"] = str(exc)
''')
        self.assertIn("совпадений", out["refused"])

    def test_an_existing_file_needs_overwrite(self):
        (self.home / "workspace/a.txt").write_text("alpha\n", encoding="utf-8")
        out = self.script('''
try:
    h.write("workspace/a.txt", "beta\\n")
except h.Refused as exc:
    out["refused"] = True
''')
        self.assertTrue(out["refused"])

    def test_outside_home_is_refused(self):
        out = self.script('''
try:
    h.read("/etc/hostname")
except h.Refused as exc:
    out["refused"] = str(exc)
''')
        self.assertIn("вне дома", out["refused"])

    def test_search_works(self):
        (self.home / "workspace/a.txt").write_text("needle here\n", encoding="utf-8")
        out = self.script('out["found"] = h.search("needle", glob="**/*.txt", root="workspace")')
        self.assertIn("a.txt:1: needle here", out["found"])


class TheWiring(unittest.TestCase):
    def test_children_see_the_module(self):
        self.assertIn(str(agent._PYHANDS_DIR), os.environ["PYTHONPATH"].split(os.pathsep))

    def test_the_folder_holds_only_this_module(self):
        names = sorted(p.name for p in (REPO / "pyhands").iterdir() if p.name != "__pycache__")
        self.assertEqual(names, ["praxis_hands.py"])

    def test_k_and_the_schemas_name_it(self):
        self.assertIn("import praxis_hands as h", agent._hands_contract_text())
        tools = {t["name"]: t for t in agent.catalog_tools_for(
            agent.ChannelContext(chat_id="42", room_id="42", is_dm=True, owner=True, known=True)) if t.get("name")}
        for name in ("shell", "python", "coding_run"):
            self.assertIn("praxis_hands", tools[name]["description"], name)


if __name__ == "__main__":
    unittest.main()
