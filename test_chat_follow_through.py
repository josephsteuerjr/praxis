"""Объявила действие — сделай его этим же ходом, а не обещай.

ЗАМЕР, ИЗ КОТОРОГО ЭТО ВЫРОСЛО (12.08, трое суток прода):

    вид            ходов   медиана рук   без единой руки
    chat_turn        283             0             77%
    task_window       86             6              6%

Механизм в обоих один, потолка у тул-цикла нет — в чате она однажды сделала 49 вызовов.
Значит дело не в «не умеет», а в том, что чат-ход кончается на первом же тексте. Её слова
в личке в тот же день: «я разогналась в объяснения и обещания вместо одного простого
действия — получилось кринжово».

⚠ ГРАНИЦА, КОТОРУЮ ЭТИ ТЕСТЫ ОХРАНЯЮТ. Обычный разговор не трогается ВООБЩЕ. Продолжение
включается по одному узкому признаку: объявила действие и не позвала ни одной руки.
"""
from __future__ import annotations

import unittest
from unittest import mock

import promises
import work_loop


def _on(**extra):
    return mock.patch.dict("os.environ", {"PRAXIS_CHAT_FOLLOW_THROUGH": "on", **extra})


class OrdinaryTalkIsNotAccused(unittest.TestCase):
    """⚠ КОНТРАКТ ИЗМЕНЁН 13.08 ВЕЧЕРОМ, И ЭТО ЗАКАЗАНО.

    Раньше обычная реплика без рук закрывала ход молча. Теперь она получает ОДИН взгляд:
    свой же текст плюс факт «рук в этом ходе: ни одной». Разбор — в `work_loop.mirror` и
    в `test_answer_from_the_source`.

    Но охраняемое здесь не изменилось ни на букву: обычную речь нельзя ОБВИНЯТЬ в
    невыполненном обещании. Зеркало показывает; укол про обещание — только там, где
    обещание было.
    """

    def test_a_plain_reply_gets_the_mirror_and_not_an_accusation(self):
        with _on():
            keep, note = work_loop.chat_decide(
                "Да, согласна. Мне ближе второй вариант, потому что он проще.",
                kind="chat_turn", hands=0, spent=0)
        self.assertTrue(keep)
        self.assertNotIn("и не сделала", note)
        self.assertIn("Напечатала", note)

    def test_conversational_filler_is_not_a_promise(self):
        """Адверсарная прополка `promises` 23.07: «возвращаюсь к твоему вопросу» — речь."""
        for text in ("Возвращаюсь к твоему вопросу: дело в кэше.",
                     "Вернусь к сказанному выше — там я ошиблась.",
                     "Сейчас объясню, почему это не так."):
            with _on():
                _, note = work_loop.chat_decide(text, kind="chat_turn", hands=0, spent=0)
            self.assertNotIn("объявила действие", note,
                             "обычная речь принята за обещание: %r" % text)

    def test_a_speech_act_is_fulfilled_by_the_message_itself(self):
        """«Сейчас расскажу» исполняется ЭТИМ ЖЕ сообщением: рассказ и есть рассказывание.
        Укол «ты объявила и не сделала» был бы здесь прямой неправдой."""
        for text in ("Сейчас расскажу, почему это так.",
                     "Покажу на примере: вот тут ломается.",
                     "Сейчас объясню короче."):
            with _on():
                _, note = work_loop.chat_decide(text, kind="chat_turn", hands=0, spent=0)
            self.assertNotIn("объявила действие", note,
                             "речевой акт принят за невыполненное действие: %r" % text)

    def test_a_turn_that_already_used_a_hand_is_left_alone(self):
        """Работа пошла — решать, договорила ли она, не наше дело."""
        with _on():
            keep, _ = work_loop.chat_decide(
                "Сейчас напишу программу по этому файлу.",
                kind="chat_turn", hands=1, spent=0)
        self.assertFalse(keep)

    def test_the_lever_down_is_the_old_behaviour_byte_for_byte(self):
        with mock.patch.dict("os.environ", {"PRAXIS_CHAT_FOLLOW_THROUGH": "off"}):
            self.assertEqual(
                work_loop.chat_decide("Сейчас напишу и пришлю.", kind="chat_turn",
                                      hands=0, spent=0),
                (False, ""))

    def test_a_work_window_never_takes_this_path(self):
        """У окна свой контракт — её `task_control`. Смешать их значит получить два."""
        with _on():
            keep, _ = work_loop.chat_decide("Сейчас проверю и вернусь.",
                                            kind="task_window", hands=0, spent=0)
        self.assertFalse(keep)


