"""07.10: три фикса её Telegram-нитьевой механики — по живому инциденту.

1. Дедуп отправки: повтор ЕЁ реплики тому же адресату с тем же текстом внутри
   окна — не новая нить (три письма Роме 17:27/17:39/18:15 с одним текстом).
2. Один входящий ответ в ЛС закрывает ВСЕ более старые pending-нити того же
   адресата, а не только самую свежую (замкнутый цикл «докладывает снова»).
3. Заказанные отчёты, закрытые одним ответом, не плодят N писем: несёт свежая
   нить, остальные гаснут с названной причиной.
4. Приоритетный префикс «!»: split_priority вырезает маркер для решения о
   пробуждении; рычаг PRAXIS_PRIORITY_PREFIX, off = прежнее поведение.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import telegram_followups as followups

VIKA_ID = 555000333


class _Clock:
    def __init__(self, value: float = 1000.0):
        self.value = float(value)

    def __call__(self) -> float:
        return self.value


class SendDedupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="praxis_dedup_")
        self.ledger = followups.FollowUpLedger(Path(self.tmp.name) / "f.json")
        self.clock = _Clock()
        patcher = mock.patch.object(followups.time, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _dm_thread(self, text: str, sent_message_id: int, **extra) -> dict:
        arguments = dict(
            target_ref=str(VIKA_ID), target_label="Вика",
            target_peer_id=VIKA_ID, target_user_id=VIKA_ID,
            sent_message_id=sent_message_id, request_text="",
            sent_at=self.clock(), sent_excerpt=text,
        )
        arguments.update(extra)
        return self.ledger.create(**arguments)

    def test_repeat_same_text_within_window_is_duplicate(self):
        self._dm_thread("напиши Роме про созвон", 101)
        self.clock.value += 25 * 60
        dup = self.ledger.recent_same_text(
            target_peer_id=VIKA_ID, text="напиши Роме про созвон")
        self.assertIsNotNone(dup, "повтор в пределах окна обязан находиться")

    def test_other_text_other_peer_answered_thread_no_duplicate(self):
        self._dm_thread("напиши Роме про созвон", 101)
        self.clock.value += 25 * 60
        self.assertIsNone(self.ledger.recent_same_text(
            target_peer_id=VIKA_ID, text="совсем другая реплика"),
            "другой текст — не дубль")
        self.assertIsNone(self.ledger.recent_same_text(
            target_peer_id=VIKA_ID + 1, text="напиши Роме про созвон"),
            "другой адресат — не дубль")
        answered = self._dm_thread("ещё раз про созвон", 102)
        self.ledger.observe_incoming(
            peer_id=VIKA_ID, sender_id=VIKA_ID, message_id=900,
            text="хорошо", reply_to_message_id=None, received_at=self.clock(),
            sender_name="Вика", sender_is_owner=False)
        self.clock.value += 25 * 60
        self.assertEqual(self.ledger.get(answered["id"])["status"], "answered")
        self.assertIsNone(self.ledger.recent_same_text(
            target_peer_id=VIKA_ID, text="ещё раз про созвон"),
            "нить без ответа была дублем; с ответом — уже нет")

    def test_expired_window_no_duplicate(self):
        self._dm_thread("напиши Роме про созвон", 101)
        self.clock.value += 2 * 3600
        self.assertIsNone(self.ledger.recent_same_text(
            target_peer_id=VIKA_ID, text="напиши Роме про созвон"),
            "за пределами окна повтор разрешён")

    def test_zero_window_disables_dedup(self):
        self._dm_thread("напиши Роме про созвон", 101)
        self.clock.value += 60
        with mock.patch.dict(os.environ, {"PRAXIS_FOLLOWUP_DEDUP_SEC": "0"}):
            self.assertIsNone(self.ledger.recent_same_text(
                target_peer_id=VIKA_ID, text="напиши Роме про созвон"),
                "PRAXIS_FOLLOWUP_DEDUP_SEC=0 обязан выключать дедуп")


class SweepOlderThreadsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="praxis_sweep_")
        self.ledger = followups.FollowUpLedger(Path(self.tmp.name) / "f.json")
        self.clock = _Clock()
        patcher = mock.patch.object(followups.time, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _dm_thread(self, text: str, sent_message_id: int, **extra) -> dict:
        arguments = dict(
            target_ref=str(VIKA_ID), target_label="Вика",
            target_peer_id=VIKA_ID, target_user_id=VIKA_ID,
            sent_message_id=sent_message_id, request_text="",
            sent_at=self.clock(), sent_excerpt=text,
        )
        arguments.update(extra)
        return self.ledger.create(**arguments)

    def test_answer_closes_all_older_pending_threads(self):
        first = self._dm_thread("вопрос 1", 101)
        self.clock.value += 600
        second = self._dm_thread("вопрос 2", 102)
        self.clock.value += 600
        matched = self.ledger.observe_incoming(
            peer_id=VIKA_ID, sender_id=VIKA_ID, message_id=900,
            text="сделаю", reply_to_message_id=None, received_at=self.clock(),
            sender_name="Вика", sender_is_owner=False)
        self.assertIsNotNone(matched)
        first = self.ledger.get(first["id"])
        second = self.ledger.get(second["id"])
        self.assertEqual(first["status"], "answered",
                         "старая нить обязана закрыться позднейшим ответом")
        self.assertEqual(second["status"], "answered")
        self.assertTrue(first["response"].get("superseded_by_later_message"))

    def test_one_answer_bears_one_owner_notice(self):
        owed = []
        for n, mid in enumerate((101, 102, 103)):
            owed.append(self._dm_thread(
                f"вопрос {n}", mid, notify_owner=True, notice_source="owner"))
            self.clock.value += 600
        matched = self.ledger.observe_incoming(
            peer_id=VIKA_ID, sender_id=VIKA_ID, message_id=900,
            text="сделаю", reply_to_message_id=None, received_at=self.clock(),
            sender_name="Вика", sender_is_owner=False)
        self.assertIsNotNone(matched)
        owing = [self.ledger.get(x["id"]) for x in owed]
        carriers = [x for x in owing if followups._owes_owner_notice(x)]
        self.assertEqual(len(carriers), 1,
                         "письмо Егору несёт ровно одна нить, не три")
        skipped = [x for x in owing
                   if x.get("notice_skipped") == "тот же ответ — отчёт уходит по свежей нити"]
        self.assertEqual(len(skipped), 2, "остальные гаснут с названной причиной")

    def test_other_addressee_and_group_peer_unaffected(self):
        group = self.ledger.create(
            target_ref="-1001240718803", target_label="AbstractDL",
            target_peer_id=-1001240718803, target_user_id=None,
            sent_message_id=55, request_text="", sent_at=self.clock() - 100,
            sent_excerpt="вопрос в группу")
        other_person = self.ledger.create(
            target_ref=str(VIKA_ID + 7), target_label="Другой человек",
            target_peer_id=VIKA_ID + 7, target_user_id=VIKA_ID + 7,
            sent_message_id=77, request_text="", sent_at=self.clock(),
            sent_excerpt="вопрос другому")
        self._dm_thread("вопрос Вике", 104)
        self.clock.value += 600
        matched = self.ledger.observe_incoming(
            peer_id=VIKA_ID, sender_id=VIKA_ID, message_id=900,
            text="ответ", reply_to_message_id=None, received_at=self.clock(),
            sender_name="Вика", sender_is_owner=False)
        self.assertIsNotNone(matched)
        self.assertEqual(
            self.ledger.get(other_person["id"])["status"], "pending",
            "нить ДРУГОМУ адресату не закрывается ответом Вики")
        self.assertEqual(
            self.ledger.get(group["id"])["status"], "pending",
            "групповой peer без target_user_id не закрывается DM-ответом")


try:
    import mtproto_runner  # noqa: E402
    _RUNNER_OK = True
except Exception as _exc:  # telethon и прочая обвязка есть только в гейте/контейнере
    _RUNNER_OK = False
    _RUNNER_EXC = _exc


@unittest.skipUnless(_RUNNER_OK, f"mtproto_runner недоступен вне гейта ({_RUNNER_EXC}); полный прогон — в Docker-гейте")
class RunnerPriorityDelegateTests(unittest.TestCase):
    def test_runner_delegates_to_ledger(self):
        import inspect
        source = inspect.getsource(mtproto_runner.split_priority)
        self.assertIn("telegram_followups.split_priority", source,
                      "раннер обязан делегировать парсер леджеру (одна точка правды)")


class PriorityPrefixTests(unittest.TestCase):
    def _split(self, text, env=None):
        if env is None:
            return followups.split_priority(text)
        with mock.patch.dict(os.environ, env):
            return followups.split_priority(text)

    def test_default_marker_exclamation(self):
        self.assertEqual(self._split("! срочно напиши Роме"), (True, "срочно напиши Роме"))
        self.assertEqual(self._split("!!двойной"), (True, "!двойной"))
        self.assertEqual(self._split("  ! сначала пробел"), (True, "сначала пробел"))

    def test_plain_text_unaffected(self):
        self.assertEqual(self._split("обычное сообщение"), (False, "обычное сообщение"))
        self.assertEqual(self._split("восклицание в конце!"), (False, "восклицание в конце!"))
        self.assertEqual(self._split("!"), (False, "!"))
        self.assertEqual(self._split(""), (False, ""))

    def test_custom_marker_and_off(self):
        self.assertEqual(
            self._split("СРОЧНО: позвони", {"PRAXIS_PRIORITY_PREFIX": "СРОЧНО:"}),
            (True, "позвони"))
        self.assertEqual(
            self._split("! не marker", {"PRAXIS_PRIORITY_PREFIX": "off"}),
            (False, "! не marker"))


if __name__ == "__main__":
    unittest.main()
