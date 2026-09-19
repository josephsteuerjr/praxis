# -*- coding: utf-8 -*-
"""Исход прогона не называет её решением то, чего она не решала (15.09).

ЖИВОЙ СЛУЧАЙ. Прогон `run-20260915T173843684899Z-ba2307a3` доставил рукой `reply` два
сообщения — #105460 и #105461, обе расписки на месте, — и записал о себе:

    status_changed → done, reason: "silent decision", details: {"message_ids": []}

Мини-приложение читает именно эту строку, и Егор видел «Завершено: решила промолчать» на
ходе, где она сказала дважды. Читала её и она сама: в 17:01 написала в чат «кусок ленты
пропустила молчанием», хотя не пропускала.

КОРЕНЬ. `evidence["silent"]` отвечает про ТЕКСТОВЫЙ путь, а под контрактом руки ответа
последний текст хода — заметка себе и наружу не идёт НИКОГДА. Значит текстовый путь пуст
в каждом ходе, и исход, прочитанный только по нему, объявлял молчанием любой ход.

ЧТО ЗАКРЕПЛЕНО ЗДЕСЬ:

* `reply_hand_message_ids` — какие именно номера унесла рука, по durable-распискам;
* исход прогона: рука говорила → «delivered by the reply hand» и номера в деталях;
  рука молчала → прежнее «silent decision» байт-в-байт;
* причина пропуска ТЕКСТОВОЙ доставки называет, что текст был заметкой, а не решением.

Запуск: python praxis_test.py test_run_label_1509 -v
"""
from __future__ import annotations

import contextlib
import json
from unittest import mock

import agent
import run_context
import run_manager
import turns
from test_turns import TurnsBase

PROJECTION = "praxis.direct-outbox-projection.v1"


class RunLabelBase(TurnsBase):
    def setUp(self):
        super().setUp()
        self.manager = run_manager.RunManager(self.tmp)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(agent, "_RUN_MANAGER", self.manager))
        self.notes = self.stack.enter_context(mock.patch.object(agent.notes, "append"))
        self.promises = self.stack.enter_context(
            mock.patch.object(agent.promises, "note_outbound"))
        ctx = run_context.RunContext.create(
            kind="chat", goal="label fixture", principal_id="owner", scope="owner",
            origin_chat_id="777", delivery_chat_id="777")
        ctx = self.manager.create(ctx, "fixture")
        self.manager.transition(ctx.run_id, "running")
        self.rid = ctx.run_id
        row = turns.begin(kind="chat", chat_id="777", scope="owner")
        row.update(run_id=self.rid, held="unspoken", note="заметка себе")
        turns.record(row)

    # ── как рука оставляет след ─────────────────────────────────────────────
    def hand_spoke(self, call_id: str, message_id, *, tool: str = "reply",
                   value: dict | None = None) -> None:
        """Один вызов руки с принятой распиской прямой отправки."""
        self.manager.start_tool(
            self.rid, call_id, tool, {"text": "сказанное"},
            side_effect=True, idempotency_key=f"turn-direct:{self.rid}:{call_id}")
        payload = value if value is not None else {
            "schema": PROJECTION,
            "key": f"telegram-outbox:{self.rid}:tool:{call_id}",
            "message_id": message_id,
            "projection": f"projected:{call_id}:{message_id}",
        }
        self.manager.store_result(
            self.rid, json.dumps(payload, ensure_ascii=False, indent=2),
            call_id=call_id, name="telegram-outbox-projection",
            media_type="application/json; charset=utf-8",
            event_kind="direct_outbox_projection", idempotent=True)
        # Живой ход закрывает вызов распиской руки (в прогоне 17:38 это seq 11 сразу за
        # проекцией). Без неё прогон остаётся с открытым вызовом и не может стать `done`.
        self.manager.store_result(
            self.rid, f"Отправила (message_id={message_id})",
            call_id=call_id, name=tool, media_type="text/plain; charset=utf-8",
            event_kind="tool_result", idempotent=True)

    def close_as_note(self) -> bool:
        """Граница хода под контрактом руки: текст — заметка, наружу не идёт."""
        agent.run_delivery_started(self.rid, chat_id="777", text_chars=0, media_count=0)
        return agent.run_delivery_completed(
            self.rid, silent=True,
            silent_reason=("turn-final text is a note; the reply hand already spoke"
                           if agent.replies_delivered(self.rid) > 0
                           else "Praxis chose silence"))

    def close_with_reason(self, reason: str) -> bool:
        """Тот же пропуск текстовой доставки, но с названной причиной.

        Эти две строки пишет ветка руки ответа (`agent.py`, контракт речи): «ход кончился
        без реплики» и «закрыла явным end_turn». Обе — НЕ объявленное молчание.
        """
        agent.run_delivery_started(self.rid, chat_id="777", text_chars=0, media_count=0)
        return agent.run_delivery_completed(self.rid, silent=True, silent_reason=reason)

    def terminal(self) -> dict:
        """Терминальный переход прогона — там же, где его читает мини-приложение.

        В манифесте лежат только `status` и `reason`; детали (номера доставленного) живут
        в событии перехода, и в живом прогоне 17:38 ложь была видна именно там:
        `reason: "silent decision", details: {"message_ids": []}`.
        """
        rows = [row for row in self.manager.events(self.rid)
                if row.get("kind") == "status_changed"
                and str(row.get("to_status") or "") in run_manager.TERMINAL_STATUSES]
        self.assertEqual(len(rows), 1, "терминальный переход не один")
        return rows[-1]

    def terminal_ids(self) -> list[str]:
        return list((self.terminal().get("details") or {}).get("message_ids") or ())

    def skip_reason(self) -> str:
        for row in self.manager.events(self.rid):
            if row.get("kind") == "delivery_skipped":
                return str(row.get("reason") or "")
        return ""


