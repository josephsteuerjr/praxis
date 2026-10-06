"""26.09 12:16–12:27 UTC — четыре её реплики в AbstractDL умерли dead_letter'ом.

Утром 26.09 в гард встал CJK-санитайзер (`_sanitize_cjk_substitutions`, вклейки glm-5.3).
Её отчёт о самом дефекте цитировал «同步» и «汇总»; гард их вырезал, в леджер ящика лёг
очищенный текст, а предсетевая сверка `run_direct_outbox_prepared` сравнивала его с
СЫРЫМИ аргументами руки → «direct Telegram text differs from tool arguments» → запись
pending без proof → «unsendable by construction». Тот же класс в третий раз (17.08
хвостовой пробел, 14.09 маркеры цитат): сверка знала только перечисленные ей снятия.

Две границы: (1) гард и сверка делят одну функцию снятий `_strip_generation_artifacts` —
что гард отправил, то сверка и принимает; подлинная подмена по-прежнему отказ;
(2) отказ сверки закрывает запись сразу, до сети: рука говорит «НЕ ушло», а не «ещё в
очереди, дошлёт сама, если сможет» про запись, которая не дойдёт никогда.

Запуск:  python praxis_test.py test_outbox_cjk_2609 -v
"""
from __future__ import annotations

import unittest
from unittest import mock

import agent
import mtproto_runner as runner
import telegram_outbox
import test_outbox_deadletter_1708 as _dl

SYNC, TOTAL = "同步", "汇总"  # «同步», «汇总» — пишем кодом, как в test_sanitize_cjk
# Начало реплики call_58a59cd9 из run-20260926T121355992001Z-8e2350ef (args 1073, леджер 1067).
REPORT = ("Проверила гипотезу Егора из #109954 прямо по файлам. В soul/ (конституция, визитка, "
          f"навыки) ровно два CJK-кластера на все маркдауны: {SYNC} в self.md и {TOTAL} в "
          "journal-live-flow.md. Оба — мои же записки про сам дефект.")
# Вклейка посреди слова — call_5575e84c из run-20260926T122556688839Z-c6dec1f7.
GLITCH = f"Нашлось два места, оба переписаны: self.md («закрывать нить{SYNC}но»). "


class _Reply(_dl._RunScaffold):
    def _start_reply(self, run_id: str, args_text: str) -> str:
        key = f"telegram-outbox:{run_id}:tool:call-reply"
        self.manager.start_tool(
            run_id, "call-reply", "reply", {"text": args_text},
            side_effect=True, idempotency_key=key,
        )
        return key


class ProofAcceptsWhatTheGuardStripped(_Reply):
    def test_quoted_cjk_stripped_by_the_guard_no_longer_kills_the_reply(self):
        context = self._create_run("cjk-report")
        self._start_reply(context.run_id, REPORT)
        sent, note = agent._strip_generation_artifacts(REPORT, "разбор в AbstractDL")
        self.assertTrue(note.startswith("cjk-inlay:"))
        self.assertNotIn(SYNC, sent)
        entry = self._reply_entry(context.run_id, ledger_text=sent)
        proof = agent.run_direct_outbox_prepared(entry, target_label="AbstractDL")
        self.assertEqual(proof["entry"]["payload"]["text"], sent)
        self.assertTrue(agent.direct_outbox_prepared(entry))

    def test_glitch_inside_a_word_with_trailing_space_passes(self):
        context = self._create_run("cjk-glitch")
        self._start_reply(context.run_id, GLITCH)
        sent = agent._strip_generation_artifacts(GLITCH)[0]
        entry = self._reply_entry(context.run_id, ledger_text=sent)
        self.assertTrue(agent.run_direct_outbox_prepared(entry, target_label="AbstractDL"))

    def test_think_markup_and_cjk_together_pass(self):
        raw = "<think>черновик</think>Ответ по делу: " + SYNC + " — вклейка."
        context = self._create_run("cjk-think")
        self._start_reply(context.run_id, raw)
        sent = agent._strip_generation_artifacts(raw)[0]
        self.assertNotIn("<think>", sent)
        self.assertNotIn(SYNC, sent)
        entry = self._reply_entry(context.run_id, ledger_text=sent)
        self.assertTrue(agent.run_direct_outbox_prepared(entry, target_label="Егор"))

    def test_cjk_room_keeps_the_text_and_the_proof_still_passes(self):
        # в CJK-комнате гард не трогает текст — леджер равен аргументам
        raw = "Ответ: 了解しました，马上做"
        sent, note = agent._strip_generation_artifacts(raw, "日本語の会話が続いています、昨日も話した")
        self.assertEqual((sent, note), (raw, ""))
        context = self._create_run("cjk-room")
        self._start_reply(context.run_id, raw)
        entry = self._reply_entry(context.run_id, ledger_text=sent)
        self.assertTrue(agent.run_direct_outbox_prepared(entry, target_label="Егор"))

    def test_a_genuinely_different_text_is_still_refused(self):
        context = self._create_run("cjk-forged")
        self._start_reply(context.run_id, REPORT)
        entry = self._reply_entry(context.run_id, ledger_text="кто-то подменил текст")
        with self.assertRaises(agent.DurableExecutionError):
            agent.run_direct_outbox_prepared(entry, target_label="Егор")


