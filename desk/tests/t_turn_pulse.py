# -*- coding: utf-8 -*-
"""Пульс хода в Telegram: «печатает…» всё время хода, пост «думаю…» у долгого (25.09, F)."""
import sys
import threading
import time
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
import turn_pulse  # noqa: E402


class FakeBot:
    def __init__(self, *, last_after_status: bool = True):
        self.typing_calls = 0
        self.posted: list[str] = []
        self.edited: list[tuple[int, str]] = []
        self.deleted: list[int] = []
        self.before_send = None
        self.last_after_status = last_after_status
        self.lock = threading.Lock()

    def typing(self, chat_id):
        with self.lock:
            self.typing_calls += 1

    def post_status(self, chat_id, text):
        with self.lock:
            self.posted.append(text)
        return 100 + len(self.posted)

    def edit_status(self, chat_id, message_id, text):
        with self.lock:
            self.edited.append((message_id, text))
        return True

    def delete_status(self, chat_id, message_id):
        with self.lock:
            self.deleted.append(message_id)
        return True

    def is_last_message(self, chat_id, message_id):
        return self.last_after_status


def _pulse(bot, **kw):
    kw.setdefault("typing_every", 0.02)
    kw.setdefault("status_after", 0.05)
    kw.setdefault("status_every", 0.05)
    return turn_pulse.TurnPulse(bot, "777", **kw)


class Pulse(unittest.TestCase):
    def test_typing_is_repeated_while_the_turn_runs(self):
        bot = FakeBot()
        pulse = _pulse(bot, typing=True, status=False).start()
        time.sleep(0.15)
        pulse.stop()
        self.assertGreaterEqual(bot.typing_calls, 3, "один typing на ход гас через 5 с")
        self.assertEqual(bot.posted, [], "пост без опции не появляется")

    def test_status_is_posted_edited_and_removed_before_the_answer(self):
        bot = FakeBot()
        pulse = _pulse(bot, typing=False, status=True).start()
        self.assertEqual(bot.before_send, pulse.retire_status, "крючок перед отправкой поставлен")
        time.sleep(0.2)
        self.assertEqual(len(bot.posted), 1)
        self.assertTrue(bot.posted[0].startswith("думаю ("), bot.posted[0])
        self.assertGreaterEqual(len(bot.edited), 1, "пост правится раз в «минуту»")
        self.assertIn("уже 1 мин", bot.edited[0][1])
        bot.before_send()          # транспорт отправляет ответ — пост снимается до него
        self.assertEqual(bot.deleted, [101])
        pulse.stop()
        self.assertEqual(bot.deleted, [101], "второй раз не удаляем")
        self.assertIsNone(bot.before_send, "крючок снят после хода")

    def test_status_is_not_edited_when_someone_wrote_after_it(self):
        bot = FakeBot(last_after_status=False)
        pulse = _pulse(bot, typing=False, status=True).start()
        time.sleep(0.2)
        pulse.stop()
        self.assertEqual(len(bot.posted), 1)
        self.assertEqual(bot.edited, [], "человек написал следом — не перебиваем")
        self.assertEqual(bot.deleted, [101], "ход кончился без ответа — пост снят")

    def test_failed_turn_leaves_an_honest_note(self):
        bot = FakeBot()
        pulse = _pulse(bot, typing=False, status=True).start()
        time.sleep(0.08)
        pulse.stop(failed="ход не дошёл до конца")
        self.assertEqual(bot.deleted, [])
        self.assertEqual(bot.edited[-1], (101, "не вышло: ход не дошёл до конца"))

    def test_transport_errors_never_break_the_turn(self):
        class Broken(FakeBot):
            def typing(self, chat_id):
                raise RuntimeError("сеть")

            def post_status(self, chat_id, text):
                raise RuntimeError("сеть")

        pulse = _pulse(Broken(), typing=True, status=True).start()
        time.sleep(0.1)
        pulse.stop()   # не поднимает


if __name__ == "__main__":
    unittest.main()
