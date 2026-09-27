"""`computer run execution=system` — поручением брокеру службы (1.2.3).

27.09 агент Егора позвал руку `computer` с execution=system и получил «open local router
\\\\.\\pipe\\PraxisBodySystem»: у Hélène этого маршрута тела нет, права системы — у брокера
службы. Стенд держит: вызов уходит в `broker_request` op=exec полным путём PowerShell с
командой отдельным аргументом, без брокера — отказ словами, а не труба Праксис.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESK))

from localharness import body  # noqa: E402


class SystemRouteTest(unittest.TestCase):
    def _agent(self, hand):
        mod = types.SimpleNamespace()
        mod.TOOL_IMPL = {"broker_request": hand} if hand else {}
        return mod

    def test_system_run_becomes_broker_exec(self):
        seen = {}

        def hand(**kw):
            seen.update(kw)
            return "квитанция: код 0"

        out = body._system_via_broker(self._agent(hand), {"action": "run", "execution": "system",
                                                          "command": "whoami /user", "timeout_ms": 45000})
        self.assertEqual(seen["op"], "exec")
        self.assertEqual(seen["action"], "ask")
        self.assertTrue(seen["cmd"].lower().endswith("powershell.exe"))
        self.assertTrue(Path(seen["cmd"]).is_absolute())
        self.assertEqual(seen["args"][-2:], ["-Command", "whoami /user"])
        self.assertEqual(seen["timeout_sec"], 45)
        self.assertTrue(seen["why"])
        self.assertIn("квитанция: код 0", out)
        self.assertNotIn("PraxisBodySystem", out)

    def test_without_broker_says_so_in_words(self):
        out = body._system_via_broker(self._agent(None), {"command": "whoami"})
        self.assertIn("служба не установлена", out)
        self.assertIn("execution=interactive", out)

    def test_empty_command_is_refused_before_asking(self):
        called = []
        out = body._system_via_broker(self._agent(lambda **kw: called.append(kw)), {"command": "  "})
        self.assertEqual(called, [])
        self.assertIn("пустая команда", out)


if __name__ == "__main__":
    unittest.main()
