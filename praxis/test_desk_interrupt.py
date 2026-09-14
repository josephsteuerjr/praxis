"""Прерывание живого хода с Пульта (12.09): файл в memory/.control → кооперативная отмена.

Запуск: python praxis_test.py test_desk_interrupt -v
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("TELEGRAM_SESSION", str(Path(tempfile.gettempdir()) / "praxis_test_desk_interrupt"))

import mtproto_runner as runner  # noqa: E402
import selfdev  # noqa: E402


class _Manager:
    def __init__(self, rows, conflict=()):
        self.rows = rows
        self.conflict = set(conflict)
        self.cancelled: list[tuple[str, str, str]] = []

    def list_runs(self, *, statuses=None, kind="", limit=None):
        return [dict(r) for r in self.rows]

    def request_cancel(self, run_id, *, actor, reason="", expected_revision=None):
        if run_id in self.conflict:
            raise RuntimeError("another control request is already pending")
        self.cancelled.append((run_id, actor, reason))
        return {"status": "cancelled"}


class DeskInterruptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_interrupt_"))
        control = self.tmp / ".control"
        for patch in (
            mock.patch.object(selfdev, "CONTROL_DIR", control),
            mock.patch.object(selfdev, "INTERRUPT_REQ", control / "interrupt.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.journal: list[str] = []
        mock.patch.object(runner.agent, "tool_journal",
                          lambda entry, salience=2: self.journal.append(entry) or "ok").start()
        self.addCleanup(mock.patch.stopall)

    def _request(self, **fields):
        selfdev.INTERRUPT_REQ.parent.mkdir(parents=True, exist_ok=True)
        selfdev.INTERRUPT_REQ.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")

    def test_no_request_is_a_noop(self):
        manager = _Manager([{"run_id": "run-1", "status": "running"}])
        with mock.patch.object(runner.agent, "_runs", lambda: manager):
            asyncio.run(runner._desk_interrupt_once())
        self.assertEqual(manager.cancelled, [])
        self.assertEqual(self.journal, [])

    def test_request_cancels_every_live_run_and_clears_the_file(self):
        manager = _Manager([{"run_id": "run-1", "status": "running"},
                            {"run_id": "run-2", "status": "paused"}])
        self._request(by="phone", reason="стоп")
        with mock.patch.object(runner.agent, "_runs", lambda: manager):
            asyncio.run(runner._desk_interrupt_once())
        self.assertEqual([c[0] for c in manager.cancelled], ["run-1", "run-2"])
        self.assertEqual(manager.cancelled[0][1], "desk:phone")
        self.assertEqual(manager.cancelled[0][2], "стоп")
        self.assertFalse(selfdev.INTERRUPT_REQ.exists(), "просьба исполнена один раз")
        self.assertTrue(any("прервано" in line for line in self.journal),
                        "она узнаёт о прерывании из своего дневника")

    def test_scope_limits_to_one_run_and_conflicts_do_not_stop_the_rest(self):
        manager = _Manager([{"run_id": "run-1"}, {"run_id": "run-2"}, {"run_id": "run-3"}],
                           conflict={"run-1"})
        with mock.patch.object(runner.agent, "_runs", lambda: manager):
            result = runner._apply_desk_interrupt({"scope": "run-2", "by": "owner"})
        self.assertEqual(result["cancelled"], ["run-2"])
        self.assertEqual(result["skipped"], [])
        with mock.patch.object(runner.agent, "_runs", lambda: manager):
            result = runner._apply_desk_interrupt({"scope": "all"})
        self.assertEqual(result["cancelled"], ["run-2", "run-3"])
        self.assertEqual(result["skipped"], ["run-1: RuntimeError"])

    def test_broken_request_file_is_ignored_and_removed(self):
        selfdev.INTERRUPT_REQ.parent.mkdir(parents=True, exist_ok=True)
        selfdev.INTERRUPT_REQ.write_text("{not json", encoding="utf-8")
        manager = _Manager([{"run_id": "run-1"}])
        with mock.patch.object(runner.agent, "_runs", lambda: manager):
            asyncio.run(runner._desk_interrupt_once())
        self.assertEqual(manager.cancelled, [])
        self.assertFalse(selfdev.INTERRUPT_REQ.exists(), "битая просьба снята, а не читается вечно")


if __name__ == "__main__":
    unittest.main()
