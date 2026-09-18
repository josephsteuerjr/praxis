# -*- coding: utf-8 -*-
"""Стенд руки брокера (задача R7): агент просит — владелец подписывает.

Запуск:  python tests/t_broker_hand.py

Живого брокера здесь нет и быть не должно: ни службы, ни трубы, ни окна
подтверждения. Проверяется ровно то, что делает харнесс, — просьба нужной формы
в `broker-asks.json`, честный ответ, когда показать её некому, и подбор
квитанции из `broker-answers.json`. Ответы владельца пишет сам стенд: в продукте
их пишет оболочка (`shell/src/main.rs::broker_pass`).

Главное, что здесь стережётся, — ГРАНИЦА. Проверки просьбы в харнессе обязаны
совпадать с `BrokerAsk::parse` из `common/broker.rs`: что отвергает служба,
владельцу даже не показывают, и разъехавшись, местная проверка начала бы
пропускать просьбы, которые всё равно кончатся отказом через минуту ожидания.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import broker  # noqa: E402

# Стенд разбирает САМ тул; есть ли брокер на этой платформе, решает
# `broker.HAS_BROKER` (только Windows). Поднимаем флаг, чтобы разбор шёл и на
# раннере macOS; отсутствие брокера проверяется отдельно (`Absent`).
broker.HAS_BROKER = True


def _tree(root: Path) -> Path:
    (root / "memory" / ".state").mkdir(parents=True, exist_ok=True)
    return root


def _desk_is_watching(tree: Path, *, pid: int | None = None) -> None:
    """Сказать, что окно слушает: блок `desk` с ЖИВЫМ pid (наш собственный)."""
    broker.answers_path(tree).write_text(json.dumps({
        "v": 1, "updated_at": "[04.09.2026 15:00:00]",
        "desk": {"watching_since": "[04.09.2026 14:59:00]",
                 "pid": os.getpid() if pid is None else pid},
        "answers": [],
    }, ensure_ascii=False), encoding="utf-8")


def _owner_answered(tree: Path, row: dict) -> None:
    data = json.loads(broker.answers_path(tree).read_text(encoding="utf-8"))
    data["answers"].append(row)
    broker.answers_path(tree).write_text(json.dumps(data, ensure_ascii=False),
                                         encoding="utf-8")


class Border(unittest.TestCase):
    """Проверки просьбы — та же граница, что в common/broker.rs."""

    def test_command_must_be_a_full_path(self):
        # Голое имя Windows ищет СНАЧАЛА в папке процесса службы, то есть в
        # папке установки: подменённый там netsh.exe исполнился бы СИСТЕМОЙ.
        self.assertIn("полным путём", broker.check_cmd("netsh.exe"))
        self.assertIn("«..»", broker.check_cmd(r"C:\Windows\..\netsh.exe"))
        self.assertEqual(broker.check_cmd(r"C:\Windows\System32\netsh.exe"), "")
        self.assertEqual(broker.check_cmd(r"\\server\share\tool.exe"), "")

    def test_args_are_an_array_and_never_one_string(self):
        said = broker.check_args("advfirewall firewall show rule")
        self.assertIn("МАССИВ", said)
        self.assertEqual(broker.check_args(["advfirewall", "show"]), "")
        self.assertIn("не-строка", broker.check_args(["ok", 7]))
        self.assertIn("нулевой байт", broker.check_args(["a\0b"]))
        self.assertIn("256", broker.check_args(["x"] * 257))

    def test_why_is_required_and_one_line(self):
        self.assertIn("ЗАЧЕМ", broker.check_why(""))
        self.assertIn("словами", broker.check_why("-"))
        self.assertIn("одной строкой", broker.check_why("правило\nи ещё строка"))
        self.assertIn("500", broker.check_why("я" * 501))
        self.assertEqual(broker.check_why("открыть порт трубы для телефона"), "")

    def test_op_and_timeout(self):
        self.assertIn("не знаю такой двери",
                      broker.check("root", "", None, "зачем-то", 60))
        self.assertIn("от 1 до 600",
                      broker.check("ping", "", None, "проверка связи", 601))
        # ping ничего не выполняет — команда ему не нужна.
        self.assertEqual(broker.check("ping", "", None, "проверка связи", 60), "")


class WithoutTheDesk(unittest.TestCase):
    """Окна нет — просьбу некому показать. Молчать об этом нельзя."""

    def test_no_answers_file_means_nobody_is_listening(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            said = broker.Broker(tree).ask(
                "spawn_interactive", r"C:\Windows\System32\ipconfig.exe", [],
                "посмотреть адрес", 60, 0)
            self.assertIn("окна", said)
            self.assertIn("не записал", said)
            # Файла просьб нет вовсе: писать в никуда и ждать десять минут —
            # это молчание, а не работа.
            self.assertFalse(broker.asks_path(tree).exists())

    def test_dead_desk_pid_is_not_a_listener(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            # Файл ответов переживает закрытие окна: блок `desk` в нём остаётся,
            # и без проверки pid он выглядел бы живым слушателем.
            _desk_is_watching(tree, pid=0x7FFFFFF0)
            listening, words = broker.Broker(tree).desk()
            self.assertFalse(listening)
            self.assertIn("закрыто", words)


class TheAsk(unittest.TestCase):
    """Просьба той формы, которую разбирает оболочка."""

    def test_ask_is_written_in_the_shape_the_desk_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            _desk_is_watching(tree)
            said = broker.Broker(tree).ask(
                "exec", r"C:\Windows\System32\netsh.exe",
                ["advfirewall", "firewall", "show", "rule", "name=all"],
                "посмотреть правила брандмауэра", 60, 0)
            # Ждать было некому — это НЕ отказ, и рука обязана сказать именно так.
            self.assertIn("не ответил", said)
            self.assertNotIn("отказал", said)
            data = json.loads(broker.asks_path(tree).read_text(encoding="utf-8"))
            self.assertEqual(data["v"], broker.V)
            row = data["requests"][0]
            self.assertEqual(row["op"], "exec")
            self.assertEqual(row["cmd"], r"C:\Windows\System32\netsh.exe")
            self.assertEqual(row["args"][0], "advfirewall")
            self.assertEqual(row["why"], "посмотреть правила брандмауэра")
            self.assertTrue(row["at_unix"] > 0)
            # id — только те знаки, которые примет оболочка (broker_wish_id);
            # чужой знак там не чистится, а отбрасывается молча.
            self.assertTrue(all(c.isalnum() or c in "-_" for c in row["id"]))
            self.assertLessEqual(len(row["id"]), 64)
            # Токена в просьбе нет: секрет подставляет оболочка.
            self.assertNotIn("token", row)

    def test_refused_ask_is_not_written_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            _desk_is_watching(tree)
            said = broker.Broker(tree).ask(
                "exec", "netsh.exe", [], "открыть порт", 60, 0)
            self.assertIn("полным путём", said)
            self.assertFalse(broker.asks_path(tree).exists())

    def test_receipt_comes_back_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            _desk_is_watching(tree)
            hand = broker.Broker(tree)
            hand.ask("spawn_interactive", r"C:\Windows\System32\ipconfig.exe",
                     ["/all"], "посмотреть адрес машины", 60, 0)
            ask_id = json.loads(
                broker.asks_path(tree).read_text(encoding="utf-8"))["requests"][0]["id"]
            _owner_answered(tree, {"id": ask_id, "decision": "allowed", "op": "exec",
                                   "why": "посмотреть адрес машины", "ok": True,
                                   "code": 0, "out": "IPv4 …", "err": "", "ms": 120})
            answer = hand.wait(ask_id, 0)
            self.assertIsNotNone(answer)
            words = broker.describe_answer(answer)
            self.assertIn("владелец разрешил", words)
            self.assertIn("код возврата 0", words)
            # Отвеченную просьбу харнесс убирает сам — как mounts.forget_answered.
            self.assertEqual(hand.asks(), [])

    def test_refusal_is_not_dressed_up_as_success(self):
        row = {"id": "b7", "decision": "refused",
               "note": "владелец отказал в окне подтверждения"}
        words = broker.describe_answer(row)
        self.assertIn("ОТКАЗ", words)
        self.assertIn("владелец отказал", words)

    def test_stale_asks_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            broker.asks_path(tree).write_text(json.dumps({
                "v": 1, "requests": [
                    {"id": "old", "op": "exec", "cmd": "C:\\x.exe", "args": [],
                     "why": "давно", "at_unix": 1},
                    {"id": "new", "op": "exec", "cmd": "C:\\x.exe", "args": [],
                     "why": "только что", "at_unix": int(__import__("time").time())},
                ]}, ensure_ascii=False), encoding="utf-8")
            left = broker.Broker(tree).forget_answered()
            self.assertEqual([r["id"] for r in left], ["new"])


class TheHand(unittest.TestCase):
    """Рука выдаётся агенту тем же приёмом, что и mount_request."""

    def test_install_offers_the_tool(self):
        class FakeTree:
            TOOL_IMPL: dict = {}
            BASE_TOOLS: list = []

        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            fake = FakeTree()
            broker.install(fake, tree, {})
            self.assertIn("broker_request", fake.TOOL_IMPL)
            self.assertTrue(callable(fake.TOOL_IMPL["broker_request"]))
            self.assertEqual([t["name"] for t in fake.BASE_TOOLS], ["broker_request"])
            self.assertTrue(broker.state()["hand"])
            # Список отвечает и без окна, и без службы — не падая.
            said = fake.TOOL_IMPL["broker_request"](action="list")
            self.assertIn("Ждущих просьб нет", said)

    def test_hand_never_raises(self):
        class FakeTree:
            TOOL_IMPL: dict = {}
            BASE_TOOLS: list = []

        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            fake = FakeTree()
            broker.install(fake, tree, {})
            hand = fake.TOOL_IMPL["broker_request"]
            self.assertIn("action бывает", hand(action="что-то своё"))
            self.assertIn("МАССИВ", hand(cmd=r"C:\x.exe", args="a b", why="зачем-то"))


class Absent(unittest.TestCase):
    """Порт без брокера (macOS): тул не выдаётся, а снимок говорит почему.

    Обещать модели тул, который откажет всегда, хуже, чем не иметь его: она
    тратила бы ходы на просьбы, которые некому подписать.
    """

    def test_no_broker_means_no_tool_and_a_named_reason(self):
        class FakeTree:
            TOOL_IMPL: dict = {}
            BASE_TOOLS: list = []

        saved = broker.HAS_BROKER
        broker.HAS_BROKER = False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake = FakeTree()
                broker.install(fake, _tree(Path(tmp)), {})
                self.assertNotIn("broker_request", fake.TOOL_IMPL)
                self.assertEqual(fake.BASE_TOOLS, [])
                self.assertFalse(broker.state()["hand"])
                self.assertIn("брокера в этой сборке нет", broker.state()["note"])
        finally:
            broker.HAS_BROKER = saved

    def test_flag_follows_the_platform(self):
        # Сам флаг — про Windows: только там есть служба, которая исполняет просьбы.
        import importlib
        fresh = importlib.reload(broker)
        try:
            self.assertEqual(fresh.HAS_BROKER, os.name == "nt")
        finally:
            fresh.HAS_BROKER = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
