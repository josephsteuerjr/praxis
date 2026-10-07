"""Инцидент 20.09 — «прервать» в Пульте означало «доставить». Три границы починки 21.09.

Из записки «почему архивы уходили» (workspace/inbox/2026-09-20):

1. Отмена хода с висящим send-намерением. 16:46 — Егор нажал «прервать»; прогон стоял
   paused с незакрытым вызовом, резюм-машинерия «узнавала исход» единственным знакомым
   способом — исполняя отправку. 19:24:14 файл дошёл (message_id 4444), 19:24:15 ход
   стал cancelled. Теперь просьба control.action=cancel в манифесте означает: записи
   этого прогона уходят в dead_letter («cancelled by owner before acceptance») БЕЗ сети.

2. Поздняя приёмка не возвращалась к ней. Повтор доносил файл до приёмки уже после
   того, как ход считал его упавшим; квитанция уходила только в Пульт (transports
   pwa), ей — ни строки. Теперь settle-ветка сверки пишет journal-строку «поздняя
   приёмка» (salience=2). Живому ходу — не дублируем: там тул-результат доедет сам.

3. Приостановка снималась сама. dead_letter-расписка закрывает незакрытый вызов
   (tool_failed) — прогон снова терминализуем, скан доводит уже авторизованную отмену.
"""
from __future__ import annotations

import asyncio
import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="praxis-cancel2109-session-"))
atexit.register(shutil.rmtree, _SESSION_DIR, True)
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ["TELEGRAM_SESSION"] = str(_SESSION_DIR / "telethon")
os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import mtproto_runner as runner  # noqa: E402
import run_context  # noqa: E402
import run_manager  # noqa: E402
import telegram_outbox  # noqa: E402


