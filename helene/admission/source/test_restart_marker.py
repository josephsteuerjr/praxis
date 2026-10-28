"""Смёрженное предложение стоит ОДНОГО перезапуска, а не двух.

31.07 на живом дереве: `selfdev.merge()` пишет заявку контура, она читает в дневнике
«перезапущусь на новом коде» и зовёт `restart_self` (07:04), а поднявшийся процесс
находит заявку непогашенной и уходит снова («перезапуск по запросу контура» 07:06:34).
Два перезапуска подряд на каждый её самомёрж — и второй она не заказывала.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import agent
import run_context
import run_manager
import selfdev


class RestartMarkerIsSatisfiedByAnyRestart(unittest.TestCase):
    def setUp(self):
        selfdev.clear_restart_request()
        self.addCleanup(selfdev.clear_restart_request)
        state_dir = Path(tempfile.mkdtemp(prefix="praxis-restart-marker-state-"))
        state_patcher = mock.patch.object(agent, "STATE_DIR", state_dir)
        state_patcher.start()
        self.addCleanup(state_patcher.stop)
        self.addCleanup(shutil.rmtree, state_dir, ignore_errors=True)

    def test_her_own_restart_clears_the_contour_request(self):
        selfdev.request_restart("proposal deadbeef merged")
        self.assertIn("merged", selfdev.restart_requested())
        with mock.patch.object(agent, "_schedule_exit"):
            agent.tool_restart_self("загрузиться на новом коде")
        self.assertEqual(selfdev.restart_requested(), "",
                         "заявка контура уже удовлетворена — второй перезапуск лишний")

    def test_restart_without_a_pending_request_is_harmless(self):
        with mock.patch.object(agent, "_schedule_exit"):
            agent.tool_restart_self("просто так")
        self.assertEqual(selfdev.restart_requested(), "")

    def test_reason_still_lands_in_state_for_the_next_boot(self):
        with mock.patch.object(agent, "_schedule_exit"):
            agent.tool_restart_self("проверка причины")
        text = (agent.STATE_DIR / "restart_reason.txt").read_text(encoding="utf-8")
        self.assertIn("проверка причины", text)
        self.assertIn("restart_self", text)


class RestartReplayEffectIdempotence(unittest.TestCase):
    """A replayed owner DM may make a new run, but not a second restart exit."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="praxis-restart-replay-")
        self.addCleanup(self.temp.cleanup)
        self.manager = run_manager.RunManager(Path(self.temp.name))
        state_dir = Path(tempfile.mkdtemp(prefix="praxis-restart-replay-state-"))
        state_patcher = mock.patch.object(agent, "STATE_DIR", state_dir)
        state_patcher.start()
        self.addCleanup(state_patcher.stop)
        self.addCleanup(shutil.rmtree, state_dir, ignore_errors=True)
        self.previous_manager = agent._RUN_MANAGER
        agent._RUN_MANAGER = self.manager
        self.addCleanup(self._restore_manager)

    def _restore_manager(self):
        agent._RUN_MANAGER = self.previous_manager

    def _run(self, run_id: str):
        channel = agent.ChannelContext(
            chat_id="101", room_id="101", principal_id="101",
            origin_message_id=777, origin_text="перезапусти себя",
            is_dm=True, owner=True, known=True, addressed=True,
            address_message_id=777, address_kind="direct",
            reply_targets=((777, "Yegor", "перезапусти себя"),),
        )
        context = run_context.RunContext.create(
            run_id=run_id, kind="chat_turn", goal="restart",
            principal_id="101", scope="owner", origin_chat_id="101",
            origin_message_ids=(777,), delivery_chat_id="101", model_profile="voice",
        )
        return self.manager.create(
            context,
            agent._run_context_markdown(
                ctx=channel, kind=context.kind, goal=context.goal,
                conversation="Yegor: перезапусти себя", history=[], extra="",
            ),
        )

    def _started(self, context, call_id: str):
        self.manager.append_event(
            context.run_id, "tool_started", call_id=call_id,
            tool="restart_self", args={"reason": "test"},
            side_effect=True, idempotent=False,
        )

    def test_replayed_owner_dm_has_one_restart_winner(self):
        first = self._run("run-restart-first")
        replay = self._run("run-restart-replay")
        self._started(first, "first-call")
        self._started(replay, "replay-call")
        with (run_context.bind_run(replay),
              mock.patch.object(agent, "_schedule_exit") as exit_process,
              mock.patch.object(agent, "tool_journal"),
              mock.patch.object(agent.selfgit, "snapshot"),
              mock.patch.object(agent.selfdev, "clear_restart_request")):
            text = agent.tool_restart_self("same Telegram update")
        exit_process.assert_not_called()
        self.assertIn("уже принят", text)

    def test_first_owner_dm_restart_still_exits(self):
        first = self._run("run-restart-only")
        self._started(first, "only-call")
        with (run_context.bind_run(first),
              mock.patch.object(agent, "_schedule_exit") as exit_process,
              mock.patch.object(agent, "tool_journal"),
              mock.patch.object(agent.selfgit, "snapshot"),
              mock.patch.object(agent.selfdev, "clear_restart_request")):
            agent.tool_restart_self("first request")
        exit_process.assert_called_once()

    def test_direct_restart_without_durable_intent_is_not_suppressed(self):
        first = self._run("run-restart-earlier")
        self._started(first, "earlier-call")
        replay = self._run("run-restart-direct")
        with (run_context.bind_run(replay),
              mock.patch.object(agent, "_schedule_exit") as exit_process,
              mock.patch.object(agent, "tool_journal"),
              mock.patch.object(agent.selfgit, "snapshot"),
              mock.patch.object(agent.selfdev, "clear_restart_request")):
            agent.tool_restart_self("direct invocation")
        exit_process.assert_called_once()
