# -*- coding: utf-8 -*-
"""CLI агентов: флаговая форма контракта Rust-волны 1.4.0.

Запуск:  python tests/t_agents_cli.py

    python agents_cli.py list --base <папка>
    python agents_cli.py add  --base <папка> --name "Мира" \
        [--soul-kind canonical|inherit|text] [--soul-file <путь>] [--soul-from <донор>]
    python agents_cli.py set-enabled --base <папка> --id mira --enabled false
    python agents_cli.py set-default --base <папка> --id mira
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
from unittest.mock import patch
from contextlib import redirect_stderr
from io import StringIO

HERE = Path(__file__).resolve().parent
DESK = HERE.parent
CLI = DESK / "localharness" / "agents_cli.py"
sys.path.insert(0, str(DESK / "localharness"))
import agents  # noqa: E402
import agents_cli  # noqa: E402

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

    def test_create_admission_at_sixteen_is_owned_by_cli(self):
        # 14 neighbours + main = 15; the last free slot is admitted once.
        lay_out(self.root, {f"agents/agent-{i:02}/helene.json": {"port": 8200 + i}
                            for i in range(14)})
        before = {p: p.read_bytes() for p in (self.root / "agents").glob("*/helene.json")}
        last = run_cli("add", "--name", "Последний", base=self.root)
        self.assertEqual(last.returncode, 0, last.stderr)
        self.assertEqual(len(agents.roster(self.root)), 16)
        refused = run_cli("add", "--name", "Лишний", base=self.root)
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertEqual(refused.stdout, "")
        self.assertIn("шестнадцать агентов", refused.stderr)
        self.assertEqual(len(agents.roster(self.root)), 16)
        self.assertFalse((self.root / "agents" / "lishniy").exists())
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))

    def test_concurrent_cli_adds_cannot_both_take_last_slot(self):
        lay_out(self.root, {f"agents/agent-{i:02}/helene.json": {"port": 8200 + i}
                            for i in range(14)})
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        argv = [sys.executable, "-X", "utf8", str(CLI), "add"]
        procs = [subprocess.Popen([*argv, "--name", name, "--base", str(self.root)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                 for name in ("Альфа", "Бета")]
        try:
            results = [p.communicate(timeout=30) for p in procs]
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        self.assertEqual(sorted(p.returncode for p in procs), [0, 2], results)
        self.assertEqual(len(agents.roster(self.root)), 16)
        self.assertEqual(len(list((self.root / "agents").glob("*/kit-birth.json"))), 1)

    def test_unavailable_lock_is_cli_refusal_without_birth(self):
        (self.root / ".agent-default.lock").mkdir()
        done = run_cli("add", "--name", "Мира", base=self.root)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertIn("замок", done.stderr)
        self.assertEqual([a.id for a in agents.roster(self.root)], ["main"])
        self.assertFalse((self.root / "agents").exists())

    def test_add_with_text_soul_writes_the_seed(self):
        done = run_cli("add", "--name", "Мира",
                       "--soul-kind", "text", "--soul-file", str(self.seed_file()),
                       base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        made = json.loads(done.stdout)
        seed = self.root / "agents" / made["id"] / agents.SEED_NAME
        self.assertEqual(seed.read_text(encoding="utf-8"), SEED)
        receipt = seed.with_name("kit-birth.json")
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["state"], "prepared")
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["tree"],
                         str((seed.parent / "data").resolve()))
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

    def test_broken_json_refuses_instead_of_wiping_the_config(self):
        # Фикс-волна 06.10 (F2): read_config битого JSON давал {} — запись
        # enabled поверх стирала бы ВЕСЬ конфиг до одного ключа. Теперь:
        # отказ словами, файл не тронут.
        cfg = self.root / "agents" / "mira" / "helene.json"
        broken = '{ "agent": { "name": "Мира", }  лишняя запятая'
        cfg.write_text(broken, encoding="utf-8", newline="\n")
        done = run_cli("set-enabled", "--id", "mira", "--enabled", "false", base=self.root)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("не разбирается", done.stderr)
        self.assertEqual(cfg.read_text(encoding="utf-8"), broken,
                         "отказ обязан оставить битый конфиг как был")
        # Пустой файл и отсутствие файла — НЕ битые: писаться может новый.
        cfg.write_text("", encoding="utf-8", newline="\n")
        done = run_cli("set-enabled", "--id", "mira", "--enabled", "true", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        cfg.write_text("не-json-вовсе", encoding="utf-8", newline="\n")
        done = run_cli("set-enabled", "--id", "mira", "--enabled", "false", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("не разбирается", done.stderr)


class SetDefault(Base):
    def setUp(self):
        super().setUp()
        lay_out(self.root, {"agents/mira/helene.json": {"agent": {"name": "Мира"}}})
        self.path = self.root / "agent-default.json"

    def test_valid_choice_roundtrips_through_the_startup_file(self):
        done = run_cli("set-default", "--id", " MIRA ", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"ok": True, "id": "mira", "name": "Мира"})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"id": "mira"})
        main = run_cli("set-default", "--id", "main", base=self.root)
        self.assertEqual(main.returncode, 0, main.stderr)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"id": "main"})

    def test_missing_or_disabled_agent_leaves_existing_choice_untouched(self):
        self.path.write_text('{"id": "main"}', encoding="utf-8")
        lay_out(self.root, {"agents/mira/helene.json": {
            "agent": {"name": "Мира"}, "enabled": False,
        }})
        for said, reason in (("mira", "снят"), ("ghost", "нет"), ("", "нет")):
            done = run_cli("set-default", "--id", said, base=self.root)
            self.assertEqual(done.returncode, 2, said)
            self.assertIn(reason, done.stderr)
            self.assertEqual(self.path.read_text(encoding="utf-8"), '{"id": "main"}')
        self.assertEqual(list(self.root.glob(".agent-default-*.tmp")), [])

    def test_broken_neighbour_config_is_not_read_as_enabled(self):
        self.path.write_text('{"id": "main"}', encoding="utf-8")
        cfg = self.root / "agents" / "mira" / "helene.json"
        cfg.write_text("{not-json}", encoding="utf-8")
        done = run_cli("set-default", "--id", "mira", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("не разбирается", done.stderr)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"id": "main"}')

    def test_non_boolean_enabled_agrees_with_rust_roster(self):
        lay_out(self.root, {"agents/mira/helene.json": {
            "agent": {"name": "Мира"}, "enabled": 0,
        }})
        done = run_cli("set-default", "--id", "mira", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"id": "mira"})

    def test_failed_replace_leaves_no_temporary_file_or_success_receipt(self):
        self.path.mkdir()
        done = run_cli("set-default", "--id", "mira", base=self.root)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertTrue(self.path.is_dir())
        self.assertEqual(list(self.root.glob(".agent-default-*.tmp")), [])

    def test_linked_preference_refuses_set_default_without_following(self):
        foreign = Path(self.tmp.name) / "foreign-choice.json"
        foreign.write_text('{"id": "main"}', encoding="utf-8")
        try:
            self.path.symlink_to(foreign)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        done = run_cli("set-default", "--id", "mira", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("ссылка", done.stderr)
        self.assertEqual(foreign.read_text(encoding="utf-8"), '{"id": "main"}')
        self.assertTrue(self.path.is_symlink())


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

    def test_remove_clears_only_its_own_saved_startup_choice(self):
        preference = self.root / "agent-default.json"
        chosen = run_cli("set-default", "--id", "mira", base=self.root)
        self.assertEqual(chosen.returncode, 0, chosen.stderr)
        removed = run_cli("remove", "--id", "mira", base=self.root)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(preference.exists())
        self.assertTrue(Path(json.loads(removed.stdout)["attic"]).is_dir())

    def test_removing_other_agent_keeps_preference_bytes(self):
        preference = self.root / "agent-default.json"
        preference.write_bytes(b'{"id": "main", "other": 4}\n')
        original = preference.read_bytes()
        removed = run_cli("remove", "--id", "mira", base=self.root)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(preference.read_bytes(), original)

    def test_removal_failure_before_rename_keeps_choice_and_agent(self):
        preference = self.root / "agent-default.json"
        preference.write_bytes(b'{"id": "mira"}\n')
        blocker = Path(self.tmp.name) / "not-a-folder"
        blocker.write_text("do not overwrite", encoding="utf-8")
        failed = run_cli("remove", "--id", "mira", "--attic", str(blocker / "child"),
                         base=self.root)
        self.assertEqual(failed.returncode, 2)
        self.assertTrue((self.root / "agents/mira/helene.json").is_file())
        self.assertEqual(preference.read_bytes(), b'{"id": "mira"}\n')

    def test_partial_cleanup_reports_destination_and_residual_preference(self):
        preference = self.root / "agent-default.json"
        preference.write_bytes(b'{"id": "mira"}\n')
        with patch.object(Path, "unlink", side_effect=PermissionError("delete refused")):
            with self.assertRaises(agents.RemovalPartialError) as error:
                agents.remove_agent(self.root, "mira", self.root / "agents-attic")
        self.assertFalse((self.root / "agents/mira").exists())
        self.assertTrue(preference.is_file())
        self.assertIn("agents-attic", str(error.exception))
        self.assertIn("agent-default.json", str(error.exception))
        stderr = StringIO()
        with patch.object(agents_cli.agents, "remove_agent", side_effect=error.exception), redirect_stderr(stderr):
            code = agents_cli.main(["remove", "--base", str(self.root), "--id", "mira"])
        self.assertEqual(code, 3)
        self.assertIn("agents-attic", stderr.getvalue())

    def test_cross_process_preference_lock_waits_before_writing(self):
        preference = self.root / "agent-default.json"
        with agents._default_writer_lock(self.root):
            env = {**os.environ, "PYTHONUTF8": "1"}
            proc = subprocess.Popen([sys.executable, "-X", "utf8", str(CLI),
                                     "set-default", "--id", "mira", "--base", str(self.root)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    proc.communicate(timeout=0.4)
                self.assertFalse(preference.exists(), "a contending writer ran without the lock")
            except BaseException:
                proc.kill()
                proc.communicate()
                raise
        stdout, stderr = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(preference.read_text(encoding="utf-8")), {"id": "mira"})

    def test_concurrent_remove_and_set_default_leave_no_dangling_choice(self):
        processes = []
        with agents._default_writer_lock(self.root):
            for what in (("remove", "--id", "mira"),
                         ("set-default", "--id", "mira")):
                processes.append(subprocess.Popen(
                    [sys.executable, "-X", "utf8", str(CLI), *what,
                     "--base", str(self.root)], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env={**os.environ, "PYTHONUTF8": "1"}))
            try:
                for proc in processes:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        proc.communicate(timeout=0.3)
                self.assertTrue((self.root / "agents/mira").is_dir())
            except BaseException:
                for proc in processes:
                    proc.kill()
                    proc.communicate()
                raise
        outputs = [proc.communicate(timeout=12) for proc in processes]
        self.assertEqual(processes[0].returncode, 0, outputs[0][1].decode("utf-8", "replace"))
        self.assertFalse((self.root / "agents/mira").exists())
        self.assertFalse((self.root / "agent-default.json").exists(),
                         "set-default must not leave a removed agent selected")
        self.assertIn(processes[1].returncode, (0, 2))

    def test_linked_source_and_attic_refuse_without_moving(self):
        foreign = Path(self.tmp.name) / "foreign"
        foreign.mkdir()
        attic = self.root / "linked-attic"
        try:
            attic.symlink_to(foreign, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        failed = run_cli("remove", "--id", "mira", "--attic", str(attic), base=self.root)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("ссылка", failed.stderr)
        self.assertEqual(list(foreign.iterdir()), [])
        self.assertTrue((self.root / "agents/mira/helene.json").is_file())
        attic.unlink()
        source = self.root / "agents/mira"
        archived = self.root / "agents/mira-preserved"
        source.rename(archived)
        source.symlink_to(archived, target_is_directory=True)
        failed = run_cli("remove", "--id", "mira", base=self.root)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("ссылка", failed.stderr)
        self.assertTrue((archived / "helene.json").is_file())

    def test_linked_preference_refuses_before_agent_move(self):
        foreign = Path(self.tmp.name) / "foreign-choice.json"
        foreign.write_text('{"id":"mira"}', encoding="utf-8")
        preference = self.root / "agent-default.json"
        try:
            preference.symlink_to(foreign)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        failed = run_cli("remove", "--id", "mira", base=self.root)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("ссылка", failed.stderr)
        self.assertEqual(foreign.read_text(encoding="utf-8"), '{"id":"mira"}')
        self.assertTrue((self.root / "agents/mira/helene.json").is_file())

    def test_linked_writer_lock_refuses_both_operations(self):
        foreign = Path(self.tmp.name) / "foreign-lock"
        foreign.write_bytes(b"do not touch")
        link = self.root / ".agent-default.lock"
        try:
            link.symlink_to(foreign)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        for operation in (("set-default", "--id", "mira"), ("remove", "--id", "mira")):
            done = run_cli(*operation, base=self.root)
            self.assertEqual(done.returncode, 2, operation)
            self.assertIn("замок", done.stderr)
        self.assertEqual(foreign.read_bytes(), b"do not touch")
        self.assertTrue((self.root / "agents/mira/helene.json").is_file())
        self.assertFalse((self.root / "agent-default.json").exists())

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
