"""05.10 — accepted-калитка текста жила со строгим == против сырых аргументов.

Гард (`_guard_outbound` → `_strip_generation_artifacts`) чистит текст ДО леджера, а
`started_args` события tool_side_effect хранит СЫРОЙ аргумент модели (execution.args =
dict(call_input), пишется до чистки). Prepared-калитка и caption-ветка accepted давно
сверяют зеркальным кортежем кандидатов (raw / strip / strip+convo); текстовая ветка
`run_direct_outbox_accepted` осталась со строгим `==` — гном-ревью 01b2f873 прямо
назвало этот latent-класс для caption («текстовая ветка — подозрение то же; вне скоупа
этого proposal»). Артефакт (think/cite/CJK-вклейка) в аргументе send_message(to=…) →
Telegram уже принял доставку, а reconcile упал в dead_letter ПОСЛЕ доставки.

Класс тот же, что 17.08 / 14.09 / 26.09: сверка знала только перечисленные ей снятия.
Границы теста: (1) принятая доставка с чисткой — закрывается как delivered; подлинная
подмена текста — по-прежнему отказ; (2) калитка не ослаблена: список кандидатов —
производные того же аргумента, fail-closed сохранён.

Запуск: python praxis_test.py test_outbox_accepted_text_gate_0510 -v
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import test_outbox_deadletter_1708 as _dl  # noqa: E402


GLITCH = "Ответ по делу: закрывать нить同步но, остаток — 训练ный."
THINK = "<think>черновик</think>Ответ: вклейка 训练ный в конце."


class _AcceptedText(_dl._RunScaffold):
    def _start_message(self, run_id: str, args_text: str) -> str:
        key = f"telegram-outbox:{run_id}:tool:call-m"
        self.manager.start_tool(
            run_id, "call-m", "send_message", {"to": "Егор", "text": args_text},
            side_effect=True, idempotency_key=key,
        )
        # accepted-калитка отвечает только за crash/timeout-путь: ран стоит в паузе
        # с незакрытым вызовом (докстринг run_direct_outbox_accepted).
        self.manager.transition(run_id, "paused", expected="running")
        return key

    def _accepted_entry(self, key: str, run_id: str, ledger_text: str) -> dict:
        import telegram_outbox
        random_id = telegram_outbox.stable_random_id(key)
        return {
            "state": "accepted", "key": key, "kind": "text",
            "run_id": run_id, "call_id": "call-m",
            "purpose": "tool:send_message", "peer_id": 100,
            "topic_id": None, "reply_to": None,
            "random_id": random_id,
            "receipt": {"message_id": 5901, "random_id": random_id},
            "payload": {"text": ledger_text},
        }


class AcceptedTextGate(_AcceptedText):
    def test_guard_cleaned_text_is_accepted_after_delivery(self):
        raw = GLITCH
        clean = agent._strip_generation_artifacts(raw, "разговор по-русски")[0]
        self.assertNotEqual(clean, raw, "фикстура обязана содержать то, что гард снимает")
        context = self._create_run("cjk-accepted")
        key = self._start_message(context.run_id, raw)
        entry = self._accepted_entry(key, context.run_id, clean)
        with unittest.mock.patch.object(agent, "project_direct_outbox_acceptance",
                                        return_value=True) as proj:
            ok = agent.run_direct_outbox_accepted(entry)
        self.assertTrue(ok, "принятая доставка не должна падать в dead_letter из-за чистки")
        proj.assert_called_once()

    def test_think_markup_in_args_is_accepted(self):
        clean = agent._strip_generation_artifacts(THINK)[0]
        self.assertNotIn("<think>", clean)
        context = self._create_run("think-accepted")
        key = self._start_message(context.run_id, THINK)
        entry = self._accepted_entry(key, context.run_id, clean)
        with unittest.mock.patch.object(agent, "project_direct_outbox_acceptance",
                                        return_value=True):
            self.assertTrue(agent.run_direct_outbox_accepted(entry))

    def test_genuinely_different_text_is_still_refused(self):
        context = self._create_run("substituted")
        key = self._start_message(context.run_id, "исходный текст намерения")
        entry = self._accepted_entry(key, context.run_id, "кто-то подменил текст")
        with self.assertRaises(agent.DurableExecutionError) as caught:
            agent.run_direct_outbox_accepted(entry)
        self.assertIn("differs from the durable tool arguments", str(caught.exception))


if __name__ == "__main__":     # pragma: no cover
    unittest.main(verbosity=2)
