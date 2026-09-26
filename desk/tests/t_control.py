# -*- coding: utf-8 -*-
"""Стенд «управление харнессом, который живёт не здесь».

Запуск:  python tests/t_control.py

Окно к серверу было смотрелкой: видно всё, сделать нельзя ничего — упавший
раннер или неподнявшееся реле чинились только через ssh. Управление добавили
файловым протоколом (`deskd/control.py`): канал КЛАДЁТ просьбу, надзор
(`server/serverboot.py`) исполняет и отвечает распиской.

Проверяется здесь то, что в этом протоколе может соврать:

  * «управление доступно» обязано считаться из реальности — свежести записки
    надзора, — а не из конфига и не из факта «мы на сервере»;
  * отказ обязан называть причину: просьба, положенная в дерево без надзора,
    осталась бы лежать молча, а окно рисовало бы «перезапускаю…»;
  * имя журнала НИКОГДА не превращается в путь: ключ окна открывает канал
    целиком, и `../helene.json` в имени журнала отдал бы ключи модели.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from deskd import control  # noqa: E402


class Ground(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        (self.tree / "memory" / ".state").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def state_file(self, name: str) -> Path:
        return self.tree / "memory" / ".state" / name

    def alive_note(self, children=None):
        control.beat(self.tree, "serverboot", "2026-09-10T10:00:00Z",
                     children if children is not None else
                     [{"id": "runner", "name": "раннер", "alive": True, "pid": 8,
                       "since_utc": "2026-09-10T10:00:01Z", "falls": 0, "halted": ""}])


class SupervisorState(Ground):
    def test_записки_нет_управления_нет_и_сказано_почему(self):
        state = control.supervisor_state(self.tree)
        self.assertFalse(state["control"]["available"])
        # 26.09 (1.1.0): записку надзора на Windows и Mac пишет сам движок; её нет —
        # значит движок не отвечает, и окно обязано сказать это словами.
        self.assertIn("движок не отвечает", state["control"]["why"])
        self.assertIn("поднимут", state["control"]["why"])
        self.assertFalse(state["alive"])

    def test_свежая_записка_даёт_управление(self):
        self.alive_note()
        state = control.supervisor_state(self.tree)
        self.assertTrue(state["alive"])
        self.assertTrue(state["control"]["available"])
        self.assertEqual(state["kind"], "serverboot")
        self.assertEqual([c["id"] for c in state["children"]], ["runner"])
        self.assertLess(state["beat_age"], 5)

    def test_протухшая_записка_не_управление(self):
        self.alive_note()
        note = json.loads(self.state_file(control.SUPERVISOR).read_text("utf-8"))
        note["beat_epoch"] = time.time() - (control.BEAT_STALE + 30)
        self.state_file(control.SUPERVISOR).write_text(json.dumps(note), encoding="utf-8")
        state = control.supervisor_state(self.tree)
        self.assertFalse(state["alive"])
        self.assertFalse(state["control"]["available"])
        self.assertIn("молчит", state["control"]["why"])

    def test_названы_все_цели_перезапуска(self):
        self.alive_note()
        state = control.supervisor_state(self.tree)
        self.assertEqual({t["id"] for t in state["targets"]}, {"runner", "relay", "all"})


class Asking(Ground):
    def test_без_надзора_просьба_не_кладётся(self):
        answer = control.ask(self.tree, "runner")
        self.assertFalse(answer["ok"])
        self.assertIn("надзора", answer["note"])
        self.assertFalse(self.state_file(control.REQUEST).exists(),
                         "просьба без надзора пролежала бы вечно — класть её нельзя")

    def test_просьба_ложится_с_целью_и_временем(self):
        self.alive_note()
        answer = control.ask(self.tree, "relay")
        self.assertTrue(answer["ok"])
        written = json.loads(self.state_file(control.REQUEST).read_text("utf-8"))
        self.assertEqual(written["target"], "relay")
        self.assertEqual(written["action"], "restart")
        self.assertTrue(written["id"])
        self.assertTrue(written["asked_utc"].endswith("Z"))

    def test_неизвестная_цель_отвергается(self):
        self.alive_note()
        answer = control.ask(self.tree, "весь сервер")
        self.assertFalse(answer["ok"])
        self.assertFalse(self.state_file(control.REQUEST).exists())

    def test_надзор_забирает_просьбу_со_стола(self):
        self.alive_note()
        control.ask(self.tree, "all")
        taken = control.take_request(self.tree)
        self.assertEqual(taken["target"], "all")
        self.assertFalse(self.state_file(control.REQUEST).exists(),
                         "взятая просьба не должна исполниться дважды")
        self.assertEqual(control.take_request(self.tree), {})

    def test_расписка_говорит_что_вышло(self):
        self.alive_note()
        control.ask(self.tree, "runner")
        request = control.take_request(self.tree)
        control.receipt(self.tree, request, True, "раннер перезапущен, pid 42")
        state = control.supervisor_state(self.tree)
        self.assertEqual(state["receipt"]["id"], request["id"])
        self.assertTrue(state["receipt"]["done"])
        self.assertIn("pid 42", state["receipt"]["note"])
        self.assertIsNone(state["pending"], "исполненная просьба со стола убрана")


class Logs(Ground):
    def test_список_говорит_чего_нет(self):
        (self.tree / "runner.log").write_text("строка\n", encoding="utf-8")
        rows = {row["id"]: row for row in control.log_names(self.tree)}
        self.assertTrue(rows["runner"]["exists"])
        self.assertFalse(rows["relay"]["exists"],
                         "нет журнала — так и сказать, а не показывать пустоту как норму")

    def test_хвост_режется_числом_строк(self):
        (self.tree / "runner.log").write_text(
            "\n".join(f"строка {n}" for n in range(1, 51)) + "\n", encoding="utf-8")
        got = control.tail(self.tree, "runner", 5)
        self.assertTrue(got["ok"])
        self.assertEqual(got["lines"], 5)
        self.assertTrue(got["text"].endswith("строка 50"))
        self.assertNotIn("строка 45", got["text"])

    def test_имя_журнала_не_превращается_в_путь(self):
        (self.tree.parent / "helene.json").write_text('{"model": {"key": "sk-живой"}}',
                                                      encoding="utf-8")
        for evil in ("../helene.json", "/etc/passwd", "runner.log", "helene.json"):
            got = control.tail(self.tree, evil)
            self.assertFalse(got["ok"], f"имя {evil!r} не должно открывать файл")

    def test_свой_журнал_реле_берётся_самый_свежий(self):
        logs = self.tree / "relay" / "logs"
        logs.mkdir(parents=True)
        (logs / "relay.log.2026-09-09").write_text("вчера\n", encoding="utf-8")
        newer = logs / "relay.log.2026-09-10"
        newer.write_text("сегодня\n", encoding="utf-8")
        import os

        os.utime(newer, (time.time(), time.time()))
        got = control.tail(self.tree, "relay_own")
        self.assertTrue(got["ok"])
        self.assertIn("сегодня", got["text"])


class ChannelDoor(unittest.TestCase):
    """Ручки управления — владельцу, а не спаренному телефону."""

    def test_телефону_сюда_нельзя(self):
        sys.path.insert(0, str(HERE.parent))
        import deskapp  # noqa: PLC0415 — стенд смотрит на таблицу канала

        for path in ("/api/supervisor", "/api/logs", "/api/log/runner"):
            self.assertNotIn(path, deskapp._DEVICE_PATHS)
            self.assertFalse(any(path.startswith(pref) for pref in deskapp._DEVICE_PREFIXES),
                             f"{path} не должен попадать под префиксы устройства")

    def test_ручки_есть_в_таблице(self):
        import deskapp  # noqa: PLC0415

        for method, path in (("GET", "/api/supervisor"), ("POST", "/api/supervisor/restart"),
                             ("GET", "/api/logs"), ("GET", "/api/log/runner")):
            route, match = deskapp.match_route(method, path)
            self.assertIsNotNone(route, f"нет маршрута {method} {path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
