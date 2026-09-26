# -*- coding: utf-8 -*-
"""Стенд «реле на сервере»: поднимается ли мозг у агента, увезённого из дома.

Запуск:  python tests/t_server_relay.py

Дыра, которую этот стенд стережёт, была не в коде, а между кодом и поставкой.
Перенос агента увозит вход в подписку ChatGPT (`data/relay/local_auth` лежит в
архиве и назван в паспорте поимённо), а `model.base_url` у такого агента
смотрит на `http://127.0.0.1:5011` — на реле, которое на Windows поднимает
оболочка. На сервере оболочки нет: до 0.5.2 в контейнере жили ровно две
службы, канал и раннер, и агент приезжал с живыми ключами и НЕМЕЛ. Признание
в `README-СЕРВЕР.md` («реле в compose не входит») признанием и оставалось.

Проверяется поэтому не «есть ли строчка про реле», а решение надзора в каждом
исходе: поднимать или нет, чем, с какими переменными — и говорит ли он вслух,
когда не поднимает. Молчание здесь равно немому агенту.
"""
from __future__ import annotations

import io
import contextlib
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))

import serverboot  # noqa: E402


def cfg(**over) -> dict:
    """Конфиг агента с подпиской ChatGPT — такой приезжает с архивом переноса."""
    base = {
        "tree": "data",
        "model": {"base_url": "http://127.0.0.1:5011", "key": "sk-frame-abc", "model": "gpt-5.6-sol"},
        "relay": {"enabled": True, "port": 5011},
    }
    base.update(over)
    return base


class RelayChild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.tree = self.base / "data"
        self.tree.mkdir()
        # «Реле» в поставке: содержимое неважно, важно, что файл есть.
        self.exe = self.base / serverboot.RELAY_NAME
        self.exe.write_bytes(b"\x7fELF not really")
        os.environ.pop("HELENE_RELAY", None)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("HELENE_RELAY", None))

    def make(self, config: dict, env: dict | None = None):
        """Позвать надзор и вернуть (ребёнок, что он сказал вслух)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            child = serverboot.relay_child(self.base, config, self.tree, env or {})
        return child, out.getvalue()

    def test_поднимает_с_домом_портом_и_ключом(self):
        child, said = self.make(cfg(relay={"enabled": True, "port": 5111}))
        self.assertIsNotNone(child, "реле включено и лежит рядом — надзор обязан его поднять")
        self.assertEqual(child.argv, [str(self.exe), "serve"])
        # Дом реле — data/relay: туда архив переноса кладёт local_auth, и там же
        # реле ищет его при старте (текущая папка процесса).
        self.assertEqual(child.cwd, self.tree / "relay")
        self.assertTrue((self.tree / "relay").is_dir())
        self.assertEqual(child.env["RELAY_PORT"], "5111")
        self.assertEqual(child.env["RELAY_API_KEY"], "sk-frame-abc")
        self.assertEqual(child.env["RELAY_INSTRUCTIONS"], "minimal")
        self.assertEqual(child.env["RELAY_LOG_DIR"], str(self.tree / "relay" / "logs"))
        self.assertEqual(child.log_path, self.tree / "relay.log")

    def test_ключ_петли_едет_только_когда_он_есть(self):
        child, _ = self.make(cfg(model={"base_url": "http://127.0.0.1:5011", "key": "  "}))
        self.assertNotIn("RELAY_API_KEY", child.env,
                         "пустой ключ мозга не должен превращаться в пустой ключ петли: "
                         "реле тогда отвергало бы КАЖДЫЙ вызов")

    def test_инструкции_из_конфига(self):
        child, _ = self.make(cfg(relay={"enabled": True, "port": 5011, "instructions": "full"}))
        self.assertEqual(child.env["RELAY_INSTRUCTIONS"], "full")

    def test_выключенное_реле_молчит(self):
        child, said = self.make(cfg(relay={"enabled": False},
                                    model={"base_url": "https://api.z.ai/api/anthropic", "key": "k"}))
        self.assertIsNone(child)
        self.assertEqual(said.strip(), "", "агенту с чужим мозгом реле не нужно — и слов о нём тоже")

    def test_мозг_смотрит_в_реле_а_реле_выключено(self):
        child, said = self.make(cfg(relay={"enabled": False, "port": 5011}))
        self.assertIsNone(child)
        self.assertIn("не будет", said,
                      "адрес мозга на петле при выключенном реле — это немой агент, "
                      "и сказать об этом надзор обязан")

    def test_реле_нет_в_поставке(self):
        self.exe.unlink()
        child, said = self.make(cfg())
        self.assertIsNone(child)
        self.assertIn("нем", said, "нет бинаря — сказать, что агент будет нем, а не молчать")
        self.assertIn("build_dist", said, "и назвать, чем это чинится")

    def test_занятый_порт_не_уводит_мозг_в_чужое_реле(self):
        held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        port = held.getsockname()[1]
        child, said = self.make(cfg(relay={"enabled": True, "port": port}))
        self.assertIsNone(child, "занятый порт — чужое реле: своё не поднимаем")
        self.assertIn(str(port), said)

    def test_путь_к_реле_можно_назвать_средой(self):
        other = self.base / "somewhere" / "relay-bin"
        other.parent.mkdir()
        other.write_bytes(b"\x7fELF")
        os.environ["HELENE_RELAY"] = str(other)
        child, _ = self.make(cfg())
        self.assertEqual(child.argv[0], str(other))


class LocalRelayUrl(unittest.TestCase):
    """Узнаём ли мы адрес локального реле — на этом держится предупреждение."""

    def test_петля_с_портом_реле(self):
        self.assertTrue(serverboot.looks_like_local_relay(cfg()))
        self.assertTrue(serverboot.looks_like_local_relay(
            cfg(model={"base_url": "http://localhost:5011/v1"})))

    def test_чужой_адрес_и_чужой_порт(self):
        self.assertFalse(serverboot.looks_like_local_relay(
            cfg(model={"base_url": "https://api.openai.com/v1"})))
        self.assertFalse(serverboot.looks_like_local_relay(
            cfg(model={"base_url": "http://127.0.0.1:8094"})),
            "порт канала — не порт реле")


class PassportSaysWhatTravels(unittest.TestCase):
    """Паспорт переноса обязан называть вход в подписку: он и правда едет."""

    def test_local_auth_назван_поимённо(self):
        sys.path.insert(0, str(HERE.parent / "localharness"))
        import carry  # noqa: PLC0415 — стенд смотрит именно на этот модуль

        spots = [name for name, _check in carry.SECRET_SPOTS]
        self.assertTrue(any("local_auth" in name for name in spots))
        self.assertIn("relay", carry.AGENT_KEYS,
                      "блок relay должен ехать с агентом: на той стороне по нему "
                      "поднимается реле")


if __name__ == "__main__":
    unittest.main(verbosity=2)
