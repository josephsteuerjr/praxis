"""Сверка принятых записей исходящего ящика не трогает закрытые прогоны (13.09).

Ретенция сняла results/ у августовских прогонов; ~30 принятых записей их доставок
сверялись заново каждый тик и каждый старт, и каждая сверка падала трейсбеком
(1 609 за 25 минут). Прогон терминален → расписку проецировать некуда → запись
считается сведённой без чтения улик.

Запуск: python praxis_test.py test_outbox_settled_1309 -v
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

import agent  # noqa: E402
import mtproto_runner as mr  # noqa: E402


class _Runs:
    def __init__(self, root: Path):
        self.root = root

    def path(self, run_id: str) -> Path:
        return self.root / run_id


class SettledRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for run_id, status in (("run-done", "done"), ("run-live", "running")):
            (self.root / run_id).mkdir()
            (self.root / run_id / "manifest.json").write_text(json.dumps({"status": status}), encoding="utf-8")
        self.calls: list[str] = []

        def boom(entry):
            self.calls.append(str(entry.get("run_id")))
            raise RuntimeError("evidence unavailable: results")
        self._p1 = mock.patch.object(agent, "_runs", lambda: _Runs(self.root))
        self._p2 = mock.patch.object(agent, "run_direct_outbox_accepted", boom, create=True)
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop); self.addCleanup(self._p2.stop)
        mr._DIRECT_OUTBOX_RECONCILED.discard("k-done"); mr._DIRECT_OUTBOX_RECONCILED.discard("k-live")
        self.addCleanup(mr._DIRECT_OUTBOX_RECONCILED.discard, "k-done")
        self.addCleanup(mr._DIRECT_OUTBOX_RECONCILED.discard, "k-live")

    def test_terminal_run_entry_is_settled_without_reading_evidence(self):
        entry = {"key": "k-done", "purpose": "tool:reply", "run_id": "run-done", "kind": "text"}
        self.assertTrue(asyncio.run(mr._reconcile_direct_outbox_entry(entry)))
        self.assertEqual(self.calls, [], "улики закрытого прогона не читаются")
        self.assertIn("k-done", mr._DIRECT_OUTBOX_RECONCILED)

    def test_live_run_entry_still_goes_through_the_reconciler(self):
        entry = {"key": "k-live", "purpose": "tool:reply", "run_id": "run-live", "kind": "text"}
        self.assertFalse(asyncio.run(mr._reconcile_direct_outbox_entry(entry)))
        self.assertEqual(self.calls, ["run-live"])
        self.assertNotIn("k-live", mr._DIRECT_OUTBOX_RECONCILED)

    def test_unknown_run_is_not_settled(self):
        self.assertFalse(mr._run_is_settled("run-missing"))
        self.assertFalse(mr._run_is_settled(""))
        self.assertTrue(mr._run_is_settled("run-done"))


class _Spool:
    """Спул медиа для стенда: только то, что зовёт `_media_cleanup_once`."""

    def __init__(self, root: Path, delivered: list[dict]):
        self.root = root
        self._delivered = delivered

    def outbox_results(self, state: str | None = None) -> tuple[dict, ...]:
        return tuple(self._delivered) if state == "delivered" else ()

    def pending(self) -> list:
        return []

    def cleanup(self) -> int:
        return 0


class SettledMediaTombstones(unittest.TestCase):
    """Тумбстон спула медиа закрытого прогона: финализацию не зовём, staged-копию снимаем.

    13.09: `_media_cleanup_once` (таймер 60 с) для последних 200 «delivered» записей звал
    `run_delivery_finalize_recovered` — у 21 августовского прогона results/ сняла
    ретенция, каждая попытка падала «ResultRef integrity» (275 в час), а копия в спуле
    жила вечно, потому что unlink стоял после финализации.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.runs = root / "runs"
        self.runs.mkdir()
        for run_id, status in (("run-done", "done"), ("run-live", "running")):
            (self.runs / run_id).mkdir()
            (self.runs / run_id / "manifest.json").write_text(json.dumps({"status": status}), encoding="utf-8")
        self.spool_root = root / "spool"
        self.spool_root.mkdir()
        (self.spool_root / "done.jpg").write_bytes(b"x")
        (self.spool_root / "live.jpg").write_bytes(b"y")
        self.media_calls: list[tuple[str, str]] = []
        self.finalize_calls: list[str] = []

        def media_result(run_id, queue_id, **kw):
            self.media_calls.append((run_id, queue_id))

        def finalize(run_id, **kw):
            self.finalize_calls.append(run_id)
            return True

        records = [
            {"queue_id": "q-done", "item": {"run_id": "run-done", "path": "done.jpg"}, "result": {"message_id": 1}},
            {"queue_id": "q-live", "item": {"run_id": "run-live", "path": "live.jpg"}, "result": {"message_id": 2}},
        ]
        for patch in (
            mock.patch.object(agent, "_runs", lambda: _Runs(self.runs)),
            mock.patch.object(agent, "run_delivery_media_result", media_result),
            mock.patch.object(agent, "run_delivery_finalize_recovered", finalize),
            mock.patch.object(mr, "_media_spool", lambda: _Spool(self.spool_root, records)),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        mr._MEDIA_TOMBSTONES_SETTLED.discard("q-done")
        self.addCleanup(mr._MEDIA_TOMBSTONES_SETTLED.discard, "q-done")

    def test_settled_run_tombstone_skips_finalize_and_drops_staged_copy(self):
        asyncio.run(mr._media_cleanup_once())
        self.assertEqual(self.finalize_calls, ["run-live"], "финализируется только живой прогон")
        self.assertEqual(self.media_calls, [("run-live", "q-live")])
        self.assertFalse((self.spool_root / "done.jpg").exists(), "staged-копия закрытого прогона снята")
        self.assertFalse((self.spool_root / "live.jpg").exists(), "живой прогон прошёл обычным путём")
        self.assertIn("q-done", mr._MEDIA_TOMBSTONES_SETTLED)

    def test_second_pass_does_not_reread_manifest_of_settled_run(self):
        asyncio.run(mr._media_cleanup_once())

        def settled_probe(run_id: str) -> bool:
            # живой прогон проверяется на каждом проходе — это нормально; закрытый — нет
            if run_id == "run-done":
                raise AssertionError("манифест закрытого прогона перечитан")
            return False

        with mock.patch.object(mr, "_run_is_settled", settled_probe):
            asyncio.run(mr._media_cleanup_once())
        self.assertEqual(self.finalize_calls, ["run-live", "run-live"])


if __name__ == "__main__":
    unittest.main()
