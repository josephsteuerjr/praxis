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
import subprocess
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

# Стенд разбирает САМО тело; есть ли оно на этой платформе, решает
# `body.HAS_BODY` (Windows и macOS). Поднимаем флаг, чтобы разбор шёл на любом
# раннере; сборка без тела (прочие POSIX) проверяется отдельно (`Absent`).
body.HAS_BODY = True


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


class Absent(unittest.TestCase):
    """Сборка без тела (прочие POSIX): ничего не поднимается, тревог нет, секции нет.

    Что уезжает на экран — снимок `computer` и строка про окна — пусто, а не
    «нет на этой платформе»: окно секцию просто не рисует. Тулу `computer`
    агент получает ответ словами, без слов о платформе.
    """

    def setUp(self):
        self.saved_state = dict(body.STATE)
        body.HAS_BODY = False

    def tearDown(self):
        body.HAS_BODY = True
        body.STATE.clear()
        body.STATE.update(self.saved_state)

    def test_flag_follows_the_platform(self):
        import importlib
        fresh = importlib.reload(body)
        try:
            # Тело есть на Windows (UIA) и на macOS (Accessibility); прочие POSIX — без.
            self.assertEqual(fresh.HAS_BODY, os.name == "nt" or sys.platform == "darwin")
            self.assertEqual(fresh.HAS_BODY, modes.HAS_COMPUTER)
            self.assertEqual(fresh.ASKS_TCC, sys.platform == "darwin")
        finally:
            fresh.HAS_BODY = True

    def test_launch_spawns_nothing_writes_nothing_and_alarms_nobody(self):
        g = Ground(_cfg(enabled=True))
        self.addCleanup(g.close)
        with self.assertNoLogs("helene.body", level="WARNING"):
            self.assertIsNone(body.launch(g.root, g.tree, _cfg(enabled=True)))
        self.assertFalse((g.tree / "memory" / ".state" / "body.json").exists(),
                         "снимок тела в сборке без тела не пишется — секции нет")
        self.assertFalse(body.STATE["available"])
        self.assertEqual(body.STATE["bridge_pid"], 0)
        said = json.dumps(body.state(), ensure_ascii=False).lower()
        for word in ("macos", "windows", "платформ"):
            self.assertNotIn(word, said, "слово о платформе в том, что может уехать на экран")
        self.assertEqual(body.windows_truth(), "", "строки про окна нет — окно её не рисует")

    def test_hand_is_removed_from_the_set_and_grants_nothing(self):
        # Сборка без тела снимает `computer` ВОВСЕ (как брокер), а не оставляет
        # ответ-заглушку: модель не должна видеть тул, который всегда откажет.
        g = Ground(_cfg())
        self.addCleanup(g.close)
        called: list = []
        agent = _fake_agent(called)
        agent.BASE_TOOLS = [{"name": "recall"}]
        agent.OWNER_TOOLS = [{"name": "computer"}, {"name": "shell"}]
        agent._computer_allowed = lambda scope: True
        body.install(agent, g.tree, _cfg(), config_path=g.config)
        self.assertNotIn("computer", agent.TOOL_IMPL, "тул остался в TOOL_IMPL")
        self.assertFalse(any(t.get("name") == "computer" for t in agent.OWNER_TOOLS),
                         "схема computer осталась в наборе — модель её увидит")
        self.assertTrue(any(t.get("name") == "shell" for t in agent.OWNER_TOOLS),
                        "снят только computer, остальные тулы на месте")
        self.assertEqual(called, [], "тело дерева не должно вызываться")
        self.assertFalse(agent._computer_allowed("computer.read"))
        # Повторная установка не падает на уже снятом туле.
        body.install(agent, g.tree, _cfg(), config_path=g.config)
        self.assertNotIn("computer", agent.TOOL_IMPL)


