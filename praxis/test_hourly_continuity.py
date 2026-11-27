"""Hourly continuity regressions; these use real on-disk pulse and run stores."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import heartbeat
import hourly_continuity
import mailroom
import mtproto_runner as runner
import run_context
import run_manager
import run_resume
import social_pulse


HEADER = "Previous hourly window — authored note"


class HourlyContinuityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="praxis-hourly-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.path = self.base / "pulse.json"
        self.manager = run_manager.RunManager(self.base)
        self.clock = time.time()
        self.start = self.clock - 3700
        self.enterContext(patch.dict(os.environ, {
            "PRAXIS_SOCIAL_PULSE_HOURS": "1", "PRAXIS_DOSSIER_ALL": "off",
            "PRAXIS_WORK_LOOP": "on", "PRAXIS_CHAT_REPLY_HAND": "on", "PRAXIS_WORK_DOD": "off",
            "PRAXIS_AUTO_RECALL_K": "0",
        }))
        for target, attr, value in (
            (social_pulse, "STATE_PATH", self.path),
            (runner, "_ONE_MIND", asyncio.Lock()),
            (runner.client, "is_connected", lambda: True),
            (runner.telegram_followups.LEDGER, "context", lambda: ""),
            (mailroom, "list_open", lambda: [
                {"hash": "unseen-hash-probe", "subject": "unseen-subject-probe", "status": "new"},
            ]),
            (heartbeat, "DECISIONS_PATH", self.base / "legacy-decisions.json"),
            (heartbeat, "_unanswered_context", lambda: ""),
            (heartbeat, "_loops_context", lambda: ""),
            (heartbeat, "_automatic_memory_context", lambda: ""),
            (heartbeat, "_forge_completions_context", lambda: ""),
            (agent, "BASE", self.base),
            (agent, "_runs", lambda: self.manager),
            (agent.llm, "configured", lambda: True),
            (agent.turns, "record", lambda turn: None),
            (agent, "_self_intent_eligible", lambda goal: False),
        ):
            self.enterContext(patch.object(target, attr, value, create=True))

    def begin(self, started=None):
        return social_pulse.begin(now=self.start if started is None else started)

    def pair(self, started=None):
        window = social_pulse.HourlyWindow(self.begin(started), run_context.new_run_id())
        social_pulse.bind_window(window)
        return window

    def land(self, window, *, scope="owner", principal="praxis:self", pulse_id=None,
             kind="task_window", status="done", details=None, finish=True, chat=None):
        context = run_context.RunContext.create(
            run_id=window.run_id, kind=kind, principal_id=principal, scope=scope,
            hourly_pulse_id=window.pulse_id if pulse_id is None else pulse_id,
            origin_chat_id=chat, delivery_chat_id=chat, goal="private goal — not a closing note",
        )
        self.manager.create(context, "private snapshot — not a closing note")
        self.manager.transition(context.run_id, "running")
        if status != "running":
            self.manager.transition(context.run_id, status, details=details)
        if finish:
            social_pulse.finish(window.pulse_id, ok=True)

    def next_context(self, started=None):
        return hourly_continuity.context(self.manager, self.pair(started or self.clock))

    def test_restart_after_durable_close_before_pulse_finish_then_next_actual_input(self):
        # Legacy decisions (if they still exist) are not evidence of the current clock.
        heartbeat.DECISIONS_PATH.write_text(json.dumps([
            {"ts": self.clock, "verdict": "legacy decision probe", "done": True},
        ]), encoding="utf-8")
        pulse = self.begin()
        provider_calls = []
        first_note = "authored result probe; checked the exact receipt"

        def provider(role, *, system, messages, **kwargs):
            current = run_context.current_run()
            provider_calls.append((current, json.dumps(messages, ensure_ascii=False),
                                   json.dumps(system, ensure_ascii=False)))
            row = next((r for r in social_pulse.runs() if r["id"] == current.hourly_pulse_id), None)
            if row is not None:
                self.assertEqual(row["window_run_id"], current.run_id)
            return agent.llm.LLMResponse(stop_reason="tool_use", blocks=[{
                "type": "tool_use", "id": "close", "name": "end_turn",
                "input": {"outcome": "done", "note": first_note if len(provider_calls) == 1 else ""},
            }])

        with patch.object(agent.llm, "chat", side_effect=provider):
            # The process loses the pulse finish write; the run and its closing word are committed.
            with patch.object(social_pulse, "finish", side_effect=OSError("crash gap")):
                with self.assertRaisesRegex(OSError, "crash gap"):
                    asyncio.run(runner._run_social_pulse(pulse))
            first, = social_pulse.runs()
            self.assertEqual(first["status"], "running")
            self.assertEqual(first["window_run_id"], provider_calls[0][0].run_id)

            self.manager = run_manager.RunManager(self.base)  # cold lookup, not the former process slot
            prior_run = self.manager.context(first["window_run_id"])
            self.assertEqual(prior_run.status, "done")
            self.assertEqual(prior_run.hourly_pulse_id, pulse)
            second = self.begin(self.clock)
            asyncio.run(runner._run_social_pulse(second))
            first, following = social_pulse.runs()
            self.assertEqual(first["status"], "interrupted")  # telemetry still tells the truth
            self.assertEqual(following["status"], "done")
            self.assertEqual(len(provider_calls), 2)
            current, messages, system = provider_calls[1]
            self.assertEqual((current.scope, current.principal_id, current.origin_chat_id),
                             ("owner", "praxis:self", None))
            self.assertNotEqual(current.run_id, prior_run.run_id)
            for marker in (HEADER, first_note, pulse, prior_run.run_id, ":evt:"):
                self.assertIn(marker, messages)
                self.assertNotIn(marker, system)
            for absent in ("legacy decision probe", "unseen-hash-probe", "unseen-subject-probe"):
                self.assertNotIn(absent, messages + system)
            self.assertNotIn(HEADER, provider_calls[0][1])
            self.assertNotIn("unseen-hash-probe", provider_calls[0][1] + provider_calls[0][2])
            self.assertEqual(social_pulse.mailbox_seen(), set())

            # A separate ordinary self window does not implicitly consume the note.
            agent.task_window("nonhourly probe", mailbox_index="")
            self.assertNotIn(HEADER, provider_calls[2][1])

    def test_valid_task_control_from_recovered_run_is_bounded_and_targeted(self):
        prior = self.pair()
        self.land(prior, status="running", finish=False)
        # Re-open the durable store while the only association is still the dispatch claim.
        self.manager = run_manager.RunManager(self.base)
        run = self.manager.context(prior.run_id)
        self.assertEqual(run.hourly_pulse_id, prior.pulse_id)
        self.assertEqual(self.manager.recover(run_ids=[run.run_id])[0]["to"], "paused")
        self.manager.resume(run.run_id, actor="test:owner", reason="resume original run")
        self.manager.transition(run.run_id, "blocked", details={"task_control": {
            "action": "blocked", "blocker": "б" * 900, "prompt": "unselected content",
        }})
        current = self.pair(self.clock)
        note = hourly_continuity.context(self.manager, current)
        word = json.loads(note.split("\n", 1)[1])
        self.assertEqual((word["tool"], word["outcome"], word["blocker"]),
                         ("task_control", "blocked", "б" * 240))
        self.assertNotIn("private goal", note)
        self.assertNotIn("unselected content", note)
        with self.assertRaises(ValueError):
            hourly_continuity.context(self.manager, social_pulse.HourlyWindow(
                current.pulse_id, run_context.new_run_id()))
        with self.assertRaises(ValueError):
            social_pulse.bind_window(current)
        self.assertEqual(hourly_continuity.context(self.manager, current, now=self.clock - 1), "")
        self.assertEqual(hourly_continuity.context(self.manager, current, now=self.clock + 3 * 86400), "")

    @staticmethod
    def closing_response(tool, note):
        return agent.llm.LLMResponse(stop_reason="tool_use", blocks=[{
            "type": "tool_use", "id": "close", "name": tool,
            "input": ({"outcome": "done", "note": note} if tool == "end_turn"
                      else {"action": "done", "evidence": note}),
        }])

    def _interrupt_closing_response(self, response, tool, has_checkpoint, *, expected_results=None):
        class ProcessExit(BaseException):
            pass

        seam = "_finish_durable_run" if has_checkpoint else "_persist_tool_loop_checkpoint"
        prior = social_pulse.HourlyWindow(self.begin(), run_context.new_run_id())
        with patch.object(agent.llm, "chat", return_value=response) as provider, \
                patch.object(agent, "_observed_tool_call", wraps=agent._observed_tool_call) as hand, \
                patch.object(agent, seam, side_effect=ProcessExit):
            with self.assertRaises(ProcessExit):
                asyncio.run(runner._task_window("hourly probe", mailbox_index="",
                                                keep_transport=True, hourly=prior))
        provider.assert_called_once()
        expected = expected_results if expected_results is not None else [("close", tool, "ok")]
        self.assertEqual(hand.call_count, len(expected))
        agent.work_loop.release(prior.run_id)
        self.manager = run_manager.RunManager(self.base)
        events = self.manager.events(prior.run_id, strict=True)
        results = [row for row in events if row["kind"] == "tool_result"]
        self.assertEqual([(row["call_id"], row["name"], row["metadata"]["outcome"])
                          for row in results], expected)
        self.assertEqual(any(row["kind"] == "run_checkpoint" for row in events), has_checkpoint)
        self.assertEqual(self.manager.context(prior.run_id).status, "running")
        self.assertEqual(self.manager.recover(run_ids=[prior.run_id])[0]["to"], "paused")
        return prior

    def _resume_without_model_hand_or_delivery(self, prior):
        with patch.object(agent.llm, "chat", side_effect=AssertionError("called model")) as provider, \
                patch.object(agent, "_observed_tool_call", side_effect=AssertionError("replayed hand")) as hand, \
                patch.object(agent, "run_delivery_started", side_effect=AssertionError("attempted delivery")) as send:
            report = agent.resume_durable_run(prior.run_id)
        provider.assert_not_called()
        hand.assert_not_called()
        send.assert_not_called()
        return report

    def _assert_recovered_word(self, prior, tool, field, note):
        self.assertEqual(self.manager.context(prior.run_id).status, "done")
        word = json.loads(self.next_context().split("\n", 1)[1])
        self.assertEqual((word["run_id"], word["pulse_id"], word["tool"], word[field]),
                         (prior.run_id, prior.pulse_id, tool, note))

    def test_resume_lands_accepted_word_at_both_durable_boundaries(self):
        for tool, field in (("end_turn", "note"), ("task_control", "evidence")):
            for has_checkpoint in (False, True):
                with self.subTest(tool=tool, has_checkpoint=has_checkpoint), patch.object(
                        social_pulse, "STATE_PATH", self.base / f"{tool}-{has_checkpoint}.json"):
                    note = f"accepted word from {tool} (checkpoint={has_checkpoint})"
                    prior = self._interrupt_closing_response(
                        self.closing_response(tool, note), tool, has_checkpoint)
                    report = self._resume_without_model_hand_or_delivery(prior)
                    self.assertEqual(report.get("status"), "completed", report)
                    self._assert_recovered_word(prior, tool, field, note)

    def _interrupt_wrapped_closing_response(self, tool, note, *, has_checkpoint):
        with patch.dict(os.environ, {"PRAXIS_TOOLS_POINTERS": "on"}):
            response = self.closing_response(tool, note)
            block = response.blocks[0]
            optional = "summary" if tool == "task_control" else "note"
            args = {optional: None, **block["input"]}
            wrapped = dict(block, name="call", input={
                "name": f" {tool} ", "args_json": json.dumps(args),
            })
            response.blocks[0] = wrapped
            prior = self._interrupt_closing_response(response, tool, has_checkpoint)
        events = self.manager.events(prior.run_id, strict=True)
        output, = [row for row in events if row["kind"] == "model_output"]
        model_output = run_resume.read_full_json_result(self.manager, prior.run_id, output["result"])
        self.assertEqual(model_output["blocks"], [wrapped])
        started, = [row for row in events if row["kind"] == "tool_started"]
        self.assertEqual((started["call_id"], started["tool"], started["args"]),
                         (block["id"], tool, block["input"]))
        return prior

    def test_resume_wrapped_task_control_before_checkpoint(self):
        note = "accepted wrapped task_control before checkpoint"
        prior = self._interrupt_wrapped_closing_response("task_control", note, has_checkpoint=False)
        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual((report["plan_kind"], report["status"]),
                         ("replay_model_tool_response", "completed"), report)
        self._assert_recovered_word(prior, "task_control", "evidence", note)

    def test_resume_wrapped_end_turn_after_checkpoint(self):
        note = "accepted wrapped end_turn after checkpoint"
        prior = self._interrupt_wrapped_closing_response("end_turn", note, has_checkpoint=True)
        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual((report["plan_kind"], report["status"]),
                         ("checkpoint_control", "completed"), report)
        self._assert_recovered_word(prior, "end_turn", "note", note)

    @staticmethod
    def _wrapped_wait_without_note():
        return {"type": "tool_use", "id": "refused-wait", "name": "call",
                "input": {"name": "end_turn", "args_json": '{"outcome":"wait"}'}}

    def _interrupt_wrapped_done_with_refused_wait(self, note, *, has_checkpoint, refusal_first=False):
        response = self.closing_response("end_turn", note)
        done = response.blocks[0]
        done.update(name="call", input={"name": "end_turn", "args_json": json.dumps(done["input"])})
        response.blocks.append(self._wrapped_wait_without_note())
        expected_results = [("close", "end_turn", "ok"), ("refused-wait", "end_turn", "refused")]
        if refusal_first:
            response.blocks.reverse()
            expected_results.reverse()
        with patch.dict(os.environ, {"PRAXIS_TOOLS_POINTERS": "on"}):
            prior = self._interrupt_closing_response(
                response, "end_turn", has_checkpoint, expected_results=expected_results,
            )
        events = self.manager.events(prior.run_id, strict=True)
        output, = [row for row in events if row["kind"] == "model_output"]
        model_output = run_resume.read_full_json_result(self.manager, prior.run_id, output["result"])
        self.assertEqual(model_output["blocks"], response.blocks)
        done_start, done_result = [
            row for row in events if row.get("call_id") == "close"
            and row["kind"] in {"tool_started", "tool_result"}]
        wait_start, wait_result = [
            row for row in events if row.get("call_id") == "refused-wait"
            and row["kind"] in {"tool_started", "tool_result"}]
        first_start, first_result, second_start, second_result = (
            (wait_start, wait_result, done_start, done_result) if refusal_first
            else (done_start, done_result, wait_start, wait_result)
        )
        self.assertLess(first_start["seq"], first_result["seq"])
        self.assertLess(first_result["seq"], second_start["seq"])
        self.assertLess(second_start["seq"], second_result["seq"])
        self.assertEqual((done_start["call_id"], done_start["tool"], done_start["args"],
                          done_result["metadata"]["target"]),
                         ("close", "end_turn", {"outcome": "done", "note": note}, "done"))
        self.assertEqual((wait_start["call_id"], wait_start["tool"], wait_start["args"]),
                         ("refused-wait", "end_turn", {"outcome": "wait"}))
        self.assertEqual(wait_result["metadata"].get("outbound"), [])
        self.assertNotIn("target", wait_result["metadata"])
        if has_checkpoint:
            checkpoint, = [row for row in events if row["kind"] == "run_checkpoint"]
            self.assertLess(second_result["seq"], checkpoint["seq"])
        return prior

    def _assert_resume_wrapped_done_with_refused_wait(self, has_checkpoint, *, refusal_first=False):
        note = "accepted done survives a refused wait in the same batch"
        prior = self._interrupt_wrapped_done_with_refused_wait(
            note, has_checkpoint=has_checkpoint, refusal_first=refusal_first,
        )
        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual((report["plan_kind"], report["status"]),
                         ("checkpoint_control" if has_checkpoint else "replay_model_tool_response",
                          "completed"), report)
        events = self.manager.events(prior.run_id, strict=True)
        self.assertEqual([row["to_status"] for row in events if row["kind"] == "status_changed"],
                         ["running", "paused", "running", "done"])
        self._assert_recovered_word(prior, "end_turn", "note", note)

    def test_resume_wrapped_done_with_refused_wait_before_checkpoint(self):
        self._assert_resume_wrapped_done_with_refused_wait(has_checkpoint=False)

    def test_resume_wrapped_done_with_refused_wait_after_checkpoint(self):
        self._assert_resume_wrapped_done_with_refused_wait(has_checkpoint=True)

    def test_resume_wrapped_done_with_earlier_refused_wait_before_checkpoint(self):
        self._assert_resume_wrapped_done_with_refused_wait(has_checkpoint=False, refusal_first=True)

    def test_resume_wrapped_done_with_earlier_refused_wait_after_checkpoint(self):
        self._assert_resume_wrapped_done_with_refused_wait(has_checkpoint=True, refusal_first=True)

    def test_resume_wrapped_refused_wait_without_accepted_sibling_stays_blocked(self):
        response = agent.llm.LLMResponse(stop_reason="tool_use", blocks=[self._wrapped_wait_without_note()])
        with patch.dict(os.environ, {"PRAXIS_TOOLS_POINTERS": "on"}):
            prior = self._interrupt_closing_response(
                response, "end_turn", False, expected_results=[("refused-wait", "end_turn", "refused")],
            )
        self._assert_wrapped_refusal_stays_blocked(prior)
        self.assertEqual(self.next_context(), "")

    def _assert_wrapped_refusal_stays_blocked(self, prior):
        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual(report["plan_kind"], "blocked", report)
        self.assertFalse(report["lease_acquired"])
        self.assertEqual(self.manager.context(prior.run_id).status, "paused")
        return report

    def test_resume_wrapped_done_rejects_uncertain_refused_sibling(self):
        for change, reason in (
            ("intent", "intent differs from model block"),
            ("schema", "wrapped hourly closing hand was not accepted"),
            ("outcome", "wrapped hourly closing hand was not accepted"),
            ("target", "wrapped hourly closing hand was not accepted"),
            ("unfinished", "wrapped hourly closing intent has no completed hand"),
        ):
            with self.subTest(change=change), patch.object(
                    social_pulse, "STATE_PATH", self.base / f"refusal-{change}.json"):
                prior = self._interrupt_wrapped_done_with_refused_wait("accepted done", has_checkpoint=True)
                rows = self.manager.events(prior.run_id, strict=True)
                started, result = [row for row in rows if row.get("call_id") == "refused-wait"
                                   and row["kind"] in {"tool_started", "tool_result"}]
                if change == "intent":
                    started["args"]["note"] = "not in model intent"
                elif change == "schema":
                    result["metadata"]["schema"] = "unknown.schema"
                elif change == "outcome":
                    result["metadata"]["outcome"] = "unknown"
                elif change == "target":
                    result["metadata"]["target"] = "done"
                elif change == "unfinished":
                    result["kind"] = "tool_uncertain_error"
                wal = self.manager.path(prior.run_id) / "events.jsonl"
                wal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                report = self._assert_wrapped_refusal_stays_blocked(prior)
                self.assertIn(reason, report["reason"])
                self.assertEqual(self.next_context(), "")

    def _interrupt_nested_hourly_close(self, *, has_checkpoint=True):
        note = "later nested word"
        child_id = "hand-" + hashlib.sha256(b"outer\x00close").hexdigest()
        response = self.closing_response("end_turn", "top-level word")
        response.blocks.append({"type": "tool_use", "id": "outer", "name": "python",
            "input": {"code": "r = h.hand('end_turn', outcome='done', "
                              "note='later nested word', _request_id='close')\n"
                              "assert r['outcome'] == 'ok'"}})
        with patch.dict(os.environ, {"PRAXIS_TOOLS_POINTERS": "off"}):
            prior = self._interrupt_closing_response(
                response, "end_turn", has_checkpoint,
                expected_results=[("close", "end_turn", "ok"), (child_id, "end_turn", "ok"),
                                  ("outer", "python", "ok")],
            )
        return prior, child_id, note

    def _assert_resume_lands_later_nested_word(self, *, has_checkpoint):
        prior, child_id, note = self._interrupt_nested_hourly_close(has_checkpoint=has_checkpoint)
        rows = self.manager.events(prior.run_id, strict=True)
        requested, = [r for r in rows if r["kind"] == "hand_requested"]
        self.assertEqual((requested["call_id"], requested["parent_call_id"]), (child_id, "outer"))
        receipts = {(r.get("call_id"), r["kind"]): r["seq"] for r in rows
                    if r["kind"] in {"tool_started", "tool_result"}}
        chain = [receipts["close", "tool_result"], receipts["outer", "tool_started"],
                 requested["seq"], receipts[child_id, "tool_started"],
                 receipts[child_id, "tool_result"], receipts["outer", "tool_result"]]
        if has_checkpoint:
            checkpoint, = [r for r in rows if r["kind"] == "run_checkpoint"]
            chain.append(checkpoint["seq"])
        self.assertTrue(all(left < right for left, right in zip(chain, chain[1:])), chain)
        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual((report["plan_kind"], report["status"]),
                         ("checkpoint_control" if has_checkpoint else "replay_model_tool_response",
                          "completed"), report)
        self._assert_recovered_word(prior, "end_turn", "note", note)

    def test_pre_response_checkpoint_can_land_later_nested_word(self):
        self._assert_resume_lands_later_nested_word(has_checkpoint=False)

    def test_post_response_checkpoint_can_land_later_nested_word(self):
        self._assert_resume_lands_later_nested_word(has_checkpoint=True)

    def test_post_response_checkpoint_accepts_unique_legacy_resultrefs(self):
        prior, _, note = self._interrupt_nested_hourly_close()
        rows = self.manager.events(prior.run_id, strict=True)
        for row in rows:
            if row["kind"] == "tool_result":
                self.assertEqual(row["result"].pop("call_id"), row["call_id"])
        wal = self.manager.path(prior.run_id) / "events.jsonl"
        wal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual((report["plan_kind"], report["status"]),
                         ("checkpoint_control", "completed"), report)
        self._assert_recovered_word(prior, "end_turn", "note", note)

    def test_post_response_checkpoint_rejects_unproved_closing_state(self):
        for change, reason in (
            ("other_word", "closing state differs"),
            ("late_result", "checkpoint ordering"),
            ("result_after_parent", "nested closing parent interval"),
            ("wrong_parent", "nested closing parent"),
            ("other_intent", "nested closing intent"),
            ("wrong_slot", "closing state differs"),
            ("foreign_ref", "ResultRef call_id differs"),
            ("legacy_reused_ref", "ResultRef is reused"),
        ):
            with self.subTest(change=change), patch.object(
                    social_pulse, "STATE_PATH", self.base / f"nested-{change}.json"):
                snapshot = agent.work_loop.snapshot

                def project_state():
                    state = snapshot()
                    if change == "other_word":
                        state["finish_note"] = "done: unaccepted note"
                    elif change == "wrong_slot":
                        state["control"] = {"action": "wait", "wake_on": "unaccepted signal"}
                    return state

                with patch.object(agent.work_loop, "snapshot", side_effect=project_state):
                    prior, child_id, _ = self._interrupt_nested_hourly_close()
                rows = self.manager.events(prior.run_id, strict=True)
                requested, = [r for r in rows if r["kind"] == "hand_requested"]
                child_result, = [r for r in rows if r.get("call_id") == child_id
                                 and r["kind"] == "tool_result"]
                if change == "wrong_parent":
                    requested["parent_call_id"] = "close"
                elif change == "other_intent":
                    requested["intent_sha256"] = "0" * 64
                elif change == "late_result":
                    checkpoint, = [r for r in rows if r["kind"] == "run_checkpoint"]
                    rows.remove(child_result)
                    rows.insert(rows.index(checkpoint) + 1, child_result)
                elif change == "result_after_parent":
                    parent_result, = [r for r in rows if r.get("call_id") == "outer"
                                      and r["kind"] == "tool_result"]
                    rows.remove(child_result)
                    rows.insert(rows.index(parent_result) + 1, child_result)
                elif change in {"foreign_ref", "legacy_reused_ref"}:
                    direct_result, = [r for r in rows if r.get("call_id") == "close"
                                      and r["kind"] == "tool_result"]
                    if change == "legacy_reused_ref":
                        for r in rows:
                            if r["kind"] == "tool_result":
                                r["result"].pop("call_id")
                    child_result["result"] = direct_result["result"]
                for seq, row in enumerate(rows, 1):
                    row.update(seq=seq, id=f"{prior.run_id}:evt:{seq:08d}")
                wal = self.manager.path(prior.run_id) / "events.jsonl"
                wal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                report = self._assert_wrapped_refusal_stays_blocked(prior)
                self.assertIn(reason, report["reason"])
                self.assertEqual(self.next_context(), "")

    def test_resume_wrapped_end_turn_rejects_different_wal_arguments(self):
        prior = self._interrupt_wrapped_closing_response("end_turn", "accepted note", has_checkpoint=True)
        wal = self.manager.path(prior.run_id) / "events.jsonl"
        rows = self.manager.events(prior.run_id, strict=True)
        started, = [row for row in rows if row["kind"] == "tool_started"]
        started["args"]["note"] = "different note in WAL"
        wal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual(report["plan_kind"], "blocked", report)
        self.assertIn("tool close intent differs from model block", report["reason"])
        self.assertFalse(report["lease_acquired"])
        self.assertEqual(self.manager.context(prior.run_id).status, "paused")
        self.assertEqual(self.next_context(), "")

    def test_refused_tool_result_does_not_supply_a_closing_word(self):
        class ProcessExit(BaseException):
            pass

        prior = social_pulse.HourlyWindow(self.begin(), run_context.new_run_id())
        response = self.closing_response("end_turn", "unaccepted closing input")

        def refused(outcome, note):
            agent.tool_report.note(outcome="refused", target=outcome)
            return f"Ход закрыт ({outcome}). Заметка: {note}"  # prose is not acceptance

        with patch.object(agent.llm, "chat", return_value=response), \
                patch.dict(agent.TOOL_IMPL, {"end_turn": refused}), \
                patch.object(agent, "_persist_tool_loop_checkpoint", side_effect=ProcessExit):
            with self.assertRaises(ProcessExit):
                asyncio.run(runner._task_window("hourly probe", mailbox_index="",
                                                keep_transport=True, hourly=prior))
        agent.work_loop.release(prior.run_id)
        self.assertEqual(self.manager.recover(run_ids=[prior.run_id])[0]["to"], "paused")
        plan = run_resume.plan_resume(self.manager, prior.run_id)
        self.assertEqual(plan.kind, "replay_model_tool_response", plan.reason)
        call, = plan.tool_calls
        self.assertEqual(call.state, "completed")
        self.assertIsNone(call.closing_word)
        with patch.object(agent.llm, "chat", side_effect=AssertionError("model reached")) as provider, \
                patch.object(agent, "_observed_tool_call", side_effect=AssertionError("replayed hand")) as hand:
            agent.resume_durable_run(prior.run_id)
        provider.assert_called_once()
        hand.assert_not_called()
        self.assertEqual(self.next_context(), "")

    def test_hourly_wait_is_not_landed_twice_after_another_restart(self):
        class ProcessExit(BaseException):
            pass

        prior = social_pulse.HourlyWindow(self.begin(), run_context.new_run_id())
        response = self.closing_response("task_control", "")
        response.blocks[0]["input"] = {"action": "wait", "wake_on": "fixture signal"}
        with patch.object(agent.llm, "chat", return_value=response), \
                patch.object(agent, "_persist_tool_loop_checkpoint", side_effect=ProcessExit):
            with self.assertRaises(ProcessExit):
                asyncio.run(runner._task_window("hourly probe", mailbox_index="",
                                                keep_transport=True, hourly=prior))
        agent.work_loop.release(prior.run_id)
        self.assertEqual(self.manager.recover(run_ids=[prior.run_id])[0]["to"], "paused")
        with patch.dict(os.environ, {"PRAXIS_WORK_WAIT_FLOOR_SEC": "0"}), \
                patch.object(agent.llm, "chat", side_effect=AssertionError("model called")) as provider:
            report = agent.resume_durable_run(prior.run_id)
        provider.assert_not_called()
        self.assertEqual(report["status"], "completed", report)
        self.assertEqual(self.manager.context(prior.run_id).status, "paused")
        # Reopen at the due wake, then die before the next model_input can be stored.
        with patch.object(agent, "_model_call", side_effect=ProcessExit):
            with self.assertRaises(ProcessExit):
                agent.resume_durable_run(prior.run_id)
        agent.work_loop.release(prior.run_id)
        self.assertEqual(self.manager.recover(run_ids=[prior.run_id])[0]["to"], "paused")
        plan = run_resume.plan_resume(self.manager, prior.run_id)
        self.assertEqual(plan.kind, "continue_checkpoint", plan.reason)
        self.assertIsNone(plan.checkpoint["work_loop"]["control"])
        self.assertFalse(plan.checkpoint["work_loop"]["finished"])

    def test_pending_bridge_lands_accepted_nested_word_without_model_or_replay(self):
        class ProcessExit(BaseException):
            pass

        prior = social_pulse.HourlyWindow(self.begin(), run_context.new_run_id())
        note = "accepted by the resumed bridge"
        response = agent.llm.LLMResponse(stop_reason="tool_use", blocks=[{
            "type": "tool_use", "id": "outer", "name": "python",
            "input": {"code": f"r = h.hand('end_turn', outcome='done', note={note!r}, "
                              "_request_id='close')\nassert r['outcome'] == 'ok'"},
        }])
        with patch.dict(os.environ, {"PRAXIS_TOOLS_POINTERS": "off"}), \
                patch.object(agent.llm, "chat", return_value=response), \
                patch.object(self.manager, "start_tool", side_effect=ProcessExit):
            with self.assertRaises(ProcessExit):
                asyncio.run(runner._task_window("hourly probe", mailbox_index="",
                                                keep_transport=True, hourly=prior))
        agent.work_loop.release(prior.run_id)
        self.assertEqual(self.manager.recover(run_ids=[prior.run_id])[0]["to"], "paused")
        first_plan = run_resume.plan_resume(self.manager, prior.run_id)
        self.assertEqual(first_plan.kind, "replay_model_tool_response", first_plan.reason)
        self.assertEqual([call.state for call in first_plan.tool_calls], ["pending_start"])
        # Lose the process again after the recovered checkpoint is persisted,
        # before the accepted word can land. Neither hand may be called again.
        with patch.object(agent.llm, "chat", side_effect=AssertionError("called model")) as model, \
                patch.object(agent, "_finish_durable_run", side_effect=ProcessExit):
            with self.assertRaises(ProcessExit):
                agent.resume_durable_run(prior.run_id)
        model.assert_not_called()
        agent.work_loop.release(prior.run_id)
        receipts = [row for row in self.manager.events(prior.run_id, strict=True)
                    if row["kind"] == "tool_result"]
        self.assertEqual([row["name"] for row in receipts], ["end_turn", "python"])
        self.assertEqual(self.manager.recover(run_ids=[prior.run_id])[0]["to"], "paused")
        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual((report["plan_kind"], report["status"]), ("checkpoint_control", "completed"), report)
        self._assert_recovered_word(prior, "end_turn", "note", note)

    def test_terminal_output_cannot_land_stale_hourly_control(self):
        class ProcessExit(BaseException):
            pass

        prior = social_pulse.HourlyWindow(self.begin(), run_context.new_run_id())
        tool_response = agent.llm.LLMResponse(stop_reason="tool_use", blocks=[{
            "type": "tool_use", "id": "read", "name": "fs_read", "input": {"path": "fixture"},
        }])
        terminal = agent.llm.LLMResponse(stop_reason="end_turn", blocks=[{
            "type": "text", "text": "terminal text, not a closing hand",
        }])
        capture = agent.work_loop.snapshot

        def stale_state():
            return {**capture(), "control": {"action": "done", "evidence": "stale fixture"}}

        with patch.dict(os.environ, {"PRAXIS_TOOLS_POINTERS": "off"}), \
                patch.object(agent.llm, "chat", side_effect=[tool_response, terminal]), \
                patch.dict(agent.TOOL_IMPL, {"fs_read": lambda **_: "fixture"}), \
                patch.object(agent.work_loop, "snapshot", side_effect=stale_state), \
                patch.object(agent, "_work_loop_continue", side_effect=ProcessExit):
            with self.assertRaises(ProcessExit):
                asyncio.run(runner._task_window("hourly probe", mailbox_index="",
                                                keep_transport=True, hourly=prior))
        agent.work_loop.release(prior.run_id)
        events = self.manager.events(prior.run_id, strict=True)
        self.assertEqual([row["name"] for row in events if row["kind"] == "tool_result"], ["fs_read"])
        outputs = [row for row in events if row["kind"] == "model_output"]
        checkpoint, = [row for row in events if row["kind"] == "run_checkpoint"]
        self.assertEqual(len(outputs), 2)
        self.assertLess(checkpoint["seq"], outputs[-1]["seq"])
        self.assertEqual(self.manager.recover(run_ids=[prior.run_id])[0]["to"], "paused")
        report = self._resume_without_model_hand_or_delivery(prior)
        self.assertEqual(report["plan_kind"], "blocked", report)
        self.assertFalse(report["lease_acquired"], report)
        self.assertEqual(self.manager.context(prior.run_id).status, "paused")
        self.assertEqual(self.next_context(), "")

    def test_consumed_wait_with_wrapped_refusal_survives_second_restart(self):
        class ProcessExit(BaseException):
            pass

        response = agent.llm.LLMResponse(stop_reason="tool_use", blocks=[{
            "type": "tool_use", "id": "wait", "name": "call",
            "input": {"name": "task_control",
                      "args_json": '{"action":"wait","wake_on":"fixture signal"}'},
        }, self._wrapped_wait_without_note()])
        with patch.dict(os.environ, {"PRAXIS_TOOLS_POINTERS": "on", "PRAXIS_WORK_WAIT_FLOOR_SEC": "0"}):
            prior = self._interrupt_closing_response(
                response, "task_control", False,
                expected_results=[("wait", "task_control", "ok"), ("refused-wait", "end_turn", "refused")],
            )
            report = self._resume_without_model_hand_or_delivery(prior)
            self.assertEqual(report["status"], "completed", report)
            self.assertEqual(self.manager.context(prior.run_id).status, "paused")
            with patch.object(agent, "_model_call", side_effect=ProcessExit):
                with self.assertRaises(ProcessExit):
                    agent.resume_durable_run(prior.run_id)
        agent.work_loop.release(prior.run_id)
        self.assertEqual(self.manager.recover(run_ids=[prior.run_id])[0]["to"], "paused")
        plan = run_resume.plan_resume(self.manager, prior.run_id)
        self.assertEqual(plan.kind, "continue_checkpoint", plan.reason)
        self.assertIsNone(plan.checkpoint["work_loop"]["control"])
        self.assertFalse(plan.checkpoint["work_loop"]["finished"])
        self.assertEqual(plan.checkpoint["work_loop"]["finish_note"], "")

    def test_recap_error_after_transition_keeps_word_for_next_independent_hour(self):
        for tool in ("task_control", "end_turn"):
            for phase in ("write_recap", "promote_recap"):
                with self.subTest(tool=tool, phase=phase), patch.object(
                        social_pulse, "STATE_PATH", self.base / f"{tool}-{phase}.json"):
                    calls = []
                    note = f"author checked the receipt before {phase} failed ({tool})"

                    def provider(role, *, system, messages, **kwargs):
                        calls.append((run_context.current_run(), json.dumps(messages, ensure_ascii=False),
                                      json.dumps(system, ensure_ascii=False)))
                        return self.closing_response(tool, note) if len(calls) == 1 else self.closing_response("end_turn", "")

                    def broken_recap(run_id, *args, **kwargs):
                        self.assertEqual(self.manager.context(run_id).status, "done")
                        raise OSError(f"artificial {phase} failure")

                    self.manager = run_manager.RunManager(self.base, promotion_hook=lambda *_: "test:promotion")
                    pulse = self.begin()
                    with patch.object(agent.llm, "chat", side_effect=provider) as chat, \
                            patch.object(agent, "run_delivery_started", side_effect=AssertionError("attempted delivery")) as send:
                        with patch.object(self.manager, phase, side_effect=broken_recap) as recap:
                            asyncio.run(runner._run_social_pulse(pulse))
                        recap.assert_called_once()
                        chat.assert_called_once()
                        prior, = social_pulse.runs()
                        self.assertEqual(prior["status"], "done")
                        run_id = prior["window_run_id"]
                        self.assertEqual(calls[0][0].run_id, run_id)
                        self.assertNotIn(HEADER, calls[0][1])
                        self.assertNotIn(note, calls[0][1] + calls[0][2])
                        self.assertEqual(self.manager.manifest(run_id)["recap"]["status"],
                                         "pending" if phase == "write_recap" else "written")
                        self.manager = run_manager.RunManager(self.base)
                        agent.task_window("ordinary self probe", mailbox_index="")
                        self.assertNotIn(HEADER, calls[1][1])
                        self.assertNotIn(note, calls[1][1] + calls[1][2])
                        following = self.begin(self.clock)
                        asyncio.run(runner._run_social_pulse(following))
                    send.assert_not_called()
                    self.assertEqual(chat.call_count, 3)
                    current, messages, system = calls[2]
                    self.assertEqual((current.scope, current.principal_id, current.hourly_pulse_id),
                                     ("owner", "praxis:self", following))
                    self.assertIsNone(current.origin_chat_id)
                    self.assertIsNone(current.delivery_chat_id)
                    self.assertNotEqual(current.run_id, run_id)
                    for marker in (HEADER, note, pulse, run_id):
                        self.assertIn(marker, messages)
                        self.assertNotIn(marker, system)
                    self.assertEqual(social_pulse.mailbox_seen(), set())

    def test_wrong_durable_identity_or_closing_evidence_never_used(self):
        cases = (
            {"pulse_id": "pulse_000000000000"}, {"scope": "group", "chat": "42"},
            {"principal": "42"}, {"kind": "coding_window"}, {"status": "running"},
            {"status": "failed"},
            {"details": {"task_control": {"action": "wait", "wake_on": "invalid"}}},
            {"details": {"hourly_end_turn": {"action": "done", "note": "foreign", "run_id": "other"}}},
            {"status": "done", "details": {"hourly_end_turn": {"action": "done"}}},
            {"status": "done", "details": {}},
        )
        for i, case in enumerate(cases):
            with self.subTest(case=case), patch.object(social_pulse, "STATE_PATH", self.base / f"pulse-{i}.json"):
                prior = self.pair()
                self.land(prior, **{"details": {"hourly_end_turn": {"action": "done", "note": "must not appear"}}, **case})
                self.assertEqual(self.next_context(), "")

    def test_no_durable_run_and_no_closure_in_legacy_or_failed_pulse(self):
        # The previous claim was persisted but process death preceded run creation.
        self.pair()
        self.assertEqual(self.next_context(), "")
        with patch.object(social_pulse, "STATE_PATH", self.base / "legacy.json"):
            prior = self.begin()
            social_pulse.finish(prior, ok=True)
            self.assertEqual(self.next_context(), "")
        with patch.object(social_pulse, "STATE_PATH", self.base / "failed.json"):
            prior = self.pair()
            self.land(prior, details={"hourly_end_turn": {"action": "done", "note": "not confirmed"}}, finish=False)
            social_pulse.finish(prior.pulse_id, ok=False)
            self.assertEqual(self.next_context(), "")

    def test_agent_mismatch_never_calls_voice_or_acks_mail(self):
        pulse = self.begin()
        def wrong(**kwargs):
            return run_context.RunContext.create(
                run_id=kwargs["run_id"], kind="task_window", scope="owner",
                principal_id="praxis:self", hourly_pulse_id="pulse_000000000000", goal="other instance",
            )
        with patch.object(agent, "_create_durable_run", side_effect=wrong), \
                patch.object(agent, "_voice") as voice:
            asyncio.run(runner._run_social_pulse(pulse))
        voice.assert_not_called()
        first, = social_pulse.runs()
        self.assertEqual(first["status"], "failed")
        self.assertEqual(social_pulse.mailbox_seen(), set())
        self.assertEqual(self.next_context(), "")

    def test_runner_cancellation_keeps_claim_for_recovery(self):
        pulse = self.begin()

        async def cancel(*args, hourly, **kwargs):
            social_pulse.bind_window(hourly)
            self.land(hourly, details={"hourly_end_turn": {"action": "done", "note": "committed word"}},
                      finish=False)
            raise asyncio.CancelledError

        with patch.object(runner, "_task_window", cancel):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(runner._run_social_pulse(pulse))
        first, = social_pulse.runs()
        self.assertEqual(first["status"], "running")
        self.assertIn("committed word", self.next_context())

    def test_malformed_claim_timestamp_is_not_rewritten(self):
        for value in ("Infinity", False, float("nan"), 1e100):
            with self.subTest(value=value):
                data = {"version": 1, "last_started_at": 100, "runs": [
                    {"id": "pulse_000000000000", "status": "done", "started_at": value},
                ]}
                original = json.dumps(data).encode()
                self.path.write_bytes(original)
                with self.assertRaises(ValueError):
                    social_pulse.begin(now=self.clock)
                self.assertEqual(self.path.read_bytes(), original)
