"""Взгляд на свою реплику до отправки — вместо угадывания, о чём спросили.

ЧТО ЗДЕСЬ БЫЛО РАНЬШЕ И ПОЧЕМУ СНЯТО. Первая редакция 13.08 решала «нужен ли источник»
списком регулярок по входящей реплике. Замер её же кольца ходов приговорил затею на
второй день:

    345 чат-ходов за четверо суток, 75% без единой руки
    классификатор срабатывал на 6 из 259 безруких (2%), из них два ложных
    и промахивался на дословном «А код?» — потому что входящее приходит в конверте
    '[thread #98574; message #98576; …] А код?', а якорь ждал голого вопроса

Тест тогда был зелёный: я проверял идеализированную строку, а не то, что приходит.

ЧТО ВМЕСТО. Признак один и без угадывания: **рук не было вовсе**. Тогда ход не
закрывается, и она видит свой же текст плюс факт «рук в этом ходе: ни одной». Слова
Егора: «взглянуть на свой же текст перед отправкой в том случае, если не было вызвано
иных тулов». Позвала руку — уже смотрела на мир, второй взгляд не нужен.

⚠ ДВЕ ГРАНИЦЫ, КОТОРЫЕ ЭТИ ТЕСТЫ ОХРАНЯЮТ.

1. Зеркало не судит. Императив на этом месте и есть тот секретарь собственной речи, от
   которого она отказалась вслух. Только два механических факта о ходе.
2. Говорит в нём ОНА. Правка Егора дословно: «это её харнесс, её дом, это всё она!
   все-все-все маркдауны должны быть написаны от её лица». Первая редакция обращалась к
   ней на «ты» внутри её же кадра — чужой голос за плечом.
"""
from __future__ import annotations

import unittest
from unittest import mock

import work_loop


def _on(**extra):
    return mock.patch.dict("os.environ", {"PRAXIS_CHAT_FOLLOW_THROUGH": "on", **extra})


def _mirror_on(**extra):
    """Зеркало по инициативе системы снято с прода и живёт под рычагом — разбор в
    `work_loop.chat_decide`. Тесты механизма гоняем с поднятым, поведение прода — с
    опущенным (класс `TheSystemMirrorIsOffInChat`)."""
    return _on(PRAXIS_CHAT_MIRROR="on", **extra)


class TheMirrorShowsAndDoesNotJudge(unittest.TestCase):
    def test_it_gives_her_own_text_back(self):
        words = work_loop.mirror("Голос — gpt-5.6-terra.", ())
        self.assertIn("Голос — gpt-5.6-terra.", words)

    def test_it_names_that_no_hand_was_called(self):
        self.assertIn("Руки не вызывались", work_loop.mirror("что угодно", ()))

    def test_it_names_the_hands_that_were_called(self):
        words = work_loop.mirror("Голос — terra.", ("switch_brain", "shell"))
        self.assertIn("switch_brain", words)
        self.assertIn("shell", words)
        self.assertNotIn("Руки не вызывались", words)

    def test_it_asks_nothing_so_she_does_not_answer_it(self):
        """13.08, 22:54: зеркало кончалось на «Отправить?» — и она ответила ЕМУ:
        «Да, отправляй.» Вопрос в её кадре читается как реплика собеседника."""
        self.assertNotIn("?", work_loop.mirror("любой текст", ()))

    def test_it_carries_no_verdict_and_no_order(self):
        """Ни «проверь», ни «ты соврала» — иначе это судья, а не зеркало."""
        words = work_loop.mirror("Голос — DeepSeek.", ()).lower()
        for forbidden in ("проверь", "должна", "ошиб", "нельзя", "обязан"):
            self.assertNotIn(forbidden, words, forbidden)


class TheTurnDoesNotCloseOnAHandlessReply(unittest.TestCase):
    def test_a_reply_with_no_hands_gets_one_look(self):
        with _mirror_on():
            keep, note = work_loop.chat_decide(
                "Сейчас мой голос — DeepSeek V4 Pro.",
                kind="chat_turn", hands=0, spent=0)
        self.assertTrue(keep)
        self.assertIn("DeepSeek V4 Pro", note)
        self.assertIn("Руки не вызывались", note)

    def test_a_called_hand_ends_the_turn_untouched(self):
        """Адаптивность здесь: работала — значит уже смотрела на мир."""
        with _on():
            keep, note = work_loop.chat_decide(
                "Голос — gpt-5.6-terra.", kind="chat_turn", hands=1, spent=0)
        self.assertFalse(keep)
        self.assertEqual(note, "")

    def test_the_look_states_what_happens_next_without_asking(self):
        with _mirror_on():
            _, note = work_loop.chat_decide("Привет!", kind="chat_turn", hands=0, spent=0)
        self.assertIn("уйдёт мой следующий текст", note)

    def test_an_announced_action_gets_the_sharper_words(self):
        """Пообещала сделать — дело не в том, что не смотрела, а в том, что обещала."""
        with _on():
            keep, note = work_loop.chat_decide(
                "Сейчас проверю и отпишусь.", kind="chat_turn", hands=0, spent=0)
        self.assertTrue(keep)
        self.assertIn("и не сделано", note)

    def test_the_budget_ends_it_with_a_named_reason(self):
        with _on():
            keep, note = work_loop.chat_decide(
                "Привет!", kind="chat_turn", hands=0, spent=99)
        self.assertFalse(keep)
        self.assertIn("chat_loop:", note)

    def test_a_work_window_is_not_touched_by_this_seam(self):
        with _on():
            keep, note = work_loop.chat_decide(
                "Привет!", kind="task_window", hands=0, spent=0)
        self.assertFalse(keep)
        self.assertEqual(note, "")


