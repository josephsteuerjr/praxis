# -*- coding: utf-8 -*-
"""Pure Python child-exit policy through its serverboot consumer and Docker closure.

Run from desk/: python tests/t_server_supervisor_policy_1610.py
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "server"))
import serverboot  # noqa: E402
from supervisor_policy import decide_exit  # noqa: E402


class ExitPolicy(unittest.TestCase):
    def child(self, code: int, *, key: str = "runner") -> serverboot.Child:
        child = serverboot.Child(key, "раннер" if key == "runner" else "реле", [], {},
                                 DESK, DESK / "unused.log")
        child.proc = types.SimpleNamespace(poll=lambda: code)
        return child

    def test_real_settle_preserves_runner_and_relay_exit_contract(self):
        for code, expected in ((42, "попросил перезапуска"),
                               (2, "нет папки с кодом"),
                               (3, "конфиг или раскладка")):
            child = self.child(code)
            child.falls = [1.0, 2.0]
            note = serverboot.settle(child, 100.0)
            self.assertIn(expected, note)
            self.assertIsNone(child.proc)
            if code == 42:
                self.assertEqual((child.falls, child.retry_at), ([], 0.0))
            else:
                self.assertEqual(child.falls, [1.0, 2.0])
                self.assertEqual(child.retry_at, 0.0)
                self.assertTrue(child.halted)
        relay = self.child(42, key="relay")
        serverboot.settle(relay, 100.0)
        self.assertEqual(relay.falls, [100.0])
        self.assertEqual(relay.retry_at, 102.0)

    def test_window_delay_cap_and_no_input_mutation(self):
        history = (0.0, 1.0, 400.0, 401.0)
        decision = decide_exit(key="runner", name="раннер", code=1, now=1000.0,
                               falls=history, retry_at=55.0, halted="prior",
                               restart_code=42)
        self.assertEqual(history, (0.0, 1.0, 400.0, 401.0))
        self.assertEqual(decision.falls, (401.0, 1000.0))
        self.assertEqual((decision.retry_at, decision.halted), (1004.0, "prior"))
        many = decide_exit(key="runner", name="раннер", code=None, now=10.0,
                           falls=(10.0,) * 12, retry_at=0.0, halted="", restart_code=42)
        self.assertEqual(many.retry_at, 70.0)

    def test_docker_copy_is_a_complete_import_closure(self):
        line = next(line for line in (DESK / "server" / "Dockerfile").read_text(encoding="utf-8").splitlines()
                    if line.startswith("COPY server/serverboot.py "))
        parts = line.split()
        self.assertEqual(parts[-1], "./server/")
        self.assertIn("server/supervisor_policy.py", parts)
        self.assertIn("server/emergency_stop.py", parts)
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "server"
            copied.mkdir()
            for item in parts[1:-1]:
                self.assertTrue(item.startswith("server/"))
                shutil.copy2(DESK / item, copied / Path(item).name)
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            result = subprocess.run([sys.executable, str(copied / "serverboot.py"), "--help"],
                                    env=env, cwd=tmp, capture_output=True, text=True,
                                    encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--config", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
