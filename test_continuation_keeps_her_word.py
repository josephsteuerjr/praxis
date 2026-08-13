"""Продолжение приглашает подумать ещё, а не стирает уже сказанное.

ЖИВОЙ СЛУЧАЙ 13.08.2026, 22:44, личка Егора. Он написал ей после починки подписки. В
кольце ходов остался такой след:

    TOOL: work_loop:продолжение 1 из 8 · продолжение 2 из 8 ·
          chat_loop:продолжения кончились (2) — сказано как есть
    OUT : ''

Она ОТВЕТИЛА. Зеркало показало ей её же текст и спросило «Отправить?». Она ответила
снова. Бюджет кончился — и цикл вернул текст ПОСЛЕДНЕГО прохода, который оказался
пустым. Наружу ушло ничего. Егор написал одно слово: «ноль».

КОРЕНЬ. Каждое продолжение замещало `reply`. В рабочем окне это безвредно: там текст —
заметка в ленте, а ход закрывает её `task_control`. В чате ответ И ЕСТЬ продукт, и
затирание означает потерю сказанного.

⚠ ГРАНИЦА, КОТОРУЮ ЭТИ ТЕСТЫ ОХРАНЯЮТ. Пустой ход, где она НИЧЕГО не сказала ни разу,
обязан остаться пустым: молчание — её законный ход, и подставлять туда чужой текст
нечего. Спасается только уже произнесённое.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import work_loop  # noqa: E402


class _Resp:
    """Ответ модели без вызова инструмента."""

    stop_reason = "end_turn"

    def __init__(self, text: str):
        self.text = text
        self.blocks = [{"type": "text", "text": text}] if text else []


def _loop(texts: list[str], *, keep: list[bool]):
    """Прогнать цикл на заданной последовательности ответов модели.

    `keep[i]` — продолжать ли ход после i-го ответа.
    """
    calls = iter(texts)
    decisions = iter(keep)

    def _model_call(system, messages, tools=None):
        return _Resp(next(calls))

    def _continue(reply, resp, messages, tool_trace, *, hands=0):
        return next(decisions, False)

    with (
        mock.patch.object(agent, "_model_call", side_effect=_model_call),
        mock.patch.object(agent, "_work_loop_continue", side_effect=_continue),
        mock.patch.object(agent, "_run_status_gate"),
        mock.patch.object(agent, "_durable_model_text", side_effect=lambda t, b, **k: t),
        mock.patch.object(agent, "_offered_function_names", return_value=set()),
        mock.patch.object(agent, "_persist_tool_loop_checkpoint"),
        mock.patch.object(work_loop, "taken", return_value=None),
    ):
        return agent._terminal_tool_loop(system="s", messages=[], tools=[])


class HerWordSurvivesTheContinuation(unittest.TestCase):
    def test_an_empty_last_pass_does_not_erase_what_she_said(self):
        """Дословный случай 22:44: сказала, продолжили, второй раз пусто."""
        out = _loop(["Спасибо! Подписка правда падала — теперь живая.", ""],
                    keep=[True, False])
        self.assertEqual(out, "Спасибо! Подписка правда падала — теперь живая.")

    def test_the_latest_non_empty_wins_not_the_first(self):
        """Передумала и сказала иначе — уходит поправленное, а не черновик."""
        out = _loop(["первый вариант", "поправленный вариант", ""],
                    keep=[True, True, False])
        self.assertEqual(out, "поправленный вариант")

    def test_a_turn_she_never_spoke_in_stays_silent(self):
        """Молчание — её законный ход. Подставлять туда нечего."""
        self.assertEqual(_loop(["", ""], keep=[True, False]), "")

    def test_without_any_continuation_nothing_changes(self):
        self.assertEqual(_loop(["обычный ответ"], keep=[False]), "обычный ответ")


class TheTraceTellsTheTruthAboutTheBudget(unittest.TestCase):
    """«продолжение 1 из 8» в чат-ходе, где бюджет 2, — мелкая ложь в её же журнале."""

    def _note(self, kind: str) -> str:
        trace: list[str] = []
        with (
            mock.patch.object(agent, "_current_run_kind", return_value=kind),
            mock.patch.object(work_loop, "decide", return_value=(True, "укол")),
            mock.patch.object(work_loop, "spend", return_value=1),
            mock.patch.object(work_loop, "taken", return_value=None),
            mock.patch.object(agent.work_store, "for_run", return_value=None),
        ):
            agent._work_loop_continue("текст", _Resp("текст"), [], trace, hands=0)
        return next(n for n in trace if n.startswith("work_loop:продолжение"))

    def test_a_work_window_names_the_work_budget(self):
        self.assertIn("из %d" % work_loop.budget(), self._note("task_window"))

    def test_a_chat_turn_names_the_chat_budget(self):
        self.assertIn("из %d" % work_loop.chat_budget(), self._note("chat_turn"))


if __name__ == "__main__":
    unittest.main()