#: Описание тула `computer` у дерева — два живых образца (ядро `praxis/agent.py`
#: и слой `helene/core/agent.py`), урезанные до фраз, в которых сидят слова
#: Windows. Остальной текст здесь не нужен: замены подстрочные, а стенд стережёт
#: ровно те подстроки, что перечислены в `body.MAC_TOOL_TEXT`.
_CORE_TOOL_TEXT = (
    "Use the connected Windows computer from any Telegram chat where this caller has an owner-issued grant. "
    "read_window reads the UI Automation control tree as text; hwnd defaults to foreground. "
    "It goes through UI Automation patterns, so no pixels are involved: DPI, a window that "
    "moved and a list that scrolled stop being your problem. "
    "CURRENT chat; run/poll/stop manage PowerShell processes. desktop_status/windows/read_window/"
    "clipboard_read/clipboard_write/processes are native interactive-desktop hands (no Office COM). Prefer the "
    "use signed steps or direction=up/down/left/right; the server converts one step to one Win32 notch. Wheel "
    "read/hash/write/replace are DIRECT file verbs on the PC disk and the primary "
    "coding path on Windows (no wcode proxy task needed; receipts bind to your current run automatically): "
    "read returns numbered lines start..end with sha256; write is fs.write_atomic (content ≤1.5MB — bigger "
    "goes the artifact route); replace swaps EXACTLY ONE occurrence of old."
)
_LAYER_TOOL_TEXT = (
    "Use the connected Windows computer from any Telegram chat where this caller has an owner-issued grant. "
    "CURRENT chat; run/poll/stop manage PowerShell processes. desktop_status/windows/read_window/activate/input/"
    "screenshot/observe/clipboard_read/clipboard_write/processes are native interactive-desktop hands (no Office COM). "
    "read_window returns the UI Automation control tree of a window as text (role, name, value, automation id, "
    "click into a text field do not assume the caret is free: many controls (WinForms TextBox) select all text on "
    "focus and type_text would REPLACE it — press End/Escape or click a second time before typing. "
    "coding path on Windows (no wcode proxy task needed; receipts bind to your current run automatically): "
    "read returns numbered lines start..end with sha256; write is fs.write_atomic (content ≤1.5MB — bigger "
    "goes the artifact route; .ps1/.psm1/.psd1 with non-ASCII text get a UTF-8 BOM so PowerShell 5.1 parses "
    "them); replace swaps EXACTLY ONE occurrence of old; expected_sha256 does "
    "compare-and-swap on both, backup=true keeps a backup."
)

#: Слова, которых в описании для Mac быть не должно.
_WINDOWS_WORDS = ("Windows", "PowerShell", "Win32", "UI Automation", "wcode", "BOM",
                  "Office COM", "WinForms")


