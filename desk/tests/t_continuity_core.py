"""Single-process contract tests against the unchanged sibling live core.

No model, shell, network, Forge worker or user data. Run: python tests/t_continuity_core.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BOOT = tempfile.TemporaryDirectory(prefix="helene-core-import-")
os.environ["PRAXIS_BASE"] = BOOT.name
sys.path.insert(0, str(ROOT))
import layout  # noqa: E402 — где на диске лежит рабочая копия дерева
# ⚠ Не `ROOT.parent / "live"`: после переезда 10.09 `desk/` живёт внутри
# репозитория `praxis`, а дерево — рядом с ним. Место знает `layout`, и знает
# один он.
sys.path[:0] = [str(ROOT / "localharness"), str(ROOT), str(layout.tree())]

import agent
import media
import run_manager
import transport
import tasks
from core import events
from alarm_clock import AlarmClock
from forge_events import ForgeEvents
from continuity import Continuity
import test_agent_resume_runtime as core_fixtures


class ContinuityTests(unittest.TestCase):
    # Reuse only artifact writers, never inherit or run the core's whole suite.
    _model = core_fixtures.AgentResumeRuntimeTests._model
    _checkpoint = core_fixtures.AgentResumeRuntimeTests._checkpoint
    _pause = core_fixtures.AgentResumeRuntimeTests._pause

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helene-continuity-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manager = run_manager.RunManager(self.base)
        self.cfg = self.base / "helene.json"
        self.cfg.write_text('{"owner":{"name":"Local owner"}}', encoding="utf-8")
        self.desks = transport.Desks(self.base, "Local owner", "Hélène")
        self.activity = []
        self.patches = [mock.patch.object(agent, "_RUN_MANAGER", self.manager),
                        mock.patch.object(agent, "_MEDIA_SPOOL", media.MediaSpool(self.base / "media")),
                        mock.patch.object(agent, "_create_durable_run", agent._create_durable_run),
                        mock.patch.object(agent, "resume_durable_run", agent.resume_durable_run),
                        mock.patch.object(agent._AgentResumeRuntime, "_validate_current_authority",
                                          agent._AgentResumeRuntime._validate_current_authority),
                        mock.patch.object(agent, "_helene_continuity", None, create=True),
                        mock.patch.object(agent.run_snapshot, "write", agent.run_snapshot.write),
                        mock.patch.object(agent, "guard_outbound_reply", side_effect=lambda draft, *a, **kw: draft),
                        mock.patch.object(agent, "_model_call", side_effect=AssertionError("real model forbidden"))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.adapter = Continuity(agent, self.desks, self.cfg,
                                  lambda run, room: self.activity.append((run, room)))
        self.adapter.install()

    def create(self):
        ctx = agent.ChannelContext(chat_id="window-abcdef12", room_id="window-abcdef12",
                                   owner=True, known=True, addressed=True, is_dm=True)
        return agent._create_durable_run(ctx=ctx, kind="chat_turn", goal="fixture",
                                         conversation="exact local conversation", history=[])

    def checkpoint(self):
        run = self.create()
        self.exact = dict(iteration=9, system=[{"type": "text", "text": "exact frame"}],
                          messages=[{"role": "user", "content": "after tools"}],
                          tools=[{"name": "fs_read", "description": "exact schema"}])
        self._checkpoint(run, **self.exact)
        self._pause(run)
        return run

    def archive(self, room="window-abcdef12"):
        path = self.base / "memory" / "groups" / (room + ".jsonl")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_exact_checkpoint_and_room(self):
        run = self.checkpoint()
        with mock.patch.object(agent, "_terminal_tool_loop", return_value="continued exactly") as loop:
            report = agent.resume_durable_run(run.run_id)
        self.assertEqual(report["plan_kind"], "continue_checkpoint", report)
        self.assertEqual(loop.call_args.kwargs["start_iteration"], 9)
        for key in ("system", "messages", "tools"):
            self.assertEqual(loop.call_args.kwargs[key], self.exact[key])
        self.assertIsNone(loop.call_args.kwargs["max_iters"])
        self.assertEqual(self.activity[-1], (run.run_id, "window-abcdef12"))

    def test_owner_changed_refuses_before_model(self):
        run = self.checkpoint()
        self.cfg.write_text('{"owner":{"name":"Another owner"}}', encoding="utf-8")
        with mock.patch.object(agent, "_terminal_tool_loop") as loop:
            report = agent.resume_durable_run(run.run_id)
        loop.assert_not_called()
        self.assertEqual(report["status"], "invalid_context", report)

    def test_unbound_old_run_is_not_adopted(self):
        run = self.checkpoint()
        with mock.patch.object(agent, "_load_exact_run_channel", return_value=(
                agent.ChannelContext(owner=True), {"authority": {}})):
            with self.assertRaises(agent.DurableExecutionError):
                self.adapter.verify_local_owner(run.run_id, run)

    def test_authored_text_archive_acceptance_survives_missing_wal_receipt(self):
        run = self.create()
        self._model(run, blocks=[{"type": "text", "text": "exact recovered answer"}],
                    stop_reason="end_turn", text="exact recovered answer")
        self._pause(run)
        report = agent.resume_durable_run(run.run_id)
        self.assertEqual(report["plan_kind"], "authored_output", report)
        with mock.patch.object(agent, "run_delivery_text_chunk_accepted", side_effect=OSError("injected crash before WAL")):
            with self.assertLogs("helene.continuity", level="ERROR"):
                self.adapter.deliver_pending_text()
        self.assertEqual(len(self.archive()), 1)
        self.adapter.desks = transport.Desks(self.base, "Local owner", "Hélène")
        self.adapter.deliver_pending_text()
        self.adapter.deliver_pending_text()
        self.assertEqual(len(self.archive()), 1)
        self.assertEqual(self.archive()[0]["text"], "exact recovered answer")
        self.assertEqual(self.manager.manifest(run.run_id)["status"], "done")
        self.assertEqual(self.archive("window"), [])

    def test_archive_receipt_replay_and_conflict(self):
        desk = self.desks.get("window-abcdef12")
        receipt = desk.deliver_once("hello", key="same-key")
        fresh = transport.Desks(self.base, "owner", "Hélène").get("window-abcdef12")
        self.assertEqual(fresh.deliver_once("hello", key="same-key"), receipt)
        with self.assertRaises(ValueError):
            fresh.deliver_once("other text", key="same-key")
        self.assertEqual(len(self.archive()), 1)

    def test_owner_pause_and_cancel_never_continue(self):
        for action in ("pause", "cancel"):
            run = self.checkpoint()
            getattr(self.manager, "request_" + action)(run.run_id, actor="helene:owner", reason="test control")
            with mock.patch.object(agent, "_terminal_tool_loop") as loop:
                agent.resume_durable_run(run.run_id)
            loop.assert_not_called()
            manifest = self.manager.manifest(run.run_id)
            if action == "pause":
                self.assertEqual(manifest["control"]["action"], action)
            else:
                self.assertEqual(manifest["status"], "cancelled")

    def test_archive_acceptance_after_torn_utf8_tail(self):
        path = self.base / "memory" / "groups" / "window-abcdef12.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"text":"torn\xe2\x82')
        desk = self.desks.get("window-abcdef12")
        receipt = desk.deliver_once("survived", key="after-torn-tail")
        self.assertEqual(desk.deliver_once("survived", key="after-torn-tail"), receipt)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        self.assertEqual(json.loads(lines[-1])["text"], "survived")

    def alarm_setup(self):
        for patch in (mock.patch.object(tasks, "TASKS", self.base / "memory" / "tasks.json"),
                      mock.patch.object(tasks, "add", tasks.add),
                      mock.patch.object(tasks, "_save", tasks._save)):
            patch.start()
            self.addCleanup(patch.stop)
        clock = AlarmClock(self.adapter, tasks)
        clock.install()
        channel = agent.ChannelContext(chat_id="window-abcdef12", owner=True)
        token = agent._TURN_CHANNEL.set(channel)
        try:
            task = tasks.add("note", goal="alarm fixture")
        finally:
            agent._TURN_CHANNEL.reset(token)
        return clock, task

    def test_alarm_origin_saved_with_intent_and_no_brain_releases_claim(self):
        clock, task = self.alarm_setup()
        self.assertEqual(tasks._load()[0]["helene_origin"]["room"], "window-abcdef12")
        invoked = []
        clock.fire(task, lambda room: invoked.append(room))
        self.assertEqual(invoked, ["window-abcdef12"])
        self.assertEqual(tasks._load()[0]["status"], "pending")
        self.assertFalse(tasks._load()[0].get("claim"))

    def test_alarm_marks_only_after_terminal_receipt(self):
        clock, task = self.alarm_setup()
        def invoke(room):
            self.assertEqual(tasks._load()[0]["status"], "pending")
            run = self.create()
            self.assertEqual(tasks._load()[0]["status"], "pending")
            self.assertTrue(self.manager.path(run.run_id).exists())
            self.manager.transition(run.run_id, "done", expected="running")
        clock.fire(task, invoke)
        self.assertEqual(tasks.due(), [])

    def test_alarm_crash_after_create_recovers_without_second_run(self):
        clock, task = self.alarm_setup()
        with mock.patch.object(tasks, "mark_fired", side_effect=OSError("injected mark failure")):
            with self.assertLogs("helene.alarms", level="ERROR"):
                def invoke(room):
                    run = self.create()
                    self.manager.transition(run.run_id, "done", expected="running")
                clock.fire(task, invoke)
        self.assertEqual(len(self.manager.run_ids()), 1)
        self.assertEqual(tasks._load()[0]["status"], "pending")
        clock.reconcile_claims()
        clock.reconcile_claims()
        self.assertEqual(tasks._load()[0]["status"], "done")
        self.assertEqual(len(self.manager.run_ids()), 1)

    def test_alarm_empty_restart_orphan_requeues_but_owner_cancel_does_not(self):
        for owner_cancel in (False, True):
            with self.subTest(owner_cancel=owner_cancel):
                clock, task = self.alarm_setup()
                created = []
                clock.fire(task, lambda room: created.append(self.create()))
                run = created[0]
                self.assertEqual(len(tasks.open_claims()), 1)
                self._pause(run)
                if owner_cancel:
                    self.manager.request_cancel(run.run_id, actor="helene:owner", reason="test")
                else:
                    agent.resume_durable_run(run.run_id)
                self.assertEqual(self.manager.manifest(run.run_id)["status"], "cancelled")
                clock.reconcile_claims()
                stored = next(t for t in tasks._load() if t["id"] == task["id"])
                self.assertEqual(stored["status"], "done" if owner_cancel else "pending")
                self.assertFalse(stored.get("claim"))
                tasks._save([])

    def test_alarm_failed_before_creation_stays_due(self):
        clock, task = self.alarm_setup()
        with self.assertRaises(OSError):
            clock.fire(task, mock.Mock(side_effect=OSError("no run")))
        self.assertEqual([t["id"] for t in tasks.due()], [task["id"]])

    def forge_setup(self):
        state = self.base / "memory" / ".state"
        for patch in (mock.patch.object(events, "JOURNAL", state / "core_events.jsonl"),
                      mock.patch.object(events, "DELIVERED", state / "core_events_delivered.json"),
                      mock.patch.object(agent.llm, "configured", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)
        forge = mock.Mock()
        forge._wake_load_seen.return_value = set()
        consumer = ForgeEvents(self.adapter, events, forge, mock.Mock(value=lambda name: 0))
        events.emit("subagent_result", "test", {"task_id": "fixture", "agent_id": "reviewer"},
                    dedup_key="forge:fixture:reviewer")
        return consumer

    def forge_run(self, *, status="done"):
        channel = agent.ChannelContext(chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                                       owner=False, known=True, _scope_override="owner")
        run = agent._create_durable_run(ctx=channel, kind="task_window", goal="forge fixture",
                                       conversation="canonical completion event")
        self.manager.transition(run.run_id, status, expected="running")
        return run

    def test_forge_done_receipt_survives_missing_delivery_mark(self):
        consumer = self.forge_setup()
        handler = mock.Mock(side_effect=lambda batch: self.forge_run())
        original = events.mark_delivered
        def fail_nonempty(keys):
            if keys:
                raise OSError("injected acceptance write failure")
            return original(keys)
        with mock.patch.object(agent, "forge_event_turn", handler):
            with mock.patch.object(events, "mark_delivered", side_effect=fail_nonempty):
                with self.assertRaises(OSError):
                    consumer.tick()
            consumer = ForgeEvents(self.adapter, events, consumer.forge, consumer.perception)
            consumer.tick()
            consumer.tick()
        self.assertEqual(handler.call_count, 1)
        self.assertEqual(events.undelivered(), [])

    def test_forge_pending_run_never_activates_second_turn(self):
        consumer = self.forge_setup()
        with mock.patch.object(agent, "forge_event_turn", side_effect=lambda batch: self.forge_run(status="paused")) as handler:
            consumer.tick()
            consumer.tick()
        self.assertEqual(handler.call_count, 1)
        self.assertEqual(len(events.undelivered()), 1)

    def test_forge_no_brain_keeps_event_and_attempt_counter(self):
        consumer = self.forge_setup()
        with mock.patch.object(agent.llm, "configured", return_value=False):
            consumer.tick()
        self.assertEqual(len(events.undelivered()), 1)
        self.assertEqual(events._load_state()["attempts"], {})

    def test_forge_empty_orphan_retries_but_owner_cancel_accepts(self):
        consumer = self.forge_setup()
        with mock.patch.object(agent, "forge_event_turn", side_effect=lambda batch: self.forge_run(status="paused")) as handler:
            consumer.tick()
            run_id = json.loads(consumer.path.read_text(encoding="utf-8"))["run_id"]
            # Use the real core's restart pause, then its empty-orphan decision.
            self.manager.resume(run_id, actor="test:restart")
            self._pause(self.manager.context(run_id))
            agent.resume_durable_run(run_id)
            self.assertTrue(self.adapter.closed_without_effects(run_id))
            consumer.tick()
            self.assertEqual(handler.call_count, 2)
            second = json.loads(consumer.path.read_text(encoding="utf-8"))["run_id"]
            self.manager.request_cancel(second, actor="helene:owner", reason="test")
            consumer.tick()
            self.assertEqual(handler.call_count, 2)
            self.assertEqual(events.undelivered(), [])


if __name__ == "__main__":
    unittest.main()
