# -*- coding: utf-8 -*-
"""Приёмка обновления — только с доказательством делом (1.2.5, слово Егора 28.09).

«Нужно ужесточить приёмку обновлений агентом — и на сервере, и на десктопе». До 1.2.5
`update_request accept` было словом: модель могла принять новую версию, не позвав ни одной
руки. Теперь рука сверяется с журналом дел испытания (`trial_proof.py`): хоть одна рабочая
рука вернула результат, и `recall` что-то нашёл. На ПК рука тоже есть — испытание там ведёт
установщик.

Запуск:  python tests/t_trial_proof_2809.py
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
from deskd import control  # noqa: E402
import trial_proof  # noqa: E402
import updates  # noqa: E402

ON_SERVER = {"HELENE_SUPERVISOR": "serverboot"}
ON_DESK = {"HELENE_SUPERVISOR": ""}


def fake_agent():
    channel = contextvars.ContextVar("chan", default=None)
    calls = []

    def funnel(name, impl, call_input):
        calls.append(name)
        return impl(**call_input)

    return types.SimpleNamespace(TOOL_IMPL={}, BASE_TOOLS=[], HAND_PURPOSE={},
                                 _TURN_CHANNEL=channel, _call_tool_with_ceiling=funnel, calls=calls)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-proof-")
        self.addCleanup(self._tmp.cleanup)
        self.tree = Path(self._tmp.name)
        trial_proof._PROOFS.clear()

    def receipt(self, state="trial", key="k3y", **extra):
        path = self.tree / "memory" / ".control" / control.UPDATE_RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        import time
        row = {"id": "p1", "state": state, "from_version": "1.2.4", "to_version": "1.2.5",
               "trial": {"key": key, "since_epoch": time.time(), "until_utc": "2026-09-29T12:00:00Z",
                         "minutes": 30}, **extra}
        path.write_text(json.dumps(row, ensure_ascii=False), "utf-8")
        # mtime кэш: два письма в одну секунду не должны прятать новое состояние
        os.utime(path, (os.path.getmtime(path) + len(state) + len(key), os.path.getmtime(path) + len(state) + len(key)))

    def install(self, env=ON_SERVER):
        agent = fake_agent()
        with mock.patch.dict(os.environ, env), mock.patch.object(updates.Path, "exists", return_value=False):
            self.assertTrue(updates.install(agent, self.tree, {}))
        return agent

    @staticmethod
    def call(agent, name, fn, **kw):
        return agent._call_tool_with_ceiling(name, fn, kw)


class Journal(Base):
    def test_outside_trial_nothing_is_written(self):
        agent = self.install()
        self.call(agent, "shell", lambda cmd: "ok", cmd="ls")
        self.assertFalse(trial_proof.proof_for(self.tree).path.exists())
        self.assertEqual(agent.calls, ["shell"], "настоящая воронка всё равно зовётся")

    def test_failures_and_empty_recall_are_not_deeds(self):
        agent = self.install()
        self.receipt()
        self.call(agent, "shell", lambda cmd: "[рука shell не позвана] нет cmd", cmd="")
        self.call(agent, "fs_read", lambda path: "", path="x")
        self.call(agent, "recall", lambda query: "Ничего не вспомнилось.", query="владелец")
        self.call(agent, "reply", lambda text: "отправлено", text="привет")
        ev = trial_proof.proof_for(self.tree).evidence("k3y")
        self.assertFalse(ev["ok"])
        self.assertEqual(len(ev["missing"]), 2)

    def test_deeds_of_another_trial_do_not_count(self):
        agent = self.install()
        self.receipt(key="old")
        self.call(agent, "shell", lambda cmd: "ok", cmd="ls")
        self.call(agent, "recall", lambda query: "Егор: вчера говорили о голосе", query="Егор")
        self.receipt(key="new")
        self.assertFalse(trial_proof.proof_for(self.tree).evidence("new")["ok"])
        self.assertTrue(trial_proof.proof_for(self.tree).evidence("old")["ok"])


class Accept(Base):
    def hand(self, agent):
        return agent.TOOL_IMPL[updates.TOOL_NAME]

    def test_accept_needs_deeds_and_carries_them_into_the_verdict(self):
        agent = self.install()
        self.receipt()
        hand = self.hand(agent)
        text = hand(action="accept", report="всё живо")
        self.assertIn("Пока не принимаю", text)
        self.assertIn("recall", text)
        verdict = self.tree / "memory" / ".control" / control.UPDATE_VERDICT
        self.assertFalse(verdict.exists())
        self.call(agent, "shell", lambda cmd: "3 files", cmd="ls")
        self.assertIn("Пока не принимаю", hand(action="accept", report="всё живо"))
        self.call(agent, "recall", lambda query: "Егор: вчера говорили о голосе", query="Егор")
        self.assertIn("принята", hand(action="accept", report="shell и память живы"))
        row = json.loads(verdict.read_text("utf-8"))
        self.assertEqual(row["verdict"], "accept")
        self.assertEqual(row["proof"]["hands"], ["shell"])
        self.assertIn("нашёл по «Егор»", row["proof"]["line"])

    def test_reject_needs_no_deeds(self):
        agent = self.install()
        self.receipt()
        self.assertIn("сломано", self.hand(agent)(action="reject", report="shell молчит"))

    def test_owner_accepts_from_the_window_without_deeds(self):
        self.receipt()
        got = control.update_verdict(self.tree, "p1", "k3y", "accept", by="window", words="")
        self.assertTrue(got["ok"])


class Desktop(Base):
    def test_desk_hand_speaks_of_the_installer(self):
        agent = self.install(ON_DESK)
        hand = agent.TOOL_IMPL[updates.TOOL_NAME]
        with mock.patch.dict(os.environ, ON_DESK), mock.patch.object(updates.Path, "exists", return_value=False):
            self.assertIn("кнопкой в окне", hand(action="status"))
            self.assertIn("кнопкой в окне", hand(action="confirm"))

    def test_receipts_are_read_only_at_home(self):
        self.receipt(desktop=True)
        with mock.patch.dict(os.environ, ON_DESK), mock.patch.object(updates.Path, "exists", return_value=False):
            self.assertIsNotNone(updates.pending_report(self.tree))
        with mock.patch.dict(os.environ, ON_SERVER):
            self.assertIsNone(updates.pending_report(self.tree), "расписка ПК на сервере — чужая")
        self.receipt()
        with mock.patch.dict(os.environ, ON_DESK), mock.patch.object(updates.Path, "exists", return_value=False):
            self.assertIsNone(updates.pending_report(self.tree), "расписка сервера на ПК — чужая")

    def test_trial_note_asks_for_deeds_and_names_the_installer(self):
        receipt = {"id": "p1", "state": "trial", "desktop": True, "from_version": "1.2.4",
                   "to_version": "1.2.5", "trial": {"until_utc": "2026-09-29T12:00:00Z", "minutes": 30},
                   "agent_code": {"mounted": True, "edited": ["tree/llm.py"], "carried": ["tree/llm.py"],
                                  "merged": [], "conflicts": [], "folder": "workspace/update-1.2.5"}}
        note = updates.trial_note(receipt)
        self.assertIn("делом", note)
        self.assertIn("установщик вернёт прежнюю версию программы", note)
        self.assertIn("перенесено 1", note)


class Watcher(Base):
    """Испытание не закрыто, а сторожа не слышно (перезагрузка, окно закрывали) — движок зовёт его."""

    def setUp(self):
        super().setUp()
        self.install = self.tree / "Helene"
        self.data = self.install / "data"
        (self.install / "backups").mkdir(parents=True)
        (self.data / "memory" / ".control").mkdir(parents=True)
        exe = updates._setup_exe(self.install)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"exe")
        updates._WATCHER_CALLED[0] = 0.0
        self.calls = []

    def state(self, phase):
        (self.install / "backups" / "update-trial.json").write_text(
            json.dumps({"phase": phase, "from_version": "1.2.4", "to_version": "1.2.5"}), "utf-8")

    def beat(self, at, desktop=True):
        (self.data / "memory" / ".control" / "updater.json").write_text(
            json.dumps({"beat_epoch": at, "desktop": desktop}), "utf-8")

    def ensure(self, now, env=ON_DESK):
        with mock.patch.dict(os.environ, env), mock.patch.object(updates.Path, "exists", return_value=False):
            return updates.ensure_watcher(self.install, self.data, now=now, spawn=self.calls.append)

    def test_open_trial_without_a_heartbeat_calls_the_watcher_once_in_a_while(self):
        self.state("trial")
        self.assertIn("позван", self.ensure(1000.0))
        self.assertEqual(self.calls[0][1:], ["--trial", "--dir", str(self.install)])
        self.assertEqual(self.ensure(1060.0), "", "не чаще раза в две минуты")
        self.assertIn("позван", self.ensure(1200.0))

    def test_live_watcher_or_closed_trial_needs_nothing(self):
        self.state("trial")
        self.beat(995.0)
        self.assertEqual(self.ensure(1000.0), "")
        self.state("done")
        self.beat(0.0)
        self.assertEqual(self.ensure(5000.0), "")
        self.assertEqual(self.calls, [])

    def test_not_on_a_server(self):
        self.state("trial")
        self.assertEqual(self.ensure(1000.0, env=ON_SERVER), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
