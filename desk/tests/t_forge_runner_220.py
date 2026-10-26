"""Real runner helpers/event/control consumers, in-process; no app or provider.

Runner is deliberately AST-loaded: importing it imports the selected authored
agent and transport stack. Only production function bodies are executed here.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desk"))
from deskd import forge_control
from localharness import source_update
from localharness.forge_events import ForgeEvents
from localharness.package_notice import read, write


def load_runner():
    path = ROOT / "desk/localharness/runner.py"
    source = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_forge_maintenance_tick", "_start_forge_maintenance", "_resume_due", "_update_report_due"}
    functions = [row for row in source.body if isinstance(row, ast.FunctionDef) and row.name in names]
    namespace = {"Path": Path, "log": logging.getLogger("runner-test"), "time": time}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace, source


def load_events():
    spec = importlib.util.spec_from_file_location("runner220_events", ROOT / "helene/core/core/events.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="helene-runner220-")
        self.addCleanup(temporary.cleanup)
        self.tree = Path(temporary.name)
        self.runner, self.ast = load_runner()
        self.owner = types.SimpleNamespace(stopped=mock.Mock(return_value=False), paused=mock.Mock(return_value=False))
        self.runner["owner_stop"] = self.owner
        self.core = types.SimpleNamespace(control=mock.Mock(return_value={"ok": True, "status": "stopped"}),
                                          tick_queued=mock.Mock())
        self.review = types.SimpleNamespace(tick=mock.Mock(), service=object())
        self.task = "code-12345678"
        write(self.tree / "memory/.forge/tasks" / self.task / "task.json", {"id": self.task})
        patch = mock.patch("subprocess.Popen", side_effect=AssertionError("process forbidden"))
        patch.start()
        self.addCleanup(patch.stop)

    def enqueue(self, action="stop", command_id="a" * 32):
        return forge_control.enqueue(self.tree, self.task, action, command_id=command_id)

    def tick(self, review=True):
        self.runner["_forge_maintenance_tick"](self.tree, self.core, self.review if review else None)

    def test_control_precedes_dequeue_and_uses_actual_command_receipt(self):
        self.enqueue()
        order = []
        self.core.control.side_effect = lambda *a, **kw: order.append("control") or {"ok": True}
        self.core.tick_queued.side_effect = lambda: order.append("drain")
        with mock.patch.object(source_update, "tick") as pipeline:
            self.tick()
        self.assertEqual(order, ["control", "drain"])
        self.core.control.assert_called_once_with(self.task, "stop", agent_id="", expected_generation="")
        self.assertEqual(forge_control.receipt(self.tree, "a" * 32)["state"], "observed")
        pipeline.assert_called_once_with(self.tree, self.core, self.review.service, paused=False, stopped=False)

    def test_pause_and_stop_block_queue_resume_but_still_consume_stop(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                self.owner.paused.return_value = True
                self.owner.stopped.return_value = stopped
                self.enqueue("resume", "b" * 32 if stopped else "c" * 32)
                self.enqueue("stop", "d" * 32 if stopped else "e" * 32)
                self.core.control.reset_mock()
                self.core.tick_queued.reset_mock()
                self.review.tick.reset_mock()
                with mock.patch.object(source_update, "tick") as pipeline:
                    self.tick()
                self.core.tick_queued.assert_not_called()
                self.assertEqual([call.args[1] for call in self.core.control.call_args_list], ["stop"])
                pipeline.assert_called_once_with(self.tree, self.core, self.review.service, paused=True, stopped=stopped)
                if stopped:
                    self.review.tick.assert_not_called()
                else:
                    self.review.tick.assert_called_once_with(paused=True)
                receipt = forge_control.receipt(self.tree, "b" * 32 if stopped else "c" * 32)
                self.assertFalse(receipt["ok"])
                self.assertEqual(receipt["result"]["status"], "refused")

    def test_unsupported_authored_control_has_explicit_receipt_no_retrofit(self):
        self.enqueue()
        self.core = types.SimpleNamespace()
        with mock.patch.object(source_update, "tick"):
            self.tick()
        receipt = forge_control.receipt(self.tree, "a" * 32)
        self.assertEqual(receipt["state"], "observed")
        self.assertEqual(receipt["result"]["status"], "unsupported")
        self.assertFalse(receipt["ok"])
        self.assertFalse(hasattr(self.core, "control"))

    def test_recovered_control_claim_is_unknown_not_replayed(self):
        self.enqueue()
        folder = self.tree / "memory/.control/forge"
        (folder / "processing").mkdir()
        (folder / "commands" / ("a" * 32 + ".json")).rename(folder / "processing" / ("a" * 32 + ".json"))
        with mock.patch.object(source_update, "tick"):
            self.tick()
        self.core.control.assert_not_called()
        self.assertEqual(forge_control.receipt(self.tree, "a" * 32)["state"], "unknown")

    def test_refused_or_uncertain_core_control_is_not_reported_successful(self):
        for index, status in enumerate(("refused", "unknown")):
            command_id = str(index) * 32
            self.enqueue(command_id=command_id)
            self.core.control.return_value = {"status": status, "reason": "canonical core receipt"}
            with mock.patch.object(source_update, "tick"):
                self.tick()
            receipt = forge_control.receipt(self.tree, command_id)
            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["result"]["status"], status)

    def test_pipeline_runs_after_review_error_and_without_pending_request(self):
        self.review.tick.side_effect = ValueError("injected review error")
        with mock.patch.object(source_update, "pending", return_value=None) as pending:
            with mock.patch.object(source_update, "tick") as pipeline:
                with self.assertLogs("runner-test", level="ERROR"):
                    self.tick()
        pipeline.assert_called_once()
        pending.assert_not_called()

    def test_actual_cancelled_pipeline_settles_without_pending(self):
        directory = self.tree / "isolated-plan"
        request = {"id": "f" * 64, "directory": str(directory), "state": "cancelled"}
        write(self.tree / "memory/.control/source-update-requests" / (request["id"] + ".json"), request)
        write(source_update.path(self.tree), request)
        write(directory / "pipeline.json", {**request, "cancel_requested": True, "reason": "Already cancelled"})
        source_update._cancel_marker(directory, "Already cancelled")
        self.assertIsNone(source_update.pending(self.tree))
        self.owner.stopped.return_value = True
        with mock.patch.object(source_update, "_stop") as settle:
            self.tick()
        settle.assert_called_once()
        operation = read(directory / "pipeline.json")
        self.assertEqual(operation["state"], "cancelled")
        # A later global Stop settles the retained cancellation; it does not
        # rewrite that earlier cancellation's recorded cause.
        self.assertEqual(operation["reason"], "Already cancelled")
        self.assertTrue((directory / "cancel.json").exists())
        self.core.tick_queued.assert_not_called()

    def test_missing_review_service_controls_still_work_without_fallback(self):
        self.enqueue()
        with mock.patch.object(source_update, "tick") as pipeline:
            self.tick(review=False)
        self.core.control.assert_called_once()
        pipeline.assert_not_called()

    def test_background_controls_progress_while_main_turn_is_blocked(self):
        self.enqueue()
        got_control = threading.Event()
        self.core.control.side_effect = lambda *a, **kw: got_control.set() or {"ok": True}
        # The caller stands for the synchronous main model: it performs no tick
        # while waiting. Only the actual production maintenance thread can work.
        with mock.patch.object(source_update, "tick"):
            stop, thread = self.runner["_start_forge_maintenance"](self.tree, self.core, self.review, interval=.01)
            try:
                self.assertTrue(got_control.wait(2), "control depends on main-loop progress")
            finally:
                stop.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(forge_control.receipt(self.tree, "a" * 32)["state"], "observed")

    def test_main_starts_maintenance_before_birth_no_duplicate_service_tick(self):
        main = next(row for row in self.ast.body if isinstance(row, ast.FunctionDef) and row.name == "main")
        calls = [row for row in ast.walk(main) if isinstance(row, ast.Call)]
        start = next(row for row in calls if isinstance(row.func, ast.Name) and row.func.id == "_start_forge_maintenance")
        birth = next(row for row in calls if isinstance(row.func, ast.Name) and row.func.id == "_maybe_birth")
        self.assertLess(start.lineno, birth.lineno)
        self.assertFalse(any(isinstance(row.func, ast.Attribute) and row.func.attr == "tick"
            and isinstance(row.func.value, ast.Name) and row.func.value.id == "_self_review" for row in calls))
        source = (ROOT / "desk/localharness/runner.py").read_text(encoding="utf-8")
        self.assertIn("speech_bridge.install(agent, tree, config_path, boundary=control_watch, owner=owner_stop)", source)

    def test_old_conflict_notifier_never_called(self):
        self.runner.update(_tree=self.tree, _desk=object(), updates=mock.Mock(), _turn_in_window=mock.Mock())
        with mock.patch.object(source_update, "pending", return_value={"state": "queued"}):
            with mock.patch.object(source_update, "notify_due", side_effect=AssertionError("ordinary wake forbidden")):
                self.runner["_update_report_due"]()
        self.runner["updates"].pending_report.assert_not_called()
        self.runner["_turn_in_window"].assert_not_called()


class EventTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="helene-forge-events220-")
        self.addCleanup(temporary.cleanup)
        self.tree = Path(temporary.name)
        self.events = load_events()
        self.events.JOURNAL = self.tree / "events.jsonl"
        self.events.DELIVERED = self.tree / "delivered.json"
        self.manager = types.SimpleNamespace(root=self.tree / "runs", run_ids=mock.Mock(return_value=[]),
            context=mock.Mock(return_value=types.SimpleNamespace(kind="task_window")),
            manifest=mock.Mock(return_value={"status": "paused"}),
            request_cancel=mock.Mock(return_value={"status": "cancelled"}))
        self.agent = types.SimpleNamespace(llm=types.SimpleNamespace(configured=lambda: True),
            forge_event_turn=mock.Mock(), _runs=lambda: self.manager,
            _load_exact_run_channel=lambda *a: (types.SimpleNamespace(praxis_self=True), {}))
        @contextmanager
        def activation(*args):
            yield
        self.continuity = types.SimpleNamespace(desks=types.SimpleNamespace(tree=self.tree),
            agent=self.agent, owner_stamp=lambda: "local-stamp", activation=activation,
            closed_without_effects=lambda run: False)
        self.forge = types.SimpleNamespace(reconcile_subagent_events=mock.Mock(), mark_seen=mock.Mock(),
                                          _wake_load_seen=lambda: set())
        self.consumer = ForgeEvents(self.continuity, self.events, self.forge, types.SimpleNamespace(value=lambda k: 0))

    def emit(self, task="code-12345678", unit="agent-12345678"):
        return self.events.emit("subagent_result", "forge", {"task_id": task, "agent_id": unit},
                                dedup_key="forge:" + task + ":" + unit)

    def metadata(self, kind="source_update", *, request=False):
        folder = self.tree / "memory/.forge/tasks/code-12345678"
        path = folder / "agents/agent-12345678/request.json" if request else folder / "task.json"
        write(path, {kind: {"id": "bound-adapter"}})

    def claim(self, rows):
        self.manager.run_ids.return_value = ["run-old"]
        self.consumer._save({"run_id": "run-old", "owner_stamp": "local-stamp", "events": rows})

    def test_historical_task_and_unit_adapters_are_quiet_normal_event_still_wakes(self):
        for kind, request in (("source_update", False), ("self_review", True)):
            with self.subTest(kind=kind):
                self.metadata(kind, request=request)
                dedicated = self.emit()
                normal = self.emit("code-87654321")
                self.consumer.tick()
                self.assertEqual(self.agent.forge_event_turn.call_args.args[0], [normal])
                self.assertNotIn(dedicated["dedup_key"], self.events._load_state()["attempts"])
                self.assertIn(dedicated["dedup_key"], self.events._load_delivered())
                self.events.mark_delivered([normal["dedup_key"]])
                # Recreate fresh source state for the other metadata representation.
                for path in self.tree.rglob("*.json*"):
                    path.unlink()

    def test_filter_negative_control_old_behavior_wakes(self):
        self.metadata()
        dedicated = self.emit()
        with mock.patch.object(self.consumer, "_dedicated", return_value=False):
            self.consumer.tick()
        self.agent.forge_event_turn.assert_called_once_with([dedicated])

    def test_mixed_historical_claim_cancels_snapshot_keeps_ordinary_event(self):
        self.metadata()
        dedicated, normal = self.emit(), self.emit("code-87654321")
        self.claim([dedicated, normal])
        self.assertFalse(self.consumer.suppress_dedicated_handoff())
        self.manager.request_cancel.assert_called_once()
        self.assertEqual(self.events.undelivered(), [normal])
        self.assertEqual(read(self.consumer.path), {})
        self.agent.forge_event_turn.assert_not_called()

    def test_uncertain_historical_run_never_reaches_generic_resume(self):
        self.metadata("self_review")
        event = self.emit()
        self.claim([event])
        self.manager.request_cancel.return_value = {"status": "paused", "control": {"action": "cancel"}}
        runner, _ = load_runner()
        runner.update(_forge_events=self.consumer, _continuity=types.SimpleNamespace(resume_due=mock.Mock()),
            owner_stop=types.SimpleNamespace(paused=lambda: False), _brain_ready=lambda: True, _set_busy=mock.Mock())
        runner["_resume_due"]()
        runner["_continuity"].resume_due.assert_not_called()
        self.assertEqual(read(self.consumer.path)["run_id"], "run-old")
        self.assertEqual(self.events.undelivered(), [event])

    def test_tick_retires_old_dedicated_claim_without_another_turn(self):
        self.metadata()
        self.claim([self.emit()])
        self.consumer.tick()
        self.manager.request_cancel.assert_called_once()
        self.agent.forge_event_turn.assert_not_called()
        self.assertEqual(self.events.undelivered(), [])

    def test_ordinary_pending_claim_is_not_migrated_or_duplicated(self):
        normal = self.emit("code-87654321")
        self.claim([normal])
        self.consumer.tick()
        self.manager.request_cancel.assert_not_called()
        self.agent.forge_event_turn.assert_not_called()
        self.assertEqual(read(self.consumer.path)["run_id"], "run-old")
        self.assertEqual(self.events.undelivered(), [normal])

    def test_retired_claim_does_not_prevent_unrelated_resume(self):
        self.metadata()
        self.claim([self.emit()])
        runner, _ = load_runner()
        runner.update(_forge_events=self.consumer, _continuity=types.SimpleNamespace(resume_due=mock.Mock()),
            owner_stop=types.SimpleNamespace(paused=lambda: False), _brain_ready=lambda: True, _set_busy=mock.Mock())
        runner["_resume_due"]()
        runner["_continuity"].resume_due.assert_called_once()
        self.manager.request_cancel.assert_called_once()

    def test_changed_owner_claim_refuses_before_cancellation_or_resume(self):
        self.metadata()
        self.claim([self.emit()])
        claim = read(self.consumer.path)
        claim["owner_stamp"] = "someone-else"
        write(self.consumer.path, claim)
        with self.assertRaisesRegex(ValueError, "different local owner"):
            self.consumer.suppress_dedicated_handoff()
        self.manager.request_cancel.assert_not_called()

    def test_non_forge_receipt_path_is_not_opened_for_classification(self):
        outside = self.tree / "private.json"
        write(outside, {"source_update": True})
        event = {"payload": {"task_id": "../private", "agent_id": "a", "causality": {
            "receipts": {"request": str(outside)}}}}
        self.assertFalse(self.consumer._dedicated(event))


if __name__ == "__main__":
    unittest.main()
