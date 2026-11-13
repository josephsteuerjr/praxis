# -*- coding: utf-8 -*-
"""Частые руки называют исход сами; служебная запись манифеста — не их действие (11.11.2026).

10.11 на экране шагов у 219 из 266 вызовов стояло «исход неизвестен»: reply, end_turn,
memory_compact, task_control и другие ничего не сообщали журналу, а в «что рука сделала
внутри» попадало атомарное переименование манифеста её же хода. Модель получает прежний текст.

Запуск: python praxis_test.py test_hand_outcomes_1111 -v
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import tool_report  # noqa: E402


class Slot:
    def __enter__(self):
        self.report = tool_report.new()
        self.token = tool_report.attach(self.report)
        return self.report

    def __exit__(self, *exc):
        tool_report.detach(self.token)


def outcome(report):
    return tool_report.summary(report)["outcome"]


class BookkeepingIsNotTheHand(unittest.TestCase):
    def test_manifest_rename_inside_the_run_is_skipped(self):
        with Slot() as report:
            tool_report._audit("os.rename", ("/app/memory/runs/2026-11/run-x/.manifest.json.1.ab.tmp",
                                             "/app/memory/runs/2026-11/run-x/manifest.json", -1, -1))
        self.assertEqual(report["operations"], [])

    def test_a_rename_of_her_own_file_is_still_recorded(self):
        with Slot() as report:
            tool_report._audit("os.rename", ("/app/workspace/a.txt.tmp", "/app/workspace/a.txt", -1, -1))
        self.assertEqual(report["operations"], [["replace", "/app/workspace/a.txt.tmp", "/app/workspace/a.txt"]])


class EndTurn(unittest.TestCase):
    def test_closing_is_ok_and_names_the_kind(self):
        with Slot() as report, mock.patch.object(agent.work_loop, "note_finished"):
            text = agent.tool_end_turn("done", "сделано")
        self.assertTrue(text.startswith("Ход закрыт (done)."))
        self.assertEqual((outcome(report), report["target"]), ("ok", "done"))

    def test_missing_outcome_is_a_refusal(self):
        with Slot() as report:
            agent.tool_end_turn("", "")
        self.assertEqual(outcome(report), "refused")

    def test_wait_without_condition_is_a_refusal(self):
        with Slot() as report:
            agent.tool_end_turn("wait", "")
        self.assertEqual(outcome(report), "refused")


class Reply(unittest.TestCase):
    def test_empty_reply_is_refused_with_the_same_text(self):
        with Slot() as report:
            text = agent.tool_reply("   ")
        self.assertEqual(text, "Пустой ответ не отправляю: скажи, что сказать.")
        self.assertEqual(outcome(report), "refused")

    def test_transport_refusal_type_decides(self):
        self.assertEqual(agent._sent_outcome(agent.DirectSendRefusal("нет адресата")), "refused")
        self.assertEqual(agent._sent_outcome("Отправила, message_id=1"), "ok")

    def test_unknown_delivery_is_in_doubt_not_failure(self):
        with Slot() as report, mock.patch.object(agent, "_direct_outbox_state", return_value={"state": "retry"}):
            agent._direct_send_outcome("Ответ", RuntimeError("x"))
        self.assertEqual(outcome(report), "in_doubt")

    def test_accepted_delivery_is_ok_even_when_the_ledger_failed(self):
        with Slot() as report, mock.patch.object(agent, "_direct_outbox_state",
                                                 return_value={"state": "accepted", "receipt": {"message_id": 7}}):
            text = agent._direct_send_outcome("Ответ", RuntimeError("x"))
        self.assertIn("ДОСТАВЛЕНО", text)
        self.assertEqual(outcome(report), "ok")

    def test_plain_transport_failure_is_failed(self):
        with Slot() as report, mock.patch.object(agent, "_direct_outbox_state", return_value=None):
            text = agent._direct_send_outcome("Ответ", OSError("down"))
        self.assertTrue(text.startswith("Не отправилось:"))
        self.assertEqual(outcome(report), "failed")


class MemoryCompact(unittest.TestCase):
    def test_unknown_action_is_refused(self):
        with Slot() as report:
            text = agent.tool_memory_compact("nope", place="1")
        self.assertIn("Не поняла действие", text)
        self.assertEqual(outcome(report), "refused")


class TheHandsOwnJsonVerdict(unittest.TestCase):
    def test_ok_field_is_the_outcome(self):
        self.assertEqual(agent._json_ok_verdict(json.dumps({"ok": True, "x": 1})), "ok")
        self.assertEqual(agent._json_ok_verdict('{"ok": false, "reason": "r"}'), "refused")

    def test_text_without_the_field_stays_unknown(self):
        for text in ("Запомнила.", '{"status": "done"}', "[1, 2]", '{"ok": "yes"}', "{broken"):
            self.assertEqual(agent._json_ok_verdict(text), "", text)

    def test_dispatcher_applies_it_only_when_the_hand_was_silent(self):
        def silent(**_):
            return '{"ok": false, "reason": "x"}'

        def spoke(**_):
            tool_report.note(outcome="partial")
            return '{"ok": true}'

        with Slot() as report:
            agent._observed_tool_call("t", silent, {})
        self.assertEqual(outcome(report), "refused")
        with Slot() as report:
            agent._observed_tool_call("t", spoke, {})
        self.assertEqual(outcome(report), "partial")


class UnreadIntent(unittest.TestCase):
    def test_arguments_that_do_not_bind_are_a_refusal(self):
        def hand(path: str) -> str:
            return "never"

        with Slot() as report:
            text = agent._call_tool_with_ceiling_inner("t", hand, {"wrong": 1})
        self.assertIn("[аргументы не приняты]", text)
        self.assertEqual(outcome(report), "refused")


if __name__ == "__main__":
    unittest.main()
