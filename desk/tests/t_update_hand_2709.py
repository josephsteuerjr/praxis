# -*- coding: utf-8 -*-
"""Стенд руки `update_request` и проводки обновления в движок и надзор (27.09).

Запуск:  python tests/t_update_hand_2709.py

Жалоба Дмитрия К: агент на сервере не мог обновить свой контейнер изнутри. Рука кладёт
план исполнителю снаружи и передаёт «да» владельца. Проверяется то, что может соврать:
  * рука выдаётся только на сервере (надзор — serverboot) и один раз;
  * план кладётся от имени агента и помнит, где его просили;
  * «да» передаётся ТОЛЬКО в ходе, который начал владелец своими словами, и с его словами;
  * движок помечает «ход начал владелец» на время хода и снимает метку после; служебный
    ход «Hélène» (будильник, рождение, отчёт) владельцем не считается;
  * итог обновления движок кладёт запиской в окно один раз;
  * под serverboot движок не берёт просьбы окна со стола надзора; код 42 — не падение.
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness"), str(DESK / "server")]
from deskd import control  # noqa: E402
import updates  # noqa: E402
import runner  # noqa: E402
import serverboot  # noqa: E402

ON_SERVER = {"HELENE_SUPERVISOR": "serverboot"}


def fake_agent(title="Людмила", chat="123", owner=True):
    channel = contextvars.ContextVar("chan", default=None)
    channel.set(types.SimpleNamespace(title=title, chat_id=chat, owner=owner))
    return types.SimpleNamespace(TOOL_IMPL={}, BASE_TOOLS=[], HAND_PURPOSE={},
                                 _TURN_CHANNEL=channel)


class Hand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-hand-")
        self.addCleanup(self._tmp.cleanup)
        self.tree = Path(self._tmp.name)
        self.owner = [False]

    def ctl(self, name) -> Path:
        return self.tree / "memory" / ".control" / name

    def put(self, name, data):
        self.ctl(name).parent.mkdir(parents=True, exist_ok=True)
        self.ctl(name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def hand(self, agent=None):
        agent = agent or fake_agent()
        with mock.patch.dict(os.environ, ON_SERVER):
            self.assertTrue(updates.install(agent, self.tree, {}, owner_spoke=lambda: self.owner[0]))
        return agent, agent.TOOL_IMPL[updates.TOOL_NAME]

    def test_только_на_сервере_и_один_раз(self):
        agent = fake_agent()
        with mock.patch.dict(os.environ, {"HELENE_SUPERVISOR": ""}), \
                mock.patch.object(updates.Path, "exists", return_value=False):
            self.assertFalse(updates.install(agent, self.tree, {}))
        self.assertEqual(agent.TOOL_IMPL, {})
        self.hand(agent)
        self.hand(agent)
        self.assertEqual([t["name"] for t in agent.BASE_TOOLS], [updates.TOOL_NAME])
        self.assertIn(updates.TOOL_NAME, agent.HAND_PURPOSE)
        # схема — то, что пропустит провайдер: имя и объект input_schema
        self.assertIn("input_schema", agent.BASE_TOOLS[0])

    def test_без_исполнителя_сказано_как_поднять(self):
        _, hand = self.hand()
        text = hand(action="status")
        self.assertIn(control.UPDATER_COMMAND, text)
        self.assertIn("План не положен", hand(action="plan"))

    def test_план_от_агента_с_местом_просьбы(self):
        self.put(control.UPDATER_BEAT, {"beat_epoch": time.time(), "ok": True,
                                        "current_version": "1.1.1", "latest": {"version": "1.1.2"}})
        _, hand = self.hand()
        self.assertIn("новее", hand(action="status"))
        text = hand(action="plan", version="1.1.2", reason="починит голос")
        self.assertIn("план положен", text)
        plan = json.loads(self.ctl(control.UPDATE_PLAN).read_text("utf-8"))
        self.assertEqual((plan["asked_by"], plan["chat"], plan["reason"]),
                         ("agent", "Людмила (123)", "починит голос"))

    def test_да_только_словами_владельца(self):
        self.put(control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "awaiting", "nonce": "n0",
                                          "from_version": "1.1.1", "to_version": "1.1.2"})
        _, hand = self.hand()
        self.owner[0] = False
        self.assertIn("начал не владелец", hand(action="confirm", owner_words="да"))
        self.assertFalse(self.ctl(control.UPDATE_CONFIRM).exists())
        self.owner[0] = True
        self.assertIn("дословно", hand(action="confirm"))
        self.assertFalse(self.ctl(control.UPDATE_CONFIRM).exists())
        text = hand(action="confirm", owner_words="Да, обновляйся")
        self.assertIn("«да» записано", text)
        confirm = json.loads(self.ctl(control.UPDATE_CONFIRM).read_text("utf-8"))
        self.assertEqual((confirm["decision"], confirm["by"], confirm["words"], confirm["nonce"]),
                         ("yes", "owner-words", "Да, обновляйся", "n0"))

    def test_снять_план_можно_и_без_владельца(self):
        self.put(control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "awaiting", "nonce": "n0"})
        _, hand = self.hand()
        self.assertIn("не обновлять", hand(action="decline", reason="места мало"))
        self.assertEqual(json.loads(self.ctl(control.UPDATE_CONFIRM).read_text("utf-8"))["decision"], "no")

    def test_подтверждать_нечего(self):
        _, hand = self.hand()
        self.assertIn("Подтверждать нечего", hand(action="confirm", owner_words="да"))
        self.assertIn("action бывает", hand(action="обнови"))

    def test_записка_об_итоге(self):
        receipt = {"id": "aaaa1111", "state": "rolled_back", "from_version": "1.1.1",
                   "to_version": "1.1.2", "note": "1.1.2 не прошла — вернул 1.1.1",
                   "checks": [{"name": "runner", "title": "агент (раннер) жив", "ok": False,
                               "note": "не отвечает"}],
                   "rollback": {"notes": ["прежний код на месте", "data/ возвращена из копии"]},
                   "plan": {"chat": "Людмила (123)"},
                   "confirmed": {"by": "owner-words", "words": "да"}}
        note = updates.report_note(receipt, owner="Людмила")
        for piece in ("не прошло и откачено", "1.1.1 → 1.1.2", "✗ агент (раннер) жив",
                      "data/ возвращена", "«Людмила (123)»", "Владелец — Людмила",
                      "«Да» дал владелец словами тебе — «да»"):
            self.assertIn(piece, note)
        # без рода: агент бывает и Фигаро
        for word in ("клала", "сама ", "должна", "рада"):
            self.assertNotIn(word, note + updates.TOOL["description"])


class EngineWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-wiring-")
        self.addCleanup(self._tmp.cleanup)
        self.tree = Path(self._tmp.name)

    def test_метка_хода_владельца_живёт_только_внутри_хода(self):
        seen = []
        tree_agent = types.SimpleNamespace(
            voice_turn_envelope=lambda *a, **k: seen.append(runner._owner_words[0]) or "конверт")
        with mock.patch.multiple(runner, _agent=tree_agent, _dialogue=lambda *a: ([], ""),
                                 _orient=lambda *a: "", _ext_hook=lambda *a: None):
            owner = types.SimpleNamespace(owner=True)
            self.assertEqual(runner._run_turn("window", "", "Людмила", owner), "конверт")
            runner._run_turn("window", "", updates.SYSTEM_SPEAKER, owner)
            runner._run_turn("tg:5", "", "Прохожий", types.SimpleNamespace(owner=False))
        self.assertEqual(seen, [True, False, False])
        self.assertFalse(runner._owner_words[0])

    def test_итог_запиской_один_раз(self):
        (self.tree / "memory" / ".control").mkdir(parents=True)
        (self.tree / "memory" / ".control" / control.UPDATE_RECEIPT).write_text(json.dumps(
            {"id": "aaaa1111", "state": "done", "from_version": "1.1.1", "to_version": "1.1.2",
             "note": "проверено", "finished_epoch": time.time()}), encoding="utf-8")
        archived = []
        desk = types.SimpleNamespace(archive=lambda text, **k: archived.append(text),
                                     life=lambda *a, **k: None)
        turns = []
        with mock.patch.dict(os.environ, ON_SERVER), \
                mock.patch.multiple(runner, _tree=self.tree, _desk=desk, _speaker="Людмила",
                                    _turn_in_window=lambda *a, **k: turns.append(k) or "spoken"):
            runner._update_report_due()
            runner._update_report_due()
        self.assertEqual(len(archived), 1)
        self.assertIn("Обновление прошло: 1.1.1 → 1.1.2", archived[0])
        self.assertIsNone(control.update_unreported(self.tree))

    def test_под_serverboot_движок_не_берёт_стол_надзора(self):
        with mock.patch.dict(os.environ, ON_SERVER):
            self.assertFalse(runner._start_supervisor(self.tree))
        env = serverboot.child_env({"PATH": "/bin", "PRAXIS_DESK_TOKEN": "x"}, self.tree, "tok")
        self.assertEqual(env["HELENE_SUPERVISOR"], "serverboot")
        self.assertNotIn("PRAXIS_DESK_TOKEN", env)
        self.assertEqual(env["HELENE_TOKEN"], "tok")

    def test_ручки_обновления_только_ключу_окна(self):
        import deskapp  # noqa: PLC0415 — тяжёлый импорт только здесь
        for path in ("/api/update", "/api/update/plan", "/api/update/confirm"):
            self.assertTrue(deskapp._scope_ok("owner", path), path)
            self.assertFalse(deskapp._scope_ok("device", path), f"телефону нельзя: {path}")
            route, _ = deskapp.match_route("POST" if path != "/api/update" else "GET", path)
            self.assertIsNotNone(route, path)

    def test_код_42_не_падение(self):
        def child(code, key="runner"):
            one = serverboot.Child(key, "раннер" if key == "runner" else "реле", [], {}, self.tree,
                                   self.tree / "x.log")
            one.proc = types.SimpleNamespace(poll=lambda: code)
            one.falls = [1.0, 2.0]
            return one

        asked = child(serverboot.RESTART_EXIT_CODE)
        self.assertIn("попросил перезапуска", serverboot.settle(asked, 100.0))
        self.assertEqual((asked.falls, asked.retry_at, asked.halted), ([], 0.0, ""))
        fell = child(1)
        serverboot.settle(fell, 100.0)
        self.assertEqual(len(fell.falls), 3)
        self.assertGreater(fell.retry_at, 100.0)
        broken = child(3)
        serverboot.settle(broken, 100.0)
        self.assertIn("конфиг", broken.halted)
        relay42 = child(serverboot.RESTART_EXIT_CODE, key="relay")
        serverboot.settle(relay42, 100.0)
        self.assertGreater(relay42.retry_at, 100.0)     # 42 от реле — обычное падение


if __name__ == "__main__":
    unittest.main(verbosity=2)
