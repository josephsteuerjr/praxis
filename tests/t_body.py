# -*- coding: utf-8 -*-
"""Стенд тела руки `computer` (порт UIA, 06.09).

Запуск:  python tests/t_body.py

Две части. Первая — без единого процесса: разбор блока `computer` в
helene.json, отказы руки словами (выключено владельцем, право не выдано),
живое перечитывание прав по mtime, подмена настроек клиента дерева и
«суверенность» хода владельца. Дерево здесь подменено заглушкой с теми же
именами, что читает `body.install` (`TOOL_IMPL`, `_COMPUTER_ACTION_SCOPES`,
`_TURN_CHANNEL`, `_is_sovereign_actor`, `_computer_allowed`).

Вторая — живьём, и только если рядом есть собранные мост и тело
(`HELENE_BODY_DIR`, либо `../_body_target/release` рядом с репозиторием):
поднять обоих, дождаться подключения, спросить `desktop.status`, погасить и
убедиться, что порт свободен. На машине без сборки эта часть пропускается
словами, а не зелёным.
"""
from __future__ import annotations

import contextvars
import json
import os
import socket
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import body  # noqa: E402
import modes  # noqa: E402


def _cfg(enabled=True, scopes=None, port=None) -> dict:
    block: dict = {"enabled": enabled}
    if scopes is not None:
        block["scopes"] = scopes
    if port is not None:
        block["port"] = port
    return {"agent_mode": "sandbox", "computer": block}


class Config(unittest.TestCase):
    def test_defaults_are_off_and_all_scopes(self):
        self.assertFalse(body.enabled({}))
        self.assertEqual(body.scopes({}), list(body.SCOPES))
        self.assertEqual(body.port({}), body.DEFAULT_PORT)
        self.assertEqual(body.DEFAULT_PORT, modes.COMPUTER_PORT_DEFAULT)
        self.assertEqual(tuple(body.SCOPES), modes.COMPUTER_SCOPES)

    def test_scopes_are_an_exact_subset_in_display_order(self):
        cfg = _cfg(scopes=["computer.apps", "computer.read", "computer.nope"])
        self.assertEqual(body.scopes(cfg), ["computer.read", "computer.apps"])
        self.assertEqual(body.scopes(_cfg(scopes="computer.read")), [])
        self.assertEqual(body.scopes(_cfg(scopes=[])), [])

    def test_port_falls_back_on_garbage(self):
        self.assertEqual(body.port(_cfg(port="9999")), 9999)
        self.assertEqual(body.port(_cfg(port=80)), body.DEFAULT_PORT)
        self.assertEqual(body.port(_cfg(port="мост")), body.DEFAULT_PORT)

    def test_modes_and_body_read_the_same_block(self):
        cfg = _cfg(scopes=["computer.files"], port=9490)
        picture = modes.computer_state(cfg)
        self.assertEqual(picture["scopes"], body.scopes(cfg))
        self.assertEqual(picture["port"], body.port(cfg))
        self.assertTrue(picture["enabled"])
        self.assertTrue(picture["explicit"])
        self.assertFalse(modes.computer_state({})["explicit"])
        option = modes.computer_option()
        self.assertEqual([s["key"] for s in option["scopes"]], list(body.SCOPES))
        self.assertIn("слабых моделей", option["warning"])

    def test_device_id_is_route_safe(self):
        value = body.device_id()
        self.assertTrue(value)
        self.assertLessEqual(len(value), 128)
        for ch in "/\\?#":
            self.assertNotIn(ch, value)


class Ground:
    def __init__(self, cfg: dict):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-body-")
        self.root = Path(self.tmp.name) / "Helene"
        self.tree = self.root / "data"
        (self.tree / "memory" / ".state").mkdir(parents=True)
        self.config = self.root / "helene.json"
        self.write(cfg)

    def write(self, cfg: dict) -> None:
        self.config.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        # mtime на NTFS шагает грубо — размер входит в штамп, время добьём.
        st = self.config.stat()
        os.utime(self.config, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))

    def close(self):
        self.tmp.cleanup()


def _fake_agent(called: list):
    agent = types.ModuleType("agent")
    agent.TOOL_IMPL = {"computer": lambda **kw: called.append(kw) or "тело ответило"}
    agent._COMPUTER_ACTION_SCOPES = {
        "status": "computer.read", "windows": "computer.apps",
        "read": "computer.files", "run": "computer.process", "observe": "computer.apps",
    }
    agent._TURN_CHANNEL = contextvars.ContextVar("t", default=None)
    agent._is_sovereign_actor = lambda: False
    agent._computer_allowed = lambda scope: False
    return agent


def _fake_body_client():
    mod = types.ModuleType("body_client")
    mod._settings = lambda: ("http://127.0.0.1:9473", "", "windows-pc")
    mod.available = lambda: bool(mod._settings()[1])
    return mod


