"""Незавершённый результат освобождает слот; ни одного настоящего воркера."""
import _sandbox  # noqa: F401
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import forge
import forge_swarm
from core import subagents


class SwarmTerminal(unittest.TestCase):
    def test_real_tick_retains_stalled_and_blocks_dependent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = root / "unit"
            unit.mkdir()
            result = {"status": "stalled", "complete": False, "result": "частичный результат", "finished": "now"}
            (unit / "result.json").write_text(json.dumps(result), encoding="utf-8")
            plan = forge_swarm.create(root, [
                {"id": "first", "brief": "первый"},
                {"id": "dependent", "brief": "зависимый", "deps": ["first"]},
                {"id": "independent", "brief": "независимый"},
            ], max_parallel=1)
            plan["nodes"][0].update(status="running", agent_id="agent-aaa")
            forge_swarm.save(root, plan)
            with patch.object(forge, "_task_root", return_value=({}, root, "")), \
                    patch.object(forge, "_task_dir", return_value=root), \
                    patch.object(forge, "_unit_dir", return_value=unit), \
                    patch.object(forge, "_event"), \
                    patch.object(forge, "agent", return_value="agent-bbb") as spawn:
                out = forge.swarm("task", action="tick")
                self.assertIn("stalled", out)
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual(spawn.call_args.kwargs["node_id"], "independent")
                loaded = forge_swarm.load(root)
                self.assertEqual([n["status"] for n in loaded["nodes"]], ["stalled", "blocked", "running"])
                # Поздний повтор наблюдения не воскрешает завершённый узел.
                (unit / "result.json").write_text(json.dumps({"status": "done"}), encoding="utf-8")
                forge.swarm("task", action="tick")
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual([n["status"] for n in forge_swarm.load(root)["nodes"]], ["stalled", "blocked", "done"])

    def test_event_retains_abi_and_names_incompleteness(self):
        payload = subagents.normalize("task", "agent", {"status": "stalled", "complete": False, "result": "ещё не всё"})
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["forge_status"], "stalled")
        line = subagents.invitation([payload])
        self.assertIn("без полного результата", line)
        self.assertNotIn("упал", line)
        self.assertIn("ещё не всё", line)


if __name__ == "__main__":
    unittest.main()
