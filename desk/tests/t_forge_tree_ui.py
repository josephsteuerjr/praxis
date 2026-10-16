# -*- coding: utf-8 -*-
"""Forge's on-disk worker receipts -> Hélène's /api/forge reader.

Run: python desk/tests/t_forge_tree_ui.py (from repository root).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from deskd import readers  # noqa: E402


def put(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    return data


class WorkerTree(unittest.TestCase):
    def test_nested_result_identity_dispositions_and_unknown_custody(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            task = base / "memory" / "work" / "tasks" / "task-one"
            put(task / "task.json", {"goal": "Sensitive project", "status": "active"})
            units = task / "agents"
            parent = units / "agent-11111111"
            child = units / "agent-22222222"
            orphan = units / "agent-33333333"
            put(parent / "request.json", {"id": parent.name, "role": "worker", "status": "running", "created": "2026-01-01"})
            put(child / "request.json", {"id": child.name, "role": "worker", "parent_unit_id": parent.name, "status": "running", "created": "2026-01-02"})
            put(orphan / "request.json", {"id": orphan.name, "role": "scout", "parent_unit_id": "agent-absent", "status": "running"})
            secret = "PRIVATE_WORKER_OUTPUT_NEVER_RENDER"
            result_bytes = put(child / "result.json", {"status": "done", "result": secret, "finished": "2026-01-03"})
            sha = hashlib.sha256(result_bytes).hexdigest()
            put(child / f"disposition-{parent.name}-{sha}.json", {"parent_unit_id": parent.name, "child_unit_id": child.name, "result_sha256": sha, "disposition": "integrated"})
            put(child / "disposition-old.json", {"parent_unit_id": parent.name, "child_unit_id": child.name, "result_sha256": "0" * 64, "disposition": "irrelevant"})
            with mock.patch.object(readers, "tree", return_value=base):
                tasks = readers.forge_tasks()
            self.assertEqual(len(tasks), 1)
            agents = tasks[0]["agents"]
            self.assertLess([a["id"] for a in agents].index(parent.name),
                            [a["id"] for a in agents].index(child.name))
            named = {a["id"]: a for a in agents}
            self.assertEqual(named[child.name]["parent_unit_id"], parent.name)
            self.assertEqual(named[child.name]["depth"], 1)
            self.assertEqual(named[child.name]["result_sha256"], sha)
            self.assertEqual(named[child.name]["dispositions"][0]["disposition"], "integrated")
            self.assertEqual(named[child.name]["stale_dispositions"], 1)
            self.assertEqual(named[parent.name]["status"], "unknown")
            self.assertEqual(named[parent.name]["request_status"], "running")
            self.assertEqual(named[parent.name]["custody"], "unknown")
            self.assertTrue(named[orphan.name]["ancestry_unknown"])
            self.assertNotIn(secret, json.dumps(tasks))

    def test_unresolved_stop_never_looks_terminal_even_if_result_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            unit = base / "memory" / "work" / "tasks" / "one" / "agents" / "agent-11111111"
            put(unit / "request.json", {"status": "running"})
            put(unit / "result.json", {"status": "stopped", "result": "private"})
            put(unit / "stop.json", {"state": "unresolved", "note": "process identity cannot be proven"})
            with mock.patch.object(readers, "tree", return_value=base):
                row = readers.forge_tasks()[0]["agents"][0]
            self.assertEqual((row["status"], row["custody"]), ("unknown", "unresolved_stop"))
            self.assertNotIn("private", json.dumps(row))
            self.assertNotIn("process identity", json.dumps(row))


if __name__ == "__main__":
    unittest.main()
