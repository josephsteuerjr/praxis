"""Инцидент 17.08 19:41 — реплика, которую оба рта пообещали и ни один не доставил.

Модель закончила ответ про аватарку хвостовым пробелом. `tool_reply` стрипит текст,
предсетевая сверка proof сравнивала леджер с СЫРЫМИ аргументами → детерминированный
отказ; рука сказала «очередь дошлёт сама, вручную не повторяй», а воркер очереди
требовал тот самый proof, чьё создание падало константой, — 12 ретраев за 2,5 часа
и молчаливый dead_letter. Егор ждал ответ час; RECAP прогона говорил Delivery `sent`.

Полный разбор: `_state/ИНЦИДЕНТ-DEADLETTER-17.08.md`. Здесь — четыре границы починки:
сверка той же нормализацией; ошибка-константа не ретраится; dead_letter объявляет
себя расписками; текст руки не обещает того, чего механизм не держит.
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

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="praxis-deadletter-session-"))
atexit.register(shutil.rmtree, _SESSION_DIR, True)
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("TELEGRAM_SESSION", str(_SESSION_DIR / "telethon"))
os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import mtproto_runner as runner  # noqa: E402
import run_context  # noqa: E402
import run_manager  # noqa: E402
import telegram_outbox  # noqa: E402


class _RunScaffold(unittest.TestCase):
    """Настоящий RunManager во временном каталоге — как в test_agent_resume_runtime."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="praxis-deadletter-")
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.manager = run_manager.RunManager(self.base)
        self.previous_manager = agent._RUN_MANAGER
        agent._RUN_MANAGER = self.manager
        self.addCleanup(self._restore)

    def _restore(self):
        agent._RUN_MANAGER = self.previous_manager

    def _create_run(self, suffix: str) -> run_context.RunContext:
        channel = agent.ChannelContext(
            chat_id="100", room_id="100", principal_id="100",
            is_dm=True, owner=True, known=True, addressed=True,
            address_message_id=7, address_kind="direct",
            reply_targets=((7, "Yegor", "continue"),),
        )
        context = run_context.RunContext.create(
            run_id=f"run-deadletter-{suffix}",
            kind="chat_turn", goal=f"deadletter {suffix}",
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

    def _reply_entry(self, run_id: str, *, ledger_text: str) -> dict:
        key = f"telegram-outbox:{run_id}:tool:call-reply"
        return {
            "state": "pending", "key": key, "kind": "text",
            "run_id": run_id, "call_id": "call-reply",
            "purpose": "tool:reply", "peer_id": 100,
            "topic_id": None, "reply_to": 7,
            "random_id": telegram_outbox.stable_random_id(key),
            "payload": {"text": ledger_text},
        }


class ProofComparesLikeWithLike(_RunScaffold):
    """Граница 1: сверка proof — той же нормализацией, которой текст ушёл."""

    def _start_reply(self, run_id: str, args_text: str) -> str:
        key = f"telegram-outbox:{run_id}:tool:call-reply"
        self.manager.start_tool(
            run_id, "call-reply", "reply", {"text": args_text},
            side_effect=True, idempotency_key=key,
        )
        return key

    def test_trailing_space_in_model_args_no_longer_kills_the_delivery(self):
        # 17.08: аргументы кончались пробелом (806 зн.), леджер нёс strip (805 зн.) —
        # и этого хватило, чтобы реплика в личку Егора не ушла никогда.
        context = self._create_run("strip")
        self._start_reply(context.run_id, "corporate». ")
        entry = self._reply_entry(context.run_id, ledger_text="corporate».")
        proof = agent.run_direct_outbox_prepared(entry, target_label="Егор")
        self.assertEqual(proof["entry"]["payload"]["text"], "corporate».")
        self.assertTrue(agent.direct_outbox_prepared(entry))

    def test_identical_text_still_passes(self):
        context = self._create_run("exact")
        self._start_reply(context.run_id, "слово в слово")
        entry = self._reply_entry(context.run_id, ledger_text="слово в слово")
        self.assertTrue(agent.run_direct_outbox_prepared(entry, target_label="Егор"))

    def test_a_genuinely_different_text_is_still_refused(self):
        # Ослабление — только до strip: защита привязки леджера к намерению остаётся.
        context = self._create_run("forged")
        self._start_reply(context.run_id, "я хотела сказать это")
        entry = self._reply_entry(context.run_id, ledger_text="кто-то подменил текст")
        with self.assertRaises(agent.DurableExecutionError):
            agent.run_direct_outbox_prepared(entry, target_label="Егор")


class ConstantErrorsStopPretendingToBeTransient(_RunScaffold):
    """Граница 2: терминальный прогон без proof не ретраится в никуда."""

    def setUp(self):
        super().setUp()
        self.old_outbox = runner._DIRECT_OUTBOX
        runner._DIRECT_OUTBOX = telegram_outbox.TelegramOutbox(self.base / "outbox")
        self.addCleanup(self._restore_outbox)

    def _restore_outbox(self):
        runner._DIRECT_OUTBOX = self.old_outbox

    def _pending_entry_without_proof(self, run_id: str) -> dict:
        return runner._direct_outbox().prepare_text(
            f"telegram-outbox:{run_id}:tool:call-reply",
            peer_id=100, reply_to=7, text="слово, которое не ушло",
            run_id=run_id, call_id="call-reply", purpose="tool:reply",
        )

    def test_terminal_run_without_proof_dead_letters_on_first_attempt(self):
        context = self._create_run("terminal")
        self.manager.transition(context.run_id, "done", expected="running")
        entry = self._pending_entry_without_proof(context.run_id)
        emit = mock.Mock(return_value={"id": "receipt"})
        with mock.patch.object(runner.owner_delivery.LEDGER, "emit", emit):
            with self.assertRaises(runner.DirectOutboxUnsendable) as caught:
                asyncio.run(runner._send_direct_outbox_entry(entry))
            state = asyncio.run(
                runner._retry_direct_outbox_entry(entry, caught.exception))
        self.assertEqual(state["state"], "dead_letter")
        # Граница 3: смерть записи объявила себя — расписка владельцу с самим текстом…
        emit.assert_called_once()
        self.assertEqual(emit.call_args.kwargs["outcome"], "failure")
        self.assertIn("слово, которое не ушло", emit.call_args.kwargs["body"])
        # …и durable-событие в леджере её прогона: «sent» больше не последнее слово.
        kinds = [str(row.get("kind") or "")
                 for row in self.manager.events(context.run_id)]
        self.assertIn("direct_outbox_dead_letter", kinds)

    def test_live_run_without_proof_is_still_retried_not_buried(self):
        # Обратная сторона: гонка с ещё живым тул-вызовом — proof может доехать.
        context = self._create_run("alive")
        entry = self._pending_entry_without_proof(context.run_id)
        emit = mock.Mock(return_value={"id": "receipt"})
        with mock.patch.object(runner.owner_delivery.LEDGER, "emit", emit):
            with self.assertRaises(agent.DurableExecutionError) as caught:
                asyncio.run(runner._send_direct_outbox_entry(entry))
            self.assertNotIsInstance(caught.exception, runner.DirectOutboxUnsendable)
            state = asyncio.run(
                runner._retry_direct_outbox_entry(entry, caught.exception))
        self.assertEqual(state["state"], "retry")
        emit.assert_not_called()

    def test_exhausted_attempts_announce_themselves_too(self):
        # Авто-dead_letter по потолку попыток (record_retry) — тот же голос расписки.
        runner._DIRECT_OUTBOX = telegram_outbox.TelegramOutbox(
            self.base / "outbox-capped", max_attempts=1)
        context = self._create_run("capped")
        entry = self._pending_entry_without_proof(context.run_id)
        emit = mock.Mock(return_value={"id": "receipt"})
        with mock.patch.object(runner.owner_delivery.LEDGER, "emit", emit):
            state = asyncio.run(
                runner._retry_direct_outbox_entry(entry, ConnectionError("offline")))
        self.assertEqual(state["state"], "dead_letter")
        emit.assert_called_once()


class TheHandStopsOverpromising(unittest.TestCase):
    """Граница 4: «очередь дошлёт сама» без оговорки — было ложью в момент произнесения."""

    def test_pending_receipt_promises_an_attempt_not_a_delivery(self):
        entry = {"state": "pending", "attempts": 0}
        with mock.patch.object(agent, "current_tool_execution",
                               return_value={"idempotency_key": "k"}), \
                mock.patch.object(agent, "_direct_outbox_state", return_value=entry):
            text = agent._direct_send_outcome(
                "Ответ", agent.DurableExecutionError("text differs"))
        self.assertIn("если сможет", text)
        self.assertIn("расписка", text)
        self.assertIn("не повторяй", text)
        self.assertNotIn("дошлёт сама;", text)


if __name__ == "__main__":
    unittest.main()
