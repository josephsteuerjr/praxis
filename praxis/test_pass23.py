"""PASS 23 Forge: task place, correct edits, processes and coding-agent wiring."""

from __future__ import annotations

import contextlib
import os
import hashlib
import re
import signal
import threading
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import forge


class ForgeCase(unittest.TestCase):
    def test_agent_spawn_preserves_positional_iteration_budget(self):
        task_id = self.start()
        with mock.patch.object(forge, "_spawn_runner", return_value=424242):
            out = forge.agent(task_id, "spawn", "", "map", "scout", 3, 5000)
        agent_id = re.search(r"(agent-[0-9a-f]+)", out).group(1)
        req = forge._read_json(forge._unit_dir(task_id, "agents", agent_id) / "request.json")
        self.assertEqual(req["max_iters"], 3)
        self.assertEqual(req["model"], "")

    def test_coding_agent_wrapper_preserves_positional_iteration_budget(self):
        import agent as main_agent
        with mock.patch.object(forge, "agent", return_value="spawned") as spawn:
            self.assertEqual(main_agent.tool_coding_agent(
                "code-test", "spawn", "", "map", "scout", 3, 5000,
                model="gpt-5.6-terra"), "spawned")
        self.assertEqual(spawn.call_args.kwargs["max_iters"], 3)
        self.assertEqual(spawn.call_args.kwargs["model"], "gpt-5.6-terra")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        (self.project / "README.md").write_text("# demo\n", encoding="utf-8")
        (self.project / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-q", "-m", "init"], check=True)
        self.old_base, self.old_state, self.old_tasks = forge.BASE, forge.STATE_DIR, forge.TASKS_DIR
        forge.BASE = self.base
        forge.STATE_DIR = self.base / "state"
        forge.TASKS_DIR = forge.STATE_DIR / "tasks"

    def tearDown(self):
        forge.BASE, forge.STATE_DIR, forge.TASKS_DIR = self.old_base, self.old_state, self.old_tasks
        self.tmp.cleanup()

    def start(self, isolation: str = "direct") -> str:
        out = forge.start("make answer exact", target=str(self.project), isolation=isolation)
        m = re.search(r"coding-задача (code-[0-9a-f]+)", out)
        self.assertIsNotNone(m, out)
        return m.group(1)

    @staticmethod
    def _cleanup_group_fixture(leader, child_fd: int) -> None:
        # Census None must not silently skip cleanup. pidfd binds the signal
        # to the child we observed even if its numeric PID was recycled.
        if child_fd >= 0:
            try:
                with contextlib.suppress(ProcessLookupError):
                    signal.pidfd_send_signal(child_fd, signal.SIGKILL)
            finally:
                os.close(child_fd)
        if leader.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(leader.pid, signal.SIGKILL)
        leader.wait(timeout=5)

    @staticmethod
    def _pin_group_fixture_child(pid: int, ready: Path) -> int:
        birth = forge._proc_started_at(pid)
        if not birth or str(ready) not in forge._proc_cmdline(pid):
            raise AssertionError("fixture child identity unavailable")
        fd = os.pidfd_open(pid)
        if (forge._proc_started_at(pid) != birth
                or str(ready) not in forge._proc_cmdline(pid)):
            os.close(fd)  # never send to the newly pinned foreign process
            raise AssertionError("fixture child changed before identity pin")
        return fd

    @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "pidfd_open")
                         and hasattr(signal, "pidfd_send_signal"), "Linux pidfd required")
    def test_recycled_fixture_child_fd_is_closed_without_signal(self):
        ready = self.base / "unique-child-marker"
        pinned_fd = -1
        leader = mock.Mock()
        leader.poll.return_value = 0
        with mock.patch.object(forge, "_proc_started_at", side_effect=["old", "new"]), \
             mock.patch.object(forge, "_proc_cmdline", return_value=str(ready)), \
             mock.patch.object(os, "pidfd_open", return_value=81), \
             mock.patch.object(os, "close") as close_fd, \
             mock.patch.object(signal, "pidfd_send_signal") as signal_child:
            with self.assertRaisesRegex(AssertionError, "changed before identity pin"):
                try:
                    pinned_fd = self._pin_group_fixture_child(1234, ready)
                finally:
                    self._cleanup_group_fixture(leader, pinned_fd)
            close_fd.assert_called_once_with(81)
            signal_child.assert_not_called()

    def test_start_orients_on_real_place_and_persists(self):
        task_id = self.start()
        task = forge.get(task_id)
        self.assertEqual(task["root"], str(self.project.resolve()))
        self.assertEqual(task["status"], "active")
        orientation = forge.inspect(task_id, "orientation")
        self.assertIn("Python 1", orientation)
        self.assertIn("README.md", orientation)
        self.assertIn("git status", orientation)
        self.assertIn(task_id, forge.list_tasks())

    def test_hash_aware_replace_rejects_stale_worker(self):
        task_id = self.start()
        read = forge.inspect(task_id, "read", path="app.py")
        old_hash = re.search(r"sha256=([0-9a-f]+)", read).group(1)
        out = forge.edit(task_id, "replace", path="app.py", old="return 41", new="return 42",
                         expected_sha256=old_hash)
        self.assertIn("sha256", out)
        stale = forge.edit(task_id, "replace", path="app.py", old="return 42", new="return 43",
                           expected_sha256=old_hash)
        self.assertIn("Конфликт версии", stale)
        self.assertIn("return 42", (self.project / "app.py").read_text(encoding="utf-8"))

    def test_concurrent_workers_serialize_mutation_and_one_rebases(self):
        task_id = self.start()
        read = forge.inspect(task_id, "read", path="app.py")
        old_hash = re.search(r"sha256=([0-9a-f]+)", read).group(1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(forge.edit, task_id, "replace", path="app.py", old="return 41",
                            new=f"return {value}", expected_sha256=old_hash)
                for value in (42, 43)
            ]
        results = [f.result() for f in futures]
        self.assertEqual(sum("Конфликт версии" in x for x in results), 1, results)
        self.assertEqual(sum("sha256" in x and "Конфликт" not in x for x in results), 1, results)
        final = (self.project / "app.py").read_text(encoding="utf-8")
        self.assertTrue("return 42" in final or "return 43" in final)

    def test_checked_multifile_patch_and_diff(self):
        task_id = self.start()
        patch = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 41