class _RunScaffold(unittest.TestCase):
    """Настоящий RunManager во временном каталоге — как в test_outbox_deadletter_1708."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-cancel2109-")
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.manager = run_manager.RunManager(self.base)
        self.previous_manager = agent._RUN_MANAGER
        agent._RUN_MANAGER = self.manager
        self.addCleanup(self._restore)
        self.old_outbox = runner._DIRECT_OUTBOX
        runner._DIRECT_OUTBOX = telegram_outbox.TelegramOutbox(self.base / "outbox")
        self.addCleanup(self._restore_outbox)
        self.old_reconciled = runner._DIRECT_OUTBOX_RECONCILED
        runner._DIRECT_OUTBOX_RECONCILED = set()
        self.addCleanup(self._restore_reconciled)

    def _restore(self):
        agent._RUN_MANAGER = self.previous_manager

    def _restore_outbox(self):
        runner._DIRECT_OUTBOX = self.old_outbox

    def _restore_reconciled(self):
        runner._DIRECT_OUTBOX_RECONCILED = self.old_reconciled

    def _create_run(self, suffix: str) -> run_context.RunContext:
        channel = agent.ChannelContext(
            chat_id="100", room_id="100", principal_id="100",
            is_dm=True, owner=True, known=True, addressed=True,
            address_message_id=7, address_kind="direct",
            reply_targets=((7, "Yegor", "continue"),),
        )
        context = run_context.RunContext.create(
            run_id=f"run-cancel2109-{suffix}",
            kind="chat_turn", goal=f"cancel2109 {suffix}",
            principal_id=str(channel.principal_id),
            scope=channel.scope,
            origin_chat_id=channel.chat_id,
            origin_message_ids=agent._run_origin_message_ids(channel),
            delivery_chat_id=channel.chat_id,
            model_profile="voice",
        )
        persisted = self.manager.create(
            context,
            agent._run_context_markdown(
                ctx=channel, kind=context.kind, goal=context.goal,
                conversation="immutable conversation", history=None,
                extra="immutable runtime frame",
            ),
        )
        self.manager.transition(persisted.run_id, "running", expected="pending")
        return self.manager.context(persisted.run_id)

    def _pending_file_entry(self, run_id: str, call_id: str) -> dict:
        source = self.base / "archive-part.txt"
        source.write_text("payload", encoding="utf-8")
        return runner._direct_outbox().prepare_file(
            f"telegram-outbox:{run_id}:tool:{call_id}",
            peer_id=100,
            source=source,
            visible_filename="archive-part.txt",
            mime="text/plain",
            caption="",
            run_id=run_id,
            call_id=call_id,
            purpose="tool:send_file",
        )


class CancelDoesNotDeliver(_RunScaffold):
    """Граница 1: отмена хода не исполняет висящее намерение."""

    def test_send_path_dead_letters_cancelled_intent_without_network(self):
        # 20.09: отмена в 16:46 доставила файл в 19:24. Теперь — ни байта в сеть.
        context = self._create_run("send")
        self.manager.start_tool(
            context.run_id, "call-send", "send_file",
            {"path": "/tmp/x", "caption": ""}, side_effect=True,
            idempotency_key=f"telegram-outbox:{context.run_id}:tool:call-send")
        entry = self._pending_file_entry(context.run_id, "call-send")
        self.manager.request_cancel(
            context.run_id, actor="desk:owner", reason="прервано из окна")
        emit = mock.Mock(return_value={"id": "receipt"})
        with mock.patch.object(runner.owner_delivery.LEDGER, "emit", emit):
            retired = asyncio.run(runner._send_direct_outbox_entry(entry))
        self.assertEqual(retired["state"], "dead_letter")
        self.assertIn("cancelled by owner before acceptance", retired["last_error"])
        # Отправки не было: расписка владельца — о НЕ-доставке, не о file_ready.
        emit.assert_called_once()
        self.assertEqual(emit.call_args.kwargs["outcome"], "failure")

    def test_clock_tick_dead_letters_cancelled_intent_before_transport(self):
        # Периодика outbox — вторая дверь: та же отмена, тот же dead_letter, без сети.
        context = self._create_run("tick")
        entry = self._pending_file_entry(context.run_id, "call-tick")
        self.manager.request_cancel(
            context.run_id, actor="desk:owner", reason="прервано из окна")
        runner._direct_outbox().record_retry(entry["key"], "offline", now=1.0)
        runner._direct_outbox().clock = lambda: 1e6  # срок повтора наступил
        with (
            mock.patch.object(
                runner, "_resolve_entity",
                mock.AsyncMock(side_effect=AssertionError("cancel reached network"))),
            mock.patch.object(runner.owner_delivery.LEDGER, "emit",
                              mock.Mock(return_value={"id": "r"})),
            mock.patch.object(agent, "direct_outbox_prepared",
                              return_value=True) as prepared,
        ):
            asyncio.run(runner._direct_outbox_once())
        prepared.assert_not_called()
        retired = runner._direct_outbox().get(entry["key"])
        self.assertEqual(retired["state"], "dead_letter")
        self.assertIn("cancelled by owner before acceptance", retired["last_error"])

    def test_live_run_intent_is_still_sent(self):
        # Обратная сторона: без просьбы об отмене намерение живого прогона уходит как раньше.
        context = self._create_run("alive")
        entry = self._pending_file_entry(context.run_id, "call-alive")
        sent = mock.AsyncMock(return_value=(
            mock.Mock(id=555), entry["random_id"]))
        with (
            mock.patch.object(agent, "direct_outbox_prepared", return_value=True),
            mock.patch.object(agent, "run_direct_outbox_accepted", return_value=True),
            mock.patch.object(runner, "_resolve_entity",
                              mock.AsyncMock(return_value=mock.Mock(id=100))),
            mock.patch.object(runner, "_send_file_idempotent", sent),
            mock.patch.object(runner, "OWNER_ID", 0),
        ):
            accepted = asyncio.run(runner._send_direct_outbox_entry(entry))
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(sent.await_count, 1)

    def test_dead_letter_entry_is_never_resurrected(self):
        # Запись, уже закрытая dead_letter'ом, повтором прогона к сети не возвращается.
        context = self._create_run("resurrect")
        entry = self._pending_file_entry(context.run_id, "call-resurrect")
        retired = runner._direct_outbox().dead_letter(
            entry["key"], "cancelled by owner before acceptance")
        with mock.patch.object(
                runner, "_resolve_entity",
                mock.AsyncMock(side_effect=AssertionError("dead row reached network"))):
            with self.assertRaises(runner.DirectOutboxUnsendable):
                asyncio.run(runner._send_direct_outbox_entry(retired))


class LateAcceptanceReturnsToHer(_RunScaffold):
    """Граница 2: приёмка после терминала хода возвращается journal-строкой."""

    def _accepted_entry(self, run_id: str, call_id: str) -> dict:
        entry = self._pending_file_entry(run_id, call_id)
        return runner._direct_outbox().mark_accepted(
            entry["key"], message_id=4444, random_id=entry["random_id"])

    def test_settled_run_gets_a_journal_line(self):
        # 20.09, 19:24: файл дошёл, ход считал его упавшим, ей не сказали ни слова.
        context = self._create_run("late")
        self.manager.transition(context.run_id, "failed", expected="running")
        accepted = self._accepted_entry(context.run_id, "call-late")
        journal = mock.Mock(return_value="Записала в дневник.")
        with mock.patch.object(agent, "tool_journal", journal):
            ok = asyncio.run(runner._reconcile_direct_outbox_entry(accepted))
        self.assertTrue(ok)
        journal.assert_called_once()
        text = journal.call_args.args[0]
        self.assertIn("поздняя приёмка", text)
        self.assertIn("archive-part.txt", text)
        self.assertIn("4444", text)
        self.assertEqual(journal.call_args.kwargs.get("salience"), 2)

    def test_live_run_gets_no_duplicate_journal_line(self):
        # Живому ходу тул-результат доедет своим маршрутом — дублировать нечего.
        context = self._create_run("live")
        accepted = self._accepted_entry(context.run_id, "call-live")
        journal = mock.Mock(return_value="Записала в дневник.")
        with (
            mock.patch.object(agent, "tool_journal", journal),
            mock.patch.object(agent, "run_direct_outbox_accepted", return_value=True),
        ):
            ok = asyncio.run(runner._reconcile_direct_outbox_entry(accepted))
        self.assertTrue(ok)
        journal.assert_not_called()


class PauseLifts(_RunScaffold):
    """Граница 3: после dead_letter-расписки прогон терминализуем, отмена доходит."""

    def test_cancel_settles_after_dead_letter_receipt(self):
        context = self._create_run("pause")
        key = f"telegram-outbox:{context.run_id}:tool:call-send"
        self.manager.start_tool(
            context.run_id, "call-send", "send_file",
            {"path": "/tmp/x", "caption": ""}, side_effect=True,
            idempotency_key=key)
        entry = self._pending_file_entry(context.run_id, "call-send")
        self.manager.request_cancel(
            context.run_id, actor="desk:owner", reason="прервано из окна")
        self.assertEqual(
            str(self.manager.manifest(context.run_id).get("status") or ""), "paused")
        # Отмена закрывает намерение dead_letter'ом (граница 1)…
        with mock.patch.object(runner.owner_delivery.LEDGER, "emit",
                               mock.Mock(return_value={"id": "r"})):
            retired = asyncio.run(runner._send_direct_outbox_entry(entry))
        # …расписка закрывает незакрытый вызов — и скан доводит уже авторизованную отмену.
        agent.record_direct_outbox_dead_letter(context.run_id, "call-send", dict(retired))
        self.manager.request_cancel(
            context.run_id, actor="desk:owner", reason="прервано из окна")
        self.assertEqual(
            str(self.manager.manifest(context.run_id).get("status") or ""), "cancelled")
        self.assertFalse(self.manager.outstanding_tools(context.run_id))


if __name__ == "__main__":
    unittest.main()
