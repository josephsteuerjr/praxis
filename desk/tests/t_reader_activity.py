"""Живая карточка видна до возврата envelope, без подмены старым прогоном."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localharness"))
from deskd import readers
import runner


class ReaderActivity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = dt.datetime(2026, 10, 1, 0, 0, 1, tzinfo=dt.timezone.utc).timestamp()
        self.receipt = {"at": self.now, "busy": True, "run": "",
                        "since": self.now - 2, "chat_id": "window-a"}

    def run_file(self, offset=0, *, chat="window-a", status="running", kind="chat_turn"):
        at = dt.datetime.fromtimestamp(self.now + offset, dt.timezone.utc)
        name = at.strftime("run-%Y%m%dT%H%M%S%fZ-") + "abcdef01"
        path = self.root / "memory" / "runs" / at.strftime("%Y-%m") / name
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(json.dumps({
            "created_at": at.isoformat(), "status": status,
            "context": {"kind": kind, "origin_chat_id": chat}}), encoding="utf-8")
        return name

    def read(self):
        path = self.root / "memory/.control/desk_inbox/.reader.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.receipt), encoding="utf-8")
        return readers.reader_status(self.root, self.now)

    def test_live_run_before_envelope_returns_across_month_boundary(self):
        name = self.run_file(-1.5)
        self.assertEqual(self.read()["run"], name)

    def test_other_room_old_terminal_and_future_are_not_current(self):
        self.run_file(-3)
        self.run_file(-1, chat="window-other")
        self.run_file(-0.5, status="done")
        self.run_file(-0.25, kind="wake")
        self.run_file(1)
        self.assertEqual(self.read()["run"], "")

    def test_ambiguous_runs_are_not_guessed(self):
        self.run_file(-1)
        self.run_file(0)
        self.assertEqual(self.read()["run"], "")

    def test_dead_or_idle_receipt_does_not_scan_runs(self):
        for change in ({"at": self.now - 60}, {"busy": False}):
            with self.subTest(change=change):
                old = dict(self.receipt)
                self.receipt.update(change)
                with patch.object(readers, "_receipt_run", side_effect=AssertionError("idle scan")):
                    self.assertFalse(self.read()["busy"])
                self.receipt = old

    def test_explicit_id_is_used_without_scanning(self):
        self.receipt["run"] = "explicit-id"
        with patch.object(readers, "_receipt_run", side_effect=AssertionError("unneeded scan")):
            self.assertEqual(self.read()["run"], "explicit-id")

    def test_harness_receipt_identifies_room_and_clears_it(self):
        with patch.object(runner, "_tree", self.root), patch.object(runner, "_busy", {}):
            runner._set_busy(True, chat_id="window-a")
            path = self.root / "memory/.control/desk_inbox/.reader.json"
            receipt = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(receipt["busy"])
            self.assertEqual(receipt["chat_id"], "window-a")
            runner._set_busy(False)
            receipt = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(receipt["busy"])
            self.assertEqual(receipt["chat_id"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