class GuardAndProofShareOneStripper(_Reply):
    """Класс, а не случай: гард чистит той функцией, чей результат принимает сверка."""

    def _owner_dm(self):
        return agent.ChannelContext(
            chat_id="100", room_id="100", principal_id="100",
            is_dm=True, owner=True, known=True, addressed=True,
            address_message_id=7, address_kind="direct",
            reply_targets=((7, "Yegor", "continue"),),
        )

    def test_the_guard_goes_through_the_shared_stripper(self):
        # Новое снятие, добавленное в _strip_generation_artifacts, гард применит сам.
        with mock.patch.object(agent, "_strip_generation_artifacts",
                               return_value=("снято общей функцией", "")) as shared:
            out = agent.guard_outbound_reply("что угодно", "контекст", ctx=self._owner_dm())
        shared.assert_called_once()
        self.assertEqual(out, "снято общей функцией")

    def test_what_the_guard_sends_the_proof_accepts(self):
        sent = agent.guard_outbound_reply(GLITCH, "разговор по-русски", ctx=self._owner_dm())
        self.assertTrue(sent)
        self.assertNotIn(SYNC, sent)
        context = self._create_run("cjk-e2e")
        self._start_reply(context.run_id, GLITCH)
        entry = self._reply_entry(context.run_id, ledger_text=sent)
        self.assertTrue(agent.run_direct_outbox_prepared(entry, target_label="Егор"))


class RefusedProofRetiresTheIntentBeforeTheNetwork(_Reply):
    def setUp(self):
        super().setUp()
        self.old_outbox = runner._DIRECT_OUTBOX
        runner._DIRECT_OUTBOX = telegram_outbox.TelegramOutbox(self.base / "outbox")
        self.addCleanup(self._restore_outbox)

    def _restore_outbox(self):
        runner._DIRECT_OUTBOX = self.old_outbox

    def test_mismatch_dead_letters_at_once_and_the_hand_says_not_sent(self):
        context = self._create_run("bind-refused")
        key = self._start_reply(context.run_id, "я хотела сказать это")
        entry = runner._direct_outbox().prepare_text(
            key, peer_id=100, reply_to=7, text="кто-то подменил текст",
            run_id=context.run_id, call_id="call-reply", purpose="tool:reply",
        )
        execution = {"run_id": context.run_id, "call_id": "call-reply", "tool": "reply",
                     "idempotency_key": key}
        emit = mock.Mock(return_value={"id": "receipt"})
        with mock.patch.object(runner.owner_delivery.LEDGER, "emit", emit):
            with self.assertRaises(agent.DurableExecutionError) as caught:
                runner._bind_direct_outbox_proof(entry, runner._durable_outbox_projection(
                    execution, {"target_label": "Егор", "target_user_id": None,
                                "pulse_id": "", "followup_request": ""}))
        row = runner._direct_outbox().get(key, verify_file=False)
        self.assertEqual(row["state"], "dead_letter")
        self.assertIn("unsendable by construction", str(row.get("last_error") or ""))
        # она узнаёт об этом ответом руки — расписка владельцу тут не нужна
        emit.assert_not_called()
        with mock.patch.object(agent, "current_tool_execution", return_value=execution), \
                mock.patch.object(agent, "_direct_outbox_state", return_value=row):
            text = agent._direct_send_outcome("Ответ", caught.exception)
        self.assertIn("НЕ ушло", text)
        self.assertNotIn("дошлёт сама", text)

    def test_a_good_proof_leaves_the_intent_pending_for_the_send(self):
        context = self._create_run("bind-ok")
        key = self._start_reply(context.run_id, "слово в слово")
        entry = runner._direct_outbox().prepare_text(
            key, peer_id=100, reply_to=7, text="слово в слово",
            run_id=context.run_id, call_id="call-reply", purpose="tool:reply",
        )
        execution = {"run_id": context.run_id, "call_id": "call-reply", "tool": "reply"}
        runner._bind_direct_outbox_proof(entry, runner._durable_outbox_projection(
            execution, {"target_label": "Егор", "target_user_id": None,
                        "pulse_id": "", "followup_request": ""}))
        row = runner._direct_outbox().get(key, verify_file=False)
        self.assertEqual(row["state"], "pending")
        self.assertTrue(agent.direct_outbox_prepared(row))


if __name__ == "__main__":
    unittest.main()