class HandMessageIds(RunLabelBase):
    """`reply_hand_message_ids`: какие номера унесла рука."""

    def test_two_replies_give_two_numbers_in_order(self):
        self.hand_spoke("call-a", 105460)
        self.hand_spoke("call-b", 105461)
        self.assertEqual(agent.reply_hand_message_ids(self.rid), ["105460", "105461"])
        self.assertEqual(agent.replies_delivered(self.rid), 2)

    def test_a_silent_turn_has_no_numbers(self):
        self.assertEqual(agent.reply_hand_message_ids(self.rid), [])
        self.assertEqual(agent.replies_delivered(self.rid), 0)

    def test_another_hand_is_not_my_reply(self):
        """`send_message` — тоже отправка, но исход хода судится по руке ответа."""
        self.hand_spoke("call-s", 900, tool="send_message")
        self.assertEqual(agent.reply_hand_message_ids(self.rid), [])

    def test_a_malformed_number_is_skipped_not_invented(self):
        for call_id, value in (("call-z", {"schema": PROJECTION, "message_id": 0}),
                               ("call-t", {"schema": PROJECTION, "message_id": True}),
                               ("call-n", {"schema": PROJECTION})):
            self.hand_spoke(call_id, None, value=value)
        self.assertEqual(agent.reply_hand_message_ids(self.rid), [])

    def test_an_unreadable_receipt_does_not_break_the_turn(self):
        """Читатель ярлыка не имеет права уронить доводку прогона."""
        self.hand_spoke("call-a", 105460)
        with mock.patch.object(agent, "_run_result_json",
                               side_effect=RuntimeError("расписка повреждена")):
            self.assertEqual(agent.reply_hand_message_ids(self.rid), [])
        self.assertEqual(agent.reply_hand_message_ids(self.rid), ["105460"])

    def test_no_run_id_is_an_empty_answer_not_a_crash(self):
        self.assertEqual(agent.reply_hand_message_ids(""), [])


class RunOutcome(RunLabelBase):
    """Что прогон говорит о себе, когда рука уже сказала."""

    def test_a_turn_that_spoke_by_hand_is_not_a_silent_decision(self):
        self.hand_spoke("call-a", 105460)
        self.hand_spoke("call-b", 105461)
        self.assertTrue(self.close_as_note())
        terminal = self.terminal()
        self.assertEqual(terminal.get("reason"), "delivered by the reply hand",
                         "ход с двумя доставленными сообщениями снова назван молчанием")
        self.assertEqual(self.terminal_ids(), ["105460", "105461"],
                         "доставленные номера пропали из исхода прогона")

    def test_a_truly_silent_turn_keeps_its_old_words(self):
        """Прежняя строка байт-в-байт: молчание остаётся её решением."""
        self.assertTrue(self.close_as_note())
        terminal = self.terminal()
        self.assertEqual(terminal.get("reason"), "silent decision")
        self.assertEqual(self.terminal_ids(), [])

    def test_the_skip_reason_names_the_note_not_her_decision(self):
        self.hand_spoke("call-a", 105460)
        self.close_as_note()
        self.assertEqual(self.skip_reason(),
                         "turn-final text is a note; the reply hand already spoke")

    def test_a_silent_turn_still_says_she_chose_silence(self):
        self.close_as_note()
        self.assertEqual(self.skip_reason(), "Praxis chose silence")

    def test_the_run_is_done_either_way(self):
        self.hand_spoke("call-a", 105460)
        self.close_as_note()
        self.assertEqual(str(self.manager.manifest(self.rid).get("status") or ""), "done")


class ThreeOutcomesNotOne(RunLabelBase):
    """Исход идёт за причиной пропуска: три случая печатались одной строкой.

    Событие `delivery_skipped` различало их всегда и прямым текстом — «not a declared
    silence». Исход прогона называл всё это её решением, то есть противоречил собственной
    расписке и выводил её решение из пустоты.
    """

    def test_a_turn_without_a_reply_hand_is_not_her_decision(self):
        self.assertTrue(self.close_with_reason(
            "turn ended without a reply hand (not a declared silence)"))
        self.assertEqual(self.terminal().get("reason"), "turn ended without a reply hand")

    def test_an_explicit_end_turn_is_named_as_itself(self):
        self.assertTrue(self.close_with_reason(
            "turn ended by her explicit end_turn (no speech)"))
        self.assertEqual(self.terminal().get("reason"), "turn closed by her explicit end_turn")

    def test_a_declared_silence_is_still_her_decision(self):
        self.assertTrue(self.close_with_reason("Praxis chose silence"))
        self.assertEqual(self.terminal().get("reason"), "silent decision")

    def test_the_hand_outranks_every_reason(self):
        """Рука говорила — значит ход не молчал, какой бы ни была причина пропуска."""
        self.hand_spoke("call-a", 105460)
        self.assertTrue(self.close_with_reason("Praxis chose silence"))
        self.assertEqual(self.terminal().get("reason"), "delivered by the reply hand")
        self.assertEqual(self.terminal_ids(), ["105460"])

    def test_the_skip_event_still_says_exactly_what_it_said(self):
        """Расписка не переписывается: исход читает её, а не заменяет."""
        reason = "turn ended without a reply hand (not a declared silence)"
        self.close_with_reason(reason)
        self.assertEqual(self.skip_reason(), reason)


if __name__ == "__main__":
    import unittest
    unittest.main()
