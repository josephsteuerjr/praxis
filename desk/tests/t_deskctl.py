# -*- coding: utf-8 -*-
"""Стенд «надзор за контейнерами»: что службе позволено и чего она не отдаёт.

Запуск:  python tests/t_deskctl.py

Служба (`server/deskctl.py`) — единственное место продукта с настоящим доступом
к докеру хоста. Поэтому здесь стерегут не «работает ли перезапуск» (это видно
живой пробой), а ГРАНИЦЫ:

  * закрытый список контейнеров — на общем сервере рядом чужие проекты, и
    «перезапусти что угодно» здесь означало бы отмычку к ним;
  * ключи провайдеров не уходят в окно ни при чтении мозга, ни при записи;
  * смена модели меняет ровно названные поля названной роли и кладёт прежний
    файл рядом — а не переписывает чужой конфиг целиком.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))

os.environ.setdefault("DESKCTL_TOKEN", "стенд")
os.environ.setdefault("DESKCTL_CONTAINERS", "praxis,relay,praxis-desk")

import deskctl  # noqa: E402


class WhiteList(unittest.TestCase):
    def test_чужой_контейнер_не_трогаем(self):
        for name in ("wedding", "hardbot2-bot-1", "", "praxis; rm -rf /"):
            self.assertFalse(deskctl.allowed(name), f"«{name}» не должен быть позволен")
            said = asyncio.run(deskctl.restart(name))
            self.assertFalse(said["ok"])
            self.assertIn("не позволено", said["note"])

    def test_свои_названы(self):
        for name in ("praxis", "relay", "praxis-desk"):
            self.assertTrue(deskctl.allowed(name))

    def test_журнал_чужого_тоже_закрыт(self):
        said = asyncio.run(deskctl.logs("wedding", 10))
        self.assertFalse(said["ok"])
        self.assertIn("не позволено", said["note"])


class Containers(unittest.TestCase):
    """Разбор ответа докера — отдельно от самого запроса: его и проверяем."""

    def test_разбор_ответа_докера(self):
        rows = {row["name"]: row for row in deskctl.digest([
            {"Names": ["/praxis"], "State": "running", "Status": "Up 15 hours",
             "Image": "praxis-praxis"},
            {"Names": ["/relay"], "State": "exited", "Status": "Exited (1) 2 minutes ago",
             "Image": "relay:local"},
        ])}
        self.assertTrue(rows["praxis"]["up"])
        self.assertFalse(rows["relay"]["up"])
        self.assertEqual(rows["relay"]["image"], "relay:local")
        self.assertFalse(rows["praxis-desk"]["known"],
                         "контейнера нет в ответе докера — так и сказать, а не выдумывать статус")

    def test_докер_молчит_отказ_называет_причину(self):
        real = deskctl._docker

        async def dead(path, method="GET", timeout=30):
            return 0, "сокета нет".encode()

        deskctl._docker = dead
        try:
            rows = asyncio.run(deskctl.containers())
        finally:
            deskctl._docker = real
        self.assertTrue(all(not row["known"] for row in rows))
        self.assertIn("сокета нет", rows[0]["why"])

    def test_только_ошибки_режут_хвост(self):
        rows = deskctl.only_error_rows(
            "\n".join(["обычная строка", "2026-09-10 ERROR всё плохо",
                       "Traceback (most recent call last):", "ещё обычная"]))
        self.assertEqual(len(rows), 2)
        self.assertIn("ERROR", rows[0])
        self.assertIn("Traceback", rows[1])

    def test_кадры_журнала_снимаются(self):
        """Докер отдаёт журнал кадрами: восемь байт заголовка на кусок."""
        text = "первая\nвторая\n"
        body = text.encode()
        framed = bytes([1, 0, 0, 0]) + len(body).to_bytes(4, "big") + body
        self.assertEqual(deskctl._plain(framed), text)
        self.assertIn("без кадров", deskctl._plain("без кадров".encode()))


class Brain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tree = Path(self.tmp.name)
        (tree / "memory").mkdir()
        self.path = tree / "memory" / "llm.json"
        self.path.write_text(json.dumps({
            "frameworks": {
                "anthropic": {"base_url": "https://api.z.ai/api/anthropic", "api_key": "живой-ключ-1"},
                "openai": {"base_url": "http://host.docker.internal:5012", "api_key": "живой-ключ-2"},
            },
            "roles": {
                "voice": {"framework": "openai", "model": "gpt-6-astra", "max_tokens": 32768,
                          "fallback_model": "glm-5.3", "reasoning_effort": "medium"},
                "evaluator": {"framework": "anthropic", "model": "glm-5.3-flash"},
            },
            "pricing": {"gpt-5.5": {"in_per_1m": 1}},
        }, ensure_ascii=False), encoding="utf-8")
        self.was = deskctl.TREE
        deskctl.TREE = tree
        self.addCleanup(lambda: setattr(deskctl, "TREE", self.was))
        self.addCleanup(self.tmp.cleanup)

    def test_ключи_провайдеров_не_уходят(self):
        said = deskctl.brain_state()
        text = json.dumps(said, ensure_ascii=False)
        self.assertNotIn("живой-ключ-1", text)
        self.assertNotIn("живой-ключ-2", text)
        self.assertTrue(said["frameworks"]["openai"]["key_present"],
                        "факт «ключ есть» показать можно — сам ключ нельзя")
        self.assertEqual(said["roles"]["voice"]["model"], "gpt-6-astra")

    def test_смена_модели_и_бэкап(self):
        said = deskctl.brain_set("voice", {"model": "glm-5.3"})
        self.assertTrue(said["ok"], said.get("note"))
        self.assertEqual(said["was"], {"model": "gpt-6-astra"})
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["roles"]["voice"]["model"], "glm-5.3")
        self.assertEqual(raw["roles"]["voice"]["max_tokens"], 32768, "чужие поля роли не трогаем")
        self.assertEqual(raw["frameworks"]["openai"]["api_key"], "живой-ключ-2", "ключи на месте")
        backups = list(self.path.parent.glob("llm.json.before-desk-*"))
        self.assertEqual(len(backups), 1, "прежний файл обязан лечь рядом")

    def test_неизвестная_роль_и_чужие_поля(self):
        self.assertFalse(deskctl.brain_set("нет-такой", {"model": "x"})["ok"])
        said = deskctl.brain_set("voice", {"api_key": "перехват", "pricing": {}})
        self.assertFalse(said["ok"], "менять можно только объявленные поля")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", raw["roles"]["voice"])


class Door(unittest.TestCase):
    def test_ручки_объявлены(self):
        paths = {(route.method, route.resource.canonical)
                 for route in deskctl.app().router.routes()}
        for want in (("GET", "/state"), ("GET", "/logs/{name}"), ("POST", "/restart/{name}"),
                     ("GET", "/brain"), ("GET", "/brain/models"), ("POST", "/brain"),
                     ("GET", "/health")):
            self.assertIn(want, paths, f"нет ручки {want}")


class ChannelSide(unittest.TestCase):
    """Канал без объявленной службы обязан отвечать словами, а не падать."""

    def test_нет_службы_нет_раздела(self):
        sys.path.insert(0, str(HERE.parent))
        from deskd import control  # noqa: PLC0415

        was = os.environ.pop("HELENE_DESKCTL", None)
        try:
            said = control.containers()
        finally:
            if was is not None:
                os.environ["HELENE_DESKCTL"] = was
        self.assertFalse(said["available"])
        self.assertIn("не объявлена", said["why"])
        self.assertEqual(said["containers"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
