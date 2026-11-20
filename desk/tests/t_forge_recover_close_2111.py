# -*- coding: utf-8 -*-
"""Owner recovery command carries the finished queue: recover closes the task.

No Forge processes, models or network: the Linux history path is driven with a
fixture boundary, the same way the packaged core tests exercise it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desk"))
sys.path.insert(0, str(ROOT / "helene" / "core"))
from deskd import forge_control as control
import forge
import forge_recovery
import frame_language


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


class RecoverClosesQueue(unittest.TestCase):
    def setUp(self):
        self._sequence = iter(range(1000))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        for name, value in [("BASE", self.base), ("STATE_DIR", self.base / "memory/.forge"),
                            ("TASKS_DIR", self.base / "memory/.forge/tasks")]:
            patch = mock.patch.object(forge, name, value)
            patch.start(); self.addCleanup(patch.stop)
        boot = int(dt.datetime.now(dt.timezone.utc).timestamp()) - 600
        self.boundary = {"boot_id": "boot-before", "host_id": "fixture-host", "boot_time": boot}
        patch = mock.patch.object(forge_recovery, "kernel_boundary", side_effect=lambda: dict(self.boundary))
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(sys, "platform", "linux")
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(forge, "_pid_alive", return_value=False)
        patch.start(); self.addCleanup(patch.stop)
        put(self.base / "memory/.forge/tasks/code-close/task.json",
            {"id": "code-close", "status": "active", "root": str(self.base / "workspace"), "scope": "self"})
        born = dt.datetime.fromtimestamp(boot - 3600, dt.timezone.utc).isoformat()
        unit = self.base / "memory/.forge/tasks/code-close/agents/agent-old"
        put(unit / "request.json", {"id": "agent-old", "task_id": "code-close",
            "root": str(self.base / "workspace"), "scope": "self", "role": "worker", "status": "running",
            "supervisor_pid": 111, "supervisor_started_at": "old-birth", "created": born})
        put(unit / "stop.json", {"state": "unresolved", "note": "historical stop"})
        (self.base / "memory/.forge/tasks/code-close/events.jsonl").write_text(
            json.dumps({"at": born, "kind": "agent_spawned", "agent_id": "agent-old"}) + "\n", encoding="utf-8")

    def command(self, action, **extra):
        root = self.base / "memory/.control/forge"
        command_id = hashlib.sha256(f"{action}-{next(self._sequence)}".encode()).hexdigest()[:32]
        put(root / "commands" / (command_id + ".json"),
            {"command_id": command_id, "task_id": "code-close", "action": action, **extra})
        return command_id

    def core(self, task_id, action, **kwargs):
        return forge.control(task_id, action, **kwargs)

    def test_owner_recover_command_closes_the_finished_queue(self):
        self.command("prepare_recovery", host_binding="verified-local-execution-host",
                     reason="owner verified the original execution host")
        prepared = control.consume_pending(self.base, self.core)
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["state"], "observed", prepared[0])
        self.assertEqual(prepared[0]["result"]["status"], "prepared")

        self.boundary.update(boot_id="boot-after", boot_time=self.boundary["boot_time"] + 600)
        self.command("recover")
        recovered = control.consume_pending(self.base, self.core)
        self.assertEqual(len(recovered), 1)
        receipt = recovered[0]["result"]
        self.assertEqual(receipt["status"], "settled", receipt)
        self.assertTrue(receipt["task_closed"])
        self.assertEqual(receipt["task_status"], "stopped")
        self.assertEqual(receipt["unresolved_units"], [])
        self.assertTrue(receipt["reason"].endswith(frame_language.text("forge.recovery.task_closed")))
        task = json.loads((self.base / "memory/.forge/tasks/code-close/task.json").read_text(encoding="utf-8"))
        self.assertEqual(task["status"], "stopped")

        # The consumed intent never replays, and a closed task stays closed:
        # task-level resume is still only an explicit-Pause unblocker.
        self.assertEqual(control.consume_pending(self.base, self.core), [])
        self.command("resume")
        resumed = control.consume_pending(self.base, self.core)
        self.assertEqual(resumed[0]["state"], "rejected", resumed[0])
        self.assertEqual(resumed[0]["result"]["status"], "refused")
        task = json.loads((self.base / "memory/.forge/tasks/code-close/task.json").read_text(encoding="utf-8"))
        self.assertEqual(task["status"], "stopped")

    def test_reverse_case_one_recover_closes_an_old_leftover(self):
        self.command("prepare_recovery", host_binding="verified-local-execution-host",
                     reason="owner verified the original execution host")
        control.consume_pending(self.base, self.core)
        self.boundary.update(boot_id="boot-after", boot_time=self.boundary["boot_time"] + 600)
        # Scoped recover left the task active, as an earlier version would.
        self.command("recover", agent_id="agent-old")
        scoped = control.consume_pending(self.base, self.core)
        self.assertEqual(scoped[0]["result"]["status"], "settled", scoped[0])
        task = json.loads((self.base / "memory/.forge/tasks/code-close/task.json").read_text(encoding="utf-8"))
        self.assertEqual(task["status"], "stopped")  # nothing else was outstanding

        self.command("recover")
        again = control.consume_pending(self.base, self.core)
        self.assertEqual(again[0]["state"], "observed", again[0])
        receipt = again[0]["result"]
        self.assertEqual(receipt["status"], "settled")
        self.assertEqual(receipt["unresolved_units"], [])
        self.assertFalse(receipt["task_closed"])  # already closed; nothing fabricated


if __name__ == "__main__":
    unittest.main()
