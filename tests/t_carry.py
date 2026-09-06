# -*- coding: utf-8 -*-
"""Стенд переноса агента (carry.py): экспорт → импорт в другое место.

Запуск:  python tests/t_carry.py

Всё во временных папках. Проверяется ровно то, что обещает шапка carry.py:
что едет и что нет (body/, журналы, замок и ключ окна, стыки mnt/), паспорт с
поимённым списком секретов, слияние конфига (агент из архива, хост местный),
прежняя data/ на месте назначения отходит в data.before-* и не удаляется,
обратный перенос тем же архивом.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import carry  # noqa: E402


def _home(base: Path, name: str, *, agent: str, key: str = "sk-секрет") -> Path:
    root = base / name
    data = root / "data"
    for rel in ("memory/.state", "memory/groups", "soul/skills", "workspace/inbox",
                "workspace/mnt", "body/state", "relay/local_auth", "relay/logs", "telegram"):
        (data / rel).mkdir(parents=True, exist_ok=True)
    (data / "soul" / "SOUL.md").write_text(f"# {agent}\nконституция", encoding="utf-8")
    (data / "soul" / "skills" / "INDEX.md").write_text("навыки", encoding="utf-8")
    (data / "memory" / "groups" / "window.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    (data / "memory" / ".state" / "anatomy.json").write_text("{}", encoding="utf-8")
    (data / "memory" / ".state" / "harness.lock").write_text("{}", encoding="utf-8")
    (data / "memory" / ".state" / "desk-token").write_text("ключ-окна", encoding="utf-8")
    (data / "memory" / ".state" / "body.json").write_text("{}", encoding="utf-8")
    (data / "memory" / "llm.json").write_text("{}", encoding="utf-8")
    (data / "body" / "state" / "body.db").write_bytes(b"\x00" * 16)
    (data / "relay" / "local_auth" / "auth.json").write_text("{}", encoding="utf-8")
    (data / "relay" / "logs" / "relay.log").write_text("лог", encoding="utf-8")
    (data / "runner.log").write_text("лог", encoding="utf-8")
    (data / "telegram" / "account.session").write_bytes(b"s")
    (data / "workspace" / "inbox" / "a.txt").write_text("вложение", encoding="utf-8")
    cfg = {
        "mode": "local", "agent_mode": "sandbox", "python": "runtime/python.exe",
        "app": "app/deskapp.py", "runner": "app/localharness/runner.py",
        "tree": "data", "code": "tree", "port": 8094,
        "agent": {"name": agent}, "owner": {"name": "Егор", "room": "Hélène"},
        "model": {"framework": "anthropic", "base_url": "https://api.z.ai/api/anthropic",
                  "model": "glm-5.3", "key": key},
        "telegram": {"bot_token": "111:AAA", "owner_id": 7},
        "sandbox": {"enabled": True, "network": True},
        "service": {"session0": False, "firewall": True},
        "computer": {"enabled": True, "port": 9480, "scopes": ["computer.read"]},
        "installed": {"version": "0.3.1", "service": False},
        "update": {"url": "https://example.invalid/latest"},
        "env": {"TZ": "Europe/Samara"},
    }
    (root / "helene.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


class Export(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-carry-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src = _home(self.base, "Helene", agent="Мира")

    def test_archive_carries_the_agent_and_not_the_host(self):
        passport = carry.export(self.src / "helene.json")
        archive = Path(passport["archive"])
        self.assertTrue(archive.is_file())
        self.assertIn("helene-Мира-", archive.name)
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
        self.assertIn("data/soul/SOUL.md", names)
        self.assertIn("data/memory/groups/window.jsonl", names)
        self.assertIn("data/relay/local_auth/auth.json", names)
        self.assertIn("data/telegram/account.session", names)
        self.assertIn("data/workspace/inbox/a.txt", names)
        self.assertIn(carry.CONFIG_NAME, names)
        self.assertIn(carry.PASSPORT, names)
        for gone in ("data/body/state/body.db", "data/relay/logs/relay.log", "data/runner.log",
                     "data/memory/.state/harness.lock", "data/memory/.state/desk-token",
                     "data/memory/.state/body.json"):
            self.assertNotIn(gone, names, gone)
        self.assertEqual(passport["agent"], "Мира")
        self.assertEqual(passport["version"], "0.3.1")
        joined = " ".join(passport["secrets"])
        for spot in ("model.key", "bot_token", "local_auth", "telegram", "llm.json"):
            self.assertIn(spot, joined)

    @unittest.skipUnless(os.name == "nt", "стыки — только Windows")
    def test_junction_in_mnt_is_not_followed(self):
        outside = self.base / "Документы"
        outside.mkdir()
        (outside / "секрет.txt").write_text("чужое", encoding="utf-8")
        link = self.src / "data" / "workspace" / "mnt" / "docs"
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            self.skipTest(f"mklink не удался: {done.stderr or done.stdout}")
        passport = carry.export(self.src / "helene.json")
        with zipfile.ZipFile(passport["archive"]) as zf:
            names = zf.namelist()
        self.assertFalse(any("mnt/" in n for n in names), names)


class Import(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-carry-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src = _home(self.base, "Helene", agent="Мира")
        self.archive = Path(carry.export(self.src / "helene.json")["archive"])

    def test_agent_blocks_win_host_blocks_stay(self):
        dst = self.base / "server"
        dst.mkdir()
        local = {"mode": "local", "agent_mode": "interactive", "python": "python",
                 "app": "app/deskapp.py", "runner": "app/localharness/runner.py",
                 "tree": "data", "code": "tree", "port": 8095,
                 "sandbox": {"enabled": False}, "computer": {"enabled": False},
                 "phone": {"enabled": True}, "agent": {"name": ""},
                 "model": {"framework": "openai", "key": ""}}
        (dst / "helene.json").write_text(json.dumps(local), encoding="utf-8")
        receipt = carry.import_(dst / "helene.json", self.archive)
        self.assertEqual(receipt["backup"], "")
        self.assertTrue((dst / "data" / "soul" / "SOUL.md").is_file())
        self.assertTrue((dst / "data" / "relay" / "local_auth" / "auth.json").is_file())
        self.assertFalse((dst / "data" / "body").exists())
        merged = json.loads((dst / "helene.json").read_text("utf-8"))
        self.assertEqual(merged["agent"]["name"], "Мира")
        self.assertEqual(merged["model"]["model"], "glm-5.3")
        self.assertEqual(merged["model"]["key"], "sk-секрет")
        self.assertEqual(merged["telegram"]["owner_id"], 7)
        self.assertEqual(merged["env"]["TZ"], "Europe/Samara")
        # Хост остаётся хостом.
        self.assertEqual(merged["agent_mode"], "interactive")
        self.assertEqual(merged["port"], 8095)
        self.assertEqual(merged["python"], "python")
        self.assertEqual(merged["sandbox"], {"enabled": False})
        self.assertEqual(merged["computer"], {"enabled": False})
        self.assertEqual(merged["phone"], {"enabled": True})
        self.assertNotIn("installed", merged)

    def test_existing_data_is_kept_aside_and_round_trip_works(self):
        dst = _home(self.base, "Other", agent="Вера", key="sk-другой")
        old_soul = (dst / "data" / "soul" / "SOUL.md").read_text("utf-8")
        receipt = carry.import_(dst / "helene.json", self.archive)
        self.assertTrue(receipt["backup"].endswith(tuple("0123456789")))
        backup = Path(receipt["backup"])
        self.assertTrue(backup.is_dir())
        self.assertEqual((backup / "soul" / "SOUL.md").read_text("utf-8"), old_soul)
        self.assertIn("Мира", (dst / "data" / "soul" / "SOUL.md").read_text("utf-8"))
        merged = json.loads((dst / "helene.json").read_text("utf-8"))
        self.assertEqual(merged["agent"]["name"], "Мира")
        self.assertEqual(merged["model"]["key"], "sk-секрет")
        # Обратно — тем же архивом.
        back = carry.export(dst / "helene.json")
        self.assertEqual(back["agent"], "Мира")
        self.assertGreater(back["files"], 3)

    def test_keep_config_leaves_the_file_alone(self):
        dst = self.base / "keep"
        dst.mkdir()
        (dst / "helene.json").write_text(json.dumps({"tree": "data", "agent": {"name": "Х"}}),
                                         encoding="utf-8")
        receipt = carry.import_(dst / "helene.json", self.archive, keep_config=True)
        self.assertFalse(receipt["config_merged"])
        self.assertEqual(json.loads((dst / "helene.json").read_text("utf-8"))["agent"]["name"], "Х")

    def test_foreign_zip_is_refused(self):
        bad = self.base / "чужой.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("data/x.txt", "x")
        with self.assertRaises(SystemExit):
            carry.read_passport(bad)

    def test_cli_show_and_export(self):
        code = carry.main(["show", str(self.archive)])
        self.assertEqual(code, 0)
        out = self.base / "явный.zip"
        self.assertEqual(carry.main(["export", "--config", str(self.src / "helene.json"),
                                     "--out", str(out)]), 0)
        self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
