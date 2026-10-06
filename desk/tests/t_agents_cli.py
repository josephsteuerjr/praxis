# -*- coding: utf-8 -*-
"""CLI агентов: флаговая форма контракта Rust-волны 1.4.0.

Запуск:  python tests/t_agents_cli.py

    python agents_cli.py list --base <папка>
    python agents_cli.py add  --base <папка> --name "Мира" \
        [--soul-kind canonical|inherit|text] [--soul-file <путь>] [--soul-from <донор>]
    python agents_cli.py set-enabled --base <папка> --id mira --enabled false
    python agents_cli.py remove --base <папка> --id mira [--attic <папка>]

Стенд зовёт программу как зовёт её Rust — отдельным процессом, `PYTHONUTF8=1`,
судя по коду возврата и одной строке JSON в stdout. Проверяется контракт, а не
удобство: отказы — stderr и код 2, успех — код 0; remove ПЕРЕНОСИТ папку в
чердак (не стирает); сид души лежит рядом с конфигом; roster не ломается от
нового файла (`agents_list` читает его же).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESK = HERE.parent
CLI = DESK / "localharness" / "agents_cli.py"
sys.path.insert(0, str(DESK / "localharness"))
import agents  # noqa: E402

SEED = "# Душа Мир\n\nМеня зовут {{agent}}, живу у {{owner}}.\n"
ROOT_CFG = {"agent": {"name": "Hélène"}, "port": 8094}


def run_cli(*args: str, base: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-X", "utf8", str(CLI), *args, "--base", str(base)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=120)


def lay_out(root: Path, files: dict) -> None:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8", newline="\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-cli-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "install"
        self.root.mkdir()
        lay_out(self.root, {"helene.json": ROOT_CFG})

    def seed_file(self) -> Path:
        path = Path(self.tmp.name) / "seed.md"
        path.write_text(SEED, encoding="utf-8", newline="\n")
        return path


class ListAndAdd(Base):
    def test_list_reports_the_roster(self):
        done = run_cli("list", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        got = json.loads(done.stdout)
        self.assertEqual([a["id"] for a in got["agents"]], ["main"])

    def test_add_with_text_soul_writes_the_seed(self):
        done = run_cli("add", "--name", "Мира",
                       "--soul-kind", "text", "--soul-file", str(self.seed_file()),
                       base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        made = json.loads(done.stdout)
        seed = self.root / "agents" / made["id"] / agents.SEED_NAME
        self.assertEqual(seed.read_text(encoding="utf-8"), SEED)
        self.assertNotIn("soul", agents.read_config(self.root / "agents" / made["id"] / "helene.json"))

    def test_add_canonical_and_bare_write_no_seed(self):
        for extra in (["--soul-kind", "canonical"], []):
            done = run_cli("add", "--name", "Зоя", *extra, base=self.root)
            self.assertEqual(done.returncode, 0, done.stderr)
            made = json.loads(done.stdout)
            self.assertFalse((self.root / "agents" / made["id"] / agents.SEED_NAME).exists())

    def test_add_inherit_takes_the_donor_soul(self):
        donor = run_cli("add", "--name", "Донор", base=self.root)
        self.assertEqual(donor.returncode, 0, donor.stderr)
        donor_id = json.loads(donor.stdout)["id"]
        soul = self.root / "agents" / donor_id / "data" / "soul" / "SOUL.md"
        soul.parent.mkdir(parents=True, exist_ok=True)
        soul.write_text("душа донора\n", encoding="utf-8", newline="\n")
        done = run_cli("add", "--name", "Мира",
                       "--soul-kind", "inherit", "--soul-from", donor_id, base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        made = json.loads(done.stdout)
        self.assertEqual((self.root / "agents" / made["id"] / agents.SEED_NAME)
                         .read_text(encoding="utf-8"), "душа донора\n")

    def test_add_inherit_of_a_missing_donor_refuses_without_side_effects(self):
        done = run_cli("add", "--name", "Мира",
                       "--soul-kind", "inherit", "--soul-from", "ghost", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("донор", done.stderr)
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main"])

    def test_add_text_soul_refuses_empty_and_missing_file(self):
        empty = Path(self.tmp.name) / "empty.md"
        empty.write_text("   \n", encoding="utf-8")
        done = run_cli("add", "--name", "Мира",
                       "--soul-kind", "text", "--soul-file", str(empty), base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("пуст", done.stderr)
        done = run_cli("add", "--name", "Мира", "--soul-kind", "text", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--soul-file", done.stderr)
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main"])

    def test_bom_in_the_soul_file_is_tolerated(self):
        bom = Path(self.tmp.name) / "bom.md"
        bom.write_bytes(b"\xef\xbb\xbf" + SEED.encode("utf-8"))
        done = run_cli("add", "--name", "Мира",
                       "--soul-kind", "text", "--soul-file", str(bom), base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        made = json.loads(done.stdout)
        seed = self.root / "agents" / made["id"] / agents.SEED_NAME
        self.assertFalse(seed.read_bytes().startswith(b"\xef\xbb\xbf"), "BOM утёк в сид")
        self.assertIn("Меня зовут", seed.read_text(encoding="utf-8"))

    def test_roster_survives_the_seed_file(self):
        # контракт UI-волны B1: agents_list не ломается от soul-seed.md в папке
        run_cli("add", "--name", "Мира",
                "--soul-kind", "text", "--soul-file", str(self.seed_file()), base=self.root)
        done = run_cli("list", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([a["id"] for a in json.loads(done.stdout)["agents"]],
                         ["main", "mira"])


class SetEnabled(Base):
    def setUp(self):
        super().setUp()
        lay_out(self.root, {"agents/mira/helene.json": {"agent": {"name": "Мира"}}})

    def test_flag_form_flips_the_agent(self):
        done = run_cli("set-enabled", "--id", "mira", "--enabled", "false", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["ok"])
        self.assertFalse(agents.find(self.root, "mira").enabled)
        done = run_cli("set-enabled", "--id", "mira", "--enabled", "true", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(agents.find(self.root, "mira").enabled)

    def test_bad_word_base_and_missing_refuse(self):
        done = run_cli("set-enabled", "--id", "mira", "--enabled", "выкл", base=self.root)
        self.assertEqual(done.returncode, 2)
        done = run_cli("set-enabled", "--id", "main", "--enabled", "false", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("корневой", done.stderr)
        done = run_cli("set-enabled", "--id", "ghost", "--enabled", "true", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("не найден", done.stderr)


class Remove(Base):
    def setUp(self):
        super().setUp()
        lay_out(self.root, {"agents/mira/helene.json": {"agent": {"name": "Мира"}}})
        (self.root / "agents" / "mira" / agents.SEED_NAME).write_text(
            SEED, encoding="utf-8", newline="\n")
        (self.root / "agents" / "mira" / "data" / "memory").mkdir(parents=True)
        (self.root / "agents" / "mira" / "data" / "memory" / "llm.json").write_text(
            "{}", encoding="utf-8", newline="\n")

    def test_remove_moves_the_whole_folder_to_the_attic(self):
        attic = Path(self.tmp.name) / "attic"
        done = run_cli("remove", "--id", "mira", "--attic", str(attic), base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        dest = Path(json.loads(done.stdout)["attic"])
        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "helene.json").is_file())
        self.assertTrue((dest / agents.SEED_NAME).is_file())
        self.assertTrue((dest / "data" / "memory" / "llm.json").is_file(), "дом уехал целиком")
        self.assertFalse((self.root / "agents" / "mira").exists())
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main"])

    def test_remove_refusals(self):
        for agent_id, word in (("main", "корневой"), ("ghost", "нет"), ("", "не назван")):
            done = run_cli("remove", "--id", agent_id, base=self.root)
            self.assertEqual(done.returncode, 2, agent_id or "(пусто)")
            self.assertIn(word, done.stderr)
        self.assertTrue((self.root / "agents" / "mira" / "helene.json").is_file(),
                        "отказ не тронул агента")

    def test_unwritable_attic_refuses_and_keeps_the_agent(self):
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("файл, а не папка\n", encoding="utf-8")
        done = run_cli("remove", "--id", "mira", "--attic", str(blocker / "attic"),
                       base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("чердак", done.stderr)
        self.assertTrue((self.root / "agents" / "mira" / "helene.json").is_file(),
                        "агент остался на месте")


if __name__ == "__main__":
    unittest.main(verbosity=2)