+    return 42
diff --git a/test_app.py b/test_app.py
new file mode 100644
--- /dev/null
+++ b/test_app.py
@@ -0,0 +1,5 @@
+import unittest
+import app
+class T(unittest.TestCase):
+    def test_answer(self): self.assertEqual(app.answer(), 42)
+
"""
        out = forge.edit(task_id, "patch", patch=patch)
        self.assertIn("Patch применён", out)
        diff = forge.inspect(task_id, "diff")
        self.assertIn("return 42", diff)
        self.assertIn("test_app.py", diff)

    def test_patch_and_workshop_edit_serialize_on_same_project(self):
        import workshop

        workspace = self.base / "workspace"
        workspace.mkdir()
        moved = workspace / "project"
        self.project.rename(moved)
        self.project = moved
        before = hashlib.sha256((moved / "app.py").read_bytes()).hexdigest()
        patch_text = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 41
+    return 42
"""
        barrier = threading.Barrier(2)
        publish = workshop._publish_bytes
        git = forge._run

        def wait_before_workshop_publish(*args, **kwargs):
            barrier.wait(timeout=5)
            return publish(*args, **kwargs)

        def wait_during_patch_check(cmd, **kwargs):
            if cmd[:3] == ["git", "apply", "--check"]:
                barrier.wait(timeout=5)  # Forge holds the common lock already
            return git(cmd, **kwargs)

        with mock.patch.object(workshop, "BASE", self.base), \
             mock.patch.object(workshop, "REPO", self.base), \
             mock.patch.object(workshop, "PROJECTS", workspace / "projects"), \
             mock.patch.object(workshop, "_publish_bytes", wait_before_workshop_publish), \
             mock.patch.object(forge, "_run", wait_during_patch_check):
            task_id = self.start()
            with ThreadPoolExecutor(max_workers=2) as pool:
                edit = pool.submit(workshop.fs_edit_result, "workspace/project/app.py",
                                   "return 41", "return 43", expected_sha256=before)
                patch = pool.submit(forge.edit, task_id, "patch", patch=patch_text)
                fs_result, patch_result = edit.result(timeout=15), patch.result(timeout=15)
        self.assertIn("Patch применён", patch_result)
        self.assertEqual(fs_result["status"], "refused")
        self.assertIn("return 42", (moved / "app.py").read_text(encoding="utf-8"))

    @unittest.skipUnless(sys.platform.startswith("linux"), "POSIX process group requires Linux")
    def test_group_census_retries_uncertainty_without_claiming_an_unknown_stop(self):
        cases = (
            ("TERM recovers", [None, True, False], [0, 0, .1, 2], "остановлен", 1),
            ("KILL recovers", [True, True, None, False], [0, 0, 2, 2, 2, 2.1], "остановлен", 2),
            ("KILL remains unknown", [True, True, None, None], [0, 0, 2, 2, 2, 4],
             "не остановлен: состав группы после KILL неизвестен", 2),
        )
        for label, observations, clock, expected, signals in cases:
            with self.subTest(label=label):
                with mock.patch.object(forge, "_kill_identity", return_value=""), \
                     mock.patch.object(forge, "_group_live_members", side_effect=observations), \
                     mock.patch.object(forge.time, "monotonic", side_effect=clock), \
                     mock.patch.object(forge.time, "sleep"), \
                     mock.patch.object(forge.os, "killpg") as signal_group:
                    self.assertEqual(forge._kill_tree(1234, "born"), expected)
                    self.assertEqual(signal_group.call_count, signals)

    @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "pidfd_open")
                         and hasattr(signal, "pidfd_send_signal"), "Linux pidfd cleanup required")
    def test_group_stop_kills_foreground_child_after_leader_exits(self):
        ready = self.base / "child-ready"
        pid_file = self.base / "child-pid"
        script = self.base / "group-parent.py"
        child_code = (
            "import signal,time,sys; from pathlib import Path; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "Path(sys.argv[1]).write_text('ready'); time.sleep(60)"
        )
        script.write_text(
            "import signal,subprocess,sys,time\n"
            "from pathlib import Path\n"
            f"child = {child_code!r}\n"
            "proc = subprocess.Popen([sys.executable,'-c',child,sys.argv[1]], "
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            "while not Path(sys.argv[1]).exists(): time.sleep(.01)\n"
            "Path(sys.argv[2]).write_text(str(proc.pid))\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "time.sleep(60)\n", encoding="utf-8")
        leader = subprocess.Popen([sys.executable, str(script), str(ready), str(pid_file)],
                                  start_new_session=True, cwd=self.base,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child_fd = -1
        try:
            deadline = time.monotonic() + 4
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pid_file.exists(), "fixture child failed to start")
            child_fd = self._pin_group_fixture_child(int(pid_file.read_text()), ready)
            birth = forge._proc_started_at(leader.pid)
            self.assertTrue(birth)
            self.assertEqual(forge._group_live_members(leader.pid), True)
            self.assertEqual(forge._kill_tree(leader.pid, birth), "остановлен")
            self.assertEqual(forge._group_live_members(leader.pid), False)
        finally:
            self._cleanup_group_fixture(leader, child_fd)

    @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "pidfd_open")
                         and hasattr(signal, "pidfd_send_signal"), "Linux pidfd cleanup required")
    def test_terminal_worker_result_does_not_spare_its_living_group(self):
        ready = self.base / "child-ready"
        pid_file = self.base / "child-pid"
        release = self.base / "release-parent"
        child_code = (
            "import signal,time,sys; from pathlib import Path; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "Path(sys.argv[1]).write_text('ready'); time.sleep(60)"
        )
        script = self.base / "exiting-worker.py"
        script.write_text(
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            f"child = {child_code!r}\n"
            "proc = subprocess.Popen([sys.executable,'-c',child,sys.argv[1]], "
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            "while not Path(sys.argv[1]).exists(): time.sleep(.01)\n"
            "Path(sys.argv[2]).write_text(str(proc.pid))\n"
            "while not Path(sys.argv[3]).exists(): time.sleep(.01)\n",
            encoding="utf-8")
        leader = subprocess.Popen([sys.executable, str(script), str(ready), str(pid_file),
                                   str(release)], start_new_session=True, cwd=self.base,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child_fd = -1
        try:
            deadline = time.monotonic() + 4
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pid_file.exists())
            child_fd = self._pin_group_fixture_child(int(pid_file.read_text()), ready)
            birth = forge._proc_started_at(leader.pid)
            self.assertTrue(birth)
            release.write_text("go", encoding="utf-8")
            leader.wait(timeout=5)
            self.assertTrue(forge._group_live_members(leader.pid),
                            "child must survive after its leader exits")
            task_id = self.start()
            unit_id = "agent-deadcafe"
            directory = forge._unit_dir(task_id, "agents", unit_id)
            forge._atomic_json(directory / "request.json", {
                "id": unit_id, "task_id": task_id, "role": "worker", "created": forge._now(),
                "supervisor_pid": leader.pid, "supervisor_started_at": birth,
            })
            forge._atomic_json(directory / "result.json", {"status": "done", "result": "kept"})
            original = (directory / "result.json").read_bytes()
            response = forge.agent(task_id, "stop", agent_id=unit_id)
            self.assertIn("supervisor group stop: остановлен", response)
            self.assertFalse(forge._group_live_members(leader.pid))
            self.assertEqual((directory / "result.json").read_bytes(), original)
        finally:
            self._cleanup_group_fixture(leader, child_fd)

    def test_foreground_run_keeps_evidence(self):
        task_id = self.start()
        out = forge.run(task_id, f'"{sys.executable}" -c "print(6*7)"', timeout=20)
        self.assertIn("42", out)
        self.assertIn("[forge: ok", out)
        history = forge.inspect(task_id, "history", end=10)
        self.assertIn('"kind": "command"', history)

    def test_background_process_is_nonblocking_and_pollable(self):
        task_id = self.start()
        out = forge.process(task_id, "start",
                            command=f'"{sys.executable}" -c "print(\'FORGE_PROCESS_OK\')"',
                            name="probe")
        proc_id = re.search(r"(proc-[0-9a-f]+)", out).group(1)
        self.assertIn("Стартовал", out)
        polled = ""
        for _ in range(60):
            polled = forge.process(task_id, "poll", process_id=proc_id)
            if "FORGE_PROCESS_OK" in polled and '"status": "done"' in polled:
                break
            time.sleep(.05)
        self.assertIn("FORGE_PROCESS_OK", polled)
        self.assertIn('"status": "done"', polled)

    def test_background_timeout_reports_distinct_state(self):
        task_id = self.start()
        out = forge.process(
            task_id, "start",
            command=f'"{sys.executable}" -c "import time; print(\'BEGIN\', flush=True); time.sleep(20)"',
            name="timeout-probe", timeout=1)
        proc_id = re.search(r"(proc-[0-9a-f]+)", out).group(1)
        polled = ""
        for _ in range(80):
            polled = forge.process(task_id, "poll", process_id=proc_id)
            if '"status": "timed_out"' in polled:
                break
            time.sleep(.05)
        self.assertIn("BEGIN", polled)
        self.assertIn('"status": "timed_out"', polled)

    def test_agent_spawn_writes_independent_request(self):
        task_id = self.start()
        with mock.patch.object(forge, "_spawn_runner", return_value=424242):
            out = forge.agent(task_id, "spawn", brief="map the test seams", role="scout")
        agent_id = re.search(r"(agent-[0-9a-f]+)", out).group(1)
        req = forge._read_json(forge._unit_dir(task_id, "agents", agent_id) / "request.json")
        self.assertEqual(req["role"], "scout")
        self.assertEqual(req["root"], str(self.project.resolve()))
        self.assertEqual(req["brief"], "map the test seams")
        self.assertEqual(req["model"], "")

    def test_agent_spawn_persists_requested_model(self):
        task_id = self.start()
        with mock.patch.object(forge, "_spawn_runner", return_value=424242):
            out = forge.agent(task_id, "spawn", brief="map with terra", role="scout",
                              model="gpt-5.6-terra")
        agent_id = re.search(r"(agent-[0-9a-f]+)", out).group(1)
        req = forge._read_json(forge._unit_dir(task_id, "agents", agent_id) / "request.json")
        self.assertEqual(req["model"], "gpt-5.6-terra")

    def test_worker_executes_real_tool_loop_and_writes_result(self):
        import forge_worker
        import llm

        task_id = self.start()
        agent_id = "agent-testloop"
        request_path = forge._unit_dir(task_id, "agents", agent_id) / "request.json"
        request = {"id": agent_id, "task_id": task_id, "goal": "make answer exact",
                   "root": str(self.project), "proposal_id": "", "role": "worker",
                   "brief": "change 41 to 42", "max_iters": 4, "created": forge._now()}
        forge._atomic_json(request_path, request)
        tool_call = llm.LLMResponse(
            blocks=[{"type": "tool_use", "id": "call-1", "name": "edit",
                     "input": {"action": "replace", "path": "app.py",
                               "old": "return 41", "new": "return 42"}}],
            stop_reason="tool_use", model="fake-coder")
        final = llm.LLMResponse(text="Changed app.py and verified the resulting diff.",
                                stop_reason="end_turn", model="fake-coder")
        with mock.patch.object(forge_worker.llm, "chat", side_effect=[tool_call, final]) as chat:
            code = forge_worker.run(request_path)
        self.assertEqual(code, 0)
        self.assertTrue(all(call.kwargs.get("model") is None for call in chat.call_args_list))
        self.assertIn("return 42", (self.project / "app.py").read_text(encoding="utf-8"))
        result = forge._read_json(request_path.parent / "result.json")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["model_requested"], "")
        self.assertEqual(result["model"], "fake-coder")
        self.assertIn("Changed app.py", result["result"])

    def test_worker_passes_model_override_and_records_actual_model(self):
        import forge_worker
        import llm

        task_id = self.start()
        agent_id = "agent-model-override"
        request_path = forge._unit_dir(task_id, "agents", agent_id) / "request.json"
        request = {"id": agent_id, "task_id": task_id, "goal": "inspect",
                   "root": str(self.project), "proposal_id": "", "role": "scout",
                   "brief": "inspect without edits", "model": "gpt-5.6-terra",
                   "max_iters": 2, "created": forge._now()}
        forge._atomic_json(request_path, request)
        final = llm.LLMResponse(text="Mapped the seams.", stop_reason="end_turn",
                                model="gpt-5.6-terra")
        with mock.patch.object(forge_worker.llm, "chat", return_value=final) as chat:
            code = forge_worker.run(request_path)
        self.assertEqual(code, 0)
        self.assertEqual(chat.call_args.kwargs["model"], "gpt-5.6-terra")
        result = forge._read_json(request_path.parent / "result.json")
        self.assertEqual(result["model_requested"], "gpt-5.6-terra")
        self.assertEqual(result["model"], "gpt-5.6-terra")
        self.assertEqual(result["status"], "done")

    def test_worker_retries_torn_iteration_before_dispatching_any_tool(self):
        import forge_worker
        import llm

        task_id = self.start()
        agent_id = "agent-retry-torn"
        request_path = forge._unit_dir(task_id, "agents", agent_id) / "request.json"
        request = {"id": agent_id, "task_id": task_id, "goal": "make answer exact",
                   "root": str(self.project), "proposal_id": "", "role": "worker",
                   "brief": "change 41 to 42", "max_iters": 4, "created": forge._now()}
        forge._atomic_json(request_path, request)
        partial = llm.LLMResponse(
            text="I was about to edit",
            blocks=[{"type": "tool_use", "id": "partial-call", "name": "edit",
                     "input": {"action": "replace", "path": "app.py",
                               "old": "return 41", "new": "return 99"}}],
            stop_reason="error", model="fake-coder")
        torn = llm.TornStreamError("incomplete chunked read", partial=partial)
        tool_call = llm.LLMResponse(
            blocks=[{"type": "tool_use", "id": "call-1", "name": "edit",
                     "input": {"action": "replace", "path": "app.py",
                               "old": "return 41", "new": "return 42"}}],
            stop_reason="tool_use", model="fake-coder")
        final = llm.LLMResponse(text="Changed once after transport recovery.",
                                stop_reason="end_turn", model="fake-coder")
        with mock.patch.dict(os.environ, {
                "PRAXIS_FORGE_TRANSPORT_RETRIES": "1",
                "PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC": "0"}), \
                mock.patch.object(forge_worker.llm, "chat",
                                  side_effect=[torn, tool_call, final]) as chat:
            code = forge_worker.run(request_path)
        self.assertEqual(code, 0)
        self.assertEqual(chat.call_count, 3)
        file_text = (self.project / "app.py").read_text(encoding="utf-8")
        self.assertIn("return 42", file_text)
        self.assertNotIn("return 99", file_text,
                         "tool-looking partial from TornStreamError must never be dispatched")
        result = forge._read_json(request_path.parent / "result.json")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["tool_calls"], 1,
                         "retry before response must not dispatch either partial or final tool twice")
        first_system = chat.call_args_list[0].kwargs["system"]
        retry_system = chat.call_args_list[1].kwargs["system"]
        self.assertEqual(first_system, retry_system,
                         "transport retry must reuse the exact logical-turn frame")

    def test_worker_retries_the_no_tools_final_summary(self):
        import forge_worker
        import llm

        task_id = self.start()
        agent_id = "agent-summary-retry"
        request_path = forge._unit_dir(task_id, "agents", agent_id) / "request.json"
        request = {"id": agent_id, "task_id": task_id, "goal": "make answer exact",
                   "root": str(self.project), "proposal_id": "", "role": "worker",
                   "brief": "change 41 to 42", "max_iters": 1, "created": forge._now()}
        forge._atomic_json(request_path, request)
        tool_call = llm.LLMResponse(
            blocks=[{"type": "tool_use", "id": "call-1", "name": "edit",
                     "input": {"action": "replace", "path": "app.py",
                               "old": "return 41", "new": "return 42"}}],
            stop_reason="tool_use", model="fake-coder")
        recovered = llm.LLMResponse(text="Summary recovered after the transport break.",
                                    stop_reason="end_turn", model="fake-coder")
        with mock.patch.dict(os.environ, {
                "PRAXIS_FORGE_TRANSPORT_RETRIES": "1",
                "PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC": "0"}), \
                mock.patch.object(forge_worker.llm, "chat", side_effect=[
                    tool_call, llm.BrokenChannelError("summary EOF"), recovered,
                ]) as chat:
            code = forge_worker.run(request_path)
        self.assertEqual(code, 0)
        self.assertEqual(chat.call_count, 3)
        self.assertIsNone(chat.call_args_list[1].kwargs["tools"])
        self.assertIsNone(chat.call_args_list[2].kwargs["tools"])
        result = forge._read_json(request_path.parent / "result.json")
        self.assertEqual(result["status"], "stalled")
        self.assertEqual(result["tool_calls"], 1)
        self.assertIn("Summary recovered", result["result"])

    def test_transport_retry_environment_is_bounded_and_finite(self):
        import forge_worker

        with mock.patch.dict(os.environ, {
                "PRAXIS_FORGE_TRANSPORT_RETRIES": "999999",
                "PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC": "nan"}):
            self.assertEqual(forge_worker._bounded_env_int(
                "PRAXIS_FORGE_TRANSPORT_RETRIES", 2, low=0, high=8), 8)
            self.assertEqual(forge_worker._bounded_env_float(
                "PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC", 2.0,
                low=0.0, high=60.0), 2.0)

    def test_worker_records_final_transport_error_after_retry_budget(self):
        import forge_worker
        import llm

        task_id = self.start()
        agent_id = "agent-retry-exhausted"
        request_path = forge._unit_dir(task_id, "agents", agent_id) / "request.json"
        request = {"id": agent_id, "task_id": task_id, "goal": "inspect",
                   "root": str(self.project), "proposal_id": "", "role": "scout",
                   "brief": "inspect without edits", "max_iters": 2, "created": forge._now()}
        forge._atomic_json(request_path, request)
        with mock.patch.dict(os.environ, {
                "PRAXIS_FORGE_TRANSPORT_RETRIES": "2",
                "PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC": "0"}), \
                mock.patch.object(forge_worker.llm, "chat",
                                  side_effect=llm.BrokenChannelError("upstream EOF")) as chat:
            code = forge_worker.run(request_path)
        self.assertEqual(code, 2)
        self.assertEqual(chat.call_count, 3)
        result = forge._read_json(request_path.parent / "result.json")
        self.assertEqual(result["status"], "error")
        self.assertIn("BrokenChannelError: upstream EOF", result["error"])
        self.assertEqual(result["trace"], [])

    def test_worker_does_not_retry_non_transport_exceptions(self):
        import forge_worker

        task_id = self.start()
        agent_id = "agent-no-retry-bug"
        request_path = forge._unit_dir(task_id, "agents", agent_id) / "request.json"
        request = {"id": agent_id, "task_id": task_id, "goal": "inspect",
                   "root": str(self.project), "proposal_id": "", "role": "scout",
                   "brief": "inspect without edits", "max_iters": 2, "created": forge._now()}
        forge._atomic_json(request_path, request)
        with mock.patch.dict(os.environ, {
                "PRAXIS_FORGE_TRANSPORT_RETRIES": "8",
                "PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC": "0"}), \
                mock.patch.object(forge_worker.llm, "chat",
                                  side_effect=RuntimeError("collector bug")) as chat:
            code = forge_worker.run(request_path)
        self.assertEqual(code, 2)
        self.assertEqual(chat.call_count, 1)
        result = forge._read_json(request_path.parent / "result.json")
        self.assertIn("RuntimeError: collector bug", result["error"])

    def test_finish_direct_task_builds_evidence_bundle(self):
        task_id = self.start()
        forge.edit(task_id, "replace", path="app.py", old="41", new="42")
        out = forge.finish(task_id, checked="python smoke", submit=False)
        self.assertIn(f"{task_id}: done", out)
        self.assertEqual(forge.get(task_id)["status"], "done")

    def test_generic_worktree_finishes_by_real_integration(self):
        task_id = self.start(isolation="worktree")
        task = forge.get(task_id)
        worktree = Path(task["worktree_root"])
        self.assertNotEqual(Path(task["root"]), self.project.resolve())
        forge.edit(task_id, "replace", path="app.py", old="41", new="42")
        out = forge.finish(task_id, title="make answer 42", checked="focused test", submit=True)
        self.assertIn("Интегрировано", out)
        self.assertIn("return 42", (self.project / "app.py").read_text(encoding="utf-8"))
        self.assertFalse(worktree.exists())