class Hand(unittest.TestCase):
    """Рука: отказы словами, живые права, суверенность владельца."""

    def setUp(self):
        self.saved_client = sys.modules.get("body_client")
        self.client = _fake_body_client()
        sys.modules["body_client"] = self.client
        self.saved_state = dict(body.STATE)
        self.saved_tokens = dict(body._TOKENS)
        self.saved_body = body._BODY
        body._TOKENS.clear()
        # Поднятое тело — заглушка: живой подъём проверяет класс Live ниже.
        body._BODY = types.SimpleNamespace(probe=lambda timeout=0.0: True)

    def tearDown(self):
        if self.saved_client is None:
            sys.modules.pop("body_client", None)
        else:
            sys.modules["body_client"] = self.saved_client
        body.STATE.clear()
        body.STATE.update(self.saved_state)
        body._TOKENS.clear()
        body._TOKENS.update(self.saved_tokens)
        body._BODY = self.saved_body

    def test_disabled_hand_names_the_card_and_never_calls_the_tree(self):
        g = Ground(_cfg(enabled=False))
        self.addCleanup(g.close)
        called: list = []
        agent = _fake_agent(called)
        body.STATE.update({"enabled": False, "available": True})
        body.install(agent, g.tree, _cfg(enabled=False), config_path=g.config)
        body.install(agent, g.tree, _cfg(enabled=False), config_path=g.config)  # не матрёшка
        said = agent.TOOL_IMPL["computer"](action="windows")
        self.assertIn("выключена владельцем", said)
        self.assertIn("Управление компьютером", said)
        self.assertIn("Ограда здесь ни при чём", said)
        self.assertEqual(called, [])
        self.assertFalse(agent._computer_allowed("computer.read"))

    def test_missing_scope_is_named_and_reread_from_disk(self):
        g = Ground(_cfg(scopes=["computer.read"]))
        self.addCleanup(g.close)
        called: list = []
        agent = _fake_agent(called)
        body.STATE.update({"enabled": True, "available": True, "connected": True})
        body._TOKENS.update({"url": "http://127.0.0.1:1", "controller": "t", "device": "pc"})
        body.install(agent, g.tree, _cfg(scopes=["computer.read"]), config_path=g.config)
        said = agent.TOOL_IMPL["computer"](action="windows")
        self.assertIn("`computer.apps`", said)
        self.assertIn("Выдано: computer.read", said)
        self.assertEqual(called, [])
        self.assertTrue(agent._computer_allowed("computer.read"))
        self.assertFalse(agent._computer_allowed("computer.apps"))
        # Владелец поставил галочку — следующий вызов уже проходит, без рестарта.
        g.write(_cfg(scopes=["computer.read", "computer.apps"]))
        self.assertEqual(agent.TOOL_IMPL["computer"](action="windows"), "тело ответило")
        self.assertEqual(called, [{"action": "windows"}])
        self.assertTrue(agent._computer_allowed("computer.apps"))
        # Снял опцию целиком — отказ снова словами.
        g.write(_cfg(enabled=False))
        self.assertIn("выключена владельцем", agent.TOOL_IMPL["computer"](action="status"))

    def test_observe_with_a_path_needs_files(self):
        g = Ground(_cfg(scopes=["computer.apps"]))
        self.addCleanup(g.close)
        called: list = []
        agent = _fake_agent(called)
        body.STATE.update({"enabled": True, "available": True, "connected": True})
        body._TOKENS.update({"url": "http://127.0.0.1:1", "controller": "t", "device": "pc"})
        body.install(agent, g.tree, _cfg(scopes=["computer.apps"]), config_path=g.config)
        self.assertIn("`computer.files`",
                      agent.TOOL_IMPL["computer"](action="observe", path="C:\\x.png"))
        self.assertEqual(agent.TOOL_IMPL["computer"](action="observe"), "тело ответило")

    def test_client_settings_come_from_memory_not_environment(self):
        g = Ground(_cfg())
        self.addCleanup(g.close)
        agent = _fake_agent([])
        body._TOKENS.update({"url": "http://127.0.0.1:9481", "controller": "секрет",
                             "device": "pc"})
        body.install(agent, g.tree, _cfg(), config_path=g.config)
        self.assertEqual(self.client._settings(), ("http://127.0.0.1:9481", "секрет", "pc"))
        self.assertTrue(self.client.available())
        self.assertNotIn("PRAXIS_BODY_CONTROLLER_TOKEN", os.environ)

    def test_owner_turn_is_sovereign(self):
        g = Ground(_cfg())
        self.addCleanup(g.close)
        agent = _fake_agent([])
        body.install(agent, g.tree, _cfg(), config_path=g.config)
        self.assertFalse(agent._is_sovereign_actor())
        agent._TURN_CHANNEL.set(types.SimpleNamespace(owner=True))
        self.assertTrue(agent._is_sovereign_actor())
        agent._TURN_CHANNEL.set(types.SimpleNamespace(owner=False))
        self.assertFalse(agent._is_sovereign_actor())

    def test_body_not_up_is_said_with_reason(self):
        g = Ground(_cfg())
        self.addCleanup(g.close)
        called: list = []
        agent = _fake_agent(called)
        body.STATE.update({"enabled": True, "available": False,
                           "reason": "в поставке нет тела", "logs": []})
        body._BODY = None
        body.install(agent, g.tree, _cfg(), config_path=g.config)
        said = agent.TOOL_IMPL["computer"](action="windows")
        self.assertIn("не поднялось", said)
        self.assertIn("в поставке нет тела", said)
        self.assertEqual(called, [])