class AnAnnouncedActionDoesNotEndTheTurn(unittest.TestCase):
    def test_she_is_asked_to_do_it_now_instead_of_promising(self):
        with _on():
            keep, note = work_loop.chat_decide(
                "Поняла. Исправляю: сейчас напишу в AbstractDL Chat.",
                kind="chat_turn", hands=0, spent=0)
        self.assertTrue(keep)
        self.assertIn("и не сделала", note)
        self.assertIn("Могу сделать прямо сейчас", note)

    def test_the_живой_случай_from_the_dm_is_caught(self):
        """Дословно из лички 12.08 — тот самый ход, который кончился обещанием."""
        for text in ("Сейчас найду настоящий checkout через serverd и открою задачу.",
                     "Иду чинить, доложу через пару минут."):
            with _on():
                keep, _ = work_loop.chat_decide(text, kind="chat_turn", hands=0, spent=0)
            self.assertTrue(keep, "обещание не поймано: %r" % text)

    def test_the_budget_is_small_and_names_itself_when_spent(self):
        """Чат — не рабочее окно: два продолжения, а не восемь."""
        self.assertEqual(work_loop.DEFAULT_CHAT_CONTINUATIONS, 2)
        with _on():
            keep, note = work_loop.chat_decide("Сейчас напишу.", kind="chat_turn",
                                               hands=0, spent=2)
        self.assertFalse(keep)
        self.assertIn("продолжения кончились", note)
        # Причина назвалась вслух — ход закрыт по бюджету, а не тихо. Формулировка сменилась
        # вместе с признаком: продолжение больше не про одно лишь объявленное действие.
        self.assertIn("сказано как есть", note)

    def test_the_nudge_quotes_her_own_words_back(self):
        with _on():
            _, note = work_loop.chat_decide("Ок, сейчас проверю логи и скажу.",
                                            kind="chat_turn", hands=0, spent=0)
        self.assertIn("проверю логи", note)
        self.assertIn("осталось 1", note)

    def test_the_bare_performative_is_caught_because_the_live_case_was_missed(self):
        """⚑ Дыра, найденная тестом 13.08 и закрытая в САМОМ детекторе, а не рядом.

        Все прежние паттерны требовали временной привязки («сейчас», «через N минут»),
        а её самая частая форма её не имеет. «Исправляюсь: напишу туда, не в канал» —
        дословная реплика из лички 12.08, после которой ход закрылся обещанием.
        """
        for text in ("Исправляюсь: напишу туда, не в канал.",
                     "Исправляю.",
                     "Отправляю в AbstractDL Chat."):
            self.assertTrue(promises.detect(text), "детектор слеп к: %r" % text)
            with _on():
                keep, _ = work_loop.chat_decide(text, kind="chat_turn", hands=0, spent=0)
            self.assertTrue(keep, "обещание не поймано: %r" % text)

    def test_the_performative_stays_narrow_and_does_not_eat_ordinary_speech(self):
        for text in ("Если хочешь, напишу подробнее — скажи.",
                     "Я обычно отправляю такие вещи одним сообщением.",
                     "Он исправляет это сам, без меня."):
            self.assertIsNone(promises.detect(text),
                              "обычная речь принята за обещание: %r" % text)

    def test_the_detector_is_the_one_that_already_existed(self):
        """Новых эвристик не заводим: расхождение двух детекторов одного и того же —
        ровно тот класс, за которым дом охотится."""
        gist = promises.detect("Сейчас напишу в чат.")
        self.assertTrue(gist)
        with _on():
            _, note = work_loop.chat_decide("Сейчас напишу в чат.", kind="chat_turn",
                                            hands=0, spent=0)
        self.assertIn(gist[:30], note)


if __name__ == "__main__":
    unittest.main()
