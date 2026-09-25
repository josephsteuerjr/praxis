"""Стенды по ревью 25.09 (A12 F5/F7/F8): проводка, а не только модули.

* `_config_watch_forever` перечитывает helene.json на тике: мозг проецируется, галочки
  `deliver_unspoken` и `status_message` применяются без перезапуска.
* `--check-extensions` печатает в stdout ровно один JSON с версией ПОСТАВКИ (`--host-version`),
  даже если код расширения печатает мусор на импорте; код выхода 0/2.
* `deliver_text` зовёт крючок `before_send` с chat_id ДО первого sendMessage; `is_last_message`
  читает `last_incoming` и `sent_now`.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "localharness"))
os.environ.setdefault("PRAXIS_TEST", "1")

import runner  # noqa: E402
import boot  # noqa: E402
import botapi  # noqa: E402


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class ConfigWatch(unittest.TestCase):
    def test_tick_projects_brain_and_applies_agent_knobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "helene.json"
            tree = Path(tmp) / "data"
            tree.mkdir()
            cfg_path.write_text(json.dumps({"model": {"model": "m1"}, "agent": {"deliver_unspoken": True},
                                            "telegram": {"status_message": False}}), encoding="utf-8")
            calls = []
            fake_agent = mock.Mock(BOUNDARY_DELIVERS_UNSPOKEN=True)
            with mock.patch.object(runner, "_CONFIG_WATCH_SEC", 0.05), \
                 mock.patch.object(boot, "project_brain", side_effect=lambda t, c: calls.append(c) or "мозг: ok"), \
                 mock.patch.object(runner, "_agent", fake_agent), \
                 mock.patch.object(runner, "_status_message", False), \
                 mock.patch.object(runner, "_deliver_unspoken", True):
                th = threading.Thread(target=runner._config_watch_forever, args=(cfg_path, tree), daemon=True)
                th.start()
                time.sleep(0.15)
                self.assertEqual(calls, [], "без изменения файла проекции нет")
                time.sleep(0.02)
                cfg_path.write_text(json.dumps({"model": {"model": "m2"}, "agent": {"deliver_unspoken": False},
                                                "telegram": {"status_message": True}}), encoding="utf-8")
                os.utime(cfg_path, None)
                self.assertTrue(_wait(lambda: len(calls) >= 1), "изменение helene.json не спроецировано")
                self.assertEqual(calls[-1]["model"]["model"], "m2")
                self.assertTrue(_wait(lambda: runner._status_message is True), "галочка «думаю» не применилась на тике")
                self.assertIs(fake_agent.BOUNDARY_DELIVERS_UNSPOKEN, False)
                # битый файл — мозг не трогается, поток жив
                cfg_path.write_text("{ не json", encoding="utf-8")
                time.sleep(0.2)
                self.assertEqual(len(calls), 1, "битый helene.json не должен проецироваться")


class CheckExtensions(unittest.TestCase):
    def _ext(self, data: Path, code: str, requires: str = ""):
        ext = data / "extensions" / "quota"
        ext.mkdir(parents=True)
        manifest = {"name": "quota", "version": "1.0.0", "api": "helene.ext/1.0", "entry": "ext:register"}
        if requires:
            manifest["requires"] = {"helene": requires}
        (ext / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
        (ext / "ext.py").write_text(code, encoding="utf-8")

    def _run(self, data: Path, host: str = "0.9.0"):
        out = io.StringIO()
        err = io.StringIO()
        argv = ["runner", "--check-extensions", "--data", str(data), "--host-version", host]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), \
             contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                runner.main()
        return cm.exception.code, out.getvalue(), err.getvalue()

    def test_stdout_is_one_json_with_the_payload_version_even_if_the_extension_prints(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            self._ext(data, 'print("quota loaded")\ndef register(api):\n    pass\n', requires=">=0.9.0")
            code, out, err = self._run(data, host="0.9.0")
            self.assertEqual(code, 0, out)
            report = json.loads(out)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["helene"], "0.9.0", "версия — поставки, из --host-version (A5 F1)")
            self.assertIn("quota loaded", err, "мусор расширения ушёл в stderr, не в отчёт (A5 F2)")

    def test_incompatible_extension_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            self._ext(data, "def register(api):\n    pass\n", requires=">=1.2.0")
            code, out, _ = self._run(data, host="0.9.0")
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(out)["ok"])


class FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        return {"message_id": 100 + len(self.calls), "chat": {"id": params.get("chat_id")}}


class TransportHooks(unittest.TestCase):
    def _bot(self):
        bot = botapi.BotTransport.__new__(botapi.BotTransport)
        bot.client = FakeClient()
        bot.before_send = None
        bot.last_incoming = {}
        bot.sent_now = []
        bot.agent_name = "Агент"
        bot.log = None
        bot.rooms = mock.Mock()      # архив комнаты — не предмет стенда
        bot.tree = Path(tempfile.mkdtemp(prefix="t_review_bot_"))
        bot.memory_life = None
        return bot

    def test_before_send_hook_fires_with_chat_before_the_first_send(self):
        bot = self._bot()
        seen = []
        bot.before_send = lambda chat_id=None: seen.append((chat_id, len(bot.client.calls)))
        bot.deliver_text("777", "привет")
        self.assertEqual(seen, [("777", 0)], "крючок — до первого sendMessage и с chat_id (A6 F7)")
        self.assertEqual(bot.client.calls[0][0], "sendMessage")

    def test_before_send_hook_without_chat_argument_still_works(self):
        bot = self._bot()
        seen = []
        bot.before_send = lambda: seen.append("x")
        bot.deliver_text("777", "привет")
        self.assertEqual(seen, ["x"])

    def test_is_last_message_reads_incoming_and_own_sends(self):
        bot = self._bot()
        bot.last_incoming["777"] = 50
        self.assertTrue(bot.is_last_message("777", 60))
        bot.last_incoming["777"] = 70
        self.assertFalse(bot.is_last_message("777", 60), "человек написал следом")
        bot.last_incoming["777"] = 50
        bot.sent_now.append(("777", 61))
        self.assertFalse(bot.is_last_message("777", 60), "агент уже ответил")


if __name__ == "__main__":
    unittest.main()
