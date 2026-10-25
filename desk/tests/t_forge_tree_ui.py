# -*- coding: utf-8 -*-
"""Forge's on-disk worker receipts -> Hélène's /api/forge reader.

Run: python desk/tests/t_forge_tree_ui.py (from repository root).
"""
from __future__ import annotations

import asyncio
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
import deskapp  # noqa: E402


def put(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    return data


class WorkerTree(unittest.TestCase):
    def test_nested_result_identity_dispositions_and_unknown_custody(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            task = base / "memory" / ".forge" / "tasks" / "task-one"
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
                device = readers.forge_device_tasks()
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
            unlinked = next(row for row in device[0]["agents"] if row["ancestry_unknown"])
            self.assertEqual(unlinked["parent_unit_id"], "")
            self.assertEqual(unlinked["status"], "unknown")

    def test_unresolved_stop_never_looks_terminal_even_if_result_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            unit = base / "memory" / ".forge" / "tasks" / "one" / "agents" / "agent-11111111"
            put(unit / "request.json", {"status": "running"})
            put(unit / "result.json", {"status": "stopped", "result": "private"})
            put(unit / "stop.json", {"state": "unresolved", "note": "process identity cannot be proven"})
            with mock.patch.object(readers, "tree", return_value=base):
                row = readers.forge_tasks()[0]["agents"][0]
            self.assertEqual((row["status"], row["custody"]), ("unknown", "unresolved_stop"))
            self.assertNotIn("private", json.dumps(row))
            self.assertNotIn("process identity", json.dumps(row))

    def test_finished_parent_nested_worker_and_device_projection_exclude_private_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            task = base / "memory" / ".forge" / "tasks" / "code-11111111"
            secret = "SENSITIVE_TOKEN_AND_C_USERS_WORKSPACE"
            put(task / "task.json", {"status": "done", "goal": secret,
                                      "source_root": secret})
            units = task / "agents"
            parent = units / "agent-22222222"
            child = units / "agent-33333333"
            put(parent / "request.json", {"role": secret, "status": "running",
                                           "cwd": secret})
            put(parent / "result.json", {"status": "done", "result": secret})
            put(child / "request.json", {"role": secret, "status": "running",
                                          "parent_unit_id": parent.name})
            put(child / "result.json", {"status": secret, "result": secret})
            with mock.patch.object(readers, "tree", return_value=base):
                owner = asyncio.run(deskapp._r_forge(deskapp.Call({}, {}, None, False, "owner")))
                device = asyncio.run(deskapp._r_forge(deskapp.Call({}, {}, None, False, "device")))
            self.assertEqual(owner[0]["goal"], secret)  # only the owner sees authored intent
            self.assertEqual(owner[0]["agents"][1]["depth"], 1)
            self.assertEqual(device, [{"id": "task-1", "status": "done", "agents": [
                {"id": "unit-1", "status": "done", "custody": "result",
                 "parent_unit_id": "", "depth": 0, "ancestry_unknown": False},
                {"id": "unit-2", "status": "unknown", "custody": "result",
                 "parent_unit_id": "unit-1", "depth": 1, "ancestry_unknown": False},
            ]}])
            self.assertNotIn(secret, json.dumps(device))
            self.assertFalse(deskapp._open_path("/api/forge"))
            self.assertFalse(deskapp._scope_ok("", "/api/forge"))
            self.assertFalse(deskapp._scope_ok("device", "/api/md"))

    def test_device_projection_keeps_unresolved_stop_and_anonymizes_odd_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            unit = base / "memory" / ".forge" / "tasks" / "private-task-name" / "agents" / "private-unit-name"
            put(unit / "request.json", {"status": "running", "role": "secret/role"})
            put(unit / "stop.json", {"state": "unresolved", "note": "secret/path"})
            with mock.patch.object(readers, "tree", return_value=base):
                device = readers.forge_device_tasks()
            self.assertEqual(device[0]["status"], "unknown")
            self.assertEqual(device[0]["agents"][0]["status"], "unknown")
            self.assertEqual(device[0]["agents"][0]["custody"], "unresolved_stop")
            for needle in ("private-", "secret", "running"):
                self.assertNotIn(needle, json.dumps(device))


if __name__ == "__main__":
    unittest.main()