class Launch(unittest.TestCase):
    def setUp(self):
        self.saved_state = dict(body.STATE)

    def tearDown(self):
        body.STATE.clear()
        body.STATE.update(self.saved_state)

    def test_disabled_option_writes_state_and_spawns_nothing(self):
        g = Ground(_cfg(enabled=False))
        self.addCleanup(g.close)
        self.assertIsNone(body.launch(g.root, g.tree, _cfg(enabled=False)))
        snap = json.loads((g.tree / "memory" / ".state" / "body.json").read_text("utf-8"))
        self.assertFalse(snap["enabled"])
        self.assertIn("выключено", snap["reason"])
        # Без exe рядом строка говорит и про это; главное — слово «выключена».
        self.assertIn("выключена", body.windows_truth())

    def test_missing_binaries_are_named(self):
        g = Ground(_cfg())
        self.addCleanup(g.close)
        saved = os.environ.pop("HELENE_BODY_DIR", None)
        try:
            self.assertIsNone(body.launch(g.root, g.tree, _cfg()))
        finally:
            if saved is not None:
                os.environ["HELENE_BODY_DIR"] = saved
        self.assertTrue(body.STATE["enabled"])
        self.assertFalse(body.STATE["available"])
        self.assertIn(body.BODY_EXE, body.STATE["reason"])
        self.assertIn("тела в поставке нет", body.windows_truth())

    def test_pick_port_skips_a_held_one(self):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        held = holder.getsockname()[1]
        self.addCleanup(holder.close)
        chosen = body.pick_port(held)
        self.assertIsNotNone(chosen)
        self.assertNotEqual(chosen, held)
        self.assertGreater(chosen, held)


def _built_pair() -> Path | None:
    override = os.environ.get("HELENE_BODY_DIR")
    candidates = [Path(override)] if override else []
    candidates.append(HERE.parent.parent / "_body_target" / "release")
    for base in candidates:
        if (base / "praxis-body.exe").is_file() and (base / "praxis-bridge.exe").is_file():
            return base
        if (base / body.BODY_EXE).is_file() and (base / body.BRIDGE_EXE).is_file():
            return base
    return None


@unittest.skipUnless(os.name == "nt" and _built_pair() is not None,
                     "мост и тело не собраны (HELENE_BODY_DIR или ../_body_target/release)")
class Live(unittest.TestCase):
    """Живьём: поднять, дождаться, спросить рабочий стол, погасить."""

    def test_bridge_and_body_come_up_answer_and_die_with_us(self):
        g = Ground(_cfg())
        self.addCleanup(g.close)
        os.environ["HELENE_BODY_DIR"] = str(_built_pair())
        self.addCleanup(lambda: os.environ.pop("HELENE_BODY_DIR", None))
        started = body.launch(g.root, g.tree, _cfg(port=9490))
        self.assertIsNotNone(started, body.STATE.get("reason"))
        self.addCleanup(body.shutdown)
        port = body.STATE["port"]
        self.assertTrue(9490 <= port <= 9490 + body.PORT_SPAN)
        self.assertNotIn("PRAXIS_BODY_TOKEN", os.environ)
        self.assertNotIn("PRAXIS_BRIDGE_CONTROLLER_TOKEN", os.environ)
        body_json = json.loads((g.tree / "body" / "body.json").read_text("utf-8"))
        self.assertNotIn("token", body_json)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and body.STATE.get("connected") is not True:
            time.sleep(0.5)
        self.assertTrue(body.STATE.get("connected"),
                        f"тело не подключилось: {body.STATE.get('reason')}; "
                        f"логи {body.STATE.get('logs')}")
        self.assertEqual(body.STATE["identity"].get("kind"), "interactive")
        answer = body.call("desktop.status", {}, timeout=10)
        self.assertTrue(answer.get("ok"), answer)
        self.assertIn("foreground", answer)
        snap = json.loads((g.tree / "memory" / ".state" / "body.json").read_text("utf-8"))
        self.assertTrue(snap["connected"])
        self.assertIn("тело живо", body.windows_truth())
        body.shutdown()
        time.sleep(1.0)
        self.assertTrue(body._port_free(port), "мост не отпустил порт после остановки")


if __name__ == "__main__":
    unittest.main(verbosity=2)
