"""Журнал действий: начало, результат, отказ и чтение обрезанного хвоста."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskd import readers


class Activity(unittest.TestCase):
    def read(self, rows, status="running"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(json.dumps({"status": status}), encoding="utf-8")
            (root / "events.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            with patch.object(readers, "run_dir", return_value=root):
                return readers.run_detail("run-test")

    def test_started_model_is_pending_not_completed(self):
        d = self.read([{"kind": "model_started", "call_id": "m"}])
        self.assertEqual(d["iterations"][0]["status"], "running")
        self.assertNotIn("ms", d["iterations"][0])

    def test_result_updates_the_same_action(self):
        rows = [{"kind": "model_started", "call_id": "m"},
                {"kind": "model_completed", "call_id": "m", "duration_ms": 50},
                {"kind": "tool_started", "call_id": "a", "tool": "shell", "args": {"cmd": "echo OK"}}]
        before = self.read(rows)["iterations"][0]
        self.assertEqual(before["status"], "completed")
        self.assertEqual(before["tools"][0]["status"], "running")
        rows.append({"kind": "tool_result", "call_id": "a", "result": {"inline": {"head": "OK"}}})
        after = self.read(rows)["iterations"][0]["tools"]
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["call_id"], "a")
        self.assertEqual(after[0]["status"], "received")
        self.assertEqual(after[0]["result"]["head"], "OK")

    def test_model_failure_is_not_an_endless_wait(self):
        for prefix in ([], [{"kind": "model_started", "call_id": "m"}]):
            with self.subTest(prefix=bool(prefix)):
                d = self.read(prefix + [{"kind": "model_failed", "call_id": "m", "error": "TimeoutError", "duration_ms": 5}])
                self.assertEqual(len(d["iterations"]), 1)
                self.assertEqual(d["iterations"][0]["status"], "failed")

    def test_tool_failure_keeps_arguments_and_error(self):
        d = self.read([{"kind": "tool_started", "call_id": "a", "tool": "shell", "args": {"cmd": "echo OK"}},
                       {"kind": "tool_failed", "call_id": "a", "error": "Interrupted"}])
        tool = d["iterations"][0]["tools"][0]
        self.assertEqual(tool["status"], "failed")
        self.assertEqual(tool["error"], "Interrupted")
        self.assertEqual(tool["args"], {"cmd": "echo OK"})

    def test_result_without_start_is_visible(self):
        d = self.read([{"kind": "tool_result", "call_id": "a", "name": "shell", "result": {"inline": {"head": "OK"}}}])
        tool = d["iterations"][0]["tools"][0]
        self.assertEqual(tool["status"], "received")
        self.assertEqual(tool["call_id"], "a")

    def test_failure_without_start_is_visible(self):
        d = self.read([{"kind": "tool_failed", "call_id": "a", "tool": "shell", "error": "Interrupted"}])
        self.assertEqual(d["iterations"][0]["tools"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
