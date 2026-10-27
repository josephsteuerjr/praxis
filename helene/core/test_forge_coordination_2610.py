"""In-process Forge coordinator consumers. ALL process/model/network seams are mocked.

Run this file alone locally; process-spawning legacy suites belong on the Linux
stand. Fixtures live under this source directory and are always removed.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import forge
import forge_swarm


class Coordination(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-core-fixture-", dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tid = "code-12345678"
        self.directory = self.root / "tasks" / self.tid
        self.directory.mkdir(parents=True)
        self.patch("TASKS_DIR", self.root / "tasks")
        self.patch("BASE", self.root)
        self.patch("_event", mock.Mock())
        self.patch("_task_run_context", lambda _: {})
        self.patch("_proc_started_at", lambda _: "born")
        self.patch("_pid_alive", lambda *args: True)
        self.patch("_group_live_members", lambda *args: False)
        self.spawn = mock.Mock(return_value=781)
        self.patch("_spawn_runner", self.spawn)
        self.task = {"id": self.tid, "root": str(self.root), "goal": "repair and verify",
                     "success_criteria": ["real consumer"], "status": "active", "max_active_workers": 16}
        forge._atomic_json(self.directory / "task.json", self.task)
        # An unmocked subprocess attempt is an immediate test failure, never a
        # native Forge execution hidden behind a passing fixture.
        for name in ("Popen", "run", "check_output", "call"):
            patch = mock.patch.object(forge.subprocess, name, side_effect=AssertionError("NO PROCESS TEST"))
            patch.start(); self.addCleanup(patch.stop)

    def patch(self, name, value):
        p = mock.patch.object(forge, name, value)
        p.start(); self.addCleanup(p.stop)

    def unit(self, ident="agent-aaaaaaaa", role="worker", parent="", status="running", result=None):
        d = self.directory / "agents" / ident
        request = {"id": ident, "task_id": self.tid, "root": str(self.root), "role": role,
                   "parent_unit_id": parent, "status": status, "brief": "unit", "created": "now"}
        if status != "queued":
            request.update(supervisor_pid=781, supervisor_started_at="born")
        forge._atomic_json(d / "request.json", request)
        if result is not None:
            forge._atomic_json(d / "result.json", result)
        return d

    def test_done_waits_for_exact_coordinator_acceptance_and_revisions(self):
        plan = forge_swarm.create(self.directory, [{"id": "build", "brief": "build"},
            {"id": "integrate", "brief": "integrate", "deps": ["build"]}])
        d = self.unit(result={"status": "done", "result": "candidate"})
        plan["nodes"][0]["agent_id"] = d.name
        forge_swarm.save(self.directory, plan)
        refresh = lambda: forge_swarm.refresh(self.directory, lambda ident: forge._agent_snapshot(self.tid, ident))
        current = refresh()
        self.assertEqual(current["nodes"][0]["status"], "awaiting_acceptance")
        self.assertEqual(forge_swarm.ready_nodes(current), [])
        raw = (d / "result.json").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        self.assertIn("identity", forge.agent(self.tid, "disposition", agent_id=d.name,
            result_sha256="0" * 64, disposition="integrated"))
        for choice in ("deferred", "integrated"):
            answer = json.loads(forge.agent(self.tid, "disposition", agent_id=d.name,
                result_sha256=digest, disposition=choice))
            self.assertEqual(answer["disposition"], choice)
        current = refresh()
        self.assertEqual([n["id"] for n in forge_swarm.ready_nodes(current)], ["integrate"])
        self.assertEqual(len(list(d.glob("disposition-*.json"))), 2)
        self.assertEqual((d / "result.json").read_bytes(), raw)
        # Another result on the same path cannot inherit the old acceptance.
        forge._atomic_json(d / "result.json", {"status": "done", "result": "different"})
        self.assertEqual(refresh()["nodes"][0]["status"], "awaiting_acceptance")

    def test_shared_writer_queues_once_and_readers_remain_parallel(self):
        first = self.unit()
        queued = forge.agent(self.tid, "spawn", brief="second writer", agent_id="agent-bbbbbbbb")
        self.assertIn("Queued agent-bbbbbbbb", queued)
        self.assertIn("shared-root", queued)
        self.assertEqual(forge._unit_state(self.directory / "agents/agent-bbbbbbbb")["status"], "queued")
        self.assertIn("уже существует", forge.agent(self.tid, "spawn", brief="duplicate", agent_id="agent-bbbbbbbb"))
        self.assertIn("Порождён", forge.agent(self.tid, "spawn", brief="read", role="reviewer", agent_id="agent-cccccccc"))
        self.assertEqual(self.spawn.call_count, 1)
        forge._atomic_json(first / "result.json", {"status": "done"})
        self.assertEqual(forge.tick_queued(self.tid), [])
        self.assertEqual(self.spawn.call_count, 1)
        forge.release_worker_root(first / "request.json")
        self.assertEqual(forge.tick_queued(self.tid)[0]["agent_id"], "agent-bbbbbbbb")
        self.assertEqual(self.spawn.call_count, 2)
        forge.tick_queued(self.tid)
        self.assertEqual(self.spawn.call_count, 2)

    def test_terminal_writer_requires_exact_release_or_proven_tree_exit(self):
        first = self.unit(result={"status": "done", "result": "one"})
        request = {"id": "agent-bbbbbbbb", "role": "worker"}
        self.assertIn("shared-root", forge._agent_capacity(self.tid, request))
        forge.release_worker_root(first / "request.json")
        self.assertEqual(forge._agent_capacity(self.tid, request), "")
        forge._atomic_json(first / "result.json", {"status": "done", "result": "two"})
        self.assertIn("shared-root", forge._agent_capacity(self.tid, request))
        with mock.patch.object(forge, "_writer_ended", return_value=True):
            self.assertEqual(forge._agent_capacity(self.tid, request), "")

    def test_selected_stop_ignores_live_sibling_and_its_jobs(self):
        selected = self.unit(status="queued")
        sibling = self.unit("agent-bbbbbbbb")
        for kind, ident in (("processes", "proc-aaaaaaaa"), ("verifications", "verify-aaaaaaaa")):
            forge._atomic_json(self.directory / kind / ident / "request.json", {
                "id": ident, "task_id": self.tid, "parent_unit_id": sibling.name, "status": "running"})
        with mock.patch.object(forge, "process") as process, mock.patch.object(forge, "verify") as verify:
            receipt = forge.control(self.tid, "stop", selected.name)
        self.assertEqual(receipt["status"], "settled")
        self.assertEqual(receipt["unknown_units"], [])
        self.assertEqual(forge._unit_state(sibling)["status"], "running")
        process.assert_not_called(); verify.assert_not_called()

    def test_selected_stop_retains_uncertain_owned_job(self):
        selected = self.unit(status="queued")
        sibling = self.unit("agent-bbbbbbbb")
        for ident, owner in (("proc-aaaaaaaa", selected.name), ("proc-bbbbbbbb", sibling.name)):
            forge._atomic_json(self.directory / "processes" / ident / "request.json", {
                "id": ident, "task_id": self.tid, "parent_unit_id": owner, "status": "running"})
        with mock.patch.object(forge, "process", return_value="unconfirmed") as process:
            receipt = forge.control(self.tid, "stop", selected.name)
        self.assertEqual(receipt["status"], "unknown")
        self.assertEqual(receipt["unknown_units"], ["proc-aaaaaaaa"])
        process.assert_called_once_with(self.tid, "stop", process_id="proc-aaaaaaaa")

    def test_snapshot_review_stop_can_settle_absent_supervisor_only_for_bound_adapter(self):
        metadata = {"schema": 1, "worker_script": "trusted-snapshot-adapter"}
        forge._atomic_json(self.directory / "task.json", {**self.task, "self_review": metadata})
        windows = types.SimpleNamespace(**{name: getattr(forge.os, name) for name in dir(forge.os)
                                          if not name.startswith('__')})
        windows.name = 'nt'
        for index, prior in enumerate((None, {"status": "done", "result": "retained"})):
            unit = self.unit(f"agent-aaaaaaa{index}", role="reviewer", result=prior)
            request = forge._read_json(unit / "request.json")
            forge._atomic_json(unit / "request.json", {**request, "self_review": metadata})
            with mock.patch.object(forge, 'os', windows), \
                 mock.patch.object(forge, '_worker_script', return_value=Path(__file__)), \
                 mock.patch.object(forge, '_pid_alive', return_value=False), \
                 mock.patch.object(forge, '_kill_tree', side_effect=AssertionError('no PID signal')):
                receipt = forge.control(self.tid, 'stop', unit.name)
            self.assertEqual(receipt['status'], 'settled')
            result = forge._read_json(unit / 'result.json')
            self.assertEqual(result, prior) if prior else self.assertEqual(result['status'], 'stopped')

    def test_snapshot_review_identity_drift_does_not_certify_absent_tree(self):
        metadata = {"schema": 1, "worker_script": "trusted-snapshot-adapter"}
        forge._atomic_json(self.directory / "task.json", {**self.task, "self_review": metadata})
        unit = self.unit(role="reviewer", result={"status": "done"})
        request = forge._read_json(unit / "request.json")
        forge._atomic_json(unit / "request.json", {**request, "self_review": {**metadata, "schema": 2}})
        windows = types.SimpleNamespace(**{name: getattr(forge.os, name) for name in dir(forge.os)
                                          if not name.startswith('__')}); windows.name = 'nt'
        with mock.patch.object(forge, 'os', windows), mock.patch.object(forge, '_pid_alive', return_value=False):
            self.assertEqual(forge.control(self.tid, 'stop', unit.name)['status'], 'unknown')

    def test_terminal_pause_and_nested_fences_refuse_admission(self):
        for status in ("done", "abandoned", "stopped"):
            forge._atomic_json(self.directory / "task.json", {**self.task, "status": status})
            self.assertIn("terminal", forge.agent(self.tid, "spawn", brief="late"))
        forge._atomic_json(self.directory / "task.json", self.task)
        forge._atomic_json(self.directory / "control.json", {"action": "pause"})
        self.assertIn("pause-fenced", forge.agent(self.tid, "spawn", brief="paused"))
        self.assertEqual(forge.control(self.tid, "resume")["status"], "resumed")
        parent = self.unit(role="scout")
        self.assertIn("read-oriented", forge.agent(self.tid, "spawn", brief="edit", role="worker",
            _parent_request_path=str(parent / "request.json")))
        forge._atomic_json(parent / "stop.json", {"state": "pending"})
        self.assertIn("stop-fenced", forge.agent(self.tid, "spawn", brief="child", role="scout",
            _parent_request_path=str(parent / "request.json")))
        self.assertFalse(forge.worker_control(parent / "request.json")["allowed"])
        self.spawn.assert_not_called()

    def test_worker_yield_is_explicit_branch_bound_and_blocks_mutation(self):
        parent = self.unit()
        pp = str(parent / "request.json")
        forge._atomic_json(self.directory / "task.json", {**self.task, "max_active_workers": 1})
        forge.agent(self.tid, "spawn", brief="child", agent_id="agent-bbbbbbbb", _parent_request_path=pp)
        child = self.directory / "agents/agent-bbbbbbbb"
        self.assertIn("exhausted", forge._agent_capacity(self.tid, forge._read_json(child / "request.json")))
        forge.tick_queued(self.tid)
        self.spawn.assert_not_called()  # waiting in prose or finishing is NOT a yield
        def finish_on_spawn(*args):
            self.assertIn("yielded", forge._admission_reason(self.tid, pp, general=True))
            # Use a separate slot budget here so the assertion targets writer
            # ownership, not the already-proven one-slot count.
            forge._atomic_json(self.directory / "task.json", self.task)
            self.assertIn("writer occupied", forge._agent_capacity(self.tid,
                {"id": "agent-cccccccc", "role": "worker"}))
            forge._atomic_json(child / "result.json", {"status": "done", "result": "child complete"})
            forge._atomic_json(child / "root-released.json", {"unit_id": child.name,
                "result_sha256": forge._result_identity(child / "result.json")})
            return 782
        self.spawn.side_effect = finish_on_spawn
        receipt = json.loads(forge.agent(self.tid, "wait", agent_id=child.name, _parent_request_path=pp))
        self.assertEqual(receipt["status"], "settled")
        self.assertEqual(receipt["result"]["result"], "child complete")
        self.assertEqual(forge._admission_reason(self.tid, pp, general=True), "")
        self.assertFalse((self.root / "unsafe.txt").exists())

    def test_yield_keeps_parallel_read_descendants_and_stop_fences(self):
        parent = self.unit()
        child = self.unit("agent-bbbbbbbb", parent=parent.name, status="queued")
        reader = self.unit("agent-cccccccc", parent=parent.name, role="scout")
        sibling = self.unit("agent-dddddddd", parent=parent.name, status="queued")
        forge._atomic_json(parent / "handoff.json", {"state": "waiting", "child_unit_id": child.name})
        self.assertEqual(forge._admission_reason(self.tid, str(reader / "request.json")), "")
        self.assertIn("another child", forge._admission_reason(self.tid, str(sibling / "request.json")))
        forge._atomic_json(parent / "stop.json", {"state": "pending"})
        self.assertIn("stop-fenced", forge._admission_reason(self.tid, str(reader / "request.json")))

    def test_windows_json_sharing_retry_is_bounded_not_suppressed(self):
        target = self.root / "publication.json"
        forge._atomic_json(target, {"old": True})
        replace = Path.replace
        calls = []
        def reader_overlap(path, destination):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError("synthetic Windows open-reader sharing violation")
            return replace(path, destination)
        win = types.SimpleNamespace(name="nt", getpid=forge.os.getpid)
        with mock.patch.object(forge, "os", win), mock.patch.object(Path, "replace", reader_overlap), \
             mock.patch.object(forge.time, "sleep") as pause:
            forge._atomic_json(target, {"new": True})
        self.assertEqual(forge._read_json(target), {"new": True})
        self.assertEqual(len(calls), 2)
        pause.assert_called_once_with(.01)
        with mock.patch.object(forge, "os", win), \
             mock.patch.object(Path, "replace", side_effect=PermissionError("persistent denial")) as denied, \
             mock.patch.object(forge.time, "sleep"), self.assertRaises(PermissionError):
            forge._atomic_json(target, {"never": True})
        self.assertEqual(denied.call_count, 21)
        self.assertEqual(forge._read_json(target), {"new": True})

    def test_detached_unknown_and_ancestor_stop_cannot_be_yielded_around(self):
        parent = self.unit()
        pp = str(parent / "request.json")
        forge.agent(self.tid, "spawn", brief="child", agent_id="agent-bbbbbbbb", _parent_request_path=pp)
        job = self.directory / "processes/proc-aaaaaaaa"
        forge._atomic_json(job / "request.json", {"id": job.name, "task_id": self.tid,
            "parent_unit_id": parent.name, "supervisor_pid": 7, "supervisor_started_at": "born"})
        forge._atomic_json(job / "stop.json", {"state": "unresolved", "note": "detached shell unknown"})
        reply = json.loads(forge.agent(self.tid, "wait", agent_id="agent-bbbbbbbb", _parent_request_path=pp))
        self.assertEqual(reply["status"], "refused")
        self.assertIn("detached writer", reply["reason"])
        self.assertFalse((parent / "handoff.json").exists())
        forge._atomic_json(job / "result.json", {"status": "done"})
        self.assertEqual(json.loads(forge.agent(self.tid, "wait", agent_id="agent-bbbbbbbb",
            _parent_request_path=pp))["status"], "refused")  # result cannot erase unknown Stop
        forge._atomic_json(job / "stop.json", {"state": "settled"})
        forge._atomic_json(parent / "stop.json", {"state": "pending"})
        reply = json.loads(forge.agent(self.tid, "wait", agent_id="agent-bbbbbbbb", _parent_request_path=pp))
        self.assertIn("stop-fenced", reply["reason"])
        self.spawn.assert_not_called()

    def test_pause_during_yield_keeps_ownership_fenced(self):
        parent = self.unit()
        pp = str(parent / "request.json")
        forge.agent(self.tid, "spawn", brief="child", agent_id="agent-bbbbbbbb", _parent_request_path=pp)
        real_tick = forge.tick_queued
        def pause(_):
            forge._atomic_json(self.directory / "control.json", {"action": "pause"})
            return real_tick(self.tid)
        with mock.patch.object(forge, "tick_queued", side_effect=pause):
            reply = json.loads(forge.agent(self.tid, "wait", agent_id="agent-bbbbbbbb", _parent_request_path=pp))
        self.assertIn("pause-fenced", reply["reason"])
        self.assertEqual(forge._read_json(parent / "handoff.json")["state"], "waiting")
        self.spawn.assert_not_called()

    def test_child_terminal_ancestor_and_unknown_root_release_stay_fenced(self):
        parent = self.unit()
        child = self.unit("agent-bbbbbbbb", parent=parent.name, status="queued")
        forge._atomic_json(parent / "result.json", {"status": "done"})
        forge.tick_queued(self.tid)
        self.assertIn("terminal", forge._read_json(child / "request.json")["queued_reason"])
        self.assertFalse(forge._root_released(self.tid, child.name))
        self.spawn.assert_not_called()

    def test_queued_stop_never_spawns_and_group_stop_never_revives(self):
        d = self.unit(status="queued", parent="agent-cccccccc")  # orphan still group-owned
        forge._atomic_json(self.directory / "task.json", {**self.task, "root": str(self.root / "missing-root")})
        with mock.patch.object(forge, "_kill_tree", side_effect=AssertionError("queued process never existed")):
            receipt = forge.control(self.tid, "stop")
        self.assertEqual(receipt["status"], "settled")
        self.assertTrue(json.loads((d / "result.json").read_text())["never_started"])
        forge.tick_queued(self.tid)
        self.spawn.assert_not_called()
        self.assertEqual(forge.control(self.tid, "resume")["status"], "refused")

    def test_group_stop_calls_detached_process_and_verification(self):
        for kind, ident in (("processes", "proc-aaaaaaaa"), ("verifications", "verify-aaaaaaaa")):
            forge._atomic_json(self.directory / kind / ident / "request.json", {"id": ident, "task_id": self.tid})
        with mock.patch.object(forge, "process", return_value="process unresolved") as process, \
             mock.patch.object(forge, "verify", return_value="verify unresolved") as verify:
            forge.control(self.tid, "stop")
        process.assert_called_once_with(self.tid, "stop", process_id="proc-aaaaaaaa")
        verify.assert_called_once_with(self.tid, "stop", verification_id="verify-aaaaaaaa")
        self.assertIn("terminal", forge.process(self.tid, "start", command="echo unsafe"))
        self.assertIn("terminal", forge.verify(self.tid, "start", commands="echo unsafe"))

    def test_rework_keeps_attempt_and_old_result_and_plan_cannot_overwrite(self):
        forge.swarm(self.tid, "plan", plan='[{"id":"build","brief":"build"}]')
        forge.swarm(self.tid, "start")
        first = forge_swarm.load(self.directory)["nodes"][0]
        d = self.directory / "agents" / first["agent_id"]
        self.assertEqual(forge._read_json(d / "request.json")["node_id"], "build")
        forge._atomic_json(d / "result.json", {"status": "done", "result": "bad"})
        forge.release_worker_root(d / "request.json")
        digest = forge._result_identity(d / "result.json")
        forge.swarm(self.tid, "rework", node_id="build", message="fix defect", result_sha256=digest)
        node = forge_swarm.load(self.directory)["nodes"][0]
        self.assertEqual(node["previous_unit_id"], d.name)
        self.assertEqual(node["attempts"][0]["result_sha256"], digest)
        self.assertIn("already exists", forge.swarm(self.tid, "plan", plan='[{"brief":"replace"}]'))
        self.assertEqual(forge._result_identity(d / "result.json"), digest)
        forge.swarm(self.tid, "tick")
        updated = forge_swarm.load(self.directory)["nodes"][0]
        self.assertNotEqual(updated["agent_id"], d.name)
        new_request = json.loads((self.directory / "agents" / updated["agent_id"] / "request.json").read_text())
        self.assertEqual(new_request["previous_unit_id"], d.name)
        # A second supported correction must not become blocked_identity because
        # both historical requests still have the same logical node_id.
        second = self.directory / "agents" / updated["agent_id"]
        forge._atomic_json(second / "result.json", {"status": "done", "result": "still bad"})
        forge.release_worker_root(second / "request.json")
        forge.swarm(self.tid, "rework", node_id="build", message="fix remaining defect",
                    result_sha256=forge._result_identity(second / "result.json"))
        forge.swarm(self.tid, "tick")
        final = forge_swarm.load(self.directory)["nodes"][0]
        self.assertNotIn(final["agent_id"], {d.name, second.name, ""})
        self.assertEqual(len(final["attempts"]), 2)
        self.assertEqual(self.spawn.call_count, 3)

    def test_swarm_recovers_only_current_reserved_execution(self):
        forge.swarm(self.tid, "plan", plan='[{"id":"build","brief":"build"}]')
        forge.swarm(self.tid, "start")
        plan = forge_swarm.load(self.directory)
        ident = plan["nodes"][0]["agent_id"]
        # Persisted reservation survives a coordinator crash before post-spawn save.
        plan["nodes"][0].update(agent_id="", status="pending", reserved_agent_id=ident)
        forge_swarm.save(self.directory, plan)
        forge.swarm(self.tid, "tick")
        self.assertEqual(forge_swarm.load(self.directory)["nodes"][0]["agent_id"], ident)
        self.assertEqual(self.spawn.call_count, 1)

    def test_missing_reserved_unit_reuses_identity_before_first_spawn(self):
        plan = forge_swarm.create(self.directory, [{"id": "build", "brief": "build"}])
        ident = "agent-abcdef01"
        plan["nodes"][0].update(agent_id=ident, reserved_agent_id=ident, status="starting")
        forge_swarm.save(self.directory, plan)
        forge.swarm(self.tid, "tick")
        self.assertEqual(forge_swarm.load(self.directory)["nodes"][0]["agent_id"], ident)
        self.assertEqual(self.spawn.call_count, 1)

    def test_coordinator_checkpoint_and_schema_limits(self):
        for step in ("review findings", "verify repaired candidate"):
            reply = forge.swarm(self.tid, "checkpoint", plan=json.dumps({"next_action": step,
                "unresolved_reviews": ["reviewer-1"], "current_candidate": "abc"}))
            self.assertIn(step, reply)
        view = json.loads(forge.inspect(self.tid, "coordination"))
        self.assertEqual(view["next_action"], "verify repaired candidate")
        self.assertEqual(view["success_criteria"], ["real consumer"])
        self.assertEqual(len(list((self.directory / "coordinator-checkpoints").glob("*.json"))), 2)
        with self.assertRaises(ValueError): forge._worker_slots(65)
        plan = forge_swarm.create(self.directory, [{"brief": "read", "role": "scout"}], max_parallel=100)
        self.assertEqual(plan["max_parallel"], 32)

    def test_resume_creates_new_bound_unit_and_preserves_prior_result(self):
        d = self.unit(result={"status": "stopped", "result": "partial"})
        raw = (d / "result.json").read_bytes()
        forge._atomic_json(d / "progress.json", {"generation": "old-generation"})
        info = {"safe": True, "generation": "old-generation", "checkpoint_sha256": "a" * 64}
        with mock.patch.object(forge, "_writer_ended", return_value=True), \
             mock.patch("forge_runtime.continuation_info", return_value=info):
            self.assertEqual(forge.control(self.tid, "resume", d.name, "wrong")["status"], "refused")
            receipt = forge.control(self.tid, "resume", d.name, "old-generation")
            duplicate = forge.control(self.tid, "resume", d.name, "old-generation")
            self.assertEqual(duplicate["agent_id"], receipt["agent_id"])
        self.assertEqual(receipt["status"], "queued")
        new = self.directory / "agents" / receipt["agent_id"]
        request = json.loads((new / "request.json").read_text())
        self.assertEqual(request["continuation_source"]["checkpoint_sha256"], "a" * 64)
        self.assertEqual(request["previous_unit_id"], d.name)
        self.assertEqual((d / "result.json").read_bytes(), raw)
        self.assertTrue(json.loads((new / "resume.json").read_text())["old_writer_ended"])

    def test_adapter_results_do_not_emit_ordinary_event_notice_or_wake(self):
        core = types.ModuleType("core")
        core.events = types.SimpleNamespace(emit=mock.Mock(), known_keys=lambda: set())
        core.notices = types.SimpleNamespace(note_node=mock.Mock())
        core.subagents = types.SimpleNamespace(normalize=mock.Mock(return_value={"status": "done"}),
            event_key=lambda *args, **kwargs: "key", overdue_minutes=lambda: 1,
            load_unit=lambda d: (forge._read_json(d / "request.json"), forge._read_json(d / "result.json")))
        d = self.unit(result={"status": "done", "finished": forge._now(), "role": "worker"})
        with mock.patch.dict(sys.modules, {"core": core}), \
             mock.patch.object(forge, "_wake_load_seen", return_value=set()), \
             mock.patch.object(forge, "reconcile_lost_tasks", return_value=0), \
             mock.patch.object(forge, "reconcile_submitted_tasks", return_value=0), \
             mock.patch.object(forge, "check_upstreams", return_value=None):
            for metadata in ("source_update", "self_review"):
                forge._atomic_json(self.directory / "task.json", {**self.task, metadata: {"schema": 1}})
                forge.emit_unit_event(self.tid, d.name, {"status": "done"})
                self.assertEqual(forge._scan_worker_completions(), [])
                self.assertEqual(forge.reconcile_subagent_events(force=True), 0)
            core.events.emit.assert_not_called()
            core.notices.note_node.assert_not_called()
            forge._atomic_json(self.directory / "task.json", self.task)
            forge.emit_unit_event(self.tid, d.name, {"status": "done"})
            core.events.emit.assert_called_once()
            core.notices.note_node.assert_called_once()

    def test_dependent_acceptance_is_bound_to_consumed_upstream_result(self):
        # Deliberately reverse the storage order: validity follows the graph, not JSON order.
        plan = forge_swarm.create(self.directory, [
            {"id": "next", "brief": "consume", "deps": ["package"]},
            {"id": "package", "brief": "package", "deps": ["check"]},
            {"id": "check", "brief": "check", "deps": ["build"]},
            {"id": "build", "brief": "build"}])
        by_id = {n["id"]: n for n in plan["nodes"]}
        for node, ident in zip((by_id[n] for n in ("build", "check", "package")),
                               ("agent-aaaaaaaa", "agent-bbbbbbbb", "agent-cccccccc")):
            d = self.unit(ident, result={"status": "done", "result": ident})
            node["agent_id"] = ident
            digest = forge._result_identity(d / "result.json")
            forge.agent(self.tid, "disposition", agent_id=ident,
                        result_sha256=digest, disposition="integrated")
        by_id["check"]["dependency_results"] = {"build": forge._result_identity(
            self.directory / "agents/agent-aaaaaaaa/result.json")}
        by_id["package"]["dependency_results"] = {"check": forge._result_identity(
            self.directory / "agents/agent-bbbbbbbb/result.json")}
        forge_swarm.save(self.directory, plan)
        refresh = lambda: forge_swarm.refresh(self.directory, lambda unit: forge._agent_snapshot(self.tid, unit))
        self.assertEqual([n["id"] for n in forge_swarm.ready_nodes(refresh())], ["next"])
        first = self.directory / "agents/agent-aaaaaaaa/result.json"
        digest = forge._result_identity(first)
        original = first.read_bytes()
        forge.agent(self.tid, "disposition", agent_id="agent-aaaaaaaa",
                    result_sha256=digest, disposition="deferred")
        current = refresh()
        changed = {n["id"]: n for n in current["nodes"]}
        for name in ("check", "package"):
            self.assertEqual(changed[name]["status"], "stale_dependencies")
            self.assertEqual(changed[name]["accepted_sha256"], "")
            self.assertEqual(changed[name]["acceptance"], "integrated")
        self.assertEqual(forge_swarm.ready_nodes(current), [])
        self.assertEqual(first.read_bytes(), original)
        # Restoring the exact upstream acceptance restores the unchanged chain;
        # all earlier dispositions remain append-only.
        forge.agent(self.tid, "disposition", agent_id="agent-aaaaaaaa",
                    result_sha256=digest, disposition="integrated")
        self.assertEqual([n["id"] for n in forge_swarm.ready_nodes(refresh())], ["next"])
        self.assertEqual(len(list(first.parent.glob("disposition-*.json"))), 3)
        forge.swarm(self.tid, "rework", node_id="build", message="new candidate", result_sha256=digest)
        self.assertEqual(forge_swarm.ready_nodes(refresh())[0]["id"], "build")
        self.assertEqual({n["id"]: n for n in refresh()["nodes"]}["package"]["status"], "stale_dependencies")

    def test_source_update_registration_rejects_drift_and_exclusive_root(self):
        module = types.ModuleType("source_update_worker")
        script = self.root / "source_update_worker.py"
        script.write_text("# trusted test adapter", encoding="utf-8")
        module.__file__ = str(script)
        modules = {"source_update_worker": module}
        # No localharness package is imported from the installed app in this fixture.
        with mock.patch.dict(sys.modules, modules), mock.patch.dict(sys.modules, {"localharness": None}):
            other = "code-87654321"
            args = (other, str(self.root), "a" * 64, "b" * 64, str(script))
            task = forge.source_update_task(*args)
            self.assertEqual(forge.source_update_task(*args), task)
            with self.assertRaises(ValueError):
                forge.source_update_task(other, str(self.root), "a" * 64, "c" * 64, str(script))
            self.assertIn("Порождён", forge.agent(other, "spawn", brief="merge", agent_id="agent-dddddddd"))
            self.assertIn("sole root", forge.agent(other, "spawn", brief="again"))
            self.assertIn("general", forge.process(other, "start", command="echo forbidden"))
            self.assertEqual(self.spawn.call_args.args[0], script)


if __name__ == "__main__":
    unittest.main()
