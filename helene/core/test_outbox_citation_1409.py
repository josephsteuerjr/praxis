"""14.09 19:0x UTC — три ответа из четырёх в AbstractDL умерли dead_letter'ом.

Голос на gpt-6-astra ставит в текст маркеры нативного веб-поиска в символах частного
диапазона Unicode: U+E200 cite U+E202 turn0search0 U+E201. Гард снимает маркеры до
отправки, в леджер ящика ложится чистый текст, а предсетевая сверка
`run_direct_outbox_prepared` сравнивала леджер с СЫРЫМИ аргументами модели (до strip) →
«direct Telegram text differs from tool arguments» → запись pending без proof → прогон
закрывается «silent decision» → воркер хоронит запись: «unsendable by construction … the
proof can never appear». Тот же класс, что инцидент 17.08 (хвостовой пробел), только
нормализация другая.

Две границы: (1) сверка принимает текст, прошедший ту же нормализацию, что и гард
(`_strip_think`), и по-прежнему отказывает на подлинной подмене; (2) стриппер снимает
обёртку цитаты целиком, а не оставляет пустую обёртку U+E200 cite U+E202 U+E201 в Telegram.

Запуск:  python praxis_test.py test_outbox_citation_1409 -v
"""
from __future__ import annotations

import unittest

import agent
import test_outbox_deadletter_1708 as _dl

# Символы частного диапазона пишем кодом, а не литералом: в исходнике они невидимы.
E_OPEN, E_CLOSE, E_SEP = chr(0xE200), chr(0xE201), chr(0xE202)
CITE = f"{E_OPEN}cite{E_SEP}turn0search0{E_CLOSE}"
CITED = ("Слишком сильная посылка: в описании обучения названы разные источники и отбор "
         "данных, а не поглощение всего доступного текста. " + CITE + "\n\n"
         "При этом **для твоего предложения** это не важно.")
CLEAN = ("Слишком сильная посылка: в описании обучения названы разные источники и отбор "
         "данных, а не поглощение всего доступного текста. \n\n"
         "При этом **для твоего предложения** это не важно.")


class StripperEatsTheWholeCitation(unittest.TestCase):
    def test_private_use_wrapper_goes_away_with_the_token(self):
        out = agent._strip_citation_tokens(CITED)
        self.assertEqual(out, CLEAN)
        for ch in (E_OPEN, E_CLOSE, E_SEP):
            self.assertNotIn(ch, out)

    def test_a_pack_of_citations_in_one_wrapper(self):
        text = f"слов. {E_OPEN}cite{E_SEP}turn0search12{E_SEP}turn0search0{E_CLOSE}\n\nДля спора"
        self.assertEqual(agent._strip_citation_tokens(text), "слов. \n\nДля спора")

    def test_bare_tokens_and_zai_form_still_stripped(self):
        self.assertEqual(agent._strip_citation_tokens("a citeturn0view0turn0search19 b"), "a  b")
        self.assertEqual(agent._strip_citation_tokens("a turn1search3 b"), "a  b")

    def test_stray_control_char_without_wrapper_is_dropped(self):
        self.assertEqual(agent._strip_citation_tokens(f"a {E_SEP} b"), "a  b")

    def test_plain_text_untouched(self):
        for text in ("обычный текст", "turn of phrase", "", "— тире и **жир**"):
            self.assertEqual(agent._strip_citation_tokens(text), text)

    def test_guard_normalisation_matches_the_proof_normalisation(self):
        # гард чистит через _strip_think; ровно с этим сверяется proof
        self.assertEqual(agent._strip_think(CITED), CLEAN.strip())


class ProofAcceptsWhatTheGuardSent(_dl._RunScaffold):
    def _start_reply(self, run_id: str, args_text: str) -> str:
        key = f"telegram-outbox:{run_id}:tool:call-reply"
        self.manager.start_tool(
            run_id, "call-reply", "reply", {"text": args_text},
            side_effect=True, idempotency_key=key,
        )
        return key

    def test_cited_model_args_and_clean_ledger_text_pass(self):
        # ровно кейс run-20260914T184647315391Z-8604768a: args 943 зн. с маркером, леджер 931
        context = self._create_run("cite")
        self._start_reply(context.run_id, CITED)
        entry = self._reply_entry(context.run_id, ledger_text=agent._strip_think(CITED))
        proof = agent.run_direct_outbox_prepared(entry, target_label="AbstractDL")
        self.assertEqual(proof["entry"]["payload"]["text"], CLEAN.strip())
        self.assertTrue(agent.direct_outbox_prepared(entry))

    def test_citation_only_normalisation_of_the_direct_send_path_passes(self):
        # send_message/narrate чистят только маркеры (без strip) — и это тоже «тот же текст»
        context = self._create_run("direct")
        self._start_reply(context.run_id, "текст " + CITE + " конец ")
        entry = self._reply_entry(context.run_id, ledger_text="текст  конец ")
        self.assertTrue(agent.run_direct_outbox_prepared(entry, target_label="Егор"))

    def test_trailing_space_case_from_17_08_still_passes(self):
        context = self._create_run("strip")
        self._start_reply(context.run_id, "corporate». ")
        entry = self._reply_entry(context.run_id, ledger_text="corporate».")
        self.assertTrue(agent.run_direct_outbox_prepared(entry, target_label="Егор"))

    def test_a_genuinely_different_text_is_still_refused(self):
        context = self._create_run("forged")
        self._start_reply(context.run_id, "я хотела сказать это " + CITE)
        entry = self._reply_entry(context.run_id, ledger_text="кто-то подменил текст")
        with self.assertRaises(agent.DurableExecutionError):
            agent.run_direct_outbox_prepared(entry, target_label="Егор")


if __name__ == "__main__":
    unittest.main()
