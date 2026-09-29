# -*- coding: utf-8 -*-
"""Поручение службе напрямую, без окна и без «да» (28.09, слово Егора).

«С включённым тумблером не надо запрашивать у владельца: говорит службе — та делает».
На Windows харнесс ходит в трубу службы сам: имя трубы — из пути установки (FNV-1a,
как `broker_pipe_name` в common/broker.rs), токен — из `memory/.state/broker-token`,
кадр — длина u32 LE и JSON. Живой трубы здесь нет: `call_service` подменяется, и
стенд держит форму поручения, слова квитанции и отказ без токена.

Запуск:  python tests/t_broker_direct_2809.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import broker  # noqa: E402

broker.HAS_BROKER = True
broker.POSIX_PATHS = False


def _tree(root: Path, token: str | None = "a" * 32) -> Path:
    state = root / "memory" / ".state"
    state.mkdir(parents=True, exist_ok=True)
    if token is not None:
        (state / "broker-token").write_text(token + "\n", encoding="utf-8")
    return root


class PipeName(unittest.TestCase):
    def test_same_name_as_the_service_for_the_same_folder(self):
        # Живое имя службы Егора 28.09 (service.log): helene-broker-b1e730f13e0af00c.
        want = r"\\.\pipe\helene-broker-b1e730f13e0af00c"
        self.assertEqual(broker.pipe_name(r"C:\Program Files\Helene"), want)
        self.assertEqual(broker.pipe_name("C:/Program Files/Helene/"), want)
        self.assertEqual(broker.pipe_name(r"\\?\C:\Program Files\HELENE"), want)

    def test_token_rules_match_the_rust_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp), "abcDEF0123456789")
            self.assertEqual(broker.read_token(tree), "abcDEF0123456789")
            (tree / "memory" / ".state" / "broker-token").write_text("короткий", encoding="utf-8")
            self.assertIsNone(broker.read_token(tree))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(broker.read_token(_tree(Path(tmp), None)))

    def test_frame_is_length_then_body(self):
        self.assertEqual(broker._frame(b"{}"), b"\x02\x00\x00\x00{}")


class DirectAsk(unittest.TestCase):
    def setUp(self):
        self.saved = (broker.DIRECT, broker.call_service)
        broker.DIRECT = True
        broker.RECENT.clear()
        self.sent = []

    def tearDown(self):
        broker.DIRECT, broker.call_service = self.saved

    def _serve(self, receipt):
        def fake(pipe, payload, wait_sec):
            self.sent.append((pipe, payload, wait_sec))
            if isinstance(receipt, Exception):
                raise receipt
            return dict(receipt, id=payload["id"])
        broker.call_service = fake

    def test_exec_goes_to_the_service_and_comes_back_as_a_receipt(self):
        self._serve({"ok": True, "op": "exec", "code": 0, "out": "nt authority\\system",
                     "err": "", "ms": 70, "note": ""})
        with tempfile.TemporaryDirectory() as tmp:
            tree = _tree(Path(tmp))
            said = broker.Broker(tree).ask("exec", r"C:\Windows\System32\whoami.exe", ["/user"],
                                           "проверка", 30, 120)
        self.assertEqual(len(self.sent), 1)
        pipe, payload, wait_sec = self.sent[0]
        self.assertTrue(pipe.startswith(r"\\.\pipe\helene-broker-"))
        self.assertEqual((payload["op"], payload["cmd"], payload["args"]),
                         ("exec", r"C:\Windows\System32\whoami.exe", ["/user"]))
        self.assertEqual(payload["token"], "a" * 32)
        self.assertEqual(payload["timeout_sec"], 30)
        self.assertEqual(payload["v"], broker.V)
        self.assertGreater(wait_sec, 30)
        self.assertIn("служба выполнила", said)
        self.assertIn("код возврата 0", said)
        self.assertIn("nt authority", said)
        # Ни окна, ни файла просьб: владельца не спрашивают.
        self.assertFalse(broker.asks_path(tree).exists())

    def test_refusal_of_the_service_is_said_in_its_words(self):
        self._serve({"ok": False, "op": "exec", "code": None, "out": "", "err": "", "ms": 0,
                     "note": "нулевая сессия выключена — поручений правами СИСТЕМЫ брокер не исполняет"})
        with tempfile.TemporaryDirectory() as tmp:
            said = broker.Broker(_tree(Path(tmp))).ask(
                "exec", r"C:\Windows\System32\whoami.exe", [], "проверка", 30, 0)
        self.assertIn("ОТКАЗ", said)
        self.assertIn("нулевая сессия выключена", said)
        self.assertNotIn("код возврата", said)

    def test_dead_pipe_is_named_not_hung(self):
        self._serve(OSError("служба не отвечает на трубе"))
        with tempfile.TemporaryDirectory() as tmp:
            said = broker.Broker(_tree(Path(tmp))).ask(
                "exec", r"C:\Windows\System32\whoami.exe", [], "проверка", 30, 0)
        self.assertIn("не вышло", said)
        self.assertIn("служба не отвечает", said)

    def test_without_a_token_nothing_is_sent(self):
        self._serve({"ok": True})
        # Служба стоит, токена ещё нет. Без закрепления на macOS (службы Windows там нет)
        # срабатывала другая честная ветка — «службы нет» (Mac-сборка 1.2.5, 29.09).
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(broker.Broker, "service", return_value=True):
            said = broker.Broker(_tree(Path(tmp), None)).ask(
                "exec", r"C:\Windows\System32\whoami.exe", [], "проверка", 30, 0)
        self.assertEqual(self.sent, [])
        self.assertIn("токена службы нет", said)

    def test_bad_ask_is_refused_before_the_pipe(self):
        self._serve({"ok": True})
        with tempfile.TemporaryDirectory() as tmp:
            said = broker.Broker(_tree(Path(tmp))).ask("exec", "whoami", [], "проверка", 30, 0)
        self.assertEqual(self.sent, [])
        self.assertIn("полным путём", said)

    def test_listing_shows_receipts_not_waiting_asks(self):
        self._serve({"ok": True, "op": "ping", "note": "брокер жив", "ms": 0})
        with tempfile.TemporaryDirectory() as tmp:
            b = broker.Broker(_tree(Path(tmp)))
            b.ask("ping", "", [], "проверка связи", 10, 0)
            said = b.listing()
        self.assertIn("напрямую", said)
        self.assertIn("брокер жив", said)
        self.assertNotIn("Ждут владельца", said)

    def test_windows_tool_has_no_owner_yes_and_no_wait(self):
        win = broker.tool_schema(mac=False)
        self.assertNotIn("«да»", win["description"])
        self.assertNotIn("wait_sec", win["input_schema"]["properties"])
        self.assertIn("spawn_interactive", win["description"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
