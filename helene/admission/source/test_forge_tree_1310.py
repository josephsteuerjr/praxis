"""Pure Forge worker-tree transitions. Never starts a native detached process."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import forge
import forge_swarm
import forge_worker


class WorkerTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old_tasks = forge.TASKS_DIR
        forge.TASKS_DIR = self.root / "tasks"
        self.addCleanup(setattr, forge, "TASKS_DIR", self.old_tasks)
        self.task_id = "code-tree1234"
        self.dir = forge.TASKS_DIR / self.task_id
        self.dir.mkdir(parents=True)
        self.task = {"id": self.task_id, "goal": "tree", "root": str(self.root),
                     "status": "active", "max_active_workers": 2, "run_context": {}}
        (self.dir / "task.json").write_text(json.dumps(self.task), encoding="utf-8")
        self.patches = [mock.patch.object(forge, "_event"),
                        mock.patch.object(forge, "emit_unit_event"),
                        mock.patch.object(forge, "_proc_started_at", return_value="born")]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def unit(self, ident: str, parent: str = "", result: dict | None = None,
             role: str = "worker", pid: int = 50) -> Path:
        path = self.dir / "agents" / ident
        path.mkdir(parents=True, exist_ok=True)
        (path / "request.json").write_text(json.dumps({
            "id": ident, "task_id": self.task_id, "parent_unit_id": parent,
            "role": role, "brief": "b", "created": "now", "status": "running",
            "supervisor_pid": pid, "supervisor_started_at": "born", "run_context": {},
        }), encoding="utf-8")
        if result is not None:
            (path / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return path

    def call(self, action, **kw):
        return forge.agent(self.task_id, action, **kw)

    def test_process_stop_requires_child_and_supervisor_proof_before_terminal_result(self):
        unit = self.dir / "processes" / "proc-aaaaaaaa"
        unit.mkdir(parents=True)
        (unit / "request.json").write_text(json.dumps({
            "id": unit.name, "task_id": self.task_id, "command": "sleep 30",
            "supervisor_pid": 71, "supervisor_started_at": "born", "status": "running",
        }), encoding="utf-8")
        (unit / "state.json").write_text(json.dumps({
            "child_pid": 72, "child_started_at": "born", "status": "running",
        }), encoding="utf-8")
        with mock.patch.object(forge, "_pid_alive", return_value=False), \
             mock.patch.object(forge, "_kill_tree", side_effect=[
                 "не остановлен: child group unknown", "остановлен",
                 "остановлен"]), \
             mock.patch.object(forge, "_event"):
            first = forge.process(self.task_id, "stop", process_id=unit.name)
            self.assertIn("терминальный статус НЕ записан", first)
            self.assertFalse((unit / "result.json").exists())
            self.assertEqual(forge._unit_state(unit)["status"], "unknown")
            self.assertIn("blocked", forge._finish_survey(self.task_id))
            second = forge.process(self.task_id, "stop", process_id=unit.name)
        self.assertIn("остановлен", second)
        self.assertEqual(forge._unit_state(unit)["status"], "stopped")
        self.assertEqual(json.loads((unit / "stop.json").read_text())["state"], "settled")

    def test_process_stop_preserves_natural_result_and_missing_child_stays_unknown(self):
        unit = self.dir / "processes" / "proc-bbbbbbbb"
        unit.mkdir(parents=True)
        request = {"id": unit.name, "task_id": self.task_id, "command": "sleep 30",
                   "supervisor_pid": 71, "supervisor_started_at": "born"}
        (unit / "request.json").write_text(json.dumps(request), encoding="utf-8")
        with mock.patch.object(forge, "_pid_alive", return_value=False), \
             mock.patch.object(forge, "_event"):
            missing = forge.process(self.task_id, "stop", process_id=unit.name)
        self.assertIn("терминальный статус НЕ записан", missing)
        self.assertEqual(forge._unit_state(unit)["status"], "unknown")
        (unit / "state.json").write_text(json.dumps({"child_pid": 72,
                                                       "child_started_at": "born"}), encoding="utf-8")
        prior = {"status": "done", "code": 0, "log": "saved"}
        (unit / "result.json").write_text(json.dumps(prior), encoding="utf-8")
        with mock.patch.object(forge, "_pid_alive", return_value=False), \
             mock.patch.object(forge, "_kill_tree", return_value="уже завершён"), \
             mock.patch.object(forge, "_event"):
            forge.process(self.task_id, "stop", process_id=unit.name)
        self.assertEqual(json.loads((unit / "result.json").read_text()), prior)

    def test_late_stop_on_terminal_process_never_mints_unknown_or_signals_reused_pid(self):
        for child_state in ({}, {"child_pid": 72, "child_started_at": "old-birth"}):
            with self.subTest(child_state=child_state):
                unit = self.dir / "processes" / ("proc-" + ("cccccccc" if child_state else "dddddddd"))
                unit.mkdir(parents=True)
                (unit / "request.json").write_text(json.dumps({
                    "id": unit.name, "task_id": self.task_id, "command": "echo done",
                    "supervisor_pid": 71, "supervisor_started_at": "old-birth",
                }), encoding="utf-8")
                (unit / "state.json").write_text(json.dumps(child_state), encoding="utf-8")
                result = {"status": "done", "code": 0, "log": "saved"}
                (unit / "result.json").write_text(json.dumps(result), encoding="utf-8")
                with mock.patch.object(forge, "_kill_tree") as kill, \
                     mock.patch.object(forge, "_event"):
                    answer = forge.process(self.task_id, "stop", process_id=unit.name)
                self.assertIn("Stop не послан", answer)
                kill.assert_not_called()
                self.assertFalse((unit / "stop.json").exists())
                self.assertEqual(forge._unit_state(unit)["status"], "done")

    def test_result_arriving_before_first_signal_settles_stop_marker(self):
        unit = self.dir / "processes" / "proc-eeeeeeee"
        unit.mkdir(parents=True)
        (unit / "request.json").write_text(json.dumps({
            "id": unit.name, "task_id": self.task_id, "command": "echo done",
            "supervisor_pid": 71, "supervisor_started_at": "born",
        }), encoding="utf-8")
        original_write = forge._atomic_json
        def capture_marker(path, value):
            original_write(path, value)
            if path == unit / "stop.json" and value.get("state") == "unresolved":
                original_write(unit / "result.json", {"status": "done", "code": 0})
        with mock.patch.object(forge, "_atomic_json", side_effect=capture_marker), \
             mock.patch.object(forge, "_kill_tree") as kill:
            self.assertIn("Stop не послан", forge.process(self.task_id, "stop", process_id=unit.name))
        kill.assert_not_called()
        self.assertEqual(forge._unit_state(unit)["status"], "done")

    def test_verification_stop_delegates_to_supervisor_and_pending_is_unknown(self):
        unit = self.dir / "verifications" / "verify-aaaaaaaa"
        unit.mkdir(parents=True)
        (unit / "request.json").write_text(json.dumps({
            "id": unit.name, "task_id": self.task_id, "status": "running",
            "supervisor_pid": 71, "supervisor_started_at": "born",
        }), encoding="utf-8")
        with mock.patch.object(forge, "_kill_tree") as kill, \
             mock.patch.object(forge, "_event"), \
             mock.patch.object(forge.time, "monotonic", side_effect=[0.0, 9.0]):
            answer = forge.verify(self.task_id, action="stop", verification_id=unit.name)
        self.assertIn("терминальный статус НЕ записан", answer)
        kill.assert_not_called()
        self.assertTrue((unit / "stop-request.json").exists())
        self.assertFalse((unit / "result.json").exists())
        self.assertEqual(forge._unit_state(unit)["status"], "unknown")
        self.assertIn('"status": "unknown"', forge.verify(self.task_id, action="poll",
                                                           verification_id=unit.name))
        (unit / "result.json").write_text(json.dumps({"status": "stopped", "checks": []}), encoding="utf-8")
        (unit / "stop.json").write_text(json.dumps({"state": "settled"}), encoding="utf-8")
        self.assertEqual(forge._unit_state(unit)["status"], "stopped")

    def test_recursive_parent_from_request_not_spoofed_and_slots_cover_all_workers(self):
        a = self.unit("agent-aaaaaaaa")
        with mock.patch.object(forge, "_pid_alive", return_value=True), \
             mock.patch.object(forge, "_spawn_runner", return_value=71):
            answer = self.call("spawn", agent_id="agent-bbbbbbbb", brief="child",
                               role="scout", spawned_by="agent-evil000",
                               _parent_request_path=str(a / "request.json"))
            self.assertIn("Порождён agent-bbbbbbbb", answer)
            request = json.loads((self.dir / "agents/agent-bbbbbbbb/request.json").read_text())
            self.assertEqual(request["parent_unit_id"], "agent-aaaaaaaa")
            self.assertEqual(request["spawned_by"], "agent-evil000")
            denied = self.call("spawn", brief="third", role="scout", spawned_by="agent-aaaaaaaa")
            self.assertIn("Active worker slots exhausted: 2/2", denied)
            bad = self.call("spawn", brief="bad", role="worker",
                            _parent_request_path=str(self.root / "request.json"))
            self.assertIn("Worker identity refused", bad)

    def test_read_oriented_parent_cannot_escalate_child(self):
        a = self.unit("agent-aaaaaaaa", role="scout")
        with mock.patch.object(forge, "_spawn_runner") as spawn:
            answer = self.call("spawn", brief="write", role="worker",
                               _parent_request_path=str(a / "request.json"))
        self.assertIn("role refused", answer)
        spawn.assert_not_called()

    def test_swarm_node_identity_conflict_blocks_duplicate_spawn(self):
        a = self.unit("agent-aaaaaaaa")
        request = json.loads((a / "request.json").read_text())
        request["node_id"] = "one"
        (a / "request.json").write_text(json.dumps(request), encoding="utf-8")
        with mock.patch.object(forge, "_spawn_runner") as spawn:
            answer = self.call("spawn", agent_id="agent-bbbbbbbb", brief="same node",
                               node_id="one", role="scout")
        self.assertIn("already has unit agent-aaaaaaaa", answer)
        spawn.assert_not_called()

    def test_exact_result_disposition_separate_and_parent_bound(self):
        a = self.unit("agent-aaaaaaaa")
        b = self.unit("agent-bbbbbbbb", parent="agent-aaaaaaaa",
                      result={"status": "done", "result": "proof"})
        original = (b / "result.json").read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        kwargs = {"agent_id": "agent-bbbbbbbb", "result_sha256": digest,
                  "disposition": "integrated", "_parent_request_path": str(a / "request.json")}
        self.assertIn('"disposition": "integrated"', self.call("disposition", **kwargs))
        self.assertEqual((b / "result.json").read_bytes(), original)
        poll = json.loads(self.call("poll", agent_id="agent-bbbbbbbb",
                                    _parent_request_path=str(a / "request.json")).split("--- worker log ---")[0])
        self.assertEqual(poll["result_sha256"], digest)
        self.assertEqual(poll["dispositions"][0]["disposition"], "integrated")
        self.assertIn("Existing disposition retained", self.call("disposition", **{
            **kwargs, "disposition": "irrelevant"}))
        self.assertIn("identity changed", self.call("disposition", **{
            **kwargs, "result_sha256": "0" * 64}))
        c = self.unit("agent-cccccccc")
        self.assertIn("Worker cannot inspect or control", self.call("disposition", **{
            **kwargs, "_parent_request_path": str(c / "request.json")}))

    def test_stop_fences_descendants_preserves_settled_and_keeps_uncertain_unknown(self):
        a = self.unit("agent-aaaaaaaa", result={"status": "done", "result": "keep"})
        b = self.unit("agent-bbbbbbbb", parent="agent-aaaaaaaa")
        c = self.unit("agent-cccccccc", parent="agent-bbbbbbbb")
        original = (a / "result.json").read_bytes()
        with mock.patch.object(forge, "_kill_tree", side_effect=["не остановлен: uncertain", "остановлен"]), \
             mock.patch.object(forge, "_pid_alive", return_value=False):
            message = self.call("stop", agent_id="agent-aaaaaaaa")
        self.assertIn("agent-cccccccc: не остановлен: uncertain", message)
        self.assertIn("agent-bbbbbbbb: остановлен", message)
        self.assertIn("detached coding_process/coding_verify jobs remain task-scoped", message)
        self.assertEqual((a / "result.json").read_bytes(), original)
        self.assertFalse((c / "result.json").exists(), "uncertain kill is never a terminal result")
        self.assertEqual(json.loads((b / "result.json").read_text())["status"], "stopped")
        self.assertTrue((c / "stop.json").exists())
        with mock.patch.object(forge, "_spawn_runner") as spawn:
            denied = self.call("spawn", brief="after stop", role="scout",
                               _parent_request_path=str(c / "request.json"))
        self.assertIn("stop-fenced", denied)
        spawn.assert_not_called()

    def test_old_unit_without_parent_stays_readable(self):
        old = self.unit("agent-aaaaaaaa")
        request = json.loads((old / "request.json").read_text())
        request.pop("parent_unit_id")
        (old / "request.json").write_text(json.dumps(request), encoding="utf-8")
        self.assertEqual(forge._worker_tree(self.task_id)[0]["parent_unit_id"], "")

    def test_stop_acknowledgement_with_live_pid_is_unknown_not_terminal(self):
        self.unit("agent-aaaaaaaa")
        with mock.patch.object(forge, "_kill_tree", return_value="остановлен"), \
             mock.patch.object(forge, "_pid_alive", return_value=True):
            answer = self.call("stop", agent_id="agent-aaaaaaaa")
        self.assertIn("unknown: stop acknowledged but supervisor remains alive", answer)
        unit = self.dir / "agents/agent-aaaaaaaa"
        self.assertFalse((unit / "result.json").exists())
        self.assertIn("unknown", json.loads((unit / "stop.json").read_text())["state"])

    def test_settled_result_is_preserved_while_live_process_is_stopped(self):
        unit = self.unit("agent-aaaaaaaa", result={"status": "done", "result": "kept"})
        original = (unit / "result.json").read_bytes()
        with mock.patch.object(forge, "_pid_alive", side_effect=[True, False]), \
             mock.patch.object(forge, "_group_live_members", return_value=False), \
             mock.patch.object(forge, "_kill_tree", return_value="остановлен") as kill:
            message = self.call("stop", agent_id="agent-aaaaaaaa")
        self.assertIn("result retained (результат сохранён); supervisor group stop: остановлен", message)
        self.assertEqual((unit / "result.json").read_bytes(), original)
        kill.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux group census")
    def test_settled_worker_with_dead_leader_still_stops_living_group(self):
        unit = self.unit("agent-aaaaaaaa", result={"status": "done", "result": "kept"})
        original = (unit / "result.json").read_bytes()
        with mock.patch.object(forge, "_pid_alive", return_value=False), \
             mock.patch.object(forge, "_group_live_members", side_effect=[True, False]), \
             mock.patch.object(forge, "_kill_tree", return_value="остановлен") as kill:
            message = self.call("stop", agent_id="agent-aaaaaaaa")
        self.assertIn("supervisor group stop: остановлен", message)
        self.assertEqual((unit / "result.json").read_bytes(), original)
        kill.assert_called_once()

    def test_worker_terminal_cas_does_not_overwrite_stop_result(self):
        unit = self.unit("agent-aaaaaaaa", result={"status": "stopped"})
        self.assertFalse(forge_worker._publish_result(
            unit / "request.json", self.task_id, {"status": "done", "result": "late"}))
        self.assertEqual(json.loads((unit / "result.json").read_text())["status"], "stopped")

    def test_worker_terminal_cas_advances_a_legacy_partial_result(self):
        unit = self.unit("agent-aaaaaaaa", result={"status": "running", "iterations": 3})
        self.assertTrue(forge_worker._publish_result(
            unit / "request.json", self.task_id, {"status": "done", "result": "settled"}))
        row = json.loads((unit / "result.json").read_text())
        self.assertEqual((row["status"], row["iterations"], row["result"]),
                         ("done", 3, "settled"))
        self.assertFalse(forge_worker._publish_result(
            unit / "request.json", self.task_id, {"status": "error"}))

    def test_disposition_requires_terminal_result_not_a_partial_hash(self):
        unit = self.unit("agent-aaaaaaaa", result={"status": "running", "iterations": 3})
        partial_hash = forge._result_identity(unit / "result.json")
        rejected = self.call("disposition", agent_id="agent-aaaaaaaa",
                             disposition="integrated", result_sha256=partial_hash)
        self.assertIn("not terminal", rejected)
        self.assertFalse(list(unit.glob("disposition-*.json")))
        self.assertTrue(forge_worker._publish_result(
            unit / "request.json", self.task_id, {"status": "done", "result": "final"}))
        final_hash = forge._result_identity(unit / "result.json")
        self.assertNotEqual(partial_hash, final_hash)
        accepted = self.call("disposition", agent_id="agent-aaaaaaaa",
                             disposition="integrated", result_sha256=final_hash)
        self.assertIn(final_hash, accepted)

    def test_swarm_ambiguous_recovery_never_spawns_third_unit(self):
        forge_swarm.create(self.dir, [{"id": "one", "brief": "task"}])
        for ident in ("agent-aaaaaaaa", "agent-bbbbbbbb"):
            unit = self.unit(ident)
            req = json.loads((unit / "request.json").read_text())
            req["node_id"] = "one"
            (unit / "request.json").write_text(json.dumps(req), encoding="utf-8")
        with mock.patch.object(forge, "agent") as spawn:
            answer = forge.swarm(self.task_id, "tick")
        spawn.assert_not_called()
        self.assertIn("blocked_identity", answer)

    def test_worker_dispatch_does_not_pass_model_parent_id(self):
        unit = self.unit("agent-aaaaaaaa")
        request = {"id": "agent-aaaaaaaa", "task_id": self.task_id,
                   "_verified_request_path": str(unit / "request.json")}
        with mock.patch.object(forge_worker.forge, "agent", return_value="yes") as call:
            forge_worker._dispatch(request, "delegate", {"action": "spawn", "brief": "child",
                                                         "role": "scout"})
        self.assertEqual(call.call_args.kwargs["_parent_request_path"], str(unit / "request.json"))
        self.assertNotIn("spawned_by", call.call_args.kwargs)

    def test_read_oriented_worker_cannot_smuggle_process_or_watch_tool(self):
        request = {"id": "agent-aaaaaaaa", "task_id": self.task_id, "role": "scout"}
        with mock.patch.object(forge_worker.forge, "run") as run, \
             mock.patch.object(forge_worker.forge, "verify") as verify, \
             mock.patch.object(forge_worker.forge, "inspect") as inspect:
            self.assertIn("refused", forge_worker._dispatch(request, "run", {"command": "echo nope"}))
            self.assertIn("refused", forge_worker._dispatch(request, "verify", {"action": "start"}))
            self.assertIn("refused", forge_worker._dispatch(request, "inspect", {"action": "watch"}))
            run.assert_not_called()
            verify.assert_not_called()
            inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
