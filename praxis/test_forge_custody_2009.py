"""Forge stop/spawn custody: стоп не уничтожает готовый результат, спавн не дублируется.

Что закреплено (срез 20.09.2026, аудит):

  stop(done)      → терминальный результат юнита сохранён байт-в-байт; kill не нужен
  kill отказался  → «stopped» НЕ пишется; прежний result не тронут; ход не умер
  taskkill rc!=0  → не «остановлен» (раньше rc молча считался успехом)
  спавн с занятым id → отказ, вторая копия юнита не создаётся
  рестарт между резервацией и спавном → тот же agent_id, дублей нет

Запуск:  python praxis_test.py test_forge_custody_2009 -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

import forge


class StopCustody(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.task_id = "custody-task"
        self.unit = forge._unit_dir(self.task_id, "agents", "agent-abc123")
        self.unit.mkdir(parents=True, exist_ok=True)

    def _request(self):
        (self.unit / "request.json").write_text(json.dumps(
            {"id": "agent-abc123", "task_id": self.task_id, "role": "worker",
             "brief": "тест", "created": "2026-09-20T00:00:00+00:00",
             "supervisor_pid": 4242, "supervisor_started_at": "2026-09-20T00:00:01+00:00",
             "status": "running"}), encoding="utf-8")

    def test_stop_of_a_done_unit_preserves_its_result(self):
        self._request()
        golden = {"status": "done", "finished": "2026-09-20T01:00:00+00:00",
                  "result": "готовый текст с трейсом", "tool_calls": 7,
                  "diff_tail": "diff --git a/x b/x\n+строка"}
        (self.unit / "result.json").write_text(json.dumps(golden, ensure_ascii=False),
                                               encoding="utf-8")
        with mock.patch.object(forge, "_kill_tree") as kill, \
                mock.patch.object(forge, "_task_root",
                                  return_value=({"goal": "g"}, self.base, "")), \
                mock.patch.object(forge, "_event") as ev, \
                mock.patch.object(forge, "emit_unit_event") as emit:
            out = forge.agent(self.task_id, "stop", agent_id="agent-abc123")
        kill.assert_not_called()
        emit.assert_not_called()
        self.assertIn("сохранён", out)
        kept = json.loads((self.unit / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(kept, golden, "готовый результат не должен перезаписываться стопом")

    def test_unproven_kill_writes_no_terminal_status(self):
        self._request()
        for stale in (self.unit / "result.json",):
            stale.unlink(missing_ok=True)
        with mock.patch.object(forge, "_kill_tree", return_value="не убила: метки рождения нет"), \
                mock.patch.object(forge, "_task_root",
                                  return_value=({"goal": "g"}, self.base, "")), \
                mock.patch.object(forge, "_event") as ev, \
                mock.patch.object(forge, "_unit_state",
                                  return_value={"status": "running", "id": "agent-abc123",
                                                "supervisor_pid": 4242,
                                                "supervisor_started_at": "x"}):
            out = forge.agent(self.task_id, "stop", agent_id="agent-abc123")
        self.assertIn("НЕ записан", out)
        self.assertFalse((self.unit / "result.json").exists(),
                         "недоказанная смерть не создаёт терминальный статус")

    def test_successful_kill_preserves_prior_partial_result_fields(self):
        self._request()
        partial = {"status": "running", "iterations": 3, "last_thought": "думаю"}
        (self.unit / "result.json").write_text(json.dumps(partial, ensure_ascii=False),
                                               encoding="utf-8")
        with mock.patch.object(forge, "_kill_tree", return_value="остановлен"), \
                mock.patch.object(forge, "_task_root",
                                  return_value=({"goal": "g"}, self.base, "")), \
                mock.patch.object(forge, "_event"), \
                mock.patch.object(forge, "emit_unit_event"):
            forge.agent(self.task_id, "stop", agent_id="agent-abc123")
        kept = json.loads((self.unit / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(kept["status"], "stopped")
        self.assertEqual(kept.get("iterations"), 3, "частичный прогресс не стёрт")
        self.assertEqual(kept.get("last_thought"), "думаю")

    def test_taskkill_returncode_is_checked(self):
        import subprocess as sp
        def fake_run(cmd, **kw):
            return sp.CompletedProcess(cmd, 1, stdout="", stderr="ОШИБКА: доступ запрещён")
        with mock.patch.object(forge, "_kill_identity", return_value=""), \
                mock.patch.object(forge, "_proc_started_at", return_value="x"), \
                mock.patch.object(forge.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(forge.os, "name", "nt"):
            msg = forge._kill_tree(4242, "x", expect="req")
        self.assertIn("не остановлен", msg)
        self.assertIn("rc=1", msg)


class SpawnReservation(unittest.TestCase):
    def test_spawn_with_existing_unit_id_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            task_id = "spawn-task"
            unit = forge._unit_dir(task_id, "agents", "agent-dedup")
            unit.mkdir(parents=True)
            (unit / "request.json").write_text(json.dumps(
                {"id": "agent-dedup", "task_id": task_id, "role": "worker",
                 "brief": "x", "status": "running"}), encoding="utf-8")
            with mock.patch.object(forge, "_task_root",
                                   return_value=({"goal": "g"}, base, "")):
                out = forge.agent(task_id, "spawn", brief="новый", agent_id="agent-dedup")
            self.assertIn("уже существует", out)
            self.assertIn("отклонён", out)

    def test_reservation_survives_restart_and_rebinds_same_id(self):
        """Краш между спавном и save: юнит существует (node_id в request.json),
        план его не знает. Тик перепривязывает СУЩЕСТВУЮЩИЙ юнит — второго спавна нет."""
        import tempfile
        import forge_swarm
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            plan = forge_swarm.create(directory, [
                {"id": "n1", "role": "worker", "brief": "первый", "deps": []},
                {"id": "n2", "role": "worker", "brief": "второй", "deps": ["n1"]},
            ])
            # Спавн успел, save — нет: юнит лежит с node_id, план считает узел pending.
            unit = directory / "agents" / "agent-ghost"
            unit.mkdir(parents=True)
            (unit / "request.json").write_text(json.dumps(
                {"id": "agent-ghost", "task_id": "t", "role": "worker",
                 "node_id": "n1", "brief": "первый", "status": "running"}),
                encoding="utf-8")
            forge_swarm.save(directory, plan)

            spawned = []

            def fake_agent(task_id, action, **kw):
                spawned.append(kw.get("agent_id"))
                return f"Порождён {kw.get('agent_id')} (worker, pid 1)."

            def fake_unit_dir(task_id, kind, unit_id):
                return directory / kind / unit_id

            with mock.patch.object(forge, "agent", side_effect=fake_agent), \
                    mock.patch.object(forge, "_task_root",
                                      return_value=({"goal": "g"}, Path(tmp), "")), \
                    mock.patch.object(forge, "_task_dir", return_value=directory), \
                    mock.patch.object(forge, "_unit_dir", side_effect=fake_unit_dir), \
                    mock.patch.object(forge, "_event"), \
                    mock.patch.object(forge, "forge_swarm", forge_swarm):
                out = forge.swarm("t", "tick")
            self.assertEqual(spawned, [], "второго спавна для существующего юнита нет")
            self.assertIn("n1 [starting] worker agent=agent-ghost", out)
            saved = forge_swarm.load(directory)
            self.assertEqual(saved["nodes"][0]["agent_id"], "agent-ghost")


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