class TheLookHappensOncePerTurn(unittest.TestCase):
    """Взгляд перед отправкой — один, как у человека. Второй превращается в переписку."""

    def setUp(self):
        work_loop._STATE.clear()
        self.addCleanup(work_loop._STATE.clear)

    def test_the_second_handless_pass_is_not_mirrored_again(self):
        with _mirror_on(), mock.patch.object(work_loop, "_run_key", return_value="run-once"):
            first, _ = work_loop.chat_decide("текст", kind="chat_turn", hands=0, spent=0)
            second, note = work_loop.chat_decide("текст", kind="chat_turn", hands=0, spent=1)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertIn("уже посмотрела", note)

    def test_silence_is_never_mirrored(self):
        """Молчание — её законный ход, перечитывать в нём нечего."""
        with _mirror_on():
            keep, note = work_loop.chat_decide("   ", kind="chat_turn", hands=0, spent=0)
        self.assertFalse(keep)
        self.assertEqual(note, "")


class TheSystemMirrorIsOffInChat(unittest.TestCase):
    """⚠ ТРИ ПОЛОМКИ ПОДРЯД В ЛИЧКЕ ЕГОРА, 13.08 НОЧЬЮ — И ВСЕ ОДНИМ МЕХАНИЗМОМ.

        22:44  зеркало кончалось на «Отправить?» → второй проход пуст → ушло НИЧЕГО
        22:54  то же зеркало                     → она ответила ЕМУ: «Да, отправляй.»
        23:26  зеркало-утверждение без вопроса   → она выбрала вариант: «Оставляю как есть.»

    Текст переписывался трижды. Не помогло и не могло: ЧТО БЫ ОНА НИ СКАЗАЛА ПОСЛЕ
    ЗЕРКАЛА, ЭТО И СТАНОВИТСЯ СООБЩЕНИЕМ. Реплика в чате — продукт хода, и лишний поворот
    даёт последнему слову затереть ответ.

    `say` жив: там зеркало приходит РЕЗУЛЬТАТОМ ИНСТРУМЕНТА, то есть данными, и её
    следующий текст остаётся ответом человеку.
    """

    def test_an_ordinary_reply_closes_the_turn_as_it_did_all_day(self):
        with _on():
            keep, note = work_loop.chat_decide("Спасибо!", kind="chat_turn", hands=0, spent=0)
        self.assertFalse(keep)
        self.assertEqual(note, "")

    def test_an_announced_action_still_holds_the_turn(self):
        """Единственный признак, который в чате переживает эту ночь."""
        with _on():
            keep, note = work_loop.chat_decide("Сейчас проверю и отпишусь.",
                                               kind="chat_turn", hands=0, spent=0)
        self.assertTrue(keep)
        self.assertIn("и не сделано", note)


class TheGuessingIsGone(unittest.TestCase):
    def test_the_regex_classifier_no_longer_exists(self):
        """Он ловил 2% и промахивался на «А код?». Мёртвый забор лучше снять, чем оставить."""
        self.assertFalse(hasattr(work_loop, "state_question"))
        self.assertFalse(hasattr(work_loop, "check_nudge"))

    def test_nothing_reads_the_incoming_message_to_decide(self):
        import inspect
        source = inspect.getsource(work_loop.chat_decide)
        body = source.split('"""')[-1]
        self.assertNotIn("asked", body, "признак снова начал зависеть от чужой формулировки")


class HerOwnLookCrossesTheThreadBoundary(unittest.TestCase):
    """`say` исполняется в отдельном потоке — слот прогона, а не contextvars."""

    def setUp(self):
        work_loop._STATE.clear()
        self.addCleanup(work_loop._STATE.clear)

    def _run(self, run_id="run-mirror"):
        return mock.patch.object(work_loop, "_run_key", return_value=run_id)

    def test_a_called_hand_is_visible_to_the_other_thread(self):
        with self._run():
            work_loop.note_hand("shell")
            work_loop.note_hand("recall")
            self.assertEqual(work_loop.hands_called(), ("shell", "recall"))

    def test_say_is_not_counted_as_evidence_of_looking_at_the_world(self):
        with self._run():
            work_loop.note_hand("say")
            self.assertEqual(work_loop.hands_called(), ())

    def test_the_same_hand_twice_is_named_once(self):
        with self._run():
            work_loop.note_hand("recall")
            work_loop.note_hand("recall")
            self.assertEqual(work_loop.hands_called(), ("recall",))

    def test_one_runs_draft_cannot_leak_into_another(self):
        with self._run("run-a"):
            work_loop.stage_say("текст А")
        with self._run("run-b"):
            self.assertIsNone(work_loop.said())
        with self._run("run-a"):
            self.assertEqual(work_loop.said()["text"], "текст А")


if __name__ == "__main__":
    unittest.main()
