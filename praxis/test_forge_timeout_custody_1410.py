"""Timeout custody contracts; mocked process trees, no real native process or network.

Run: python praxis_test.py test_forge_timeout_custody_1410 -v
"""
from __future__ import annotations

import json
import importlib.util
import os
import signal
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import forge_process
import forge_verify
import forge


class FakeCommand:
    pid = 43210

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("command", timeout)


class TimeoutCustody(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def request(self, **fields):
        directory = self.root / "unit"
        directory.mkdir(exist_ok=True)
        path = directory / "request.json"
        path.write_text(json.dumps({"cwd": str(self.root), "command": "test", "timeout": 1,
                                    **fields}), encoding="utf-8")
        return path

    def foreground_unit(self, proc):
        directory = self.root / "processes" / "proc-fixture"
        directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps({
            "id": "proc-fixture", "task_id": "task-fixture", "command": "echo child",
            "supervisor_pid": proc.pid, "status": "running",
        }), encoding="utf-8")
        (directory / "state.json").write_text(json.dumps({
            "child_pid": proc.pid, "child_started_at": "", "foreground": True,
        }), encoding="utf-8")
        (directory / "stop.json").write_text(json.dumps({
            "state": "unresolved", "note": "timeout tree unconfirmed",
        }), encoding="utf-8")
        return directory

    def edition_forge(self):
        # The actual candidate overlay, not a copy of its stop routine.
        path = Path(__file__).resolve().parents[1] / "helene" / "core" / "forge.py"
        spec = importlib.util.spec_from_file_location("helene_forge_custody_test", path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @unittest.skipUnless(sys.platform == "linux", "foreground census uses Linux /proc")
    def test_transient_unreadable_census_retries_before_confirming_stop(self):
        # One unrelated /proc entry can disappear mid-scan. A single None must
        # neither certify an exit nor strand a TERM-ignoring child immediately.
        for module in (forge, self.edition_forge()):
            with self.subTest(module=module.__name__):
                fake = FakeCommand()
                with mock.patch.object(module.os, "killpg") as kill, \
                     mock.patch.object(module, "_group_live_members", side_effect=[None, False]), \
                     mock.patch.object(fake, "wait", return_value=0):
                    stopped, reason = module._stop_foreground(fake)
                self.assertTrue(stopped, reason)
                kill.assert_called_once_with(fake.pid, signal.SIGTERM)

    @unittest.skipUnless(sys.platform == "linux", "foreground census uses Linux /proc")
    def test_persistently_unreadable_census_never_sends_kill_or_claims_exit(self):
        for module in (forge, self.edition_forge()):
            with self.subTest(module=module.__name__):
                fake = FakeCommand()
                with mock.patch.object(module.os, "killpg") as kill, \
                     mock.patch.object(module, "_group_live_members", return_value=None), \
                     mock.patch.object(module.time, "monotonic", side_effect=[0, 2]):
                    stopped, reason = module._stop_foreground(fake)
                self.assertFalse(stopped)
                self.assertIn("census unavailable", reason)
                kill.assert_called_once_with(fake.pid, signal.SIGTERM)

    def test_edition_windows_foreground_never_falls_back_to_generic_pid_kill(self):
        edition = self.edition_forge()
        fake = FakeCommand()
        directory = self.foreground_unit(fake)
        with mock.patch.object(edition, "_task_root", return_value=({"scope": "self"}, self.root, "")), \
             mock.patch.object(edition, "_unit_dir", return_value=directory), \
             mock.patch.object(edition, "_event"), \
             mock.patch.object(edition, "_kill_tree") as generic_kill, \
             mock.patch.object(edition.os, "name", "nt"), \
             mock.patch.object(FakeCommand, "poll", return_value=0), \
             mock.patch.object(edition.subprocess, "run") as taskkill, \
             mock.patch.dict(edition._RUNNERS, {fake.pid: fake}):
            result = edition.process("task-fixture", "stop", process_id="proc-fixture")
        self.assertIn("терминальный статус НЕ записан", result)
        self.assertEqual(json.loads((directory / "stop.json").read_text())["state"], "unresolved")
        taskkill.assert_not_called()
        generic_kill.assert_not_called()

    def test_edition_mac_group_census_stays_conservative(self):
        edition = self.edition_forge()
        with mock.patch.object(edition.sys, "platform", "darwin"), \
             mock.patch.object(edition.subprocess, "run", return_value=mock.Mock(
                 stdout="43210 Z\n43210 S\n", returncode=0)):
            self.assertTrue(edition._group_live_members(43210))
        with mock.patch.object(edition.sys, "platform", "darwin"), \
             mock.patch.object(edition.subprocess, "run", return_value=mock.Mock(
                 stdout="43210 Z\nunknown\n", returncode=0)):
            self.assertIsNone(edition._group_live_members(43210))

    def test_windows_foreground_shell_reused_pid_refuses_taskkill_and_settlement(self):
        fake = FakeCommand()
        directory = self.foreground_unit(fake)
        with mock.patch.object(forge, "_task_root", return_value=({"scope": "self"}, self.root, "")), \
             mock.patch.object(forge, "_unit_dir", return_value=directory), \
             mock.patch.object(forge, "_event"), \
             mock.patch.object(forge, "_kill_tree") as generic_kill, \
             mock.patch.object(forge.os, "name", "nt"), \
             mock.patch.object(FakeCommand, "poll", return_value=0), \
             mock.patch.object(forge.subprocess, "run") as taskkill, \
             mock.patch.dict(forge._RUNNERS, {fake.pid: fake}):
            result = forge.process("task-fixture", "stop", process_id="proc-fixture")
        self.assertIn("терминальный статус НЕ записан", result)
        self.assertEqual(json.loads((directory / "stop.json").read_text())["state"], "unresolved")
        self.assertFalse((directory / "result.json").exists())
        taskkill.assert_not_called()
        generic_kill.assert_not_called()

    def test_windows_foreground_rc128_is_not_clean_exit(self):
        fake = FakeCommand()
        directory = self.foreground_unit(fake)
        with mock.patch.object(forge, "_task_root", return_value=({"scope": "self"}, self.root, "")), \
             mock.patch.object(forge, "_unit_dir", return_value=directory), \
             mock.patch.object(forge, "_event"), \
             mock.patch.object(forge, "_kill_tree") as generic_kill, \
             mock.patch.object(forge.os, "name", "nt"), \
             mock.patch.object(forge.subprocess, "run", return_value=mock.Mock(returncode=128)) as taskkill, \
             mock.patch.dict(forge._RUNNERS, {fake.pid: fake}):
            result = forge.process("task-fixture", "stop", process_id="proc-fixture")
        self.assertIn("taskkill rc=128", result)
        self.assertFalse((directory / "result.json").exists())
        self.assertEqual(json.loads((directory / "stop.json").read_text())["state"], "unresolved")
        self.assertEqual(taskkill.call_count, 1)
        generic_kill.assert_not_called()

    def test_windows_foreground_acknowledged_tree_stop_keeps_terminal_semantics(self):
        fake = mock.Mock(pid=43210)
        fake.poll.return_value = None
        fake.wait.return_value = 0
        directory = self.foreground_unit(fake)
        with mock.patch.object(forge, "_task_root", return_value=({"scope": "self"}, self.root, "")), \
             mock.patch.object(forge, "_unit_dir", return_value=directory), \
             mock.patch.object(forge, "_event"), \
             mock.patch.object(forge.os, "name", "nt"), \
             mock.patch.object(forge.subprocess, "run", return_value=mock.Mock(returncode=0)), \
             mock.patch.dict(forge._RUNNERS, {fake.pid: fake}):
            result = forge.process("task-fixture", "stop", process_id="proc-fixture")
        self.assertIn("остановлен", result)
        self.assertEqual(json.loads((directory / "result.json").read_text())["status"], "stopped")
        self.assertEqual(json.loads((directory / "stop.json").read_text())["state"], "settled")

    def test_mac_group_census_distinguishes_live_zombie_empty_and_uncertain(self):
        def census(output, rc=0):
            with mock.patch.object(forge.sys, "platform", "darwin"), \
                 mock.patch.object(forge.subprocess, "run", return_value=mock.Mock(
                     stdout=output, returncode=rc)) as ps:
                value = forge._group_live_members(43210)
            self.assertEqual(ps.call_args.args[0], ["ps", "-A", "-o", "pgid=", "-o", "stat="])
            return value
        self.assertTrue(census("43210 S\n111 S\n"))
        self.assertFalse(census("43210 Z\n111 S\n"))
        self.assertIsNone(census("111 S\nunreadable\n"))
        self.assertIsNone(census("111 S\n", rc=1))

    def test_mac_foreground_term_live_member_escalates_kill_before_settling(self):
        fake = mock.Mock(pid=43210)
        fake.wait.return_value = 0
        # TERM and the grace-end census see the live child; KILL's next
        # census proves empty. No actual signal or process is touched.
        ticks = iter((0.0, .1, .2, 1.1, 2.0, 2.1))
        with mock.patch.object(forge.os, "name", "posix"), \
             mock.patch.object(forge.sys, "platform", "darwin"), \
             mock.patch.object(forge.time, "monotonic", side_effect=lambda: next(ticks)), \
             mock.patch.object(forge.time, "sleep"), \
             mock.patch.object(forge, "_group_live_members", side_effect=[None, True, True, True, False]), \
             mock.patch.object(forge.signal, "SIGKILL", 9, create=True), \
             mock.patch.object(forge.os, "killpg", create=True) as killpg:
            stopped, note = forge._stop_foreground(fake)
        self.assertTrue(stopped, note)
        self.assertEqual(killpg.call_args_list,
                         [mock.call(fake.pid, signal.SIGTERM), mock.call(fake.pid, 9)])

    @unittest.skipUnless(sys.platform == "linux", "foreground census uses Linux /proc")
    def test_unreadable_census_after_kill_keeps_timeout_unknown(self):
        for module in (forge, self.edition_forge()):
            with self.subTest(module=module.__name__):
                fake = FakeCommand()
                ticks = iter((0.0, .1, 1.1, 2.0, 2.1, 3.1))
                with mock.patch.object(module.os, "killpg") as kill, \
                     mock.patch.object(module, "_group_live_members",
                                       side_effect=[True, True, True, None, None]), \
                     mock.patch.object(module.time, "monotonic", side_effect=lambda: next(ticks)), \
                     mock.patch.object(module.time, "sleep"):
                    stopped, reason = module._stop_foreground(fake)
                self.assertFalse(stopped)
                self.assertIn("census unavailable", reason)
                self.assertEqual(kill.call_args_list, [mock.call(fake.pid, signal.SIGTERM),
                                                       mock.call(fake.pid, signal.SIGKILL)])

    def test_windows_taskkill_failure_does_not_claim_tree_exit(self):
        with mock.patch.object(forge_process.os, "name", "nt"), \
             mock.patch.object(forge_process.subprocess, "run", return_value=mock.Mock(
                 returncode=5, stderr="Access denied")) as taskkill:
            stopped, note = forge_process._terminate_tree(FakeCommand())
        self.assertFalse(stopped)
        self.assertIn("Access denied", note)
        self.assertIn("/T", taskkill.call_args.args[0])
        self.assertIn("/F", taskkill.call_args.args[0])

    def test_foreground_unconfirmed_timeout_becomes_pollable_unit(self):
        fake = FakeCommand()
        d = self.root / "processes"
        def unit(_task, _kind, ident):
            directory = d / ident
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        with mock.patch.object(forge, "_task_root", return_value=({"scope": "self"}, self.root, "")), \
             mock.patch.object(forge, "_task_dir", return_value=self.root), \
             mock.patch.object(forge, "_unit_dir", side_effect=unit), \
             mock.patch.object(forge, "_proc_started_at", return_value="birth-fixture"), \
             mock.patch.object(forge, "_stop_foreground", return_value=(False, "group unconfirmed")), \
             mock.patch.object(forge, "_event"), \
             mock.patch.object(forge.subprocess, "Popen", return_value=fake):
            result = forge.run("task-fixture", "echo child", timeout=1)
        self.addCleanup(forge._RUNNERS.pop, fake.pid, None)
        self.assertIn("forge: unknown", result)
        self.assertIn("process_id=", result)
        rows = list(d.glob("*/stop.json"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0].read_text())["state"], "unresolved")
        self.assertFalse((rows[0].parent / "result.json").exists())
        self.assertEqual(json.loads((rows[0].parent / "state.json").read_text())
                         ["child_started_at"], "birth-fixture")

    def test_windows_exited_shell_pid_is_not_signalled_after_possible_reuse(self):
        with mock.patch.object(forge_process.os, "name", "nt"), \
             mock.patch.object(FakeCommand, "poll", return_value=0), \
             mock.patch.object(forge_process.subprocess, "run") as taskkill:
            stopped, note = forge_process._terminate_tree(FakeCommand())
        self.assertFalse(stopped)
        self.assertIn("descendants unproven", note)
        taskkill.assert_not_called()

    def test_posix_shell_exits_on_term_but_live_child_gets_kill(self):
        # Census stays live after TERM even though the Popen shell could exit.
        # A successful wait on the shell must never suppress the group KILL.
        with mock.patch.object(forge_process.os, "name", "posix"), \
             mock.patch.object(forge_process, "_group_members", return_value=True), \
             mock.patch.object(forge_process, "_wait_group_gone", side_effect=[True, False]), \
             mock.patch.object(forge_process.signal, "SIGKILL", 9, create=True), \
             mock.patch.object(forge_process.os, "killpg", create=True) as killpg:
            stopped, note = forge_process._terminate_tree(FakeCommand())
        self.assertTrue(stopped, note)
        self.assertEqual(killpg.call_args_list,
                         [mock.call(FakeCommand.pid, signal.SIGTERM),
                          mock.call(FakeCommand.pid, 9)])

    def test_process_timeout_unknown_writes_stop_and_no_terminal_result(self):
        req = self.request()
        with mock.patch.object(forge_process.subprocess, "Popen", return_value=FakeCommand()), \
             mock.patch.object(forge_process, "_terminate_tree",
                               return_value=(False, "taskkill permission denied")):
            self.assertEqual(forge_process.run(req), 2)
        unit = req.parent
        self.assertFalse((unit / "result.json").exists())
        stop = json.loads((unit / "stop.json").read_text(encoding="utf-8"))
        self.assertEqual(stop["state"], "unresolved")
        self.assertIn("taskkill permission denied", stop["note"])
        self.assertEqual(json.loads((unit / "state.json").read_text())["status"], "unknown")

    def test_process_timeout_with_proven_cleanup_writes_terminal_timeout(self):
        req = self.request()
        with mock.patch.object(forge_process.subprocess, "Popen", return_value=FakeCommand()), \
             mock.patch.object(forge_process, "_terminate_tree",
                               return_value=(True, "group empty")):
            self.assertEqual(forge_process.run(req), 1)
        self.assertEqual(json.loads((req.parent / "result.json").read_text())["status"],
                         "timed_out")
        self.assertFalse((req.parent / "stop.json").exists())

    def test_verify_timeout_launches_private_group_and_does_not_pass(self):
        directory = self.root / "matrix"
        with mock.patch.object(forge_verify.os, "name", "posix"), \
             mock.patch.object(forge_verify.subprocess, "Popen", return_value=FakeCommand()) as popen, \
             mock.patch.object(forge_verify, "_terminate_tree",
                               return_value=(False, "group still live")):
            row = forge_verify._run_one(0, {"id": "build", "command": "build"},
                                        self.root, directory, 1, {"checks": {}},
                                        __import__("threading").Lock())
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(row["status"], "unknown")
        self.assertIn("group still live", row["liveness"])
        self.assertNotEqual(row["status"], "timed_out")

    def test_verify_unknown_matrix_has_stop_receipt_not_terminal_result(self):
        req = self.request(root=str(self.root), checks=[{"id": "test", "command": "test"}],
                           max_parallel=1)
        with mock.patch.object(forge_verify, "_run_one", return_value={
            "id": "test", "status": "unknown", "liveness": "child still live"}):
            self.assertEqual(forge_verify.run(req), 2)
        self.assertFalse((req.parent / "result.json").exists())
        stop = json.loads((req.parent / "stop.json").read_text(encoding="utf-8"))
        self.assertEqual(stop["state"], "unresolved")
        self.assertIn("child still live", stop["note"])

    def test_verify_supervisor_error_after_spawn_cannot_terminalize_live_group(self):
        req = self.request(root=str(self.root), checks=[{"id": "test", "command": "test"}],
                           max_parallel=1)
        with mock.patch.object(forge_verify.subprocess, "Popen", return_value=FakeCommand()), \
             mock.patch.object(forge_verify, "_wait_local", side_effect=RuntimeError("read failure")), \
             mock.patch.object(forge_verify, "_terminate_tree", return_value=(False, "denied")):
            self.assertEqual(forge_verify.run(req), 2)
        self.assertFalse((req.parent / "result.json").exists())
        self.assertEqual(json.loads((req.parent / "stop.json").read_text())["state"],
                         "unresolved")
        self.assertEqual(json.loads((req.parent / "state.json").read_text())["checks"]["test"]["status"],
                         "unknown")

    def test_marker_cancels_running_check_with_group_receipt(self):
        req = self.request(root=str(self.root), checks=[{"id": "test", "command": "test"}],
                           max_parallel=1)
        marker = req.parent / "stop-request.json"

        class Delayed(FakeCommand):
            def wait(self, timeout=None):
                marker.write_text(json.dumps({"reason": "verify.stop"}), encoding="utf-8")
                (req.parent / "stop.json").write_text(
                    json.dumps({"state": "unresolved", "note": "parent bounded wait"}),
                    encoding="utf-8")
                raise subprocess.TimeoutExpired("test", timeout)

        with mock.patch.object(forge_verify.subprocess, "Popen", return_value=Delayed()), \
             mock.patch.object(forge_verify, "_terminate_tree",
                               return_value=(True, "private group empty")):
            self.assertEqual(forge_verify.run(req), 1)
        result = json.loads((req.parent / "result.json").read_text())
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["checks"][0]["status"], "stopped")
        self.assertTrue(result["checks"][0]["custody"]["group_exit_verified"])
        self.assertEqual(json.loads((req.parent / "stop.json").read_text())["state"],
                         "settled")

    def test_repeated_prestart_marker_never_spawns_command(self):
        req = self.request(root=str(self.root), checks=[{"id": "test", "command": "test"}],
                           max_parallel=1)
        marker = req.parent / "stop-request.json"
        marker.write_text(json.dumps({"reason": "verify.stop"}), encoding="utf-8")
        marker.write_text(json.dumps({"reason": "verify.stop"}), encoding="utf-8")
        with mock.patch.object(forge_verify.subprocess, "Popen") as popen:
            self.assertEqual(forge_verify.run(req), 1)
        popen.assert_not_called()
        result = json.loads((req.parent / "result.json").read_text())
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["checks"][0]["custody"]["spawned"])
        self.assertEqual(json.loads((req.parent / "stop.json").read_text())["state"], "settled")

    @unittest.skipIf(os.name == "nt", "real POSIX groups run only on a Linux/macOS test host")
    def test_posix_real_timeout_cleans_shell_and_ignored_term_descendant(self):
        req = self.request(command=(f"{shlex.quote(sys.executable)} -c "
                                    "'import signal,time; signal.signal(signal.SIGTERM, "
                                    "signal.SIG_IGN); time.sleep(30)'"))
        self.assertEqual(forge_process.run(req), 1)
        result = json.loads((req.parent / "result.json").read_text())
        self.assertEqual(result["status"], "timed_out")
        state = json.loads((req.parent / "state.json").read_text())
        self.assertFalse(forge_process._group_members(state["child_pid"]))

    @unittest.skipUnless(sys.platform == "linux", "Forge foreground census uses Linux /proc")
    def test_foreground_timeout_reaps_same_group_descendant(self):
        # Child ignores TERM, and detaches stdout from the shell so a leader-only
        # communicate() would otherwise return with a live process behind it.
        command = (f"{shlex.quote(sys.executable)} -c 'import signal,time; "
                   "signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)' "
                   ">/dev/null 2>&1 & wait")
        seen = []
        stop = forge._stop_foreground
        def checked(proc):
            seen.append(proc.pid)
            return stop(proc)
        with mock.patch.object(forge, "_task_root", return_value=({"scope": "self"}, self.root, "")), \
             mock.patch.object(forge, "_task_dir", return_value=self.root), \
             mock.patch.object(forge, "_event"), \
             mock.patch.object(forge, "_stop_foreground", side_effect=checked):
            result = forge.run("task-fixture", command, timeout=1)
        self.assertIn("forge: timed_out", result)
        self.assertEqual(len(seen), 1)
        self.assertFalse(forge._group_live_members(seen[0]))


if __name__ == "__main__":
    unittest.main()
