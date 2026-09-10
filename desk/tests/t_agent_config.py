# -*- coding: utf-8 -*-
"""Настройки серверного агента из окна: что правится, что нет, кто вправе.

Запуск:  python tests/t_agent_config.py

Главное здесь — не «сохранилось», а ГРАНИЦА. Окно правит агента (мозг, бот,
имена, голос), но не раскладку установки: где python, где код, на каком порту
канал, что со службой и с реле. Ключ окна и так открывает многое — записку
агенту, правку его конституции, — но подменить программу, которой он
запускается, он не должен.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "localharness"))

from deskd import agentcfg  # noqa: E402
from deskd import readers  # noqa: E402

FULL = {
    "mode": "local",
    "python": "runtime/python.exe",
    "app": "app/deskapp.py",
    "runner": "app/localharness/runner.py",
    "code": "tree",
    "tree": "data",
    "port": 8094,
    "agent": {"name": "Hélène"},
    "owner": {"name": "Егор", "room": "Hélène"},
    "model": {"framework": "openai", "base_url": "https://api.openai.com/v1",
              "key": "sk-old", "model": "gpt-5"},
    "telegram": {"bot_token": "111:AAA", "owner_id": 42},
    "voice": {"enabled": True, "model": "turbo"},
    "service": {"session0": False},
    "relay": {"enabled": True},
    "update": {"url": "https://example/latest"},
    "setup_complete": True,
}


class Ground(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "helene.json"
        self.write(FULL)
        real = readers.config_path
        readers.config_path = lambda: self.path
        self.addCleanup(lambda: setattr(readers, "config_path", real))

    def write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8", newline="\n")

    def read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


class WhatTheWindowSees(Ground):
    def test_раскладка_окну_не_показывается(self):
        said = agentcfg.state()
        self.assertTrue(said["ok"])
        for key in ("python", "app", "runner", "code", "tree", "port", "service",
                    "relay", "update", "mode", "setup_complete"):
            self.assertNotIn(key, said["config"],
                             f"{key} — про раскладку установки, экрану он не нужен")
        for key in ("model", "telegram", "agent", "owner", "voice"):
            self.assertIn(key, said["config"])

    def test_отпечаток_свежести_есть_и_он_строка(self):
        said = agentcfg.state()
        self.assertIsInstance(said["mtime_ns"], str)
        self.assertTrue(said["mtime_ns"])

    def test_нет_конфига_рядом_говорится_словами(self):
        readers.config_path = lambda: None
        said = agentcfg.state()
        self.assertFalse(said["ok"])
        self.assertIn("helene.json", said["why"])

    def test_битый_конфиг_не_роняет_ручку(self):
        self.path.write_text("{ это не json", encoding="utf-8")
        said = agentcfg.state()
        self.assertFalse(said["ok"])
        self.assertIn("не разобрался", said["why"])


class WhatTheWindowMayChange(Ground):
    def test_мозг_правится(self):
        got = agentcfg.save({"model": {"framework": "openai", "key": "sk-new",
                                       "model": "gpt-5", "base_url": "https://api.openai.com/v1"}})
        self.assertTrue(got["ok"])
        self.assertEqual(self.read()["model"]["key"], "sk-new")
        self.assertEqual(got["saved"], ["model"])

    def test_раскладка_отбрасывается_и_называется(self):
        """⚠ Главный стенд файла: подменить программу агента из окна нельзя."""
        got = agentcfg.save({
            "model": {"key": "sk-new"},
            "runner": "C:/чужое/runner.py",
            "python": "C:/чужое/python.exe",
            "service": {"session0": True},
        })
        self.assertTrue(got["ok"])
        self.assertEqual(got["saved"], ["model"])
        self.assertEqual(sorted(got["dropped"]), ["python", "runner", "service"])
        after = self.read()
        self.assertEqual(after["runner"], FULL["runner"], "раннер остался прежним")
        self.assertEqual(after["python"], FULL["python"])
        self.assertEqual(after["service"], FULL["service"])

    def test_ничего_разрешённого_нет_отказ(self):
        got = agentcfg.save({"runner": "чужое"})
        self.assertFalse(got["ok"])
        self.assertEqual(got["code"], "nothing_editable")
        self.assertEqual(self.read()["runner"], FULL["runner"])

    def test_остальные_ключи_файла_остаются(self):
        agentcfg.save({"telegram": {"bot_token": "222:BBB", "owner_id": 7}})
        after = self.read()
        self.assertEqual(after["telegram"]["bot_token"], "222:BBB")
        self.assertEqual(after["model"]["key"], "sk-old", "чужой блок не тронут")
        self.assertEqual(after["port"], 8094)
        self.assertTrue(after["setup_complete"])

    def test_расписка_говорит_про_перезапуск(self):
        got = agentcfg.save({"agent": {"name": "Мира"}})
        self.assertIn("перезапусти", got["note"])


class Freshness(Ground):
    def test_чужая_правка_не_затирается_молча(self):
        seen = agentcfg.state()["mtime_ns"]
        # Кто-то (сам агент рукой switch_brain) записал конфиг после того, как
        # окно его открыло.
        changed = dict(FULL)
        changed["model"] = dict(FULL["model"], model="glm-5.3")
        import time
        time.sleep(0.01)
        self.write(changed)
        got = agentcfg.save({"model": {"key": "sk-window"}}, seen)
        self.assertFalse(got["ok"])
        self.assertEqual(got["code"], "conflict")
        self.assertEqual(self.read()["model"]["model"], "glm-5.3",
                         "правка агента осталась — окно её не затёрло")

    def test_свежий_отпечаток_пропускается(self):
        seen = agentcfg.state()["mtime_ns"]
        got = agentcfg.save({"model": {"key": "sk-window"}}, seen)
        self.assertTrue(got["ok"])
        self.assertEqual(self.read()["model"]["key"], "sk-window")


class ChannelDoor(unittest.TestCase):
    def test_ручки_есть_и_телефону_туда_нельзя(self):
        import deskapp
        paths = {r.path for r in deskapp.ROUTES}
        self.assertIn("/api/agent-config", paths)
        self.assertNotIn("/api/agent-config", deskapp._DEVICE_PATHS,
                         "телефон правит слово агенту, а не его мозг и бот-токен")


if __name__ == "__main__":
    unittest.main(verbosity=2)
