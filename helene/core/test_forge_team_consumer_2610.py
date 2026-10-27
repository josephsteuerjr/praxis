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
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

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


@contextlib.contextmanager
def worker_environment():
    original_inspect = forge.inspect
    def inspect(tid, action, **kwargs):
        return "synthetic diff (no Git invocation)" if action == "diff" else original_inspect(tid, action, **kwargs)
    workshop = types.ModuleType("workshop")
    workshop.mutation_lock = lambda: contextlib.nullcontext(types.SimpleNamespace(lost_to_takeover=False))
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(forge_worker, "_system", lambda req: json.dumps(req)))
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
        app = next((parent / name for parent in CORE.parents for name in ('desk', 'app')
                    if (parent / name / 'deskd/forge_reader.py').is_file()), None)
        if app is None:
            self.skipTest('Desktop reader integration requires the app; core consumer assertions ran above')
        with mock.patch.object(sys, 'path', [str(app), *sys.path]):
            from deskd import forge_reader
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
    unittest.main()
