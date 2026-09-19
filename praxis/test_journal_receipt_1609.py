"""Дневник пишет то, что произошло, а не то, что я сказала модели.

ДЕФЕКТ, НАЙДЕННЫЙ 16.09 НА ЖИВОМ ДНЕВНИКЕ. Рука `reply` приклеивает к своему возврату
напоминание «Если это всё — зови `end_turn`; сказать ещё — `reply` снова…». Это слово,
обращённое к модели. Дневник же писался с УЖЕ СКЛЕЕННОЙ строки и резал её на 300-м знаке,
поэтому в запись попадало около шестидесяти знаков сказанного и следом машинный хвост:

    - 02:01 (s2) [ответ] Отправила → Yegor Kosyrev (…, message_id=4130): Хорошо-хорошо,
      не спрашиваю)) Кандидатов приняла, очередь пу Если это всё — зови `end_turn`; …

Этот дневник читает она сама. Класс тот же, что у «решила промолчать»: запись говорила не
о том, что произошло.

ЧТО ЗДЕСЬ ЗАКРЕПЛЕНО. Два свойства порознь: подсказка остаётся в возврате РУКИ (она нужна
модели) и не попадает в ДНЕВНИК (он нужен ей). И третье: строка дневника — это расписка
моста и ничего кроме неё.

⚠ ЧТО ЗДЕСЬ ПОДСТАВНОЕ И ПОЧЕМУ ЭТО НАЗВАНО. Мост уровня A из `test_reply_hand` отдаёт
короткую квитанцию без текста сообщения. Живой мост кладёт текст В квитанцию — это видно
в самом дневнике прода («Отправила → … message_id=4130: Хорошо-хорошо, не спрашиваю))»).
Там, где проверяется вытеснение текста, ставлю мост такой же формы, как живой, и говорю
об этом вслух: иначе тест мерил бы мир, которого нет.

Проверок на дословный текст квитанции здесь нет — пины по свойствам.

Запуск:  python praxis_test.py test_journal_receipt_1609 -v
"""

from __future__ import annotations

import unittest
from unittest import mock

import agent
from test_reply_hand import _TurnHarness, _calls_reply, _says

HINT = "зови `end_turn`"


class _WatchedJournal:
    """Наблюдатель дневника: копит строки и ПРОПУСКАЕТ их в настоящий `tool_journal`."""

    def __init__(self):
        self._real = agent.tool_journal
        self.lines: list[str] = []

    def __call__(self, line, salience=1, **kw):
        self.lines.append(str(line))
        return self._real(line, salience=salience, **kw)

    def by(self, tag: str) -> list[str]:
        return [x for x in self.lines if x.startswith(tag)]


class _LifelikeBridge:
    """Мост формы живого: квитанция НЕСЁТ отправленный текст, как на проде."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, chat_id, text, reply_to=""):
        self.calls.append((str(chat_id), str(text), str(reply_to)))
        return f"Отправила → Егор (chat_id={chat_id}, message_id=901): {text}"


class _RefusingBridge:
    """Мост, отказавший строкой: `DirectSendRefusal` — подкласс `str`, не исключение."""

    def __init__(self, why: str = "Не отправила: адресат не найден"):
        self.calls: list[tuple[str, str, str]] = []
        self.why = why

    def __call__(self, chat_id, text, reply_to=""):
        self.calls.append((str(chat_id), str(text), str(reply_to)))
        return agent.DirectSendRefusal(self.why)


class TheJournalCarriesTheReceipt(_TurnHarness):
    def setUp(self):
        super().setUp()
        self.journal = _WatchedJournal()
        self.stack.enter_context(mock.patch.object(agent, "tool_journal", self.journal))

    def test_the_hint_reaches_the_model_but_not_the_diary(self):
        self.turn(_calls_reply("c1", "Кандидатов приняла, очередь пустая"), _says("всё"))

        hand = self.hands.by("reply")
        self.assertTrue(hand, "рука ответа не звалась")
        self.assertIn(HINT, hand[-1], "подсказка нужна модели и обязана остаться в возврате")

        wrote = self.journal.by("[ответ]")
        self.assertTrue(wrote, "дневник не получил записи об ответе")
        self.assertNotIn(HINT, wrote[-1], "подсказка модели в дневник не попадает")

    def test_the_journal_line_is_the_receipt_and_nothing_else(self):
        bridge = _LifelikeBridge()
        self.turn(_calls_reply("c1", "проба формы"), _says("всё"), bridge=bridge)

        wrote = self.journal.by("[ответ]")[-1]
        receipt = bridge("101", "проба формы")   # та же форма, что отдал мост в ходе
        self.assertEqual(wrote, f"[ответ] {agent._clip_reason(receipt, 300)}",
                         "строка дневника — расписка моста и ничего кроме неё")

    def test_the_hint_no_longer_displaces_what_was_said(self):
        """Длинная реплика: прежде подсказка съедала её хвост внутри 300 знаков."""
        said = ("Кандидатов приняла, очередь пустая. Полный прогон suites крутится в фоне, "
                "будильник поставила на исход. Если зелёный — спрошу про рестарт.")
        bridge = _LifelikeBridge()
        self.turn(_calls_reply("c1", said), _says("всё"), bridge=bridge)

        wrote = self.journal.by("[ответ]")[-1]
        self.assertIn(said[:60], wrote, "начало сказанного на месте")
        self.assertIn(said[-40:], wrote, "и хвост сказанного больше не вытеснен машинным")
        self.assertNotIn(HINT, wrote)

    def test_a_refused_send_is_not_dressed_up_as_delivery(self):
        """Отказ моста — тоже расписка, и в дневник он едет как отказ, без подсказки."""
        bridge = _RefusingBridge()
        self.turn(_calls_reply("c1", "проба"), _says("всё"), bridge=bridge)

        hand = self.hands.by("reply")
        self.assertTrue(hand)
        self.assertNotIn(HINT, hand[-1], "к отказу подсказка не клеится и не клеилась")
        wrote = self.journal.by("[ответ]")
        self.assertTrue(wrote)
        self.assertNotIn(HINT, wrote[-1])
        self.assertIn("Не отправила", wrote[-1], "дневник называет отказ отказом")


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
