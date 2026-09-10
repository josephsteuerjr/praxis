"""Два фильтра `[private]` обязаны отвечать ОДИНАКОВО. Сторож на согласие, не на поведение.

Зачем этот файл. Их двое по построению, и это осознанно: живой
(`agent._strip_participant_private_blocks`) стоит рядом со своим потребителем и
режет то, что реально доезжает до промпта голоса; теневой
(`frame_shadow.strip_private_blocks`) — read-only копия, по которой проверяют,
не течёт ли кадр. Докстринг живого прямо это называет: «Shadow has an analogous,
separately tested read-only filter».

Что случилось 27–28.08. `c82f38c8` («закрыть остаточные Shadow privacy P0») снял
guard `not listish` — но ТОЛЬКО в теневой копии. Живая осталась прежней. Замер:
из 36 минимальных двухстрочных входов разошлись 7, и во ВСЕХ живой оставлял
больше тени. То есть вторая строка приватной записи уезжала в промпт, а рядом
стояла подпись «строк приватных записей снято 1».

Направление расхождения — худшее из возможных: **тень выглядела ЧИЩЕ живого**,
поэтому любая проверка «не течёт ли кадр», сделанная по тени, давала зелёный на
открытой дыре. Расхождение прожило сутки и не было замечено ничем, потому что
каждый фильтр покрыт СВОИМИ тестами и ни один не сверял их друг с другом.

⚠ Этот тест НЕ закрепляет, что именно считать продолжением приватной записи.
Это её решение, и оно обратимо в одну строку (`and not listish` в обоих местах).
Тест закрепляет ровно то, что двое обязаны решать это ОДИНАКОВО.
"""
import unittest

import agent
import frame_shadow

MARK = "- [private] "

# Корпус подобран так, чтобы бить по КАЖДОЙ ветке обоих разборов: маркер списка и
# абзац, отступ и его отсутствие, пустая строка между, заголовок, цитата, ограда,
# вложенность, конец файла без перевода строки.
FIRSTS = (
    "- [private] тайна",
    "* [private] тайна",
    "1. [private] тайна",
    "[private] тайна абзацем",
    "## [private] тайна заголовком",
    "  - [private] тайна с отступом",
    "> [private] тайна цитатой",
)
SECONDS = (
    "продолжение без отступа",
    "  продолжение с отступом",
    "",
    "- новый пункт",
    "## новый заголовок",
    "> цитата",
    "```",
    "\tпродолжение табом",
)
TAILS = ("", "хвост\n", "- публичный пункт\n")


def _corpus():
    for first in FIRSTS:
        for second in SECONDS:
            for tail in TAILS:
                yield f"{first}\n{second}\n{tail}"
    # Плюс несколько форм, которые не собираются перебором.
    yield "- [private] одна строка без перевода в конце"
    yield f"{MARK}а\n{MARK}б\nхвост\n"
    yield "публично\n\n- [private] тайна\n\nпублично снова\n"
    yield "- пункт\n  - [private] вложенная тайна\n  продолжение\n"
    yield ""
    yield "\n\n\n"
    yield "совсем без приватного\n- пункт\nабзац\n"


class ThePrivateFiltersMustNotDiverge(unittest.TestCase):
    def test_both_filters_return_the_same_body_and_the_same_count(self):
        corpus = list(_corpus())
        self.assertGreater(len(corpus), 100, "корпус усох — сторож ослаб")
        diverged = []
        for text in corpus:
            live_body, live_hidden = agent._strip_participant_private_blocks(text)
            shadow_body, shadow_hidden = frame_shadow.strip_private_blocks(text)
            if (live_body, live_hidden) != (shadow_body, shadow_hidden):
                diverged.append((text, live_body, live_hidden, shadow_body, shadow_hidden))
        if diverged:
            first = diverged[0]
            self.fail(
                f"фильтры разошлись на {len(diverged)} входах из {len(corpus)}.\n"
                f"первый:\n  вход  : {first[0]!r}\n"
                f"  живой : {first[1]!r} (снято {first[2]})\n"
                f"  тень  : {first[3]!r} (снято {first[4]})\n"
                "Направление неважно: они обязаны решать одинаково. "
                "Если меняешь границу — меняй ОБА места."
            )

    def test_the_known_case_that_broke_is_covered(self):
        # Дословно тот вход, на котором расхождение нашли 28.08.
        text = "- [private] он уходит из компании\nи просил не говорить никому\n- публичный пункт\n"
        live_body, live_hidden = agent._strip_participant_private_blocks(text)
        shadow_body, shadow_hidden = frame_shadow.strip_private_blocks(text)
        self.assertEqual((live_body, live_hidden), (shadow_body, shadow_hidden),
                         "живой и тень разошлись на исходном репро")
        self.assertNotIn("и просил не говорить никому", live_body,
                         "продолжение приватной записи осталось в теле, которое уедет "
                         "в промпт голоса")

    def test_the_disclosure_matches_what_was_actually_removed(self):
        # Подпись в кадре («снято N строк») обязана совпадать с тем, что снято.
        for text in _corpus():
            body, hidden = agent._strip_participant_private_blocks(text)
            removed = len(text.splitlines()) - len(body.splitlines())
            self.assertEqual(removed, hidden,
                             f"подпись врёт: сказано {hidden}, снято {removed}, вход {text!r}")


if __name__ == "__main__":
    unittest.main()