class ForgeAgentSurfaceCase(unittest.TestCase):
    def test_owner_surface_contains_complete_forge_control_plane(self):
        import agent
        names = {tool["name"] for tool in agent.OWNER_TOOLS}
        expected = {"coding_session", "coding_inspect", "coding_edit", "coding_run",
                    "coding_process", "coding_agent", "coding_checkpoint"}
        self.assertTrue(expected <= names)
        self.assertTrue(expected <= set(agent.TOOL_IMPL))

    def test_worker_system_declares_stable_role_cache_address(self):
        """Fresh Forge contexts do not use agent.build_system_parts, but still need cache affinity."""
        import forge_worker
        import llm

        addresses = {}
        with mock.patch.object(forge_worker.forge, "inspect", return_value="orientation"), \
                mock.patch.dict(os.environ, {"PRAXIS_CACHE_KEY": "on"}):
            for role in ("scout", "worker", "reviewer"):
                request = {"id": f"agent-{role}", "role": role, "task_id": "task-a",
                           "goal": "goal", "brief": "brief"}
                system = forge_worker._system(request)
                self.assertTrue(system.startswith(f"audience_key=forge_{role}\ncache_scope="))
                addresses[role] = llm.cache_address("gpt-5.6-sol", system)
                self.assertIn(f":forge_{role}:-:", addresses[role])
                # Динамическая проза меняется между ходами, адрес одного юнита — нет.
                request["brief"] = "a different next turn"
                self.assertEqual(addresses[role], llm.cache_address(
                    "gpt-5.6-sol", forge_worker._system(request)))
        self.assertEqual(len(set(addresses.values())), 3)

        # Одинаковая роль в соседних задачах/юнитах не должна склеивать контексты.
        with mock.patch.object(forge_worker.forge, "inspect", return_value="orientation"):
            first = forge_worker._system({"id": "agent-one", "role": "scout",
                                          "task_id": "task-a", "goal": "g", "brief": "b"})
            second = forge_worker._system({"id": "agent-two", "role": "scout",
                                           "task_id": "task-b", "goal": "g", "brief": "b"})
        self.assertNotEqual(llm.cache_address("gpt-5.6-sol", first),
                            llm.cache_address("gpt-5.6-sol", second))

        # Технический scope не становится инъекцией в обычном разговорном кадре.
        owner_frame = "private owner channel\n"
        self.assertEqual(llm.cache_address("gpt-5.6-sol", owner_frame),
                         llm.cache_address("gpt-5.6-sol",
                                           owner_frame + "cache_scope=attacker\n"))

    def test_worker_and_scout_get_deliberately_different_roles(self):
        import forge_worker
        self.assertEqual(forge_worker.EDIT_TOOL["name"], "edit")
        self.assertEqual(forge_worker.DELEGATE_TOOL["name"], "delegate")


if __name__ == "__main__":
    unittest.main()
