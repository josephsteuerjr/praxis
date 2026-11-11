# -*- coding: utf-8 -*-
"""Приёмка единого edit, 10.11.2026: четыре дефекта, найденные после выкладки.

Настоящие отдельные процессы: так работает скрипт из shell, project_run и coding_run.

Запуск: python praxis_test.py test_edit_accept_1011 -v
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PRAXIS_TEST", "1")

import forge  # noqa: E402
import forge_script  # noqa: E402
import workshop  # noqa: E402

REPO = Path(__file__).resolve().parent
DEAD = re.compile(r"\b(?:fs_edit|coding_edit)\b")
SHADOWS = ("editor", "workshop", "forge", "hands", "edit_batch", "forge_script")


class Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "workspace").mkdir()
        (self.home / "memory/.state").mkdir(parents=True)
        self.env = {**os.environ, "PRAXIS_BASE": str(self.home), "PRAXIS_HANDS_ROOT": str(REPO),
                    "PYTHONPATH": str(REPO / "pyhands"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.env.pop(forge_script.ENV, None)

    def script(self, body: str, cwd=None) -> dict:
        code = "import json, praxis_hands as h\nout = {}\n" + body + "\nprint('@@' + json.dumps(out, ensure_ascii=False))\n"
        p = subprocess.run([sys.executable, "-B", "-c", code], cwd=cwd or self.home, env=self.env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return json.loads(p.stdout.rsplit("@@", 1)[1])


class TheBaseStaysWithItsCheckout(unittest.TestCase):
    def issue(self, base):
        with patch.dict(os.environ):
            os.environ.pop("PRAXIS_BASE", None)
            env, _ = forge_script.issue("code-x", "proc-x", "", base=base)
        return env

    def test_the_default_base_is_not_named_to_a_command(self):
        self.assertNotIn("PRAXIS_BASE", self.issue(REPO))

    def test_a_deliberately_other_base_still_is(self):
        self.assertEqual(self.issue("/tmp/elsewhere")["PRAXIS_BASE"], "/tmp/elsewhere")


class ProjectFilesAndTheCore(Home):
    def setUp(self):
        super().setUp()
        self.project = self.home / "workspace/proj"
        self.project.mkdir()
        for name in SHADOWS:
            (self.project / f"{name}.py").write_text(f'ORIGIN = "project-{name}"\n', encoding="utf-8")

    def test_project_modules_imported_first_survive_the_hands(self):
        out = self.script('''
import editor, workshop, forge
h.write("workspace/proj/a.txt", "alpha\\n")
text, version = h.read("workspace/proj/a.txt")
h.edit("workspace/proj/a.txt", "alpha", "bravo", expected_sha256=version)
out["found"] = "bravo" in h.search("bravo", glob="**/*.txt", root="workspace/proj")
import editor as again
out["origin"] = again.ORIGIN
out["same"] = again is editor
out["forge"] = forge.ORIGIN
''', cwd=self.project)
        self.assertEqual(out, {"found": True, "origin": "project-editor", "same": True, "forge": "project-forge"})
        self.assertEqual((self.project / "a.txt").read_text(encoding="utf-8"), "bravo\n")

    def test_a_project_module_imported_after_the_hands_is_the_projects(self):
        out = self.script('''
h.write("workspace/proj/a.txt", "alpha\\n")
import editor
out["origin"] = editor.ORIGIN
''', cwd=self.project)
        self.assertEqual(out["origin"], "project-editor")

    def test_the_core_comes_from_the_root_of_the_hands(self):
        out = self.script('''
with h._core():
    import forge as core
    out["core"] = core.__file__
import forge as project
out["project"] = project.ORIGIN
''', cwd=self.project)
        self.assertEqual(Path(out["core"]).resolve(), REPO / "forge.py")
        self.assertEqual(out["project"], "project-forge")


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires actual Linux command custody")
class AForgeCommandInAProjectWithCoreNames(unittest.TestCase):
    def test_a_foreground_script_edits_its_own_task(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        root = base / "project"
        root.mkdir()
        (root / "a").write_text("alpha\n", encoding="utf-8")
        for name in SHADOWS:
            (root / f"{name}.py").write_text(f'ORIGIN = "project-{name}"\n', encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@local", "commit", "-qm", "i"], check=True)
        for obj, name, value in ((forge, "BASE", base), (forge, "STATE_DIR", base / "memory/.forge"),
                                 (forge, "TASKS_DIR", base / "memory/.forge/tasks"), (workshop, "BASE", base)):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"PRAXIS_BASE": str(base), "PRAXIS_HANDS_ROOT": str(REPO)})
        env.start()
        self.addCleanup(env.stop)
        task = re.search(r"code-[a-f0-9]+", forge.start("names", target=str(root), isolation="direct")).group()
        code = ('import praxis_hands as h\ntext, version = h.read("a")\n'
                'h.edit("a", "alpha", "bravo", expected_sha256=version)\n'
                'import editor\nassert editor.ORIGIN == "project-editor"\nprint("SCRIPT-PASS")\n')
        out = forge.run(task, shlex.quote(sys.executable) + " -B -c " + shlex.quote(code), timeout=30)
        self.assertIn("SCRIPT-PASS", out, out)
        self.assertEqual((root / "a").read_text(encoding="utf-8"), "bravo\n")


class EveryHomeEditLeavesAReceipt(Home):
    def test_replace_write_batch_and_refusal(self):
        self.script('''
a = h.write("workspace/a.txt", "one\\n")
b = h.write("workspace/b.txt", "two\\n")
a = h.edit("workspace/a.txt", "one", "uno", expected_sha256=a)
h.edit(files=[{"path": "workspace/a.txt", "expected_sha256": a, "edits": [{"old": "uno", "new": "eins"}]},
              {"path": "workspace/b.txt", "expected_sha256": b, "edits": [{"old": "two", "new": "zwei"}]}])
try:
    h.edit("workspace/a.txt", "нет такого", "x")
except h.Refused:
    pass
''')
        rows = [json.loads(line) for line in (self.home / "memory/.state/hands.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["op"], r["ok"], r["target"]) for r in rows],
                         [("write", True, "workspace/a.txt"), ("write", True, "workspace/b.txt"),
                          ("edit", True, "workspace/a.txt"), ("edit", True, "workspace/a.txt"),
                          ("edit", True, "workspace/b.txt"), ("edit", False, "workspace/a.txt")])


class TheWordsNameLiveHands(Home):
    def test_no_skill_names_a_dead_hand(self):
        skills = sorted((REPO / "soul/skills").glob("*.md"))
        self.assertTrue(skills)
        for skill in skills:
            self.assertIsNone(DEAD.search(skill.read_text(encoding="utf-8")), skill.name)

    def test_write_refusals_name_edit(self):
        out = self.script('''
h.write("workspace/a.txt", "x" * 100)
for key, kwargs in (("exists", {}), ("shrink", {"overwrite": True})):
    try:
        h.write("workspace/a.txt", "y" * 10, **kwargs)
    except h.Refused as exc:
        out[key] = str(exc)
''')
        for key in ("exists", "shrink"):
            self.assertIn("edit", out[key])
            self.assertIsNone(DEAD.search(out[key]), out[key])

    def test_a_non_utf8_read_names_edit(self):
        (self.home / "workspace/latin.txt").write_bytes(b"caf\xe9\n")
        out = self.script('''
with h._core():
    import workshop
    out["read"] = workshop.fs_read("workspace/latin.txt")
''')
        self.assertIn("do not edit from this view", out["read"])
        self.assertIsNone(DEAD.search(out["read"]), out["read"])


if __name__ == "__main__":
    unittest.main()