class Platform(unittest.TestCase):
    """Порт на macOS: имена без `.exe`, группа процессов вместо job-объекта,
    разрешения системы в снимке и описание тула словами macOS. Всё, что здесь
    чистое, гоняется и на Windows."""

    def setUp(self):
        self.saved_state = dict(body.STATE)
        self.saved_asks = body.ASKS_TCC

    def tearDown(self):
        body.STATE.clear()
        body.STATE.update(self.saved_state)
        body.ASKS_TCC = self.saved_asks

    def test_exe_names_follow_the_platform(self):
        self.assertEqual(body.exe_name("helene-body").endswith(".exe"), os.name == "nt")
        self.assertEqual(body.BODY_EXE, body.exe_name("helene-body"))
        self.assertEqual(body.BRIDGE_EXE, body.exe_name("helene-bridge"))
        # На Windows — ровно те имена, что и до порта.
        if os.name == "nt":
            self.assertEqual((body.BRIDGE_EXE, body.BODY_EXE), ("helene-bridge.exe", "helene-body.exe"))

    def test_children_get_a_console_less_window_or_a_process_group(self):
        self.assertEqual(body.spawn_kwargs(posix=True), {"start_new_session": True})
        self.assertEqual(body.spawn_kwargs(posix=False), {"creationflags": 0x08000000})
        self.assertEqual(body.spawn_kwargs(), body.spawn_kwargs(posix=os.name != "nt"))

    def test_default_device_is_not_windows_pc_off_windows(self):
        self.assertEqual(body.DEFAULT_DEVICE, "windows-pc" if os.name == "nt" else "mac")
        saved = dict(body._TOKENS)
        body._TOKENS.clear()
        try:
            self.assertEqual(body._settings()[2], body.DEFAULT_DEVICE)
        finally:
            body._TOKENS.update(saved)

    def test_mac_tool_text_drops_every_windows_word(self):
        for sample in (_CORE_TOOL_TEXT, _LAYER_TOOL_TEXT):
            said = body.mac_tool_text(sample)
            for word in _WINDOWS_WORDS:
                self.assertNotIn(word, said, f"«{word}» осталось: {said[:200]}")
            self.assertIn("Use the connected computer (macOS)", said)
            self.assertIn("manage shell processes (zsh)", said)
            self.assertIn("native desktop hands (Accessibility)", said)
            self.assertIn("receipts bind to your current run automatically", said)
            # Ни одна замена не порвала соседнее предложение.
            self.assertIn("goes the artifact route); replace swaps", said)
            # Идемпотентно: второй проход ничего не меняет.
            self.assertEqual(body.mac_tool_text(said), said)
        self.assertEqual(body.mac_tool_text("nothing to do here"), "nothing to do here")
        self.assertEqual(body.mac_tool_text(""), "")

    def test_describe_for_mac_patches_each_schema_once(self):
        tool = {"name": "computer", "description": _CORE_TOOL_TEXT,
                "input_schema": {"type": "object", "properties": {
                    "command": {"type": "string",
                                "description": "run/poll/stop manage PowerShell processes"}}}}
        agent = types.ModuleType("agent")
        agent.OWNER_TOOLS = [tool, {"name": "shell", "description": "Windows shell"}]
        agent.TOOLS = [tool]                     # тот же словарь во втором списке
        agent.BASE_TOOLS = "не список"           # чужая форма не роняет
        self.assertEqual(body.describe_for_mac(agent), 1)
        self.assertNotIn("Windows", tool["description"])
        self.assertNotIn("PowerShell", tool["input_schema"]["properties"]["command"]["description"])
        # Соседний тул не трогаем: правится только `computer`.
        self.assertEqual(agent.OWNER_TOOLS[1]["description"], "Windows shell")
        before = json.dumps(tool, ensure_ascii=False, sort_keys=True)
        self.assertEqual(body.describe_for_mac(agent), 1)
        self.assertEqual(json.dumps(tool, ensure_ascii=False, sort_keys=True), before)
        self.assertEqual(body.describe_for_mac(types.ModuleType("empty")), 0)

    def test_install_on_darwin_rewrites_the_description_and_on_windows_leaves_it(self):
        from unittest.mock import patch
        g = Ground(_cfg())
        self.addCleanup(g.close)
        saved_client = sys.modules.get("body_client")
        sys.modules["body_client"] = _fake_body_client()
        self.addCleanup(lambda: sys.modules.__setitem__("body_client", saved_client)
                        if saved_client else sys.modules.pop("body_client", None))
        for platform, expect in (("darwin", "Use the connected computer (macOS)"),
                                 ("win32", "Use the connected Windows computer")):
            agent = _fake_agent([])
            agent.OWNER_TOOLS = [{"name": "computer", "description": _CORE_TOOL_TEXT}]
            with patch.object(sys, "platform", platform):
                body.install(agent, g.tree, _cfg(), config_path=g.config)
            self.assertIn(expect, agent.OWNER_TOOLS[0]["description"], platform)

    def test_probe_desktop_fills_tcc_hints_and_platform(self):
        from unittest.mock import patch
        g = Ground(_cfg())
        self.addCleanup(g.close)
        b = body.Body(g.root, g.tree, _cfg())
        answers = [
            {"ok": True, "platform": "macos", "scale": 2.0,
             "tcc": {"screen_recording": False, "accessibility": True},
             "hints": ["нет разрешения «Запись экрана»: Системные настройки → …", "", 7]},
            {"ok": True, "platform": "macos"},                # тело старее движка: без tcc
            {"ok": False, "code": "timeout", "error": "нет ответа"},
        ]
        with patch.object(body, "call", lambda cap, args=None, timeout=0: answers.pop(0)):
            b.probe_desktop()
            self.assertEqual(body.STATE["tcc"], {"screen_recording": False, "accessibility": True})
            self.assertEqual(body.STATE["hints"], ["нет разрешения «Запись экрана»: Системные настройки → …", "7"])
            self.assertEqual(body.STATE["platform"], "macos")
            body.STATE.update({"enabled": True, "available": True, "scopes": ["computer.apps"]})
            self.assertIn("«Запись экрана»", body.windows_truth())
            self.assertNotIn("«Универсальный доступ»", body.windows_truth())
            b.probe_desktop()
            self.assertIsNone(body.STATE["tcc"], "без tcc в ответе — «не спрашивали», а не «нет»")
            self.assertEqual(body.STATE["hints"], [])
            self.assertEqual(body.tcc_words(), "")
            body.STATE["tcc"] = {"screen_recording": True, "accessibility": True}
            b.probe_desktop()                                 # отказ не трогает прежнее
            self.assertEqual(body.STATE["tcc"], {"screen_recording": True, "accessibility": True})
            self.assertEqual(body.tcc_words(), "", "все разрешения есть — хвоста нет")

    def test_desktop_is_asked_only_after_the_body_answered_and_only_where_tcc_is(self):
        from unittest.mock import patch
        g = Ground(_cfg())
        self.addCleanup(g.close)
        b = body.Body(g.root, g.tree, _cfg())
        asked: list[str] = []

        def fake_call(cap, args=None, timeout=0):
            asked.append(cap)
            if cap == "body.status":
                return {"ok": True, "identity": {"kind": "interactive"}}
            return {"ok": True, "platform": "macos",
                    "tcc": {"screen_recording": True, "accessibility": True}, "hints": []}

        with patch.object(body, "call", fake_call):
            body.ASKS_TCC = True
            self.assertTrue(b.probe())
            self.assertEqual(asked, ["body.status", "desktop.status"])
            asked.clear()
            body.ASKS_TCC = False
            self.assertTrue(b.probe())
            self.assertEqual(asked, ["body.status"], "на Windows проба одна")
        asked.clear()
        with patch.object(body, "call", lambda cap, args=None, timeout=0:
                          asked.append(cap) or {"ok": False, "error": "нет"}):
            body.ASKS_TCC = True
            self.assertFalse(b.probe())
            self.assertEqual(asked, ["body.status"], "без тела про разрешения не спрашиваем")


