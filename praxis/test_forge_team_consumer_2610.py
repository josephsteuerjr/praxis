"""Actual Forge/team/worker consumer with a deterministic provider, never a model.

Default: worker processes are replaced by in-process threads; subprocess is forbidden.
Server only: PRAXIS_TEST_FORGE_PROCESS=1 enables the SAME consumers with actual
_spawn_runner/Python processes. This file is also their --request entry point.
No installation, network, model, authored user tree or profile is involved.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

if os.environ.get("PRAXIS_STAND_OLD") == "1":
    # Old variants are source-only siblings in the disposable Linux stand.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_previous"))

import forge
import forge_swarm
import forge_worker


CORE = Path(__file__).resolve().parent
TERMINAL = forge._TERMINAL_AGENT_STATES


def tool(name, **arguments):
    return types.SimpleNamespace(text="", model="deterministic-provider", usage={}, stop_reason="tool_use",
        blocks=[{"type": "tool_use", "id": "call-" + os.urandom(8).hex(), "name": name, "input": arguments}])


def reply(text):
    return types.SimpleNamespace(text=text, model="deterministic-provider", usage={}, stop_reason="end_turn",
                                 blocks=[{"type": "text", "text": text}])


def provider(*args, **kwargs):
    """The only fake cognitive component. Tools/dispatcher/journal/core are real."""
    initial = kwargs["messages"][0]["content"]
    brief = initial.splitlines()[0]
    if brief.startswith("cold-"):
        return cold_provider(brief, kwargs)
    request = json.loads(kwargs["system"])
    brief, root = request["brief"], Path(request["root"])
    results = [item["content"] for message in kwargs["messages"] if message["role"] == "user"
               and isinstance(message["content"], list) for item in message["content"]
               if item.get("type") == "tool_result"]
    step = len(results)
    if brief in {"read-a", "read-b"}:
        if not step:
            (root / (brief + ".started")).write_text("provider entered", encoding="utf-8")
            end = time.monotonic() + 8
            while not all((root / (name + ".started")).exists() for name in ("read-a", "read-b")):
                if time.monotonic() > end:
                    raise AssertionError("two read workers were not admitted in parallel")
                time.sleep(.02)
            return tool("inspect", action="read", path="seed.txt")
        return reply("read result " + brief)
    if brief in {"build", "repair"}:
        if not step:
            return tool("edit", action="write", path="candidate.txt", content="GOOD" if brief == "repair" else "BAD")
        return reply("candidate " + (root / "candidate.txt").read_text(encoding="utf-8"))
    if brief == "review":
        if not step:
            return tool("inspect", action="read", path="candidate.txt")
        return reply("clear" if "GOOD" in results[-1] else "NEEDS_CHANGES")
    if brief == "stand-legacy-normal":
        if not step:
            return tool("edit", action="write", path="legacy-probe.txt", content="older worker ran")
        return reply("legacy worker settled")
    if brief == "package":
        if not step:
            return tool("edit", action="write", path="package.txt", content=(root / "candidate.txt").read_text(encoding="utf-8"))
        return reply("dependent consumed repaired candidate")
    if brief == "nested-parent":
        if step == 0:
            return tool("edit", action="write", path="serial.txt", content="P")
        if step == 1:
            return tool("delegate", action="spawn", role="worker", brief="nested-child")
        child = re.search(r"agent-[0-9a-f]{8}", results[1]).group()
        if step == 2:
            # This is not an extra cognition turn while waiting: the next call
            # remains inside the actual blocking delegate tool until child exit.
            return tool("delegate", action="wait", agent_id=child)
        settled = json.loads(results[2])
        assert settled["status"] == "settled", settled
        assert settled["result"]["result"] == "child complete", settled
        if step == 3:
            return tool("delegate", action="disposition", agent_id=child,
                        result_sha256=settled["result_sha256"], disposition="integrated")
        if step == 4:
            return tool("edit", action="replace", path="serial.txt", old="PC", new="PCP")
        return reply("parent consumed exact child result")
    if brief == "nested-child":
        if not step:
            pp = forge._unit_dir(request["task_id"], "agents", request["parent_unit_id"]) / "request.json"
            denied = forge.edit(request["task_id"], "write", path="unsafe.txt", content="no",
                                _parent_request_path=str(pp))
            assert "yielded" in denied, denied
            return tool("edit", action="replace", path="serial.txt", old="P", new="PC")
        return reply("child complete")
    raise AssertionError("unexpected test brief: " + brief)


def cold_provider(brief, wire):
    system = wire["system"]
    root = Path(re.search(r"^Exact task workspace address: (.*)$", system, re.M).group(1))
    with (root / (brief + ".wire")).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(pid=os.getpid(), system=system, messages=wire["messages"]),
                                ensure_ascii=False) + "\n")
    results = [item["content"] for msg in wire["messages"] if msg["role"] == "user"
               and isinstance(msg["content"], list) for item in msg["content"]
               if item.get("type") == "tool_result"]
    n = len(results)
    if brief == "cold-leaf":
        if not n:
            (root / "leaf-entered").touch()
            deadline = time.monotonic() + 15
            while not (root / "release-leaf").exists():
                assert time.monotonic() < deadline, "fixture failed to release leaf"
                time.sleep(.02)
            return tool("edit", action="replace", path="serial.txt", old="PM", new="PML")
        return reply("leaf settled")
    if brief not in {"cold-parent", "cold-middle"}:
        raise AssertionError(brief)
    parent = brief == "cold-parent"
    if not n:
        if parent:
            return tool("edit", action="write", path="serial.txt", content="P")
        return tool("edit", action="replace", path="serial.txt", old="P", new="PM")
    if n == 1:
        return tool("delegate", action="spawn", role="worker",
                    brief="cold-middle" if parent else "cold-leaf", success_criteria=["STAND-CHILD-CRITERION"])
    child = re.search(r"agent-[0-9a-f]{8}", results[1]).group()
    if n == 2:
        return tool("delegate", action="wait", agent_id=child)
    settled = json.loads(results[2])
    assert settled["status"] == "settled" and settled["agent_id"] == child, settled
    assert settled["result"]["result"] == ("middle settled" if parent else "leaf settled"), settled
    if n == 3:
        return tool("delegate", action="disposition", agent_id=child,
                    result_sha256=settled["result_sha256"], disposition="integrated")
    if n == 4:
        assert json.loads(results[3])["disposition"] == "integrated"
        return tool("edit", action="replace", path="serial.txt",
                    old="PMLM" if parent else "PML", new="PMLMP" if parent else "PMLM")
    return reply("parent settled" if parent else "middle settled")


@contextlib.contextmanager
def worker_environment():
    actual_system = forge_worker._system
    def system(req):
        return actual_system(req) if req["brief"].startswith("cold-") else json.dumps(req)
    original_inspect = forge.inspect
    def inspect(tid, action, **kwargs):
        return "synthetic diff (no Git invocation)" if action == "diff" else original_inspect(tid, action, **kwargs)
    workshop = types.ModuleType("workshop")
    workshop.mutation_lock = lambda: contextlib.nullcontext(types.SimpleNamespace(lost_to_takeover=False))
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(forge_worker, "_system", system))
        stack.enter_context(mock.patch.object(forge_worker.llm, "chat", provider))
        stack.enter_context(mock.patch.object(forge_worker.llm, "assistant_message",
            lambda response, blocks: {"role": "assistant", "content": blocks}, create=True))
        stack.enter_context(mock.patch.object(forge, "inspect", inspect))
        stack.enter_context(mock.patch.object(forge, "emit_unit_event"))
        stack.enter_context(mock.patch.object(forge, "_task_run_context", return_value={}))
        stack.enter_context(mock.patch.object(forge, "_worker_script", return_value=Path(__file__).resolve()))
        stack.enter_context(mock.patch.dict(sys.modules, {"workshop": workshop}))
        yield


def process_entry(path):
    request = json.loads(path.read_text(encoding="utf-8"))
    forge.BASE = Path(request["root"])
    forge.TASKS_DIR = path.parents[3]  # tasks/, not tasks/<task_id>/
    with worker_environment():
        return forge_worker.run(path)


class TeamConsumers:
    process_mode = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-team-", dir=CORE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tid = "code-12345678"
        self.directory = self.root / "memory" / ".forge" / "tasks" / self.tid
        self.directory.mkdir(parents=True)
        self.threads = {}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(forge, "BASE", self.root))
        self.stack.enter_context(mock.patch.object(forge, "TASKS_DIR", self.directory.parent))
        self.stack.enter_context(worker_environment())
        forge._atomic_json(self.directory / "task.json", {"id": self.tid, "root": str(self.root),
            "goal": "actual deterministic team consumer", "status": "active", "max_active_workers": 16})
        (self.root / "seed.txt").write_text("consumer source", encoding="utf-8")
        if not self.process_mode:
            self.stack.enter_context(mock.patch.object(forge, "_spawn_runner", self.spawn_thread))
            process_birth = forge._proc_started_at
            self.stack.enter_context(mock.patch.object(forge, "_proc_started_at",
                lambda pid: process_birth(pid) if pid == os.getpid() else "thread-test"))
            self.stack.enter_context(mock.patch.object(forge, "_pid_alive",
                lambda pid, *_: bool(self.threads.get(pid) and self.threads[pid].is_alive())))
            self.stack.enter_context(mock.patch.object(forge, "_group_live_members", return_value=False))
            for name in ("Popen", "run", "call", "check_output"):
                self.stack.enter_context(mock.patch.object(forge.subprocess, name,
                    side_effect=AssertionError("NO LOCAL SUBPROCESS")))
        self.addCleanup(self.join_workers)

    def spawn_thread(self, script, request, log):
        pid = 80000 + len(self.threads)
        thread = threading.Thread(target=forge_worker.run, args=(request,), daemon=True)
        self.threads[pid] = thread
        thread.start()
        return pid

    def join_workers(self):
        for thread in list(self.threads.values()):
            thread.join(4)
            self.assertFalse(thread.is_alive(), "test worker failed to relinquish ownership")
        if self.process_mode:
            for directory in (self.directory / "agents").glob("agent-*"):
                request = forge._read_json(directory / "request.json", {}) or {}
                proc = forge._RUNNERS.get(int(request.get("supervisor_pid") or 0))
                if proc is not None:
                    proc.wait(timeout=10)

    def wait_result(self, ident):
        path = self.directory / "agents" / ident
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if forge._root_released(self.tid, ident):
                result = forge._read_json(path / "result.json")
                self.assertEqual(result["status"], "done", result)
                return result
            time.sleep(.02)
        self.fail("worker did not complete: " + ident + " " + str(forge._read_json(path / "result.json"))
                  + " log=" + forge._tail(path / "supervisor.log", 4000))

    def nodes(self):
        return {n["id"]: n for n in forge_swarm.load(self.directory)["nodes"]}

    def accept(self, ident, choice="integrated"):
        digest = forge._result_identity(self.directory / "agents" / ident / "result.json")
        result = json.loads(forge.agent(self.tid, "disposition", agent_id=ident,
            result_sha256=digest, disposition=choice))
        self.assertEqual(result["disposition"], choice)
        return digest

    def test_nested_writer_executes_and_parent_consumes_without_concurrency(self):
        forge._atomic_json(self.directory / "task.json", {**forge.get(self.tid), "max_active_workers": 1})
        output = forge.agent(self.tid, "spawn", brief="nested-parent")
        ident = re.search(r"agent-[0-9a-f]{8}", output).group()
        result = self.wait_result(ident)
        self.assertEqual((self.root / "serial.txt").read_text(encoding="utf-8"), "PCP")
        self.assertFalse((self.root / "unsafe.txt").exists())
        self.assertEqual(result["result"], "parent consumed exact child result")
        self.assertEqual(result["iters_used"], 6, "wait must not run extra parent model rounds")
        self.assertEqual([r["tool"] for r in result["trace"]], ["edit", "delegate", "delegate", "delegate", "edit"])
        waiting = json.loads(result["trace"][2]["output"])
        child = self.directory / "agents" / waiting["agent_id"]
        self.assertEqual(waiting["result_sha256"], forge._result_identity(child / "result.json"))
        self.assertEqual(forge._agent_snapshot(self.tid, child.name)["current_disposition"]["disposition"], "integrated")
        self.assertEqual(forge._read_json(self.directory / "agents" / ident / "handoff.json")["state"], "returned")
        events = [json.loads(line) for line in (self.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        kinds = [r["kind"] for r in events]
        self.assertLess(kinds.index("worker_yielded"), kinds.index("worker_reacquired"))

    def test_coordinator_parallel_reads_review_repair_accept_dependent(self):
        plan = [{"id": "read-a", "role": "scout", "brief": "read-a"},
                {"id": "read-b", "role": "scout", "brief": "read-b"},
                {"id": "build", "brief": "build", "deps": ["read-a", "read-b"]},
                {"id": "review", "role": "reviewer", "brief": "review", "deps": ["build"]},
                {"id": "package", "brief": "package", "deps": ["review"]}]
        forge.swarm(self.tid, "plan", plan=json.dumps(plan))
        forge.swarm(self.tid, "start")
        for name in ("read-a", "read-b"):
            self.wait_result(self.nodes()[name]["agent_id"])
        forge.swarm(self.tid, "tick")
        self.assertEqual(self.nodes()["build"]["agent_id"], "")  # done != accepted
        for name in ("read-a", "read-b"):
            self.accept(self.nodes()[name]["agent_id"])
        forge.swarm(self.tid, "tick")
        build = self.nodes()["build"]["agent_id"]
        self.wait_result(build)
        old_build = (self.directory / "agents" / build / "result.json").read_bytes()
        build_sha = self.accept(build)
        forge.swarm(self.tid, "tick")
        review = self.nodes()["review"]["agent_id"]
        self.assertEqual(self.wait_result(review)["result"], "NEEDS_CHANGES")
        old_review = (self.directory / "agents" / review / "result.json").read_bytes()
        review_sha = self.accept(review, "deferred")
        forge.swarm(self.tid, "tick")
        self.assertEqual(self.nodes()["package"]["agent_id"], "")
        forge.swarm(self.tid, "checkpoint", plan=json.dumps({"next_action": "repair review finding"}))
        forge.swarm(self.tid, "rework", node_id="build", message="repair", result_sha256=build_sha)
        forge.swarm(self.tid, "tick")
        repaired = self.nodes()["build"]["agent_id"]
        self.assertNotEqual(repaired, build)
        self.wait_result(repaired)
        self.accept(repaired)
        forge.swarm(self.tid, "rework", node_id="review", message="review", result_sha256=review_sha)
        forge.swarm(self.tid, "tick")
        reviewed = self.nodes()["review"]["agent_id"]
        self.assertEqual(self.wait_result(reviewed)["result"], "clear")
        forge.swarm(self.tid, "tick")
        self.assertEqual(self.nodes()["package"]["agent_id"], "")
        self.accept(reviewed)
        forge.swarm(self.tid, "tick")
        self.wait_result(self.nodes()["package"]["agent_id"])
        self.assertEqual((self.root / "package.txt").read_text(encoding="utf-8"), "GOOD")
        self.assertEqual((self.directory / "agents" / build / "result.json").read_bytes(), old_build)
        self.assertEqual((self.directory / "agents" / review / "result.json").read_bytes(), old_review)
        self.assertEqual(len(self.nodes()["build"]["attempts"]), 1)
        self.assertEqual(len(self.nodes()["review"]["attempts"]), 1)
        # Production workers, not a second UI fixture schema, created these
        # records. Exercise the exact owner projection/event/payload readers.
        with self.subTest(component='desktop reader integration'):
            self._assert_desktop_projection(repaired)

    def _assert_desktop_projection(self, repaired):
        import forge_reader
        detail = forge_reader.task_detail(self.root, self.tid)
        self.assertEqual({row["role"] for row in detail["agents"]}, {"scout", "worker", "reviewer"})
        projected = next(row for row in detail["agents"] if row["id"] == repaired)
        self.assertEqual(projected["status"], "done")
        self.assertEqual(projected["current_disposition"], "integrated")
        self.assertEqual(projected["result_ref"]["sha256"], forge._result_identity(
            self.directory / "agents" / repaired / "result.json"))
        events = forge_reader.events(self.root, self.tid, repaired, limit=100)
        self.assertFalse(events["has_more"])
        call = next(row for row in events["events"] if row["kind"] == "tool_start" and row["tool"] == "edit")
        payload = forge_reader.payload(self.root, self.tid, repaired, call["input_ref"]["sha256"])
        self.assertEqual(json.loads(payload["text"])["content"], "GOOD")
        self.assertTrue(any(row["kind"] == "tool_result" for row in events["events"]))


class InProcessTeam(TeamConsumers, unittest.TestCase):
    pass


@unittest.skipUnless(os.environ.get("PRAXIS_TEST_FORGE_PROCESS") == "1", "server-only real-process consumer")
class ProcessTeam(TeamConsumers, unittest.TestCase):
    process_mode = True


@unittest.skipUnless(sys.platform.startswith("linux") and os.environ.get("PRAXIS_TEST_FORGE_PROCESS") == "1",
                     "isolated Linux process recovery")
class ColdWaitRecovery(TeamConsumers, unittest.TestCase):
    process_mode = True

    def test_same_logical_worker_returns_after_parent_and_child_owner_exit(self):
        self.exercise_wait(outage=False)

    def test_unpublished_new_process_identity_cannot_execute_or_relaunch(self):
        self.exercise_wait(outage=True)

    def test_post_spawn_log_close_failure_does_not_publish_terminal(self):
        self.exercise_wait(outage=2)

    def exercise_wait(self, *, outage):
        task = {**forge.get(self.tid), "goal": "STAND-EXACT-WORK", "base_commit": "a" * 40,
                "max_active_workers": 1, "success_criteria": ["STAND-EXACT-CRITERION"]}
        forge._save_task(task)
        started = forge.agent(self.tid, "spawn", brief="cold-parent", sources=[{
            "path": "seed.txt", "sha256": forge._hash(self.root / "seed.txt", full=True)}])
        self.assertRegex(started, r"agent-[0-9a-f]{8}", started)
        aid = re.search(r"agent-[0-9a-f]{8}", started).group()
        units = self.directory / "agents"
        parent = units / aid
        deadline = time.monotonic() + 12
        while not (self.root / "leaf-entered").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((self.root / "leaf-entered").exists(), forge._tail(parent / "supervisor.log", 5000))
        parent_wait = forge._read_json(parent / "handoff.json")
        middle = units / parent_wait["child_unit_id"]
        middle_wait = forge._read_json(middle / "handoff.json")
        leaf_id = middle_wait["child_unit_id"]
        pre = {unit: (unit / "checkpoint.json").read_bytes() for unit in (parent, middle)}
        pids = {}
        for unit in (parent, middle):
            request = forge._read_json(unit / "request.json")
            pids[unit] = request["supervisor_pid"]
            self.assertTrue(forge._pid_alive(pids[unit], request["supervisor_started_at"]))
            os.kill(pids[unit], 9)
        forge._RUNNERS[pids[parent]].wait(timeout=6)
        for unit in (parent, middle):
            self.assertTrue(forge._writer_ended(unit))
        self.assertEqual((self.root / "serial.txt").read_text(), "PM")
        # Different work can settle first; it is not a substitute for the selected leaf.
        other = self.root / "memory" / ".forge" / "tasks" / "code-neighbor" / "agents" / "agent-99999999"
        forge._atomic_json(other / "result.json", {"status": "done", "result": "NEIGHBOR-RESULT"})
        (self.root / "seed.txt").write_text("CHANGED-LIVE-SOURCE", encoding="utf-8")
        (self.root / "release-leaf").touch()
        self.wait_result(leaf_id)
        with (self.root / "new-coordinator.log").open("w+", encoding="utf-8") as log:
            args = [sys.executable, __file__, "--recover-queue", str(self.root), aid]
            env = {**os.environ, "PRAXIS_STAND_UNPUBLISHED": str(int(outage))}
            process = subprocess.run(args, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=16)
            log.seek(0)
            self.assertEqual(process.returncode, 44 if outage is True else 0,
                             log.read() + forge._tail(middle / "supervisor.log", 4000)
                             + forge._tail(parent / "supervisor.log", 4000))
        if outage:
            self.assert_start_gap_held(middle, pre[middle], args)
            return
        self.assertEqual(self.wait_result(aid)["result"], "parent settled")
        self.assertEqual((self.root / "serial.txt").read_text(), "PMLMP")
        self.assertEqual({p.name for p in units.iterdir()}, {aid, middle.name, leaf_id})
        for brief, unit, waiting in (("cold-parent", parent, parent_wait), ("cold-middle", middle, middle_wait)):
            wire = [json.loads(line) for line in (self.root / (brief + ".wire")).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(wire), 6)
            self.assertEqual([row["pid"] for row in wire[:3]], [pids[unit]] * 3)
            self.assertNotEqual(wire[3]["pid"], pids[unit])
            self.assertEqual(wire[0]["system"], wire[3]["system"])
            self.assertEqual(wire[0]["messages"][0], wire[3]["messages"][0])
            self.assertIn("STAND-EXACT-WORK", wire[3]["system"])
            self.assertIn(unit.name, wire[3]["system"])
            self.assertNotIn("NEIGHBOR-RESULT", json.dumps(wire[3]))
            state = json.loads(pre[unit]); call = state["pending"]["calls"][0]
            event_rows = [json.loads(line) for line in (unit / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(sum(r.get("call_id") == call["id"] and r["kind"] == "tool_dispatch" for r in event_rows), 1)
            recovered, = [r for r in event_rows if r.get("recovered_from") == "forge_child_result"]
            self.assertEqual((recovered["generation"], recovered["call_id"]), (state["generation"], call["id"]))
            exact = wire[3]["messages"][-1]["content"][0]
            self.assertEqual(exact["tool_use_id"], call["id"])
            self.assertEqual(json.loads(exact["content"])["agent_id"], waiting["child_unit_id"])
        self.assertIn("consumer source", (self.root / "cold-parent.wire").read_text(encoding="utf-8"))
        self.assertNotIn("CHANGED-LIVE-SOURCE", (self.root / "cold-parent.wire").read_text(encoding="utf-8"))
        if os.environ.get("PRAXIS_STAND_ROLLBACK") == "1":
            self.rehearse_post_drain_rollback(units)

    def assert_start_gap_held(self, middle, checkpoint, args):
        deadline = time.monotonic() + 6
        while "Continuation refused" not in forge._tail(middle / "supervisor.log"):
            self.assertFalse((middle / "result.json").exists(), "unowned process published a false terminal outcome")
            self.assertLess(time.monotonic(), deadline, "orphaned launch did not reach the ownership fence")
            time.sleep(.02)
        state = forge._unit_state(middle)
        self.assertEqual(state["status"], "unknown", state.get("liveness"))
        self.assertTrue(any(marker in state["liveness"] for marker in
                            ("launch or acceptance is unconfirmed", "wait restart refused")), state)
        self.assertFalse((middle / "result.json").exists())
        self.assertEqual((middle / "checkpoint.json").read_bytes(), checkpoint)
        cmd = [*args[:2], "--inspect-held-queue", *args[3:]]
        env = {**os.environ, 'PRAXIS_STAND_EXPECT_PAUSE': str(int((middle.parent.parent / 'control.json').exists()))}
        probe = subprocess.run(cmd, capture_output=True, timeout=5, env=env)
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
        self.assertEqual((self.root / "serial.txt").read_text(), "PML")
        self.assertEqual(len((self.root / "cold-middle.wire").read_text(encoding="utf-8").splitlines()), 3)

    def rehearse_post_drain_rollback(self, units):
        before = {}
        for directory in units.iterdir():
            self.assertTrue(forge._writer_ended(directory), directory.name)
            self.assertFalse((directory / "resume.json").exists())
            for name in ("events.jsonl", "checkpoint.json", "result.json", "request.json"):
                path = directory / name
                before[path] = path.read_bytes()
        old = subprocess.run([sys.executable, __file__, "--older-probe", str(self.root)],
                             env={**os.environ, "PRAXIS_STAND_OLD": "1"}, capture_output=True, timeout=12)
        self.assertEqual(old.returncode, 0, old.stdout + old.stderr)
        self.assertIn(b"POST_DRAIN_OLD_CONSUMER_PASS", old.stdout)
        self.assertEqual((self.root / "legacy-probe.txt").read_text(), "older worker ran")
        self.assertEqual((self.root / "serial.txt").read_text(), "PMLMP")
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    # Avoid collecting the inherited baseline witnesses a third time.
    test_coordinator_parallel_reads_review_repair_accept_dependent = None
    test_nested_writer_executes_and_parent_consumes_without_concurrency = None


def recover_queue(root: Path, parent: str, *, held=False) -> int:
    forge.BASE = root
    forge.TASKS_DIR = root / "memory" / ".forge" / "tasks"
    with worker_environment(), contextlib.ExitStack() as stack:
        if held:
            if os.environ.get('PRAXIS_STAND_EXPECT_PAUSE') == '1':
                assert forge.control("code-12345678", "resume")["status"] == "resumed"
            for _ in range(3):
                assert forge.tick_queued() == []
            return 0
        if os.environ.get("PRAXIS_STAND_UNPUBLISHED") == "1":
            spawn = forge._spawn_runner
            def lose_coordinator(*call):
                spawn(*call)
                # Hold the same mutex as the launch while injecting Pause.
                # An unpublished child must not turn it into its own result.
                forge._atomic_json(forge._task_dir("code-12345678") / "control.json", {"action": "pause"})
                os._exit(44)  # physical spawn happened; new PID was not published
            stack.enter_context(mock.patch.object(forge, "_spawn_runner", lose_coordinator))
        if os.environ.get("PRAXIS_STAND_UNPUBLISHED") == "2":
            open_path = Path.open
            class CloseFails:
                def __init__(self, handle):
                    self.handle = handle
                def fileno(self):
                    return self.handle.fileno()
                def close(self):
                    self.handle.close()
                    raise OSError("injected close after Popen")
            def open_once(path, *args, **kwargs):
                handle = open_path(path, *args, **kwargs)
                if path.name == 'supervisor.log' and args[:1] == ('a',):
                    return CloseFails(handle)
                return handle
            with mock.patch.object(Path, 'open', open_once):
                forge.tick_queued('code-12345678')
            processes = list(forge._RUNNERS.values())
            assert len(processes) == 1, processes
            assert processes[0].wait(timeout=7) == 2
            return 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for row in forge.tick_queued():
                print(row, flush=True)
            if forge._root_released("code-12345678", parent):
                forge.tick_queued()  # duplicate delivery must not relaunch
                for process in list(forge._RUNNERS.values()):
                    process.wait(timeout=5)
                return 0
            time.sleep(.02)
    return 1


def older_probe(root: Path) -> int:
    assert Path(forge.__file__).parent.name == "_previous"
    forge.BASE = root
    forge.TASKS_DIR = root / "memory" / ".forge" / "tasks"
    task_id = "code-12345678"
    with worker_environment():
        for _ in range(3):
            assert forge.tick_queued() == []
        assert all(row["status"] == "done" for row in forge._units(task_id, "agents"))
        assert (root / "serial.txt").read_text() == "PMLMP"
        other = forge.agent(task_id, "spawn", brief="stand-legacy-normal")
        ident = re.search(r"agent-[0-9a-f]{8}", other).group()
        pid = forge._read_json(forge._unit_dir(task_id, "agents", ident) / "request.json")["supervisor_pid"]
        assert forge._RUNNERS[pid].wait(timeout=7) == 0
        assert forge._root_released(task_id, ident)
        assert forge.tick_queued() == []
        print("POST_DRAIN_OLD_CONSUMER_PASS", flush=True)
    return 0


class Bootstrap(unittest.TestCase):
    def test_process_bootstrap_selects_canonical_tasks_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / 'tasks/code-12345678'
            request = task / 'agents/agent-12345678/request.json'
            request.parent.mkdir(parents=True)
            request.write_text(json.dumps({'root': str(root)}), encoding='utf-8')
            (task / 'task.json').write_text(json.dumps({'id': 'code-12345678', 'status': 'active'}), encoding='utf-8')
            def run(path):
                self.assertEqual(forge._task_dir('code-12345678'), task)
                self.assertEqual(forge.get('code-12345678')['status'], 'active')
                return 0
            with mock.patch.object(forge, 'BASE', forge.BASE), mock.patch.object(forge, 'TASKS_DIR', forge.TASKS_DIR), mock.patch.object(forge_worker, 'run', run):
                self.assertEqual(process_entry(request), 0)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--request":
        raise SystemExit(process_entry(Path(sys.argv[2])))
    if len(sys.argv) == 4 and sys.argv[1] in {"--recover-queue", "--inspect-held-queue"}:
        raise SystemExit(recover_queue(Path(sys.argv[2]), sys.argv[3], held=sys.argv[1] == "--inspect-held-queue"))
    if len(sys.argv) == 3 and sys.argv[1] == "--older-probe":
        raise SystemExit(older_probe(Path(sys.argv[2])))
    unittest.main()