@unittest.skipUnless(os.name != "nt", "группа процессов — только POSIX (на Windows job-объект)")
class ProcessGroup(unittest.TestCase):
    """POSIX: ребёнок поднимается лидером своей группы и гасится группой —
    SIGTERM, а кто его игнорирует, тот получает SIGKILL. Подставной ребёнок —
    этот же питон."""

    def test_child_leads_its_group_and_dies_by_sigterm(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                stdin=subprocess.DEVNULL, **body.spawn_kwargs())
        self.assertEqual(os.getpgid(proc.pid), proc.pid, "ребёнок не лидер своей группы")
        started = time.monotonic()
        body.kill_group(proc, grace=3.0)
        self.assertIsNotNone(proc.poll(), "ребёнок пережил kill_group")
        self.assertLess(time.monotonic() - started, 3.0, "SIGTERM должен был хватить")

    def test_a_child_ignoring_sigterm_is_killed_after_grace(self):
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
            stdin=subprocess.DEVNULL, **body.spawn_kwargs())
        time.sleep(0.5)                                       # дать ребёнку поставить обработчик
        started = time.monotonic()
        body.kill_group(proc, grace=1.0)
        self.assertIsNotNone(proc.poll(), "ребёнок пережил SIGKILL группе")
        self.assertLess(time.monotonic() - started, 5.0)


def _built_pair() -> Path | None:
    # Место сборки тела знает `layout` — и знает один он: до 10.09 этот путь
    # был жёстко вписан здесь, в сборке и в приборе реле сразу.
    sys.path.insert(0, str(HERE.parent))
    import layout  # noqa: PLC0415
    candidates = [layout.body_target()]
    for base in candidates:
        if (base / "praxis-body.exe").is_file() and (base / "praxis-bridge.exe").is_file():
            return base
        if (base / body.BODY_EXE).is_file() and (base / body.BRIDGE_EXE).is_file():
            return base
    return None


@unittest.skipUnless(os.name == "nt" and _built_pair() is not None,
                     "мост и тело не собраны (HELENE_BODY_DIR или ../_body_target/release)")
class Spool(unittest.TestCase):
    """п. 1.14: спул моста подчищается — старые кадры контроллера и отвеченные
    ответы уходят, свежее и чужое (`to_device`) остаётся."""

    def test_prune_keeps_fresh_and_drops_dead(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "spool.db"
            con = sqlite3.connect(str(db))
            con.executescript("""
                CREATE TABLE frames (message_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                    direction TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE responses (device_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    operation_id TEXT, frame_type TEXT NOT NULL, terminal INTEGER NOT NULL,
                    payload TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(device_id, request_id));
            """)
            old = "2026-09-06T17:10:06.049717200+00:00"
            import datetime as dt
            fresh = dt.datetime.now(dt.timezone.utc).isoformat()
            rows = [("m1", "pc", "to_controller", 1, "x", old),
                    ("m2", "pc", "to_controller", 2, "x", fresh),
                    ("m3", "pc", "to_device", 1, "x", old)]
            con.executemany("INSERT INTO frames VALUES (?,?,?,?,?,0,?)", rows)
            con.executemany("INSERT INTO responses VALUES (?,?,?,?,?,?,?)", [
                ("pc", "r1", None, "result", 1, "x", old),
                ("pc", "r2", None, "result", 1, "x", fresh),
                ("pc", "r3", None, "accepted", 0, "x", old)])
            con.commit()
            con.close()
            pruned = body.prune_spool(db)
            self.assertEqual(pruned, {"frames": 1, "responses": 1})
            con = sqlite3.connect(str(db))
            left = sorted(r[0] for r in con.execute("SELECT message_id FROM frames"))
            self.assertEqual(left, ["m2", "m3"], "свежий кадр и кадр телу остались")
            left = sorted(r[0] for r in con.execute("SELECT request_id FROM responses"))
            self.assertEqual(left, ["r2", "r3"], "свежий и незавершённый ответы остались")
            con.close()
            self.assertEqual(body.prune_spool(db), {"frames": 0, "responses": 0})
        self.assertEqual(body.prune_spool(Path(tmp) / "нет.db"), {"frames": 0, "responses": 0})


@unittest.skipUnless(os.name == "nt", "живое тело (UIA) — здесь только Windows; на macOS его "
                     "гоняет tests/t_body_macos.py на раннере")
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
